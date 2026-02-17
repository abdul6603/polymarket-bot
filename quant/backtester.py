"""Core Backtest Engine — Mode B (trade replay) + Mode A (candle replay).

Accuracy-matched to the live SignalEngine in bot/signals.py.
Filter order matches live: consensus → confidence → edge → market safety → asset premium → UP premium.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import numpy as np

from bot.price_cache import Candle
from bot.indicators import (
    IndicatorVote, rsi, macd, ema_crossover, heikin_ashi,
    bollinger_bands, momentum, volume_spike,
)

log = logging.getLogger(__name__)

# Default regime adjustments (matching bot/regime.py REGIME_TABLE)
_REGIME_TABLE = {
    "extreme_fear":  {"edge_mult": 1.3, "consensus_off": 1, "conf_floor": 0.30},
    "fear":          {"edge_mult": 1.1, "consensus_off": 0, "conf_floor": 0.25},
    "neutral":       {"edge_mult": 1.0, "consensus_off": 0, "conf_floor": 0.25},
    "greed":         {"edge_mult": 1.2, "consensus_off": 1, "conf_floor": 0.30},
    "extreme_greed": {"edge_mult": 1.5, "consensus_off": 2, "conf_floor": 0.35},
}
_DEFAULT_REGIME = {"edge_mult": 1.0, "consensus_off": 0, "conf_floor": 0.25}


@dataclass
class BacktestParams:
    """Parameters to test against historical data."""
    # Indicator weights (same keys as signals.WEIGHTS)
    weights: dict[str, float] = field(default_factory=dict)
    # Timeframe-dependent weight scaling
    tf_weight_scale: dict[str, dict[str, float]] = field(default_factory=dict)
    # Signal filters
    min_consensus: int = 7
    min_confidence: float = 0.25
    up_confidence_premium: float = 0.08
    min_edge_absolute: float = 0.08
    min_edge_by_tf: dict[str, float] = field(default_factory=lambda: {
        "5m": 0.08, "15m": 0.08, "1h": 0.05, "4h": 0.04,
    })
    # Asset-specific edge premium (e.g. solana needs 1.5x edge)
    asset_edge_premiums: dict[str, float] = field(default_factory=lambda: {
        "bitcoin": 1.0, "ethereum": 0.9, "solana": 1.5,
    })
    # Probability clamp
    prob_clamp: dict[str, tuple[float, float]] = field(default_factory=lambda: {
        "5m": (0.30, 0.70), "15m": (0.25, 0.75),
        "1h": (0.20, 0.80), "4h": (0.15, 0.85),
    })
    # Whether to simulate regime adjustments from trade records
    use_regime: bool = True
    # Market safety filter: reject contrarian signals against strong market odds
    use_market_safety: bool = True
    market_safety_threshold: float = 0.15  # market lean must exceed this to trigger
    # Indicator-specific params (for Mode A candle replay)
    rsi_period: int = 14
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    ema_fast: int = 8
    ema_slow: int = 21
    bb_period: int = 20
    mom_short: int = 8
    mom_long: int = 30
    # Label for this parameter set
    label: str = "default"


@dataclass
class BacktestResult:
    """Results from a backtest run."""
    label: str = ""
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    max_consecutive_losses: int = 0
    avg_edge: float = 0.0
    avg_confidence: float = 0.0
    total_signals: int = 0
    signals_filtered: int = 0
    signals_by_asset: dict[str, dict] = field(default_factory=dict)
    signals_by_timeframe: dict[str, dict] = field(default_factory=dict)
    signals_by_direction: dict[str, dict] = field(default_factory=dict)
    signals_by_regime: dict[str, dict] = field(default_factory=dict)
    indicator_contributions: dict[str, dict] = field(default_factory=dict)
    filter_reasons: dict[str, int] = field(default_factory=dict)
    elapsed_seconds: float = 0.0
    params: dict = field(default_factory=dict)


def _estimate_fees(timeframe: str, implied_up_price: float | None) -> float:
    """Estimate total Polymarket fees — matches bot/signals.py _estimate_fees().

    - 2% winner fee (always, on payout)
    - Up to 3% taker fee on 15m markets (peaks at 50/50 odds)
    """
    winner_fee = 0.02
    taker_fee = 0.0
    if timeframe == "15m":
        ip = implied_up_price if implied_up_price is not None else 0.5
        distance = abs(ip - 0.5)
        taker_fee = 0.03 * max(1.0 - distance * 2, 0)
    return winner_fee + taker_fee


def replay_historical_trades(
    trades: list[dict],
    params: BacktestParams,
) -> BacktestResult:
    """Mode B: Replay historical trades with different weight/threshold combos.

    Matches the live SignalEngine filter chain:
    1. Read recorded indicator_votes (direction each indicator voted)
    2. Apply new weights + tf_weight_scale → weighted score
    3. Consensus filter (with regime consensus_offset)
    4. Confidence filter (with regime confidence_floor)
    5. Edge calculation using implied_up_price from trade record
    6. Dynamic fee subtraction
    7. Market safety filter (reject contrarian vs strong market)
    8. Asset edge premium + regime edge_multiplier
    9. UP confidence premium
    """
    t0 = time.time()
    result = BacktestResult(label=params.label)

    wins = 0
    losses = 0
    filtered = 0
    filter_reasons: dict[str, int] = {}
    edges: list[float] = []
    confidences: list[float] = []
    edge_weighted_wins: float = 0.0
    edge_weighted_losses: float = 0.0
    consecutive_losses = 0
    max_consec_losses = 0
    by_asset: dict[str, dict] = {}
    by_tf: dict[str, dict] = {}
    by_dir: dict[str, dict] = {}
    by_regime: dict[str, dict] = {}
    indicator_correct: dict[str, list[int]] = {}

    def _filter(reason: str):
        nonlocal filtered
        filtered += 1
        filter_reasons[reason] = filter_reasons.get(reason, 0) + 1

    for trade in trades:
        votes = trade.get("indicator_votes", {})
        if not votes:
            continue

        timeframe = trade.get("timeframe", "5m")
        asset = trade.get("asset", "bitcoin")
        outcome = trade.get("outcome", "")
        implied_up_price = trade.get("implied_up_price")
        regime_label = trade.get("regime_label", "neutral") if params.use_regime else "neutral"

        if outcome not in ("up", "down"):
            continue

        # Look up regime adjustments
        regime = _REGIME_TABLE.get(regime_label, _DEFAULT_REGIME)

        tf_scale = params.tf_weight_scale.get(timeframe, {})

        # ── Weighted Ensemble Score from recorded votes ──
        # Historical votes only store direction ("up"/"down"), not per-indicator confidence.
        # Use 1.0 as binary weight — score is purely weight-driven.
        weighted_sum = 0.0
        weight_total = 0.0
        up_count = 0
        down_count = 0
        active_count = 0

        for ind_name, ind_dir in votes.items():
            base_w = params.weights.get(ind_name, 1.0)
            if base_w <= 0:
                continue

            scale = tf_scale.get(ind_name, 1.0)
            w = base_w * scale

            sign = 1.0 if ind_dir == "up" else -1.0
            weighted_sum += w * sign  # binary direction, weight-driven
            weight_total += w

            if ind_dir == "up":
                up_count += 1
            else:
                down_count += 1
            active_count += 1

            # Track indicator correctness
            if ind_name not in indicator_correct:
                indicator_correct[ind_name] = []
            indicator_correct[ind_name].append(1 if ind_dir == outcome else 0)

        if weight_total == 0 or active_count < 3:
            _filter("too_few_indicators")
            continue

        score = weighted_sum / weight_total  # -1 to +1
        majority_dir = "up" if up_count >= down_count else "down"
        agree_count = max(up_count, down_count)
        total_indicators = active_count

        # ── Filter 1: Consensus (with regime adjustment) ──
        effective_consensus = params.min_consensus + regime["consensus_off"]
        effective_consensus = max(params.min_consensus, effective_consensus)  # never below base

        if agree_count < effective_consensus:
            _filter("consensus")
            continue

        # ── Filter 2: Confidence (with regime floor) ──
        confidence = min(abs(score), 1.0)
        effective_conf_floor = regime["conf_floor"]
        if confidence < effective_conf_floor:
            _filter("confidence")
            continue

        # ── Edge Calculation using implied_up_price ──
        lo, hi = params.prob_clamp.get(timeframe, (0.30, 0.70))
        raw_prob = 0.5 + score * 0.25
        prob_up = max(lo, min(hi, raw_prob))

        if implied_up_price is not None and 0.01 < implied_up_price < 0.99:
            edge_up = prob_up - implied_up_price
            edge_down = (1 - prob_up) - (1 - implied_up_price)
        else:
            edge_up = prob_up - 0.50
            edge_down = (1 - prob_up) - 0.50

        # ── Dynamic Fee Subtraction (matches live formula) ──
        fees = _estimate_fees(timeframe, implied_up_price)
        edge_up -= fees
        edge_down -= fees

        # Consensus-driven edge (matching live signals.py logic)
        consensus_edge = edge_up if majority_dir == "up" else edge_down

        # ── Filter 3: Market Safety ──
        if params.use_market_safety and implied_up_price is not None and 0.01 < implied_up_price < 0.99:
            market_dir = "up" if implied_up_price > 0.5 else "down"
            market_strength = abs(implied_up_price - 0.5)

            if majority_dir != market_dir and market_strength > params.market_safety_threshold:
                # Our consensus contradicts strong market lean
                contrarian_min = max(params.min_consensus + 2, int(total_indicators * 0.75))
                if agree_count < contrarian_min:
                    _filter("market_safety")
                    continue

        # ── Filter 4: Minimum Edge (with regime multiplier + asset premium) ──
        asset_premium = params.asset_edge_premiums.get(asset, 1.0)
        min_edge = params.min_edge_by_tf.get(timeframe, params.min_edge_absolute)
        min_edge *= regime["edge_mult"] * asset_premium
        min_edge = max(min_edge, params.min_edge_absolute)  # hard floor

        if consensus_edge < min_edge:
            _filter("edge_too_low")
            continue

        # ── Filter 5: UP Confidence Premium (after edge check, matching live) ──
        if majority_dir == "up":
            up_floor = effective_conf_floor + params.up_confidence_premium
            if confidence < up_floor:
                _filter("up_confidence_premium")
                continue

        # ── Signal passed all filters — check outcome ──
        edges.append(consensus_edge)
        confidences.append(confidence)
        won = (majority_dir == outcome)

        if won:
            wins += 1
            edge_weighted_wins += consensus_edge
            consecutive_losses = 0
        else:
            losses += 1
            edge_weighted_losses += consensus_edge
            consecutive_losses += 1
            max_consec_losses = max(max_consec_losses, consecutive_losses)

        # Breakdown tracking
        for bucket, key in [(by_asset, asset), (by_tf, timeframe),
                            (by_dir, majority_dir), (by_regime, regime_label)]:
            if key not in bucket:
                bucket[key] = {"wins": 0, "losses": 0}
            if won:
                bucket[key]["wins"] += 1
            else:
                bucket[key]["losses"] += 1

    total = wins + losses
    result.wins = wins
    result.losses = losses
    result.win_rate = (wins / total * 100) if total > 0 else 0.0
    # Profit factor: edge-weighted wins / edge-weighted losses
    if edge_weighted_losses > 0:
        result.profit_factor = edge_weighted_wins / edge_weighted_losses
    else:
        result.profit_factor = float(wins) if wins > 0 else 0.0
    result.max_consecutive_losses = max_consec_losses
    result.avg_edge = (sum(edges) / len(edges)) if edges else 0.0
    result.avg_confidence = (sum(confidences) / len(confidences)) if confidences else 0.0
    result.total_signals = total
    result.signals_filtered = filtered
    result.signals_by_asset = by_asset
    result.signals_by_timeframe = by_tf
    result.signals_by_direction = by_dir
    result.signals_by_regime = by_regime
    result.filter_reasons = filter_reasons
    result.elapsed_seconds = time.time() - t0

    # Indicator contributions
    for ind_name, correct_list in indicator_correct.items():
        if correct_list:
            result.indicator_contributions[ind_name] = {
                "votes": len(correct_list),
                "correct": sum(correct_list),
                "accuracy": sum(correct_list) / len(correct_list),
            }

    # Store full params (including actual weight values, not just a hash)
    result.params = {
        "label": params.label,
        "min_consensus": params.min_consensus,
        "min_confidence": params.min_confidence,
        "up_confidence_premium": params.up_confidence_premium,
        "min_edge_absolute": params.min_edge_absolute,
        "asset_edge_premiums": params.asset_edge_premiums,
        "use_regime": params.use_regime,
        "use_market_safety": params.use_market_safety,
        "weights": {k: round(v, 3) for k, v in params.weights.items() if v > 0},
    }

    return result


def backtest_candle_indicators(
    candles_by_asset: dict[str, list[Candle]],
    trades: list[dict],
    params: BacktestParams,
) -> BacktestResult:
    """Mode A: Replay candles through the 8 candle-computable indicators.

    Slides a 200-candle window across all candles, computes indicator votes
    with different parameter values, then matches to historical trade windows.
    """
    t0 = time.time()
    result = BacktestResult(label=f"{params.label}_candle")

    # Build trade lookup: (asset, approximate_minute) -> outcome
    trade_lookup: dict[tuple[str, int], str] = {}
    for t in trades:
        if t.get("resolved") and t.get("outcome") in ("up", "down"):
            asset = t.get("asset", "bitcoin")
            ts_min = int(t.get("timestamp", 0)) // 60
            trade_lookup[(asset, ts_min)] = t["outcome"]

    wins = 0
    losses = 0
    filtered = 0

    for asset, candles in candles_by_asset.items():
        if len(candles) < 200:
            continue

        closes = [c.close for c in candles]

        for i in range(200, len(candles)):
            window = candles[i - 200:i]
            close_window = closes[i - 200:i]

            # Compute candle-based indicators with test params
            votes: dict[str, IndicatorVote | None] = {}
            try:
                votes["rsi"] = rsi(close_window, period=params.rsi_period)
                votes["macd"] = macd(close_window, fast=params.macd_fast,
                                     slow=params.macd_slow, signal_period=params.macd_signal)
                votes["ema"] = ema_crossover(close_window, fast=params.ema_fast,
                                             slow=params.ema_slow)
                votes["heikin_ashi"] = heikin_ashi(window)
                votes["bollinger"] = bollinger_bands(close_window, period=params.bb_period)
                votes["momentum"] = momentum(close_window, short_window=params.mom_short,
                                             long_window=params.mom_long)
                votes["volume_spike"] = volume_spike(window)
            except Exception:
                continue

            # Filter None votes
            active = {k: v for k, v in votes.items() if v is not None}
            if len(active) < 3:
                filtered += 1
                continue

            # Count directions
            up_count = sum(1 for v in active.values() if v.direction == "up")
            down_count = len(active) - up_count
            majority_dir = "up" if up_count >= down_count else "down"

            # Match to a historical trade at this timestamp
            ts_min = int(candles[i].timestamp) // 60
            outcome = trade_lookup.get((asset, ts_min))
            if outcome is None:
                # Try nearby minutes (trade might be offset by 1-2 min)
                for offset in range(-2, 3):
                    outcome = trade_lookup.get((asset, ts_min + offset))
                    if outcome:
                        break
            if outcome is None:
                continue

            won = (majority_dir == outcome)
            if won:
                wins += 1
            else:
                losses += 1

    total = wins + losses
    result.wins = wins
    result.losses = losses
    result.win_rate = (wins / total * 100) if total > 0 else 0.0
    result.profit_factor = (wins / losses) if losses > 0 else float(wins)
    result.total_signals = total
    result.signals_filtered = filtered
    result.elapsed_seconds = time.time() - t0

    return result
