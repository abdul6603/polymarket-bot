"""Price-derived regime proxy for Odin backtesting.

Since CoinGlass historical data isn't available, we approximate
market regime from candle data using three signals:
  1. Volatility: rolling ATR / price  (high vol = choppy/manipulation)
  2. Momentum:   EMA-20 vs EMA-50 slope (trend direction + strength)
  3. Volume:     rolling Z-score        (volume spikes = regime shifts)

Outputs a dict matching what Odin's conviction engine expects:
  {regime, direction_bias, score, multiplier, funding_rate, ...}
"""
from __future__ import annotations

import logging
from enum import Enum

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


class Regime(Enum):
    STRONG_BULL = "strong_bull"
    BULL = "bull"
    NEUTRAL = "neutral"
    BEAR = "bear"
    STRONG_BEAR = "strong_bear"
    CHOPPY = "choppy"


# Multiplier mapping (matches Odin's macro tracker output)
REGIME_MULTIPLIER = {
    Regime.STRONG_BULL: 1.0,
    Regime.BULL: 0.8,
    Regime.NEUTRAL: 0.5,
    Regime.BEAR: 0.8,
    Regime.STRONG_BEAR: 1.0,
    Regime.CHOPPY: 0.3,
}


def classify_regime(df: pd.DataFrame, lookback: int = 50) -> dict:
    """Classify market regime from OHLCV candle data.

    Args:
        df: DataFrame with columns [open, high, low, close, volume].
            Must have at least `lookback + 50` rows.
        lookback: Number of bars for rolling calculations.

    Returns:
        Dict with keys: regime, direction_bias, score (0-100),
        multiplier (0.0-1.0), funding_rate (always 0 — no data),
        volatility_pct, momentum, volume_zscore.
    """
    if len(df) < lookback + 50:
        return _neutral_regime()

    df = df.copy()
    df.columns = [c.lower() for c in df.columns]
    close = df["close"].values
    high = df["high"].values
    low = df["low"].values
    volume = df["volume"].values

    # --- Signal 1: Volatility (ATR / price) ---
    atr = _rolling_atr(high, low, close, period=14)
    vol_pct = atr[-1] / close[-1] * 100 if close[-1] > 0 else 0

    # Classify volatility tier
    avg_vol = np.mean(atr[-lookback:]) / np.mean(close[-lookback:]) * 100
    vol_ratio = vol_pct / avg_vol if avg_vol > 0 else 1.0

    # --- Signal 2: Momentum (EMA crossover + slope) ---
    ema_fast = _ema(close, 20)
    ema_slow = _ema(close, 50)

    # Current spread
    spread = (ema_fast[-1] - ema_slow[-1]) / close[-1] * 100

    # Slope of fast EMA over last 10 bars (annualized direction)
    if len(ema_fast) >= 10:
        slope = (ema_fast[-1] - ema_fast[-10]) / ema_fast[-10] * 100
    else:
        slope = 0

    # --- Signal 3: Volume Z-score ---
    if len(volume) >= lookback:
        vol_mean = np.mean(volume[-lookback:])
        vol_std = np.std(volume[-lookback:])
        vol_zscore = (volume[-1] - vol_mean) / vol_std if vol_std > 0 else 0
    else:
        vol_zscore = 0

    # --- Regime Classification ---
    regime, score = _classify(spread, slope, vol_ratio, vol_zscore)

    # Direction bias
    if regime in (Regime.STRONG_BULL, Regime.BULL):
        direction_bias = "LONG"
    elif regime in (Regime.STRONG_BEAR, Regime.BEAR):
        direction_bias = "SHORT"
    else:
        direction_bias = "NONE"

    multiplier = REGIME_MULTIPLIER[regime]

    return {
        "regime": regime.value,
        "direction_bias": direction_bias,
        "score": round(score, 1),
        "multiplier": round(multiplier, 3),
        "funding_rate": 0,  # No historical funding data
        "volatility_pct": round(vol_pct, 3),
        "momentum": round(spread, 3),
        "slope": round(slope, 3),
        "volume_zscore": round(vol_zscore, 2),
        "vol_ratio": round(vol_ratio, 2),
        # Empty opportunities list (no CoinGlass in backtest)
        "opportunities": [],
    }


def _classify(
    spread: float, slope: float, vol_ratio: float, vol_zscore: float,
) -> tuple[Regime, float]:
    """Map signals to regime + score (0-100)."""
    # Choppy: high volatility + no clear direction
    if vol_ratio > 1.8 and abs(spread) < 0.5:
        return Regime.CHOPPY, 30

    # Strong bull: positive spread + positive slope + not extreme vol
    if spread > 1.5 and slope > 1.0:
        score = min(100, 60 + spread * 5 + slope * 3)
        return Regime.STRONG_BULL, score

    if spread > 0.5 and slope > 0.3:
        score = min(80, 50 + spread * 5 + slope * 3)
        return Regime.BULL, score

    # Strong bear: negative spread + negative slope
    if spread < -1.5 and slope < -1.0:
        score = min(100, 60 + abs(spread) * 5 + abs(slope) * 3)
        return Regime.STRONG_BEAR, score

    if spread < -0.5 and slope < -0.3:
        score = min(80, 50 + abs(spread) * 5 + abs(slope) * 3)
        return Regime.BEAR, score

    # Neutral: mixed or weak signals
    score = 40 + abs(spread) * 3
    return Regime.NEUTRAL, min(60, score)


def _neutral_regime() -> dict:
    return {
        "regime": "neutral",
        "direction_bias": "NONE",
        "score": 50,
        "multiplier": 0.5,
        "funding_rate": 0,
        "volatility_pct": 0,
        "momentum": 0,
        "slope": 0,
        "volume_zscore": 0,
        "vol_ratio": 1.0,
        "opportunities": [],
    }


def _rolling_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray,
                 period: int = 14) -> np.ndarray:
    """Calculate ATR using Wilder's smoothing."""
    n = len(high)
    tr = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1]),
        )

    atr = np.zeros(n)
    if n >= period + 1:
        atr[period] = np.mean(tr[1:period + 1])
        for i in range(period + 1, n):
            atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period

    return atr


def _ema(data: np.ndarray, period: int) -> np.ndarray:
    """Exponential moving average."""
    alpha = 2.0 / (period + 1)
    ema = np.zeros(len(data))
    ema[0] = data[0]
    for i in range(1, len(data)):
        ema[i] = alpha * data[i] + (1 - alpha) * ema[i - 1]
    return ema
