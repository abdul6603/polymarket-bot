"""Snipe Executor — GTC LIMIT orders on CLOB for snipe trades.

Strategy: Place GTC (Good Till Cancelled) LIMIT BUY orders at $0.65.
Unlike FAK (Fill and Kill), GTC orders rest on the book when liquidity is thin.
This matches the whale approach (Feisty-Garage: LIMIT at $0.62-$0.63).

Benefits over FAK:
  - Survives empty order books (rests as a bid, attracts sellers)
  - Zero taker fees (maker order) + earns maker rebates
  - Gets filled at resting sell prices (often cheaper than our limit)
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

# GTC LIMIT order config
# Price cap $0.65: whale range ($0.62-$0.63), sweeps book up to this price
# Entry at T-120s: direction confirmed, book still has liquidity
WAVES = [
    (1, 1.00, 0.65, 120),   # GTC LIMIT: 100% budget, $0.65 cap, T-120s
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
    asset: str = "bitcoin"
    waves: list[WaveResult] = field(default_factory=list)
    total_size_usd: float = 0.0
    total_shares: float = 0.0
    avg_entry: float = 0.0
    started_at: float = field(default_factory=time.time)


class PyramidExecutor:
    """Executes GTC LIMIT orders on CLOB."""

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
        self._pending_order_id: str | None = None
        self._pending_token_id: str = ""

    def start_position(self, market_id: str, direction: str, open_price: float, asset: str = "bitcoin") -> None:
        """Initialize a new snipe position for this window."""
        self._active_position = SnipePosition(
            market_id=market_id,
            direction=direction,
            open_price=open_price,
            asset=asset,
        )
        self._pending_order_id = None
        log.info(
            "[SNIPE] Position started: %s %s %s (open=$%.2f)",
            asset.upper(), direction.upper(), market_id[:12], open_price,
        )

    def should_fire_wave(self, wave_num: int, remaining_s: float, implied_price: float) -> bool:
        """Check if conditions are met to fire a specific wave."""
        if not self._active_position:
            return False

        fired_waves = {w.wave_num for w in self._active_position.waves}
        if wave_num in fired_waves:
            return False
        for prev in range(1, wave_num):
            if prev not in fired_waves:
                return False

        _, _, price_cap, fire_below = WAVES[wave_num - 1]
        if remaining_s > fire_below:
            return False
        if implied_price > price_cap:
            log.info("[SNIPE] Wave %d blocked: price $%.3f > cap $%.3f", wave_num, implied_price, price_cap)
            return False
        return True

    def execute_wave(
        self,
        wave_num: int,
        token_id: str,
        implied_price: float,
    ) -> WaveResult | None:
        """Execute a single wave — places GTC LIMIT order."""
        if not self._active_position:
            return None

        _, budget_frac, price_cap, _ = WAVES[wave_num - 1]
        size_usd = self._budget * budget_frac

        price = price_cap
        price = max(0.01, min(0.99, round(price, 2)))
        shares = int(size_usd / price)

        if self._dry_run:
            order_id = f"snipe-dry-w{wave_num}-{int(time.time())}"
            log.info(
                "[SNIPE][DRY] GTC Wave %d: %s %.1f shares @ $%.3f ($%.2f) | %s",
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
            self._record_fill(result)
            return result
        else:
            result = self._place_gtc_order(wave_num, token_id, price, shares, size_usd)
            if result:
                self._record_fill(result)
            return result

    def _record_fill(self, result: WaveResult) -> None:
        """Record a fill in the active position."""
        if not self._active_position:
            return
        self._active_position.waves.append(result)
        self._active_position.total_size_usd += result.size_usd
        self._active_position.total_shares += result.shares
        total_cost = sum(w.size_usd for w in self._active_position.waves)
        total_shares = sum(w.shares for w in self._active_position.waves)
        self._active_position.avg_entry = total_cost / total_shares if total_shares > 0 else 0
        log.info(
            "[SNIPE] Fill recorded | $%.2f invested, %.1f shares, avg=$%.3f",
            self._active_position.total_size_usd,
            self._active_position.total_shares, self._active_position.avg_entry,
        )

    def _place_gtc_order(
        self,
        wave_num: int,
        token_id: str,
        price: float,
        shares: float,
        size_usd: float,
    ) -> WaveResult | None:
        """Place a GTC LIMIT order on CLOB.

        GTC sweeps existing sells up to our price, then rests the remainder as a bid.
        Fills at resting sell prices (often cheaper than our limit).
        Zero taker fees as maker + earns rebates.
        """
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
            status = resp.get("status", "")

            log.info("[SNIPE] CLOB GTC response: %s", json.dumps(resp)[:500])

            if status.lower() in ("matched", "filled"):
                # Parse fill data only for filled orders
                actual_shares, actual_price, actual_size = self._parse_fill_data(
                    resp, shares, price, size_usd
                )
                if actual_shares < 1:
                    log.warning("[SNIPE] GTC near-zero fill: %.2f shares | %s", actual_shares, order_id)
                    return None
                log.info(
                    "[SNIPE][LIVE] GTC FILLED: %s %.1f shares @ $%.3f ($%.2f) | %s",
                    self._active_position.direction.upper(),
                    actual_shares, actual_price, actual_size, order_id,
                )
                self._pending_order_id = None
                return WaveResult(
                    wave_num=wave_num,
                    direction=self._active_position.direction,
                    size_usd=actual_size,
                    price=actual_price,
                    shares=actual_shares,
                    token_id=token_id,
                    order_id=order_id,
                    filled=True,
                )

            elif status.lower() == "live":
                self._pending_order_id = order_id
                self._pending_token_id = token_id

                # Check for partial immediate fills
                taking = resp.get("takingAmount") or ""
                making = resp.get("makingAmount") or ""
                if taking and making:
                    partial_shares = float(taking)
                    partial_size = float(making)
                    partial_price = partial_size / partial_shares if partial_shares > 0 else price
                    if partial_shares >= 1:
                        log.info(
                            "[SNIPE][LIVE] GTC PARTIAL: %.1f/%d shares @ $%.3f + resting | %s",
                            partial_shares, shares, partial_price, order_id,
                        )
                        return WaveResult(
                            wave_num=wave_num,
                            direction=self._active_position.direction,
                            size_usd=round(partial_size, 2),
                            price=partial_price,
                            shares=partial_shares,
                            token_id=token_id,
                            order_id=order_id,
                            filled=True,
                        )

                log.info(
                    "[SNIPE] GTC RESTING at $%.3f (%d shares) | %s",
                    price, shares, order_id,
                )
                return None

            else:
                log.warning("[SNIPE] GTC unexpected status: %s | %s", status, order_id)
                return None

        except Exception as e:
            log.error("[SNIPE] GTC order failed (wave %d): %s", wave_num, str(e)[:200])
            return None

    def _parse_fill_data(
        self, resp: dict, default_shares: float, default_price: float, default_size: float
    ) -> tuple[float, float, float]:
        """Parse actual fill data from CLOB response."""
        actual_shares = 0.0
        actual_price = default_price
        actual_size = 0.0

        taking = resp.get("takingAmount") or None  # "" → None
        making = resp.get("makingAmount") or None  # "" → None
        if taking is not None and making is not None:
            actual_shares = float(taking)
            actual_size = float(making)
            actual_price = actual_size / actual_shares if actual_shares > 0 else default_price
        else:
            avg_price = resp.get("averagePrice") or resp.get("average_price")
            if avg_price is not None:
                actual_price = float(avg_price)
            matched = resp.get("matchedAmount") or resp.get("matched_amount")
            if matched is not None:
                actual_shares = float(matched)
            else:
                actual_shares = default_shares
            actual_size = round(actual_shares * actual_price, 2)

        return actual_shares, actual_price, actual_size

    def cancel_pending_order(self) -> None:
        """Cancel any resting GTC order."""
        if not self._pending_order_id or not self._client:
            self._pending_order_id = None
            return
        try:
            self._client.cancel(self._pending_order_id)
            log.info("[SNIPE] Cancelled GTC order %s", self._pending_order_id)
        except Exception as e:
            log.warning("[SNIPE] Cancel failed: %s", str(e)[:150])
        self._pending_order_id = None

    def poll_pending_order(self) -> WaveResult | None:
        """Check if resting GTC order got filled. Returns WaveResult if filled."""
        if not self._pending_order_id or not self._client or not self._active_position:
            return None
        try:
            order = self._client.get_order(self._pending_order_id)
            if not order:
                return None

            status = (order.get("status") or "").lower()
            size_matched = float(
                order.get("size_matched") or order.get("sizeMatched") or 0
            )
            original_size = float(
                order.get("original_size") or order.get("originalSize")
                or order.get("size") or 0
            )
            avg_price = float(
                order.get("associate_trades_avg_price")
                or order.get("average_price")
                or order.get("price") or 0
            )

            if size_matched < 1:
                return None

            fill_pct = size_matched / original_size if original_size > 0 else 0

            if status in ("matched", "filled") or fill_pct >= 0.90:
                actual_size = round(size_matched * avg_price, 2)
                log.info(
                    "[SNIPE] GTC FILLED (poll): %.0f/%.0f shares @ $%.3f ($%.2f)",
                    size_matched, original_size, avg_price, actual_size,
                )
                oid = self._pending_order_id
                self._pending_order_id = None
                result = WaveResult(
                    wave_num=1,
                    direction=self._active_position.direction,
                    size_usd=actual_size,
                    price=avg_price,
                    shares=size_matched,
                    token_id=self._pending_token_id,
                    order_id=oid or "unknown",
                    filled=True,
                )
                self._record_fill(result)
                return result

            if size_matched > 0:
                log.info(
                    "[SNIPE] GTC partial: %.0f/%.0f shares (%.0f%%)",
                    size_matched, original_size, fill_pct * 100,
                )
            return None

        except Exception as e:
            log.warning("[SNIPE] Poll order error: %s", str(e)[:150])
            return None

    def finalize_partial_fill(self) -> WaveResult | None:
        """After cancelling, check if any partial fills happened and record them."""
        if not self._pending_order_id or not self._client or not self._active_position:
            return None
        try:
            order = self._client.get_order(self._pending_order_id)
            if not order:
                return None
            size_matched = float(
                order.get("size_matched") or order.get("sizeMatched") or 0
            )
            avg_price = float(
                order.get("associate_trades_avg_price")
                or order.get("average_price")
                or order.get("price") or 0
            )
            if size_matched >= 1:
                actual_size = round(size_matched * avg_price, 2)
                result = WaveResult(
                    wave_num=1,
                    direction=self._active_position.direction,
                    size_usd=actual_size,
                    price=avg_price,
                    shares=size_matched,
                    token_id=self._pending_token_id,
                    order_id=self._pending_order_id or "unknown",
                    filled=True,
                )
                self._record_fill(result)
                return result
        except Exception:
            pass
        return None

    def close_position(self, resolved_direction: str = "") -> dict | None:
        """Close active position after window resolves. Returns result summary."""
        pos = self._active_position
        if not pos or not pos.waves:
            self._active_position = None
            self._pending_order_id = None
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
            "asset": pos.asset,
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
        self._pending_order_id = None

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

    @property
    def has_pending_order(self) -> bool:
        return self._pending_order_id is not None

    def get_status(self) -> dict:
        """Dashboard-friendly status."""
        pos = self._active_position
        if pos:
            return {
                "active": True,
                "market_id": pos.market_id[:12],
                "asset": pos.asset,
                "direction": pos.direction,
                "waves_fired": len(pos.waves),
                "total_invested": round(pos.total_size_usd, 2),
                "total_shares": round(pos.total_shares, 2),
                "avg_entry": round(pos.avg_entry, 4),
                "pending_order": self._pending_order_id is not None,
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
