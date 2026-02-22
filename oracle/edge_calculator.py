"""Edge calculator — compares Oracle probability vs market price."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from oracle.config import OracleConfig
from oracle.scanner import WeeklyMarket

log = logging.getLogger(__name__)


@dataclass
class TradeSignal:
    """A trade signal with edge and sizing."""
    market: WeeklyMarket
    oracle_prob: float          # Oracle's estimated YES probability
    market_prob: float          # Current market YES price
    edge: float                 # oracle_prob - market_prob (positive = buy YES)
    edge_abs: float             # abs(edge)
    side: str                   # "YES" or "NO"
    conviction: str             # "SKIP", "LOW", "MEDIUM", "HIGH"
    size: float                 # dollar amount to bet
    expected_value: float       # size * edge


def calculate_edges(
    cfg: OracleConfig,
    markets: list[WeeklyMarket],
    predictions: dict[str, float],
    current_exposure: float = 0.0,
    weekly_pnl: float = 0.0,
) -> list[TradeSignal]:
    """Calculate edge for each market and generate trade signals."""
    signals: list[TradeSignal] = []

    for m in markets:
        oracle_prob = predictions.get(m.condition_id)
        if oracle_prob is None:
            continue

        market_prob = m.yes_price
        edge = oracle_prob - market_prob

        # Determine side: buy YES if oracle > market, buy NO if oracle < market
        if edge > 0:
            side = "YES"
            effective_edge = edge
        else:
            side = "NO"
            effective_edge = abs(edge)

        conviction = cfg.conviction_label(effective_edge)
        size = cfg.conviction_size(effective_edge)

        # Check exposure limit
        if current_exposure + size > cfg.max_exposure:
            remaining = max(0, cfg.max_exposure - current_exposure)
            if remaining < 5:  # not worth it below $5
                conviction = "SKIP"
                size = 0.0
            else:
                size = remaining

        # Check weekly loss limit
        if weekly_pnl < 0 and abs(weekly_pnl) >= cfg.weekly_loss_limit:
            conviction = "SKIP"
            size = 0.0

        ev = size * effective_edge if size > 0 else 0.0

        signals.append(TradeSignal(
            market=m,
            oracle_prob=oracle_prob,
            market_prob=market_prob,
            edge=edge,
            edge_abs=effective_edge,
            side=side,
            conviction=conviction,
            size=size,
            expected_value=ev,
        ))

    # Sort by edge (highest first), filter out SKIPs for actionable list
    signals.sort(key=lambda s: s.edge_abs, reverse=True)

    tradeable = [s for s in signals if s.conviction != "SKIP"]
    skipped = len(signals) - len(tradeable)
    log.info(
        "Edge calculation: %d tradeable, %d skipped (total %d markets)",
        len(tradeable), skipped, len(signals),
    )

    return signals


def select_trades(
    cfg: OracleConfig,
    signals: list[TradeSignal],
) -> list[TradeSignal]:
    """Select the best trades within max_trades_per_week limit."""
    tradeable = [s for s in signals if s.conviction != "SKIP" and s.size > 0]

    # Take top N by edge, respecting max trades
    selected = tradeable[:cfg.max_trades_per_week]

    total_size = sum(s.size for s in selected)
    total_ev = sum(s.expected_value for s in selected)
    log.info(
        "Selected %d trades: total_size=$%.2f, total_EV=$%.2f",
        len(selected), total_size, total_ev,
    )

    return selected
