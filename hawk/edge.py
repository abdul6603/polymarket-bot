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


# Fix 2: R:R ratio filter — only bet when potential win > 1.2x potential loss
MIN_RR_RATIO = 1.5
MAX_TOKEN_PRICE = 0.40  # Never buy tokens above $0.40 (need 2.5:1 minimum)

# Fix 5: Confidence floor — reject GPT guesses (sportsbook-backed exempt)
MIN_CONFIDENCE = 0.60

# Fix 6: Edge sanity cap — edges above 30% almost always mean bad data (wrong line, stale odds)
MAX_EDGE_SANITY = 0.30

_YES_OUTCOMES = {"yes", "up", "over"}
_NO_OUTCOMES = {"no", "down", "under"}


def _get_market_price(market: HawkMarket, outcome: str = "yes") -> float:
    """Get current market price for a given outcome (handles Yes/No/Over/Under)."""
    target = _YES_OUTCOMES if outcome == "yes" else _NO_OUTCOMES
    for t in market.tokens:
        tok_outcome = (t.get("outcome") or "").lower()
        if tok_outcome in target:
            try:
                return float(t.get("price", 0.5))
            except (ValueError, TypeError):
                return 0.5
    # Fallback: first token = "yes" equivalent, second = "no" equivalent
    tokens = market.tokens
    if len(tokens) == 2:
        idx = 0 if outcome == "yes" else 1
        try:
            return float(tokens[idx].get("price", 0.5))
        except (ValueError, TypeError):
            return 0.5
    return 0.5


