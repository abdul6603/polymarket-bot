from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import numpy as np

from bot.http_session import get_session
from bot.price_cache import Candle

log = logging.getLogger(__name__)


@dataclass
class IndicatorVote:
    direction: str   # "up" or "down"
    confidence: float  # 0.0 - 1.0
    raw_value: float = 0.0  # underlying numeric value for logging


# ── Timeframe-specific indicator parameters ──
# Short timeframes need faster indicators; long timeframes need more history.
TIMEFRAME_PARAMS = {
    "5m":  {"rsi_period": 7,  "macd_fast": 6,  "macd_slow": 12, "macd_signal": 6,
            "ema_fast": 5,  "ema_slow": 13, "bb_period": 10,
            "mom_short": 5,  "mom_long": 15},
    "15m": {"rsi_period": 14, "macd_fast": 12, "macd_slow": 26, "macd_signal": 9,
            "ema_fast": 8,  "ema_slow": 21, "bb_period": 20,
            "mom_short": 8,  "mom_long": 30},
    "1h":  {"rsi_period": 21, "macd_fast": 12, "macd_slow": 26, "macd_signal": 9,
            "ema_fast": 12, "ema_slow": 26, "bb_period": 20,
            "mom_short": 10, "mom_long": 40},
    "4h":  {"rsi_period": 28, "macd_fast": 24, "macd_slow": 52, "macd_signal": 18,
            "ema_fast": 20, "ema_slow": 50, "bb_period": 40,
            "mom_short": 15, "mom_long": 60},
}

DEFAULT_PARAMS = TIMEFRAME_PARAMS["15m"]


def get_params(timeframe: str) -> dict:
    return TIMEFRAME_PARAMS.get(timeframe, DEFAULT_PARAMS)


def _ema(data: np.ndarray, span: int) -> np.ndarray:
    """Compute EMA over a numpy array."""
    alpha = 2.0 / (span + 1)
    out = np.empty_like(data, dtype=float)
    out[0] = data[0]
    for i in range(1, len(data)):
        out[i] = alpha * data[i] + (1 - alpha) * out[i - 1]
    return out


# ── Core Technical Indicators ──

def rsi(closes: list[float], period: int = 14) -> IndicatorVote | None:
    """RSI: oversold < 30 → UP, overbought > 70 → DOWN."""
    if len(closes) < period + 1:
        return None

    arr = np.array(closes[-(period + 1):])
    deltas = np.diff(arr)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = np.mean(gains)
    avg_loss = np.mean(losses)

    if avg_loss == 0:
        rsi_val = 100.0
    else:
        rs = avg_gain / avg_loss
        rsi_val = 100.0 - (100.0 / (1.0 + rs))

    if rsi_val < 30:
        conf = (30 - rsi_val) / 30
        return IndicatorVote(direction="up", confidence=min(conf, 1.0), raw_value=rsi_val)
    elif rsi_val > 70:
        conf = (rsi_val - 70) / 30
        return IndicatorVote(direction="down", confidence=min(conf, 1.0), raw_value=rsi_val)
    else:
        # Neutral zone (30-70) — no directional signal, avoids UP bias
        return None


def ema_crossover(closes: list[float], fast: int = 8, slow: int = 21) -> IndicatorVote | None:
    """EMA crossover: fast EMA > slow → UP, fast < slow → DOWN."""
    if len(closes) < slow + 5:
        return None

    arr = np.array(closes)
    fast_ema = _ema(arr, fast)
    slow_ema = _ema(arr, slow)

    gap = (fast_ema[-1] - slow_ema[-1]) / slow_ema[-1]
    direction = "up" if gap > 0 else "down"
    conf = min(abs(gap) * 100, 1.0)

    return IndicatorVote(direction=direction, confidence=conf, raw_value=gap * 100)


def bollinger_bands(closes: list[float], period: int = 20, num_std: float = 2.0) -> IndicatorVote | None:
    """Bollinger Bands: price near lower band → UP, near upper → DOWN."""
    if len(closes) < period:
        return None

    window = np.array(closes[-period:])
    sma = np.mean(window)
    std = np.std(window)
    if std == 0:
        return None  # No volatility = no signal (was biasing "up")

    upper = sma + num_std * std
    lower = sma - num_std * std
    price = closes[-1]

    band_width = upper - lower
    pos = (price - lower) / band_width  # 0 = at lower, 1 = at upper

    if pos < 0.2:
        conf = (0.2 - pos) / 0.2
        return IndicatorVote(direction="up", confidence=min(conf, 1.0), raw_value=pos)
    elif pos > 0.8:
        conf = (pos - 0.8) / 0.2
        return IndicatorVote(direction="down", confidence=min(conf, 1.0), raw_value=pos)
    else:
        # Neutral zone (0.2-0.8) — no directional signal, avoids UP bias
        return None


