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
    size_usd: float
    entry_price: float
    order_id: str
    opened_at: float = field(default_factory=time.time)
    strategy: str = "directional"  # "directional" or "straddle"


class PositionTracker:
    """In-memory tracker for open positions, seeded from disk on startup."""

    def __init__(self):
        self._positions: dict[str, Position] = {}  # order_id -> Position
        self._seed_from_trades()

    def _seed_from_trades(self) -> None:
        """Load unresolved live trades from trades.jsonl so restarts don't reset exposure."""
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
                    # Estimate size from probability and edge
                    prob = rec.get("probability", 0.5)
                    order_id = rec.get("trade_id", "")
                    market_id = rec.get("market_id", "")
                    if order_id in self._positions:
                        continue
                    # Use a conservative estimate of trade size
                    # (actual size not stored in trades.jsonl, use $25-50 range)
                    estimated_size = 35.0  # midpoint of TRADE_MIN/MAX
                    self._positions[order_id] = Position(
                        market_id=market_id,
                        token_id=rec.get("token_id", ""),
                        direction=rec.get("direction", ""),
                        size_usd=estimated_size,
                        entry_price=prob,
                        order_id=order_id,
                    )
            if self._positions:
                log.info(
                    "Seeded %d unresolved positions from disk (est. exposure: $%.0f)",
                    len(self._positions), self.total_exposure,
                )
        except Exception:
            log.exception("Failed to seed positions from trades.jsonl")

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

    def has_position_for_market(self, market_id: str) -> bool:
        return any(p.market_id == market_id for p in self._positions.values())


def _get_real_positions_value() -> float | None:
    """Read the cached Polymarket positions value from the balance file.

    Returns the on-chain positions_value or None if unavailable/stale.
    """
    try:
        if not BALANCE_CACHE_FILE.exists():
            return None
        cached = json.loads(BALANCE_CACHE_FILE.read_text())
        # Only trust cache if less than 5 min old
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

    Args:
        trade_size_usd: Actual trade size from ConvictionEngine. Falls back to cfg.order_size_usd.

    Returns:
        (allowed, reason) — True if trade is allowed, otherwise reason string.
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
