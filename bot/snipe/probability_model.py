"""Probability Model — Brownian motion probability for resolution scalping.

Pure math module. No state, no I/O, no network calls.
Used by ResolutionScalper to estimate the probability that BTC/ETH/SOL/XRP
finishes above/below the strike price before window closes.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class ProbabilityEstimate:
    """Result of a probability calculation."""
    probability: float      # P(finish above strike) or P(finish below strike)
    z_score: float          # Standard normal z-score
    sigma: float            # Per-second volatility used
    drift: float            # Per-second drift used
    distance_pct: float     # Current distance from strike as %
    remaining_s: float      # Seconds until resolution
    direction: str          # "up" or "down"
    adjustments: dict       # What adjustments were applied


def _phi(x: float) -> float:
    """Standard normal CDF via erfc (no scipy dependency)."""
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def estimate_volatility(closes: list[float], alpha: float = 0.3) -> float:
    """EMA-weighted stddev of log-returns from 1-min candles.

    Args:
        closes: Last N close prices (1-min candles). Need >= 2.
        alpha: EMA smoothing factor (higher = more weight on recent).

    Returns:
        Per-second sigma. Floored at 1e-7 to avoid division by zero.
    """
    if len(closes) < 2:
        return 1e-7

    # Log returns
    returns = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0 and closes[i] > 0:
            returns.append(math.log(closes[i] / closes[i - 1]))
    if not returns:
        return 1e-7

    # EMA-weighted variance
    weight = 1.0
    weighted_sum = 0.0
    weighted_sq_sum = 0.0
    total_weight = 0.0
    for r in reversed(returns):
        weighted_sum += weight * r
        weighted_sq_sum += weight * r * r
        total_weight += weight
        weight *= (1.0 - alpha)

    if total_weight < 1e-12:
        return 1e-7

    mean = weighted_sum / total_weight
    variance = weighted_sq_sum / total_weight - mean * mean
    sigma_per_min = math.sqrt(max(variance, 0.0))

    # Convert per-minute to per-second: sigma_s = sigma_m / sqrt(60)
    sigma_per_sec = sigma_per_min / math.sqrt(60.0)
    return max(sigma_per_sec, 1e-7)


def estimate_drift(closes: list[float], alpha: float = 0.5) -> float:
    """EMA-weighted mean of last N log-returns for momentum drift.

    Args:
        closes: Last N close prices (1-min candles). Need >= 2.
        alpha: EMA smoothing factor (higher = more weight on recent).

    Returns:
        Per-second drift (mu).
    """
    if len(closes) < 2:
        return 0.0

    returns = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0 and closes[i] > 0:
            returns.append(math.log(closes[i] / closes[i - 1]))
    if not returns:
        return 0.0

    # EMA-weighted mean
    weight = 1.0
    weighted_sum = 0.0
    total_weight = 0.0
    for r in reversed(returns):
        weighted_sum += weight * r
        total_weight += weight
        weight *= (1.0 - alpha)

    if total_weight < 1e-12:
        return 0.0

    drift_per_min = weighted_sum / total_weight
    # Convert per-minute to per-second
    return drift_per_min / 60.0


def calculate_probability(
    current_price: float,
    strike_price: float,
    remaining_s: float,
    sigma_per_sec: float,
    drift_per_sec: float,
    ob_imbalance: float = 0.0,
) -> ProbabilityEstimate:
    """Core Brownian motion probability calculation.

    P(S_T > K) = Phi(z) where:
        z = (ln(S/K) + (mu + ob_tilt - 0.5*sigma^2)*tau) / (sigma * sqrt(tau))

    Args:
        current_price: Current spot price (S).
        strike_price: Window open price / strike (K).
        remaining_s: Seconds until resolution (tau).
        sigma_per_sec: Per-second volatility.
        drift_per_sec: Per-second momentum drift.
        ob_imbalance: Orderbook imbalance [-1, +1]. Positive = buy pressure.

    Returns:
        ProbabilityEstimate with probability, z_score, and metadata.
    """
    if current_price <= 0 or strike_price <= 0 or remaining_s <= 0:
        return ProbabilityEstimate(
            probability=0.5, z_score=0.0, sigma=sigma_per_sec,
            drift=drift_per_sec, distance_pct=0.0, remaining_s=remaining_s,
            direction="neutral", adjustments={},
        )

    tau = remaining_s
    sigma = max(sigma_per_sec, 1e-7)

    # Orderbook tilt: imbalance adjusts drift
    ob_tilt = ob_imbalance * 0.30 * sigma
    adjustments = {"ob_tilt": ob_tilt}

    mu = drift_per_sec + ob_tilt

    # z = (ln(S/K) + (mu - 0.5*sigma^2)*tau) / (sigma * sqrt(tau))
    ln_ratio = math.log(current_price / strike_price)
    numerator = ln_ratio + (mu - 0.5 * sigma * sigma) * tau
    denominator = sigma * math.sqrt(tau)

    if denominator < 1e-12:
        # No time or volatility — price is deterministic
        p_up = 1.0 if current_price > strike_price else 0.0
        z = 10.0 if current_price > strike_price else -10.0
    else:
        z = numerator / denominator
        p_up = _phi(z)

    # Tail haircut: 5% reduction when T < 30s (fat tails in crypto)
    if remaining_s < 30:
        haircut = 0.05
        p_up = p_up * (1.0 - haircut) + 0.5 * haircut  # Blend toward 50%
        adjustments["tail_haircut"] = haircut

    # Determine direction and probability
    distance_pct = (current_price - strike_price) / strike_price * 100.0
    if current_price >= strike_price:
        direction = "up"
        probability = p_up
    else:
        direction = "down"
        probability = 1.0 - p_up

    return ProbabilityEstimate(
        probability=probability,
        z_score=z if direction == "up" else -z,
        sigma=sigma,
        drift=drift_per_sec,
        distance_pct=distance_pct,
        remaining_s=remaining_s,
        direction=direction,
        adjustments=adjustments,
    )


def calculate_edge(prob: float, market_price: float) -> float:
    """Edge = calculated probability - market price (what we'd pay)."""
    return prob - market_price


def calculate_ev(prob: float, market_price: float, fee_rate: float = 0.02) -> float:
    """Expected value per dollar: prob * $1.00 - market_price - fees."""
    return prob * 1.0 - market_price - fee_rate


def kelly_size(
    prob: float,
    market_price: float,
    bankroll: float,
    fraction: float = 0.25,
) -> float:
    """Quarter-Kelly bet sizing.

    Kelly: f* = (p*b - q) / b  where b = (1/price) - 1, q = 1-p
    Then scale by fraction (0.25 = quarter Kelly).

    Returns dollar amount to bet, floored at 0.
    """
    if market_price <= 0 or market_price >= 1.0 or prob <= 0:
        return 0.0

    b = (1.0 / market_price) - 1.0  # Odds ratio
    q = 1.0 - prob
    kelly_frac = (prob * b - q) / b

    if kelly_frac <= 0:
        return 0.0

    bet = fraction * kelly_frac * bankroll
    return max(bet, 0.0)


def detect_large_candle(
    candles: list[dict],
    sigma: float,
    lookback_s: float = 60.0,
) -> bool:
    """Detect if a recent candle had an abnormally large range.

    True if any candle in the lookback window has range > 2*sigma*sqrt(duration).
    This indicates a volatile spike — skip to avoid getting caught.

    Args:
        candles: List of candle dicts with 'high', 'low', 'timestamp' keys.
        sigma: Per-second volatility.
        lookback_s: How far back to check (seconds).
    """
    import time as _time
    now = _time.time()
    threshold_mult = 2.0

    for c in candles:
        age = now - c.get("timestamp", 0)
        if age > lookback_s or age < 0:
            continue

        high = c.get("high", 0)
        low = c.get("low", 0)
        if high <= 0 or low <= 0:
            continue

        candle_range = math.log(high / low)
        duration = 60.0  # 1-min candle
        expected_range = sigma * math.sqrt(duration)
        if expected_range > 0 and candle_range > threshold_mult * expected_range:
            return True

    return False