def momentum(closes: list[float], short_window: int = 8, long_window: int = 30) -> IndicatorVote | None:
    """Short MA vs long MA momentum."""
    if len(closes) < long_window:
        return None

    short_avg = np.mean(closes[-short_window:])
    long_avg = np.mean(closes[-long_window:])

    mom = (short_avg - long_avg) / long_avg
    direction = "up" if mom > 0 else "down"
    conf = min(abs(mom) * 50, 1.0)

    return IndicatorVote(direction=direction, confidence=conf, raw_value=mom * 100)


def vwap(candles: list[Candle]) -> IndicatorVote | None:
    """VWAP: price above VWAP → UP, below → DOWN."""
    if len(candles) < 10:
        return None

    total_vp = 0.0
    total_vol = 0.0
    for c in candles:
        typical = (c.high + c.low + c.close) / 3.0
        total_vp += typical * c.volume
        total_vol += c.volume

    if total_vol == 0:
        return None

    vwap_val = total_vp / total_vol
    price = candles[-1].close
    diff = (price - vwap_val) / vwap_val

    direction = "up" if diff > 0 else "down"
    conf = min(abs(diff) * 100, 1.0)

    return IndicatorVote(direction=direction, confidence=conf, raw_value=vwap_val)


def macd(closes: list[float], fast: int = 12, slow: int = 26, signal_period: int = 9) -> IndicatorVote | None:
    """MACD: signal line crossover. MACD > signal → UP, below → DOWN."""
    if len(closes) < slow + signal_period:
        return None

    arr = np.array(closes)
    fast_ema = _ema(arr, fast)
    slow_ema = _ema(arr, slow)
    macd_line = fast_ema - slow_ema
    signal_line = _ema(macd_line, signal_period)

    histogram = macd_line[-1] - signal_line[-1]
    prev_histogram = macd_line[-2] - signal_line[-2]

    direction = "up" if histogram > 0 else "down"
    magnitude = abs(histogram) / closes[-1] * 100
    conf = min(magnitude * 20, 1.0)

    # Boost confidence on crossover (sign change)
    if (histogram > 0 and prev_histogram <= 0) or (histogram < 0 and prev_histogram >= 0):
        conf = min(conf + 0.3, 1.0)

    return IndicatorVote(direction=direction, confidence=conf, raw_value=histogram)


def heikin_ashi(candles: list[Candle]) -> IndicatorVote | None:
    """Heikin Ashi trend detection: consecutive HA candle direction → trend signal."""
    if len(candles) < 10:
        return None

    ha_closes: list[float] = []
    ha_opens: list[float] = []

    c0 = candles[0]
    ha_open = (c0.open + c0.close) / 2
    ha_close = (c0.open + c0.high + c0.low + c0.close) / 4
    ha_opens.append(ha_open)
    ha_closes.append(ha_close)

    for c in candles[1:]:
        ha_close = (c.open + c.high + c.low + c.close) / 4
        ha_open = (ha_opens[-1] + ha_closes[-1]) / 2
        ha_opens.append(ha_open)
        ha_closes.append(ha_close)

    bullish_streak = 0
    bearish_streak = 0
    for i in range(len(ha_closes) - 1, -1, -1):
        if ha_closes[i] > ha_opens[i]:
            if bearish_streak > 0:
                break
            bullish_streak += 1
        elif ha_closes[i] < ha_opens[i]:
            if bullish_streak > 0:
                break
            bearish_streak += 1
        else:
            break

    streak = max(bullish_streak, bearish_streak)
    if streak < 2:
        return None  # No clear streak = no signal (was biasing "up")

    direction = "up" if bullish_streak > bearish_streak else "down"
    conf = min(streak / 5.0, 1.0)

    return IndicatorVote(direction=direction, confidence=conf, raw_value=streak if direction == "up" else -streak)


# ── Order Flow & Market Microstructure ──

def order_flow_delta(buy_volume: float, sell_volume: float) -> IndicatorVote | None:
    """Cumulative volume delta: net buy volume vs net sell volume."""
    total = buy_volume + sell_volume
    if total == 0:
        return None

    delta = (buy_volume - sell_volume) / total
    direction = "up" if delta > 0 else "down"
    conf = min(abs(delta), 1.0)

    return IndicatorVote(direction=direction, confidence=conf, raw_value=delta * 100)


