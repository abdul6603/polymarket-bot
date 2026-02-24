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


def find_cross_platform_pairs(
    poly_markets: list[WeeklyMarket],
    kalshi_markets: list[WeeklyMarket],
    min_divergence: float = 0.03,
) -> dict[str, dict]:
    """Find same markets on both Polymarket and Kalshi with price divergence.

    Returns dict keyed by poly condition_id with kalshi counterpart info:
    {
        "poly_cid": {
            "kalshi_cid": "kalshi_KXBTC-...",
            "kalshi_price": 0.55,
            "poly_price": 0.50,
            "divergence": 0.05,
            "cross_platform": True,
        }
    }
    """
    from difflib import SequenceMatcher
    pairs: dict[str, dict] = {}

    for poly in poly_markets:
        if poly.condition_id.startswith("kalshi_"):
            continue  # Skip Kalshi markets in poly list

        best_match = None
        best_score = 0.0

        for kalshi in kalshi_markets:
            # Must be same asset
            if kalshi.asset != poly.asset:
                continue

            # Question similarity
            score = SequenceMatcher(
                None,
                poly.question.lower().replace("?", "").strip(),
                kalshi.question.lower().replace("?", "").strip(),
            ).ratio()

            # Boost for matching threshold prices
            if poly.threshold and kalshi.threshold:
                if abs(poly.threshold - kalshi.threshold) < 100:  # Within $100
                    score += 0.2

            if score > best_score:
                best_score = score
                best_match = kalshi

        if best_match and best_score >= 0.4:
            divergence = abs(best_match.yes_price - poly.yes_price)
            if divergence >= min_divergence:
                pairs[poly.condition_id] = {
                    "kalshi_cid": best_match.condition_id,
                    "kalshi_price": best_match.yes_price,
                    "poly_price": poly.yes_price,
                    "divergence": divergence,
                    "match_score": best_score,
                    "cross_platform": True,
                }
                log.info(
                    "[CROSS-PLATFORM] %s ↔ %s | Poly=%.0f%% Kalshi=%.0f%% | Div=%.1f%% | Match=%.0f%%",
                    poly.question[:40], best_match.question[:40],
                    poly.yes_price * 100, best_match.yes_price * 100,
                    divergence * 100, best_score * 100,
                )

    if pairs:
        log.info("[CROSS-PLATFORM] Found %d cross-platform pairs with divergence >= %.0f%%",
                 len(pairs), min_divergence * 100)
    return pairs


def calculate_edges(
    cfg: OracleConfig,
    markets: list[WeeklyMarket],
    predictions: dict[str, float],
    current_exposure: float = 0.0,
    weekly_pnl: float = 0.0,
    cross_platform_pairs: dict[str, dict] | None = None,
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

        # Cross-platform edge boost: two platforms disagree = stronger signal
        xp_boost = False
        if cross_platform_pairs and m.condition_id in cross_platform_pairs:
            xp_info = cross_platform_pairs[m.condition_id]
            xp_divergence = xp_info.get("divergence", 0)
            if xp_divergence > 0.03:
                xp_boost = True
                log.info("[CROSS-PLATFORM] Edge boost for %s: divergence=%.1f%%",
                         m.asset.upper(), xp_divergence * 100)

        conviction = cfg.conviction_label(effective_edge)
        size = cfg.conviction_size(effective_edge)

        # Boost sizing by 20% when cross-platform divergence confirms our edge
        if xp_boost and size > 0:
            size = min(size * 1.2, cfg.risk_per_trade * 1.5)

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


MAX_TRADES_PER_ASSET = 2  # Never put more than 2 bets on one asset


def select_trades(
    cfg: OracleConfig,
    signals: list[TradeSignal],
) -> list[TradeSignal]:
    """Select the best trades within max_trades_per_week limit.

    Enforces per-asset concentration limit to prevent all-in on one asset.
    """
    tradeable = [s for s in signals if s.conviction != "SKIP" and s.size > 0]

    # Select top trades while enforcing per-asset cap
    selected: list[TradeSignal] = []
    asset_counts: dict[str, int] = {}

    for s in tradeable:
        if len(selected) >= cfg.max_trades_per_week:
            break
        asset = s.market.asset
        count = asset_counts.get(asset, 0)
        if count >= MAX_TRADES_PER_ASSET:
            continue
        selected.append(s)
        asset_counts[asset] = count + 1

    total_size = sum(s.size for s in selected)
    total_ev = sum(s.expected_value for s in selected)
    asset_summary = ", ".join(f"{a}={c}" for a, c in sorted(asset_counts.items()))
    log.info(
        "Selected %d trades: total_size=$%.2f, total_EV=$%.2f | per-asset: %s",
        len(selected), total_size, total_ev, asset_summary,
    )

    return selected
