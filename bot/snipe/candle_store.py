"""In-memory candle accumulator — builds 5m/15m candles from BTC price ticks.

Detects BOS (Break of Structure) and CHoCH (Change of Character) patterns
for Smart Money Concepts analysis on the 5-minute BTC chart.

Fed by the snipe engine every 2s tick with the latest Binance BTC price.
No external data source needed — builds candles from ticks we already fetch.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass


@dataclass
class Candle:
    """OHLC candle aligned to clock boundary."""
    timestamp: float  # Start of candle (aligned to period boundary)
    open: float
    high: float
    low: float
    close: float
    tick_count: int = 0
    closed: bool = False


@dataclass
class SwingPoint:
    """Confirmed swing high or low."""
    timestamp: float
    price: float
    type: str  # "high" or "low"


# Period sizes in seconds
PERIODS = {"5m": 300, "15m": 900}
MAX_CANDLES = 50
MAX_SWINGS = 20


class CandleStore:
    """Accumulates price ticks into OHLC candles and detects structure."""

    def __init__(self):
        self._candles: dict[str, deque[Candle]] = {
            tf: deque(maxlen=MAX_CANDLES) for tf in PERIODS
        }
        self._current: dict[str, Candle | None] = {tf: None for tf in PERIODS}
        self._swings: dict[str, deque[SwingPoint]] = {
            tf: deque(maxlen=MAX_SWINGS) for tf in PERIODS
        }

    def feed_tick(self, price: float, timestamp: float | None = None) -> None:
        """Feed a new price tick. Updates all timeframe candles."""
        if timestamp is None:
            timestamp = time.time()
        if price <= 0:
            return

        for tf, period_s in PERIODS.items():
            candle_start = (int(timestamp) // period_s) * period_s
            current = self._current[tf]

            if current is None or current.timestamp != candle_start:
                # Close previous candle and start new one
                if current is not None:
                    current.closed = True
                    self._candles[tf].append(current)
                    self._detect_swings(tf)

                self._current[tf] = Candle(
                    timestamp=candle_start,
                    open=price, high=price, low=price, close=price,
                    tick_count=1,
                )
            else:
                current.high = max(current.high, price)
                current.low = min(current.low, price)
                current.close = price
                current.tick_count += 1

    def _detect_swings(self, tf: str) -> None:
        """Detect swing highs/lows from completed candles (3-bar pattern)."""
        candles = self._candles[tf]
        if len(candles) < 3:
            return

        prev = candles[-3]
        candidate = candles[-2]
        confirm = candles[-1]

        # Swing high: candidate.high > both neighbors
        if candidate.high > prev.high and candidate.high > confirm.high:
            self._swings[tf].append(SwingPoint(
                timestamp=candidate.timestamp,
                price=candidate.high,
                type="high",
            ))

        # Swing low: candidate.low < both neighbors
        if candidate.low < prev.low and candidate.low < confirm.low:
            self._swings[tf].append(SwingPoint(
                timestamp=candidate.timestamp,
                price=candidate.low,
                type="low",
            ))

    def get_structure(self, tf: str) -> dict:
        """Get BOS/CHoCH analysis for a timeframe.

        Returns:
            bos: "bullish" | "bearish" | None
            choch: "bullish" | "bearish" | None
            trend: "bullish" | "bearish" | "neutral"
            last_swing_high / last_swing_low: float | None
        """
        swings = list(self._swings.get(tf, []))
        current = self._current.get(tf)

        empty = {
            "bos": None, "choch": None,
            "last_swing_high": None, "last_swing_low": None,
            "trend": "neutral",
        }
        if len(swings) < 2 or current is None:
            return empty

        # Find last swing high and low
        last_high = None
        last_low = None
        for s in reversed(swings):
            if s.type == "high" and last_high is None:
                last_high = s
            elif s.type == "low" and last_low is None:
                last_low = s
            if last_high and last_low:
                break

        current_price = current.close
        bos = None
        choch = None

        # BOS: current price breaks past the last swing point
        if last_high and current_price > last_high.price:
            bos = "bullish"
        elif last_low and current_price < last_low.price:
            bos = "bearish"

        # Determine trend from swing sequence
        highs = [s for s in swings if s.type == "high"]
        lows = [s for s in swings if s.type == "low"]

        trend = "neutral"
        if len(highs) >= 2 and len(lows) >= 2:
            hh = highs[-1].price > highs[-2].price  # Higher high
            hl = lows[-1].price > lows[-2].price     # Higher low
            lh = highs[-1].price < highs[-2].price   # Lower high
            ll = lows[-1].price < lows[-2].price     # Lower low

            if hh and hl:
                trend = "bullish"
            elif lh and ll:
                trend = "bearish"

            # CHoCH: price breaks structure against the prevailing trend
            if trend == "bullish" and last_low and current_price < last_low.price:
                choch = "bearish"
            elif trend == "bearish" and last_high and current_price > last_high.price:
                choch = "bullish"

        return {
            "bos": bos,
            "choch": choch,
            "last_swing_high": last_high.price if last_high else None,
            "last_swing_low": last_low.price if last_low else None,
            "trend": trend,
        }

    def get_status(self) -> dict:
        """Dashboard-friendly status."""
        result = {}
        for tf in PERIODS:
            candles = self._candles[tf]
            current = self._current[tf]
            structure = self.get_structure(tf)
            result[tf] = {
                "completed_candles": len(candles),
                "current": {
                    "open": current.open,
                    "high": current.high,
                    "low": current.low,
                    "close": current.close,
                    "ticks": current.tick_count,
                } if current else None,
                "structure": structure,
            }
        return result