def price_divergence(
    binance_price: float,
    price_3m_ago: float | None,
    polymarket_implied: float | None,
) -> IndicatorVote | None:
    """Detect when Binance price momentum diverges from Polymarket implied price.

    When Binance shows directional momentum but Polymarket hasn't caught up,
    bet WITH the Binance direction (the Gabagool strategy).
    """
    if binance_price <= 0 or price_3m_ago is None or price_3m_ago <= 0:
        return None

    # Short-term Binance momentum (last 3 minutes)
    pct_change = (binance_price - price_3m_ago) / price_3m_ago

    # If Polymarket implied is available, check if it's lagging
    if polymarket_implied is not None and 0.01 < polymarket_implied < 0.99:
        # How much Polymarket leans in a direction (-1 to +1)
        poly_lean = (polymarket_implied - 0.5) * 2
        # Binance direction strength
        binance_strength = pct_change * 100  # e.g. 0.2% = 0.2

        # Divergence: Binance moved but Polymarket hasn't caught up
        if abs(pct_change) > 0.0005:  # at least 0.05% move
            direction = "up" if pct_change > 0 else "down"
            # Confidence based on how much Binance moved vs how little Poly adjusted
            move_size = abs(pct_change) * 100
            poly_adjustment = abs(poly_lean)
            gap = move_size - poly_adjustment * 5
            if gap > 0:
                conf = min(gap * 0.4, 0.9)
                return IndicatorVote(direction=direction, confidence=conf, raw_value=pct_change * 100)

    # Fallback: just use Binance momentum if significant
    if abs(pct_change) > 0.001:  # 0.1% move
        direction = "up" if pct_change > 0 else "down"
        conf = min(abs(pct_change) * 200, 0.7)
        return IndicatorVote(direction=direction, confidence=conf, raw_value=pct_change * 100)

    return None


def liquidity_signal(
    total_bid_depth: float,
    total_ask_depth: float,
    spread: float,
) -> IndicatorVote | None:
    """Polymarket liquidity imbalance: deep bids vs asks + spread tightness."""
    total = total_bid_depth + total_ask_depth
    if total == 0:
        return None

    imbalance = (total_bid_depth - total_ask_depth) / total
    direction = "up" if imbalance > 0 else "down"

    spread_factor = max(1.0 - spread * 10, 0.2)
    conf = min(abs(imbalance) * spread_factor, 1.0)

    return IndicatorVote(direction=direction, confidence=conf, raw_value=imbalance * 100)


# ── New Indicators ──

def atr(candles: list[Candle], period: int = 14) -> float | None:
    """Average True Range — returns ATR as fraction of price (volatility measure).

    Returns None if insufficient data. Not an IndicatorVote — used as a filter.
    """
    if len(candles) < period + 1:
        return None

    trs = []
    for i in range(1, len(candles)):
        c = candles[i]
        prev_c = candles[i - 1]
        tr = max(
            c.high - c.low,
            abs(c.high - prev_c.close),
            abs(c.low - prev_c.close),
        )
        trs.append(tr)

    atr_val = float(np.mean(trs[-period:]))
    price = candles[-1].close
    if price <= 0:
        return None
    return atr_val / price  # as fraction of price


def temporal_arb(
    current_price: float,
    price_3m_ago: float | None,
    implied_up: float | None,
    timeframe: str,
) -> IndicatorVote | None:
    """Temporal arbitrage: Binance moved but Polymarket hasn't caught up.

    This is the highest-edge strategy — when spot price already confirmed a
    direction but Polymarket odds still sit near 50/50. Only for short timeframes.
    """
    if timeframe not in ("5m", "15m"):
        return None
    if price_3m_ago is None or price_3m_ago <= 0 or current_price <= 0:
        return None

    pct_move = (current_price - price_3m_ago) / price_3m_ago

    # Need a meaningful move (>0.1%) AND Polymarket still near 50/50
    if abs(pct_move) < 0.001:
        return None

    if implied_up is not None and abs(implied_up - 0.5) < 0.08:
        # Polymarket hasn't priced it in yet — high confidence arb
        direction = "up" if pct_move > 0 else "down"
        conf = min(abs(pct_move) * 400, 0.95)
        return IndicatorVote(direction=direction, confidence=conf, raw_value=pct_move * 100)

    # Even without implied price data, a large Binance move is informative
    if abs(pct_move) > 0.002:  # >0.2% move
        direction = "up" if pct_move > 0 else "down"
        conf = min(abs(pct_move) * 200, 0.8)
        return IndicatorVote(direction=direction, confidence=conf, raw_value=pct_move * 100)

    return None


def volume_spike(candles: list[Candle], threshold: float = 2.0, lookback: int = 20) -> IndicatorVote | None:
    """Detect volume spikes >2x average — strong directional signal."""
    if len(candles) < lookback + 1:
        return None

    avg_vol = float(np.mean([c.volume for c in candles[-lookback - 1:-1]]))
    if avg_vol <= 0:
        return None

    current_vol = candles[-1].volume
    if current_vol > avg_vol * threshold:
        direction = "up" if candles[-1].close > candles[-1].open else "down"
        conf = min((current_vol / avg_vol - 1) / 3.0, 1.0)
        return IndicatorVote(direction=direction, confidence=conf, raw_value=current_vol / avg_vol)

    return None


