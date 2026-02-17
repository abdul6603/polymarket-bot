"""Edge Calculator + Kelly Sizing + Risk Meter for Hawk V2."""
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
    risk_score: int = 5
    time_left_hours: float = 0.0
    urgency_label: str = ""


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


def calculate_risk_score(
    edge: float,
    time_left_hours: float,
    confidence: float,
    volume: float,
    category: str,
    viper_intel_count: int = 0,
) -> int:
    """Calculate risk score 1-10 from 6 factors.

    Lower = safer. Labels: 1-3 LOW, 4-6 MEDIUM, 7-8 HIGH, 9-10 EXTREME.
    """
    score = 5  # Base

    # Time pressure: ending soon = lower risk (more info available)
    if time_left_hours <= 6:
        score -= 1
    elif time_left_hours > 72:
        score += 1

    # Edge magnitude: big edge = lower risk
    if edge > 0.20:
        score -= 2
    elif edge > 0.15:
        score -= 1
    elif edge < 0.08:
        score += 1

    # Intel backing
    if viper_intel_count >= 3:
        score -= 1
    elif viper_intel_count == 0:
        score += 1

    # GPT confidence
    if confidence > 0.8:
        score -= 1
    elif confidence < 0.5:
        score += 1

    # Volume: high volume = efficient (harder edge), low = neglected (easier edge)
    if volume > 100000:
        score += 1
    elif volume < 10000:
        score -= 1

    # Category volatility
    if category == "politics":
        score += 1

    return max(1, min(10, score))


def risk_label(score: int) -> str:
    """Human label for risk score."""
    if score <= 3:
        return "LOW"
    elif score <= 6:
        return "MEDIUM"
    elif score <= 8:
        return "HIGH"
    return "EXTREME"


def urgency_label(time_left_hours: float) -> str:
    """Urgency badge label."""
    if time_left_hours <= 6:
        return "ENDING NOW"
    elif time_left_hours <= 24:
        return "ENDING SOON"
    elif time_left_hours <= 48:
        return "TOMORROW"
    elif time_left_hours <= 72:
        return "THIS WEEK"
    return ""


def kelly_size(
    true_prob: float,
    market_price: float,
    bankroll: float,
    max_bet: float,
    fraction: float = 0.35,
) -> float:
    """Kelly sizing with configurable fraction.

    Args:
        true_prob: Our estimated probability of winning
        market_price: Current market price we'd pay
        bankroll: Total bankroll
        max_bet: Max single bet
        fraction: Kelly fraction (0.35 = ~third Kelly for V2)
    """
    if market_price <= 0 or market_price >= 1 or true_prob <= 0:
        return 0.0
    payout = (1.0 / market_price) - 1.0
    if payout <= 0:
        return 0.0
    kelly_full = (true_prob * payout - (1 - true_prob)) / payout
    if kelly_full <= 0:
        return 0.0
    size = bankroll * kelly_full * fraction
    return max(1.0, min(max_bet, size))


def calculate_edge(
    market: HawkMarket,
    estimate: ProbabilityEstimate,
    cfg: HawkConfig,
    bankroll: float | None = None,
) -> TradeOpportunity | None:
    """Compare GPT prob vs market price, return None if edge < min_edge."""
    effective_bankroll = bankroll or cfg.bankroll_usd

    yes_price = _get_market_price(market, "yes")
    no_price = _get_market_price(market, "no")
    est_prob = estimate.estimated_prob

    yes_edge = est_prob - yes_price
    no_edge = (1 - est_prob) - no_price

    if yes_edge >= cfg.min_edge and yes_edge >= no_edge:
        direction = "yes"
        edge = yes_edge
        token_id = _get_token_id(market, "yes")
        buy_price = yes_price
        true_prob = est_prob
    elif no_edge >= cfg.min_edge:
        direction = "no"
        edge = no_edge
        token_id = _get_token_id(market, "no")
        buy_price = no_price
        true_prob = 1 - est_prob
    else:
        return None

    if not token_id:
        return None

    kf = kelly_size(true_prob, buy_price, effective_bankroll, cfg.max_bet_usd, cfg.kelly_fraction)
    if kf < 1.0:
        return None

    ev = edge * kf
    tlh = market.time_left_hours

    return TradeOpportunity(
        market=market,
        estimate=estimate,
        edge=edge,
        direction=direction,
        token_id=token_id,
        kelly_fraction=kf / effective_bankroll,
        position_size_usd=kf,
        expected_value=ev,
        risk_score=calculate_risk_score(edge, tlh, estimate.confidence, market.volume, market.category),
        time_left_hours=tlh,
        urgency_label=urgency_label(tlh),
    )


def calculate_confidence_tier(
    opp: TradeOpportunity,
    has_viper_intel: bool = False,
    viper_intel_count: int = 0,
) -> dict:
    """Score an opportunity and assign a confidence tier.

    Returns dict with 'score' (0-100) and 'tier' (HIGH/MEDIUM/SPECULATIVE).
    """
    score = 0

    # Edge component (40 pts max)
    score += min(40, int(opp.edge * 200))

    # Confidence component (30 pts max)
    score += int(opp.estimate.confidence * 30)

    # Viper intel bonus (15 pts)
    if has_viper_intel:
        score += 15

    # Volume bonus (15 pts)
    if opp.market.volume > 50000:
        score += 15
    elif opp.market.volume > 10000:
        score += 8

    # V2: Time urgency bonus
    if opp.time_left_hours > 0:
        if opp.time_left_hours < 24:
            score += 15
        elif opp.time_left_hours < 48:
            score += 10
        elif opp.time_left_hours < 72:
            score += 5

    # V2: Risk level inverse bonus
    if opp.risk_score <= 3:
        score += 10
    elif opp.risk_score <= 6:
        score += 5

    tier = "HIGH" if score >= 70 else "MEDIUM" if score >= 50 else "SPECULATIVE"
    return {"score": min(100, score), "tier": tier}


def rank_opportunities(opps: list[TradeOpportunity]) -> list[TradeOpportunity]:
    """Sort by expected value descending."""
    return sorted(opps, key=lambda o: o.expected_value, reverse=True)
