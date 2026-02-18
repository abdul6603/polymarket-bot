from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from bot.config import Config
from bot.signals import Signal

log = logging.getLogger(__name__)

BALANCE_CACHE_FILE = Path(__file__).parent.parent / "data" / "polymarket_balance.json"
TRADES_FILE = Path(__file__).parent.parent / "data" / "trades.jsonl"


@dataclass
class Position:
    market_id: str
    token_id: str
    direction: str
    size_usd: float          # actual USD value of position (shares * current_price)
    entry_price: float
    order_id: str
    shares: float = 0.0      # actual share count from chain
    opened_at: float = field(default_factory=time.time)
    strategy: str = "directional"  # "directional" or "straddle"


class PositionTracker:
    """In-memory tracker for open positions, synced from real Polymarket balances."""

    def __init__(self):
        self._positions: dict[str, Position] = {}  # order_id -> Position

    def sync_from_chain(self, client) -> None:
        """Sync positions from real on-chain Polymarket token balances.

        Queries the CLOB API for actual share balances of all tokens
        we've ever traded, so the tracker matches reality.
        """
        if client is None:
            log.info("No CLOB client — skipping chain sync, falling back to trades.jsonl")
            self._seed_from_trades()
            return

        try:
            from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
        except ImportError:
            log.warning("py_clob_client not available — falling back to trades.jsonl")
            self._seed_from_trades()
            return

        # Collect unique token_ids from recent trades (last 200)
        token_meta = {}  # token_id -> {asset, direction, market_id, question}
        if TRADES_FILE.exists():
            try:
                with open(TRADES_FILE) as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        rec = json.loads(line)
                        if rec.get("dry_run", True):
                            continue
                        tid = rec.get("token_id", "")
                        if tid and tid not in token_meta:
                            token_meta[tid] = {
                                "asset": rec.get("asset", "unknown"),
                                "direction": rec.get("direction", "unknown"),
                                "market_id": rec.get("market_id", ""),
                                "question": rec.get("question", ""),
                                "probability": rec.get("probability", 0.5),
                            }
            except Exception:
                log.exception("Failed to read trades for chain sync")

        if not token_meta:
            log.info("No trade history — tracker starts empty")
            return

        # Check which markets are still open (only count active risk)
        from bot.http_session import get_session
        open_markets: set[str] = set()
        checked_markets: set[str] = set()
        for meta in token_meta.values():
            mid = meta["market_id"]
            if mid in checked_markets:
                continue
            checked_markets.add(mid)
            try:
                resp = get_session().get(
                    f"https://clob.polymarket.com/markets/{mid}", timeout=10,
                )
                if resp.status_code == 200 and not resp.json().get("closed", True):
                    open_markets.add(mid)
            except Exception:
                pass

        log.info("[CHAIN SYNC] %d/%d markets still open", len(open_markets), len(checked_markets))

        # Query on-chain balance for tokens in OPEN markets only
        synced = 0
        _market_prices: dict[str, dict[str, float]] = {}  # market_id -> {token_id -> price}
        for token_id, meta in token_meta.items():
            if meta["market_id"] not in open_markets:
                continue  # skip closed/resolved markets — those are just unclaimed winnings

            try:
                params = BalanceAllowanceParams(
                    asset_type=AssetType.CONDITIONAL,
                    token_id=token_id,
                )
                result = client.get_balance_allowance(params)
                shares = int(result.get("balance", "0")) / 1e6

                if shares <= 0.5:  # ignore dust
                    continue

                # Get current price from CLOB market endpoint (not orderbook — too thin)
                current_price = meta["probability"]  # fallback to entry price
                mid = meta["market_id"]
                if mid in _market_prices:
                    # Already fetched this market's prices
                    current_price = _market_prices[mid].get(token_id, current_price)
                else:
                    try:
                        resp = get_session().get(
                            f"https://clob.polymarket.com/markets/{mid}", timeout=10,
                        )
                        if resp.status_code == 200:
                            for t in resp.json().get("tokens", []):
                                tp = t.get("price")
                                tid_check = t.get("token_id", "")
                                if tp is not None:
                                    _market_prices.setdefault(mid, {})[tid_check] = float(tp)
                            current_price = _market_prices.get(mid, {}).get(
                                token_id, current_price
                            )
                    except Exception:
                        pass  # keep entry price

                # Skip resolved positions (price hit $1 or $0 = already won/lost)
                if current_price >= 0.99 or current_price <= 0.01:
                    log.info(
                        "[CHAIN SYNC] %s %s: %.1f shares @ $%.3f — RESOLVED (not counting as exposure)",
                        meta["asset"].upper(), meta["direction"], shares, current_price,
                    )
                    continue

                size_usd = shares * current_price
                pos_key = f"chain_{token_id[:16]}"

                self._positions[pos_key] = Position(
                    market_id=meta["market_id"],
                    token_id=token_id,
                    direction=meta["direction"],
                    size_usd=round(size_usd, 2),
                    entry_price=meta["probability"],
                    order_id=pos_key,
                    shares=round(shares, 1),
                )
                synced += 1
                log.info(
                    "[CHAIN SYNC] %s %s: %.1f shares @ $%.3f = $%.2f | %s",
                    meta["asset"].upper(), meta["direction"],
                    shares, current_price, size_usd,
                    meta["question"][:50],
                )

            except Exception as e:
                log.debug("Chain sync failed for token %s: %s", token_id[:16], str(e)[:100])

        total_exp = self.total_exposure
        log.info(
            "[CHAIN SYNC] Synced %d real positions from Polymarket (total exposure: $%.2f)",
            synced, total_exp,
        )

    def _seed_from_trades(self) -> None:
        """Fallback: load unresolved trades from disk when no CLOB client available."""
        if not TRADES_FILE.exists():
            return
        try:
            with open(TRADES_FILE) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    if rec.get("dry_run", True) or rec.get("resolved", False):
                        continue
                    order_id = rec.get("trade_id", "")
                    if order_id in self._positions:
                        continue
                    self._positions[order_id] = Position(
                        market_id=rec.get("market_id", ""),
                        token_id=rec.get("token_id", ""),
                        direction=rec.get("direction", ""),
                        size_usd=35.0,
                        entry_price=rec.get("probability", 0.5),
                        order_id=order_id,
                    )
            if self._positions:
                log.info(
                    "Seeded %d positions from trades.jsonl (est. exposure: $%.0f)",
                    len(self._positions), self.total_exposure,
                )
        except Exception:
            log.exception("Failed to seed from trades.jsonl")

    @property
    def open_positions(self) -> list[Position]:
        return list(self._positions.values())

    @property
    def total_exposure(self) -> float:
        return sum(p.size_usd for p in self._positions.values())

    @property
    def count(self) -> int:
        return len(self._positions)

    def add(self, pos: Position) -> None:
        self._positions[pos.order_id] = pos
        log.info(
            "Opened position: %s %s $%.2f @ %.3f (order %s)",
            pos.direction, pos.token_id[:16], pos.size_usd, pos.entry_price, pos.order_id,
        )

    def remove(self, order_id: str) -> Position | None:
        pos = self._positions.pop(order_id, None)
        if pos:
            log.info("Closed position: order %s", order_id)
        return pos

    def remove_resolved_trade(self, trade_id: str) -> None:
        """Remove a position when its trade resolves (called by PerformanceTracker)."""
        if trade_id in self._positions:
            del self._positions[trade_id]

    def remove_by_token(self, token_id: str) -> None:
        """Remove all positions for a token (used when chain sync detects 0 balance)."""
        to_remove = [k for k, p in self._positions.items() if p.token_id == token_id]
        for k in to_remove:
            del self._positions[k]

    def has_position_for_market(self, market_id: str) -> bool:
        return any(p.market_id == market_id for p in self._positions.values())


