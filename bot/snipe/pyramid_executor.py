"""Pyramid Executor — places 3-wave orders on CLOB for snipe trades.

Wave structure:
  Wave 1 (T-180s to T-120s): 40% of budget, price cap $0.60
  Wave 2 (T-120s to T-60s):  35% of budget, price cap $0.70
  Wave 3 (T-60s to T-0s):    25% of budget, price cap $0.75
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from bot.config import Config

log = logging.getLogger("garves.snipe")

SNIPE_TRADES_FILE = Path(__file__).parent.parent.parent / "data" / "snipe_trades.jsonl"

# (wave_num, budget_fraction, price_cap, fire_when_remaining_below)
WAVES = [
    (1, 0.40, 0.60, 180),   # Wave 1: 40%, cap $0.60, T-180s
    (2, 0.35, 0.70, 120),   # Wave 2: 35%, cap $0.70, T-120s
    (3, 0.25, 0.75, 65),    # Wave 3: 25%, cap $0.75, T-65s
]


@dataclass
class WaveResult:
    """Result of a single wave execution."""
    wave_num: int
    direction: str
    size_usd: float
    price: float
    shares: float
    token_id: str
    order_id: str
    filled: bool
    timestamp: float = field(default_factory=time.time)


@dataclass
class SnipePosition:
    """Tracks all waves for a single window."""
    market_id: str
    direction: str
    open_price: float
    waves: list[WaveResult] = field(default_factory=list)
    total_size_usd: float = 0.0
    total_shares: float = 0.0
    avg_entry: float = 0.0
    started_at: float = field(default_factory=time.time)


class PyramidExecutor:
    """Executes 3-wave pyramid entries on CLOB."""

    def __init__(
        self,
        cfg: Config,
        clob_client,
        dry_run: bool = True,
        budget_per_window: float = 50.0,
    ):
        self._cfg = cfg
        self._client = clob_client
        self._dry_run = dry_run
        self._budget = budget_per_window
        self._active_position: SnipePosition | None = None
        self._completed: list[SnipePosition] = []

    def start_position(self, market_id: str, direction: str, open_price: float) -> None:
        """Initialize a new snipe position for this window."""
        self._active_position = SnipePosition(
            market_id=market_id,
            direction=direction,
            open_price=open_price,
        )
        log.info(
            "[SNIPE] Position started: %s %s (BTC open=$%.2f)",
            direction.upper(), market_id[:12], open_price,
        )

    def should_fire_wave(self, wave_num: int, remaining_s: float, implied_price: float) -> bool:
        """Check if conditions are met to fire a specific wave."""
        if not self._active_position:
            return False

        fired_waves = {w.wave_num for w in self._active_position.waves}

        # Already fired?
        if wave_num in fired_waves:
            return False

        # Previous waves must be fired first
        for prev in range(1, wave_num):
            if prev not in fired_waves:
                return False

        _, _, price_cap, fire_below = WAVES[wave_num - 1]

        # Timing: fire when remaining_s drops below threshold
        if remaining_s > fire_below:
            return False

        # Price cap: don't overpay
        if implied_price > price_cap:
            log.info(
                "[SNIPE] Wave %d blocked: price $%.3f > cap $%.3f",
                wave_num, implied_price, price_cap,
            )
            return False

        return True

    def execute_wave(
        self,
        wave_num: int,
        token_id: str,
        implied_price: float,
    ) -> WaveResult | None:
        """Execute a single wave of the pyramid."""
        if not self._active_position:
            return None

        _, budget_frac, price_cap, _ = WAVES[wave_num - 1]
        size_usd = self._budget * budget_frac

        price = min(implied_price, price_cap)
        price = max(0.01, min(0.99, round(price, 2)))
        shares = size_usd / price

        if self._dry_run:
            order_id = f"snipe-dry-w{wave_num}-{int(time.time())}"
            log.info(
                "[SNIPE][DRY] Wave %d: %s %.1f shares @ $%.3f ($%.2f) | %s",
                wave_num, self._active_position.direction.upper(),
                shares, price, size_usd, order_id,
            )
            result = WaveResult(
                wave_num=wave_num,
                direction=self._active_position.direction,
                size_usd=size_usd,
                price=price,
                shares=shares,
                token_id=token_id,
                order_id=order_id,
                filled=True,
            )
        else:
            result = self._place_live_order(wave_num, token_id, price, shares, size_usd)
            if not result:
                return None

        self._active_position.waves.append(result)
        self._active_position.total_size_usd += size_usd
        self._active_position.total_shares += shares

        # Weighted average entry
        total_cost = sum(w.price * w.shares for w in self._active_position.waves)
        total_shares = sum(w.shares for w in self._active_position.waves)
        self._active_position.avg_entry = total_cost / total_shares if total_shares > 0 else 0

        log.info(
            "[SNIPE] Wave %d done | Cumulative: $%.2f invested, %.1f shares, avg=$%.3f",
            wave_num, self._active_position.total_size_usd,
            self._active_position.total_shares, self._active_position.avg_entry,
        )
        return result

    def _place_live_order(
        self,
        wave_num: int,
        token_id: str,
        price: float,
        shares: float,
        size_usd: float,
    ) -> WaveResult | None:
        """Place a live order on CLOB."""
        if not self._client:
            log.error("[SNIPE] No CLOB client for live order")
            return None

        try:
            from py_clob_client.clob_types import OrderArgs, OrderType
            from py_clob_client.order_builder.constants import BUY

            order_args = OrderArgs(
                price=price,
                size=shares,
                side=BUY,
                token_id=token_id,
            )
            signed_order = self._client.create_order(order_args)
            resp = self._client.post_order(signed_order, OrderType.GTC)
            order_id = resp.get("orderID") or resp.get("id", "unknown")

            log.info(
                "[SNIPE][LIVE] Wave %d: %s %.1f shares @ $%.3f ($%.2f) | %s",
                wave_num, self._active_position.direction.upper(),
                shares, price, size_usd, order_id,
            )

            return WaveResult(
                wave_num=wave_num,
                direction=self._active_position.direction,
                size_usd=size_usd,
                price=price,
                shares=shares,
                token_id=token_id,
                order_id=order_id,
                filled=True,
            )
        except Exception as e:
            log.error("[SNIPE] Live order failed (wave %d): %s", wave_num, str(e)[:200])
            return None

    def close_position(self, resolved_direction: str = "") -> dict | None:
        """Close active position after window resolves. Returns result summary."""
        pos = self._active_position
        if not pos or not pos.waves:
            self._active_position = None
            return None

        won = None
        if resolved_direction:
            won = resolved_direction.lower() == pos.direction.lower()

        pnl = 0.0
        if won is True:
            pnl = pos.total_shares - pos.total_size_usd
        elif won is False:
            pnl = -pos.total_size_usd

        result = {
            "market_id": pos.market_id,
            "direction": pos.direction,
            "waves": len(pos.waves),
            "total_size_usd": round(pos.total_size_usd, 2),
            "total_shares": round(pos.total_shares, 2),
            "avg_entry": round(pos.avg_entry, 4),
            "pnl_usd": round(pnl, 2),
            "won": won,
            "open_price": pos.open_price,
            "hold_s": round(time.time() - pos.started_at),
        }

        self._log_trade(result)
        self._completed.append(pos)
        self._active_position = None

        status = "WIN" if won else "LOSS" if won is False else "PENDING"
        log.info(
            "[SNIPE] Position closed: %s %s | %d waves | $%.2f invested | "
            "avg=$%.3f | PnL $%+.2f | %s",
            pos.direction.upper(), pos.market_id[:12], len(pos.waves),
            pos.total_size_usd, pos.avg_entry, pnl, status,
        )
        return result

    def _log_trade(self, result: dict) -> None:
        """Append trade to snipe trades JSONL file."""
        try:
            SNIPE_TRADES_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(SNIPE_TRADES_FILE, "a") as f:
                f.write(json.dumps({**result, "timestamp": time.time()}) + "\n")
        except Exception:
            pass

    @property
    def has_active_position(self) -> bool:
        return self._active_position is not None

    @property
    def active_direction(self) -> str:
        if self._active_position:
            return self._active_position.direction
        return ""

    @property
    def waves_fired(self) -> int:
        if not self._active_position:
            return 0
        return len(self._active_position.waves)

    def get_status(self) -> dict:
        """Dashboard-friendly status."""
        pos = self._active_position
        if pos:
            return {
                "active": True,
                "market_id": pos.market_id[:12],
                "direction": pos.direction,
                "waves_fired": len(pos.waves),
                "total_invested": round(pos.total_size_usd, 2),
                "total_shares": round(pos.total_shares, 2),
                "avg_entry": round(pos.avg_entry, 4),
            }
        return {"active": False}

    def get_history(self, limit: int = 20) -> list[dict]:
        """Recent completed snipe trades."""
        trades = []
        try:
            if SNIPE_TRADES_FILE.exists():
                lines = SNIPE_TRADES_FILE.read_text().strip().split("\n")
                for line in lines[-limit:]:
                    if line.strip():
                        trades.append(json.loads(line))
        except Exception:
            pass
        return trades
