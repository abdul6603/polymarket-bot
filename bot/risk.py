from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from bot.config import Config
from bot.signals import Signal

log = logging.getLogger(__name__)


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
    """In-memory tracker for open positions."""

    def __init__(self):
        self._positions: dict[str, Position] = {}  # order_id -> Position

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

    def has_position_for_market(self, market_id: str) -> bool:
        return any(p.market_id == market_id for p in self._positions.values())


def check_risk(
    cfg: Config,
    signal: Signal,
    tracker: PositionTracker,
    market_id: str,
) -> tuple[bool, str]:
    """Gate a trade on risk limits.

    Returns:
        (allowed, reason) — True if trade is allowed, otherwise reason string.
    """
    min_edge = cfg.min_edge_pct / 100.0

    if signal.edge < min_edge:
        return False, f"Edge {signal.edge:.3f} below minimum {min_edge:.3f}"

    if tracker.count >= cfg.max_concurrent_positions:
        return False, f"Max concurrent positions reached ({cfg.max_concurrent_positions})"

    new_exposure = tracker.total_exposure + cfg.order_size_usd
    if new_exposure > cfg.max_position_usd:
        return False, f"Would exceed max exposure: ${new_exposure:.2f} > ${cfg.max_position_usd:.2f}"

    if tracker.has_position_for_market(market_id):
        return False, f"Already have position in market {market_id}"

    log.info(
        "Risk check passed: edge=%.3f, positions=%d/%d, exposure=$%.2f/$%.2f",
        signal.edge, tracker.count, cfg.max_concurrent_positions,
        tracker.total_exposure, cfg.max_position_usd,
    )
    return True, "ok"
