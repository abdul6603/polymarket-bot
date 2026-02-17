"""Edge Calculator + Kelly Sizing for Hawk trades."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from hawk.config import HawkConfig
from hawk.scanner import HawkMarket
from hawk.analyst import ProbabilityEstimate

log = logging.getLogger(__name__)


@dataclass
class TradeOpportunity:
    market: HawkMarket
    estimate: ProbabilityEstimate
    edge: float
    direction: str  # "yes" or "no"
    token_id: str
    kelly_fraction: float
    position_size_usd: float
    expected_value: float


def _get_market_price(market: HawkMarket, outcome: str = "yes") -> float:
    """Get current market price for a given outcome."""
    for t in market.tokens:
        tok_outcome = (t.get("outcome") or "").lower()
        if tok_outcome == outcome:
            try:
                return float(t.get("price", 0.5))
            except (ValueError, TypeError):
                return 0.5
    return 0.5


def _get_token_id(market: HawkMarket, outcome: str = "yes") -> str:
    """Get the token_id for a given outcome."""
    for t in market.tokens:
        tok_outcome = (t.get("outcome") or "").lower()
        if tok_outcome == outcome:
            return t.get("token_id", "")
    return ""


def kelly_size(
    edge: float,
    odds: float,
    bankroll: float,
    max_bet: float,
    fraction: float = 0.25,
) -> float:
    """Quarter-Kelly sizing. Same formula as bot/execution.py."""
    if odds <= 0 or edge <= 0:
        return 0.0
    payout = (1.0 / odds) - 1.0
    if payout <= 0:
        return 0.0
    kelly_full = (edge * payout - (1 - edge)) / payout
    if kelly_full <= 0:
        return 0.0
    size = bankroll * kelly_full * fraction
    return max(1.0, min(max_bet, size))


def calculate_edge(
    market: HawkMarket,
    estimate: ProbabilityEstimate,
    cfg: HawkConfig,
) -> TradeOpportunity | None:
    """Compare GPT prob vs market price, return None if edge < min_edge."""
    yes_price = _get_market_price(market, "yes")
    no_price = _get_market_price(market, "no")
    est_prob = estimate.estimated_prob

    # Check YES side: we think prob is higher than market
    yes_edge = est_prob - yes_price
    # Check NO side: we think prob is lower than market
    no_edge = (1 - est_prob) - no_price

    if yes_edge >= cfg.min_edge and yes_edge >= no_edge:
        direction = "yes"
        edge = yes_edge
        token_id = _get_token_id(market, "yes")
        odds = yes_price
    elif no_edge >= cfg.min_edge:
        direction = "no"
        edge = no_edge
        token_id = _get_token_id(market, "no")
        odds = no_price
    else:
        return None

    if not token_id:
        return None

    kf = kelly_size(edge, odds, cfg.bankroll_usd, cfg.max_bet_usd)
    if kf < 1.0:
        return None

    ev = edge * kf
    return TradeOpportunity(
        market=market,
        estimate=estimate,
        edge=edge,
        direction=direction,
        token_id=token_id,
        kelly_fraction=kf / cfg.bankroll_usd,
        position_size_usd=kf,
        expected_value=ev,
    )


def rank_opportunities(opps: list[TradeOpportunity]) -> list[TradeOpportunity]:
    """Sort by expected value descending."""
    return sorted(opps, key=lambda o: o.expected_value, reverse=True)
