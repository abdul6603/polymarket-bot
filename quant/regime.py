"""Quant Regime Detection — classify market conditions for regime-tagged backtesting.

Provides two complementary regime classifiers:
  1. Volatility-based: ATR / price ratio → low_vol / normal / high_vol / extreme_vol
  2. Trend-based: rolling returns slope → strong_down / down / ranging / up / strong_up

Combined regime label: e.g. "high_vol_up", "normal_ranging", "extreme_vol_down"

Used by walk_forward_v2 to tag each fold and only apply optimized params
when the current market regime matches the training regime.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import numpy as np

log = logging.getLogger(__name__)

# Regime label constants
VOL_LABELS = ("low_vol", "normal", "high_vol", "extreme_vol")
TREND_LABELS = ("strong_down", "down", "ranging", "up", "strong_up")


@dataclass
class RegimeTag:
    """Regime classification for a time window."""
    volatility: str = "normal"         # low_vol / normal / high_vol / extreme_vol
    trend: str = "ranging"             # strong_down / down / ranging / up / strong_up
    combined: str = "normal_ranging"   # "{vol}_{trend}"
    # Raw metrics
    vol_ratio: float = 0.0             # ATR / price (annualized-ish)
    trend_slope: float = 0.0           # normalized return slope
    fng_label: str = ""                # Fear & Greed label if available
    confidence: float = 0.0            # 0-1 how clearly this regime is classified


@dataclass
class RegimeAnalysis:
    """Full regime analysis of a trade set or time window."""
    current_regime: RegimeTag = field(default_factory=RegimeTag)
    regime_distribution: dict[str, int] = field(default_factory=dict)
    regime_performance: dict[str, dict] = field(default_factory=dict)
    best_regime: str = ""
    worst_regime: str = ""
    regime_count: int = 0


def classify_volatility(prices: list[float], window: int = 20) -> tuple[str, float]:
    """Classify volatility using ATR-like measure relative to price.

    Returns (label, vol_ratio).
    Thresholds calibrated for crypto (higher baseline vol than equities).
    """
    if len(prices) < window + 1:
        return "normal", 0.0

    arr = np.array(prices[-window - 1:])
    returns = np.abs(np.diff(arr) / arr[:-1])
    vol_ratio = float(returns.mean())

    # Crypto-calibrated thresholds (daily % move)
    if vol_ratio < 0.005:       # < 0.5% avg daily move
        return "low_vol", vol_ratio
    elif vol_ratio < 0.015:     # 0.5% - 1.5%
        return "normal", vol_ratio
    elif vol_ratio < 0.035:     # 1.5% - 3.5%
        return "high_vol", vol_ratio
    else:                       # > 3.5%
        return "extreme_vol", vol_ratio


def classify_trend(prices: list[float], window: int = 20) -> tuple[str, float]:
    """Classify trend direction using linear regression slope of returns.

    Returns (label, normalized_slope).
    """
    if len(prices) < window:
        return "ranging", 0.0

    arr = np.array(prices[-window:])
    # Normalize to percentage change from start
    pct_change = (arr[-1] / arr[0] - 1) * 100

    # Also look at linear regression slope for consistency
    x = np.arange(len(arr))
    slope = float(np.polyfit(x, arr, 1)[0])
    norm_slope = slope / arr.mean() * 100  # as % of price per period

    # Use total pct_change as primary signal
    if pct_change < -8:
        return "strong_down", norm_slope
    elif pct_change < -3:
        return "down", norm_slope
    elif pct_change < 3:
        return "ranging", norm_slope
    elif pct_change < 8:
        return "up", norm_slope
    else:
        return "strong_up", norm_slope


def classify_regime(prices: list[float], window: int = 20) -> RegimeTag:
    """Full regime classification combining volatility and trend."""
    vol_label, vol_ratio = classify_volatility(prices, window)
    trend_label, trend_slope = classify_trend(prices, window)

    # Confidence: higher when classification is clear (far from boundaries)
    vol_conf = min(1.0, abs(vol_ratio - 0.015) / 0.015) if vol_ratio > 0 else 0.5
    trend_conf = min(1.0, abs(trend_slope) / 0.5) if trend_slope != 0 else 0.3
    confidence = (vol_conf + trend_conf) / 2

    return RegimeTag(
        volatility=vol_label,
        trend=trend_label,
        combined=f"{vol_label}_{trend_label}",
        vol_ratio=round(vol_ratio, 5),
        trend_slope=round(trend_slope, 4),
        confidence=round(confidence, 3),
    )


def tag_trades_with_regime(
    trades: list[dict],
    candles_by_asset: dict[str, list] | None = None,
    window: int = 20,
) -> list[dict]:
    """Tag each trade with its market regime at time of entry.

    If candles are available, uses price-based regime detection.
    Falls back to the trade's existing regime_label (from Fear & Greed).
    Returns trades with added 'quant_regime' field.
    """
    # Build price lookup from candles: {asset: [(timestamp, close), ...]}
    price_series: dict[str, list[tuple[float, float]]] = {}
    if candles_by_asset:
        for asset, candles in candles_by_asset.items():
            price_series[asset] = [(c.timestamp, c.close) for c in candles]

    tagged = []
    for trade in trades:
        t = dict(trade)  # don't mutate original
        asset = t.get("asset", "bitcoin")
        ts = t.get("timestamp", 0)

        # Try price-based regime
        if asset in price_series and len(price_series[asset]) > window:
            # Find prices up to trade timestamp
            prices = [p for ts_p, p in price_series[asset] if ts_p <= ts]
            if len(prices) >= window:
                regime = classify_regime(prices, window)
                t["quant_regime"] = regime.combined
                t["quant_regime_vol"] = regime.volatility
                t["quant_regime_trend"] = regime.trend
                t["quant_regime_confidence"] = regime.confidence
                tagged.append(t)
                continue

        # Fallback: map existing Fear & Greed regime_label to simplified regime
        fng_label = t.get("regime_label", "neutral")
        vol = "normal"
        if fng_label in ("extreme_fear", "extreme_greed"):
            vol = "high_vol"
        trend = "ranging"
        if fng_label in ("fear", "extreme_fear"):
            trend = "down"
        elif fng_label in ("greed", "extreme_greed"):
            trend = "up"

        t["quant_regime"] = f"{vol}_{trend}"
        t["quant_regime_vol"] = vol
        t["quant_regime_trend"] = trend
        t["quant_regime_confidence"] = 0.3  # low confidence from FnG fallback
        tagged.append(t)

    return tagged


def analyze_regime_performance(trades: list[dict]) -> RegimeAnalysis:
    """Analyze trading performance broken down by regime.

    Returns which regimes perform best/worst, helping decide
    when to apply optimized params vs when to stay conservative.
    """
    result = RegimeAnalysis()

    # Ensure trades have regime tags
    regime_stats: dict[str, dict] = {}
    for t in trades:
        regime = t.get("quant_regime", t.get("regime_label", "unknown"))
        if regime not in regime_stats:
            regime_stats[regime] = {"wins": 0, "losses": 0, "edges": [], "pnls": []}

        if t.get("won"):
            regime_stats[regime]["wins"] += 1
        elif t.get("won") is not None:
            regime_stats[regime]["losses"] += 1

        if t.get("edge"):
            regime_stats[regime]["edges"].append(t["edge"])
        if t.get("pnl"):
            regime_stats[regime]["pnls"].append(t["pnl"])

    # Build performance summary
    best_wr = -1.0
    worst_wr = 101.0
    for regime, stats in regime_stats.items():
        total = stats["wins"] + stats["losses"]
        if total < 3:
            continue

        wr = stats["wins"] / total * 100
        avg_edge = sum(stats["edges"]) / len(stats["edges"]) if stats["edges"] else 0
        total_pnl = sum(stats["pnls"])

        result.regime_performance[regime] = {
            "wins": stats["wins"],
            "losses": stats["losses"],
            "total": total,
            "win_rate": round(wr, 1),
            "avg_edge": round(avg_edge * 100, 2),
            "total_pnl": round(total_pnl, 2),
        }
        result.regime_distribution[regime] = total

        if wr > best_wr:
            best_wr = wr
            result.best_regime = regime
        if wr < worst_wr:
            worst_wr = wr
            result.worst_regime = regime

    result.regime_count = len(regime_stats)

    # Current regime from most recent trade
    if trades:
        latest = trades[-1]
        result.current_regime = RegimeTag(
            combined=latest.get("quant_regime", "unknown"),
            volatility=latest.get("quant_regime_vol", "unknown"),
            trend=latest.get("quant_regime_trend", "unknown"),
        )

    return result


def get_regime_filtered_params(
    regime_performance: dict[str, dict],
    current_regime: str,
    optimized_params: dict,
    default_params: dict,
    min_regime_trades: int = 10,
    min_regime_wr: float = 50.0,
) -> tuple[dict, str]:
    """Decide whether to use optimized params based on current regime performance.

    Returns (params_to_use, reason).
    - If current regime has strong historical performance (WR > min_regime_wr): use optimized
    - If current regime is unknown or weak: use defaults (conservative)
    """
    perf = regime_performance.get(current_regime)

    if perf is None:
        return default_params, f"Unknown regime '{current_regime}' — using defaults"

    if perf["total"] < min_regime_trades:
        return default_params, (
            f"Regime '{current_regime}' has only {perf['total']} trades "
            f"(need {min_regime_trades}) — using defaults"
        )

    if perf["win_rate"] < min_regime_wr:
        return default_params, (
            f"Regime '{current_regime}' WR={perf['win_rate']}% < {min_regime_wr}% "
            f"— using defaults (poor regime performance)"
        )

    return optimized_params, (
        f"Regime '{current_regime}' WR={perf['win_rate']}% ({perf['total']} trades) "
        f"— using optimized params"
    )