def _get_token_id(market: HawkMarket, outcome: str = "yes") -> str:
    """Get the token_id for a given outcome (handles Yes/No/Over/Under + team names)."""
    target = _YES_OUTCOMES if outcome == "yes" else _NO_OUTCOMES
    for t in market.tokens:
        tok_outcome = (t.get("outcome") or "").lower()
        if tok_outcome in target:
            return t.get("token_id", "")
    # Fallback: first token = "yes" equivalent, second = "no" equivalent
    tokens = market.tokens
    if len(tokens) == 2:
        idx = 0 if outcome == "yes" else 1
        return tokens[idx].get("token_id", "")
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

    # Time pressure: too soon = high risk (no exit time), sweet spot = 6-48h
    if time_left_hours < 2:
        score += 3  # Extremely risky — no time for edge
    elif time_left_hours < 6:
        score += 1  # Tight
    elif time_left_hours <= 48:
        score -= 1  # Sweet spot
    elif time_left_hours > 72:
        score += 1  # Too far out — more uncertainty

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
    elif category == "crypto_event":
        score += 2  # Crypto events highly volatile, poor WR historically

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
    if time_left_hours < 2:
        return "TOO SOON"
    elif time_left_hours <= 6:
        return "ENDING SOON"
    elif time_left_hours <= 24:
        return "TODAY"
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
    """Compare estimated prob vs market price, return None if edge < min_edge.

    V3: Confidence-adjusted min_edge. Low-confidence trades need higher edge
    to compensate for uncertainty. Sportsbook-backed trades get lower threshold.
    """
    effective_bankroll = bankroll or cfg.bankroll_usd

    yes_price = _get_market_price(market, "yes")
    no_price = _get_market_price(market, "no")
    est_prob = estimate.estimated_prob

    # V4: Dynamic min_edge based on confidence and data quality
    has_sportsbook = getattr(estimate, 'sportsbook_prob', None) is not None
    has_weather_model = getattr(estimate, 'edge_source', '') == 'weather_model'
    if has_sportsbook:
        # Sportsbook-backed: lower threshold — 40 bookmakers > GPT guesses
        effective_min_edge = max(0.05, cfg.min_edge - 0.05)
    elif has_weather_model:
        # Weather model-backed: lower threshold — ensemble data is very accurate
        effective_min_edge = max(0.05, cfg.min_edge - 0.05)
    elif estimate.confidence >= 0.6:
        effective_min_edge = cfg.min_edge
    elif estimate.confidence >= 0.4:
        # Medium confidence: require more edge
        effective_min_edge = cfg.min_edge + 0.05
    else:
        # Low confidence (GPT guessing): require very high edge
        effective_min_edge = max(0.15, cfg.min_edge + 0.10)

    yes_edge = est_prob - yes_price
    no_edge = (1 - est_prob) - no_price
    best_edge = max(yes_edge, no_edge)

    source_tag = "SB" if has_sportsbook else "WX" if has_weather_model else "GPT"
    if best_edge > 0.02:
        log.info("Edge calc [%s]: %s | prob=%.2f market=%.2f | YES=%.1f%% NO=%.1f%% | min=%.1f%% conf=%.1f",
                 source_tag, market.question[:45], est_prob, yes_price,
                 yes_edge * 100, no_edge * 100, effective_min_edge * 100, estimate.confidence)

    if yes_edge >= effective_min_edge and yes_edge >= no_edge:
        direction = "yes"
        edge = yes_edge
        token_id = _get_token_id(market, "yes")
        buy_price = yes_price
        true_prob = est_prob
    elif no_edge >= effective_min_edge:
        direction = "no"
        edge = no_edge
        token_id = _get_token_id(market, "no")
        buy_price = no_price
        true_prob = 1 - est_prob
    else:
        return None

    if not token_id:
        return None

    # Fix 6: Edge sanity cap — if edge is absurdly large, data is probably wrong
    # Weather model exempt: NOAA/Open-Meteo ensemble is highly accurate for short-term forecasts
    if edge > MAX_EDGE_SANITY and not has_weather_model:
        log.warning(
            "SUSPICIOUS EDGE REJECTED: %.1f%% > %.0f%% max | prob=%.2f market=%.2f | %s "
            "(likely bad sportsbook data or stale odds — refusing to bet on phantom edge)",
            edge * 100, MAX_EDGE_SANITY * 100, est_prob, buy_price, market.question[:60],
        )
        return None

    # Fix 5: Confidence floor — reject low-confidence GPT guesses (sportsbook + weather exempt)
    if not has_sportsbook and not has_weather_model and estimate.confidence < MIN_CONFIDENCE:
        log.info("Rejected low-confidence trade: conf=%.2f < %.2f | %s",
                 estimate.confidence, MIN_CONFIDENCE, market.question[:50])
        return None

    # Fix 2: R:R ratio filter — potential win must exceed 1.2x potential loss
    if buy_price > MAX_TOKEN_PRICE:
        log.info("Rejected high-price token: $%.2f > $%.2f | %s",
                 buy_price, MAX_TOKEN_PRICE, market.question[:50])
        return None

    potential_win = (1.0 - buy_price)  # Win payout per $1 token
    potential_loss = buy_price          # Loss = price paid
    if potential_loss > 0:
        rr_ratio = potential_win / potential_loss
        if rr_ratio < MIN_RR_RATIO:
            log.info("Rejected bad R:R: %.2f < %.2f | price=%.2f | %s",
                     rr_ratio, MIN_RR_RATIO, buy_price, market.question[:50])
            return None

    # V4: Cross-platform intelligence (used for risk adjustment, NOT edge inflation)
    xp_count = getattr(estimate, 'cross_platform_count', 0)

    kf = kelly_size(true_prob, buy_price, effective_bankroll, cfg.max_bet_usd, cfg.kelly_fraction)
    if kf < 1.0:
        return None

    ev = edge * kf
    tlh = market.time_left_hours

    # V4: Adjust risk score based on cross-platform confirmation
    risk_adj = 0
    if xp_count >= 2:
        risk_adj = -2  # Lower risk with multiple platform confirmation
    elif xp_count == 1:
        risk_adj = -1

    return TradeOpportunity(
        market=market,
        estimate=estimate,
        edge=edge,
        direction=direction,
        token_id=token_id,
        kelly_fraction=kf / effective_bankroll,
        position_size_usd=kf,
        expected_value=ev,
        risk_score=max(1, calculate_risk_score(edge, tlh, estimate.confidence, market.volume, market.category) + risk_adj),
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