# ── Sentiment: Fear & Greed Index ──

_fng_cache: dict = {"value": None, "timestamp": 0.0}
_FNG_CACHE_TTL = 300  # refresh every 5 minutes


def fear_greed_index() -> IndicatorVote | None:
    """Crypto Fear & Greed Index — contrarian signal.

    0-24 = Extreme Fear -> bullish (buy when others are fearful)
    25-44 = Fear -> slightly bullish
    45-55 = Neutral -> no signal
    56-74 = Greed -> slightly bearish
    75-100 = Extreme Greed -> bearish (sell when others are greedy)
    """
    global _fng_cache
    now = time.time()

    if _fng_cache["value"] is not None and now - _fng_cache["timestamp"] < _FNG_CACHE_TTL:
        fng_val = _fng_cache["value"]
    else:
        try:
            resp = get_session().get("https://api.alternative.me/fng/?limit=1", timeout=5)
            if resp.status_code != 200:
                return None
            data = resp.json()
            fng_val = int(data["data"][0]["value"])
            _fng_cache = {"value": fng_val, "timestamp": now}
            log.debug("Fear & Greed Index: %d", fng_val)
        except Exception:
            return None

    if fng_val <= 24:
        conf = (25 - fng_val) / 25.0
        return IndicatorVote(direction="up", confidence=min(conf, 1.0), raw_value=fng_val)
    elif fng_val <= 44:
        conf = (45 - fng_val) / 45.0 * 0.5
        return IndicatorVote(direction="up", confidence=max(conf, 0.1), raw_value=fng_val)
    elif fng_val >= 75:
        conf = (fng_val - 74) / 26.0
        return IndicatorVote(direction="down", confidence=min(conf, 1.0), raw_value=fng_val)
    elif fng_val >= 56:
        conf = (fng_val - 55) / 45.0 * 0.5
        return IndicatorVote(direction="down", confidence=max(conf, 0.1), raw_value=fng_val)
    else:
        # Neutral zone (45-55) — no signal
        return None


# ── Derivatives-Based Indicators ──

def funding_rate_signal(rate: float) -> IndicatorVote | None:
    """Funding rate contrarian signal from Binance Futures.

    Positive funding = longs paying shorts = overleveraged long → bearish.
    Negative funding = shorts paying longs = overleveraged short → bullish.
    Neutral zone: |rate| < 0.0001 (0.01%) — no signal.
    """
    if abs(rate) < 0.0001:
        return None

    direction = "down" if rate > 0 else "up"
    # Scale: 0.01% = low conf, 0.05% = medium, 0.1%+ = high
    conf = min(abs(rate) * 5000, 1.0)
    return IndicatorVote(direction=direction, confidence=conf, raw_value=rate * 10000)


def liquidation_cascade_signal(
    long_liq_usd: float,
    short_liq_usd: float,
    cascade_detected: bool,
    cascade_direction: str,
) -> IndicatorVote | None:
    """Liquidation pressure signal from Binance Futures.

    Heavy long liquidations (SELL) = bearish cascade (longs forced out).
    Heavy short liquidations (BUY) = bullish squeeze (shorts squeezed).
    Cascade events amplify confidence.
    """
    total = long_liq_usd + short_liq_usd
    if total < 10000:  # less than $10K not meaningful
        return None

    if long_liq_usd > short_liq_usd:
        direction = "down"
        ratio = long_liq_usd / max(short_liq_usd, 1)
    else:
        direction = "up"
        ratio = short_liq_usd / max(long_liq_usd, 1)

    conf = min(ratio / 5.0, 0.8)
    if cascade_detected:
        conf = min(conf + 0.3, 1.0)

    return IndicatorVote(direction=direction, confidence=conf, raw_value=total)


def spot_depth_signal(bids: list, asks: list) -> IndicatorVote | None:
    """Binance spot order book depth imbalance (top 5 levels).

    Heavy bids vs asks → bullish/bearish pressure.
    bids/asks format: [[price_str, qty_str], ...]
    """
    if not bids or not asks:
        return None

    bid_depth = sum(float(b[0]) * float(b[1]) for b in bids)
    ask_depth = sum(float(a[0]) * float(a[1]) for a in asks)
    total = bid_depth + ask_depth

    if total == 0:
        return None

    imbalance = (bid_depth - ask_depth) / total
    if abs(imbalance) < 0.05:  # less than 5% imbalance — noise
        return None

    direction = "up" if imbalance > 0 else "down"
    conf = min(abs(imbalance) * 2, 1.0)

    return IndicatorVote(direction=direction, confidence=conf, raw_value=imbalance * 100)