def _get_real_positions_value() -> float | None:
    """Read the cached Polymarket positions value from the balance file."""
    try:
        if not BALANCE_CACHE_FILE.exists():
            return None
        cached = json.loads(BALANCE_CACHE_FILE.read_text())
        if time.time() - cached.get("fetched_at", 0) > 300:
            return None
        return cached.get("positions_value")
    except Exception:
        return None


MAX_TOTAL_EXPOSURE = 150.0  # Hard cap — never exceed $150 in total positions
MAX_SINGLE_POSITION = 50.0  # Hard cap — no single trade > $50


def check_risk(
    cfg: Config,
    signal: Signal,
    tracker: PositionTracker,
    market_id: str,
    trade_size_usd: float | None = None,
) -> tuple[bool, str]:
    """Gate a trade on risk limits.

    Uses BOTH in-memory tracker AND real Polymarket positions to prevent
    exposure from exceeding limits even after restarts.
    """
    size = trade_size_usd if trade_size_usd is not None else cfg.order_size_usd

    # Check 0: Single position cap
    if size > MAX_SINGLE_POSITION:
        return False, f"Single position ${size:.2f} exceeds ${MAX_SINGLE_POSITION:.2f} cap"

    # Check 1: Max concurrent positions (in-memory)
    if tracker.count >= cfg.max_concurrent_positions:
        return False, f"Max concurrent positions reached ({cfg.max_concurrent_positions})"

    # Check 2: In-memory exposure cap
    new_exposure = tracker.total_exposure + size
    if new_exposure > MAX_TOTAL_EXPOSURE:
        return False, f"Would exceed max exposure: ${new_exposure:.2f} > ${MAX_TOTAL_EXPOSURE:.2f}"

    # Check 3: Real Polymarket positions value (survives restarts)
    real_positions = _get_real_positions_value()
    if real_positions is not None and real_positions + size > MAX_TOTAL_EXPOSURE:
        return False, (
            f"Real Polymarket exposure too high: ${real_positions:.2f} + ${size:.2f} "
            f"= ${real_positions + size:.2f} > ${MAX_TOTAL_EXPOSURE:.2f}"
        )

    # Check 4: No duplicate market positions
    if tracker.has_position_for_market(market_id):
        return False, f"Already have position in market {market_id}"

    log.info(
        "Risk check passed: edge=%.3f, size=$%.2f, positions=%d/%d, "
        "tracker_exposure=$%.2f, real_exposure=$%.2f, cap=$%.2f",
        signal.edge, size, tracker.count, cfg.max_concurrent_positions,
        tracker.total_exposure, real_positions or 0.0, MAX_TOTAL_EXPOSURE,
    )
    return True, "ok"
