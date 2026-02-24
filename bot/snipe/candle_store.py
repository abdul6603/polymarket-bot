"""In-memory candle accumulator — builds 5m/15m/1h candles from price ticks.

Detects BOS (Break of Structure) and CHoCH (Change of Character) patterns
for Smart Money Concepts analysis on multi-asset, multi-timeframe charts.

Fed by the snipe engine every 2s tick with the latest Binance prices.
No external data source needed — builds candles from ticks we already fetch.

v8: Multi-asset (BTC/ETH/SOL/XRP) + 1h timeframe for MTF confirmation.
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
PERIODS = {"5m": 300, "15m": 900, "1h": 3600}
ASSETS = ("bitcoin", "ethereum", "solana", "xrp")
MAX_CANDLES = 50
MAX_SWINGS = 20


class CandleStore:
    """Accumulates price ticks into OHLC candles and detects structure."""

    def __init__(self):
        # Nested: asset -> timeframe -> deque
        self._candles: dict[str, dict[str, deque[Candle]]] = {
            asset: {tf: deque(maxlen=MAX_CANDLES) for tf in PERIODS}
            for asset in ASSETS
        }
        self._current: dict[str, dict[str, Candle | None]] = {
            asset: {tf: None for tf in PERIODS}
            for asset in ASSETS
        }
        self._swings: dict[str, dict[str, deque[SwingPoint]]] = {
            asset: {tf: deque(maxlen=MAX_SWINGS) for tf in PERIODS}
            for asset in ASSETS
        }

    def feed_tick(self, asset: str, price: float, timestamp: float | None = None) -> None:
        """Feed a new price tick for an asset. Updates all timeframe candles."""
        if timestamp is None:
            timestamp = time.time()
        if price <= 0:
            return
        if asset not in self._candles:
            return

        for tf, period_s in PERIODS.items():
            candle_start = (int(timestamp) // period_s) * period_s
            current = self._current[asset][tf]

            if current is None or current.timestamp != candle_start:
                # Close previous candle and start new one
                if current is not None:
                    current.closed = True
                    self._candles[asset][tf].append(current)
                    self._detect_swings(asset, tf)

                self._current[asset][tf] = Candle(
                    timestamp=candle_start,
                    open=price, high=price, low=price, close=price,
                    tick_count=1,
                )
            else:
                current.high = max(current.high, price)
                current.low = min(current.low, price)
                current.close = price
                current.tick_count += 1

    def seed_from_klines(self, asset: str, tf: str,
                         klines: list[dict]) -> int:
        """Pre-load closed candles from Binance klines REST data.

        Args:
            asset: e.g. "bitcoin"
            tf: e.g. "5m", "15m", "1h"
            klines: list of dicts with keys: timestamp, open, high, low, close

        Returns:
            Number of candles seeded.
        """
        if asset not in self._candles or tf not in PERIODS:
            return 0
        count = 0
        for k in klines:
            candle = Candle(
                timestamp=float(k["timestamp"]),
                open=float(k["open"]),
                high=float(k["high"]),
                low=float(k["low"]),
                close=float(k["close"]),
                tick_count=0,
                closed=True,
            )
            self._candles[asset][tf].append(candle)
            count += 1
            if count >= 3:
                self._detect_swings(asset, tf)
        return count

    def _detect_swings(self, asset: str, tf: str) -> None:
        """Detect swing highs/lows from completed candles (3-bar pattern)."""
        candles = self._candles[asset][tf]
        if len(candles) < 3:
            return

        prev = candles[-3]
        candidate = candles[-2]
        confirm = candles[-1]

        # Swing high: candidate.high > both neighbors
        if candidate.high > prev.high and candidate.high > confirm.high:
            self._swings[asset][tf].append(SwingPoint(
                timestamp=candidate.timestamp,
                price=candidate.high,
                type="high",
            ))

        # Swing low: candidate.low < both neighbors
        if candidate.low < prev.low and candidate.low < confirm.low:
            self._swings[asset][tf].append(SwingPoint(
                timestamp=candidate.timestamp,
                price=candidate.low,
                type="low",
            ))

    def get_structure(self, asset: str, tf: str) -> dict:
        """Get BOS/CHoCH analysis for an asset on a timeframe.

        Returns:
            bos: "bullish" | "bearish" | None
            choch: "bullish" | "bearish" | None
            trend: "bullish" | "bearish" | "neutral"
            last_swing_high / last_swing_low: float | None
        """
        empty = {
            "bos": None, "choch": None,
            "last_swing_high": None, "last_swing_low": None,
            "trend": "neutral",
        }
        if asset not in self._swings:
            return empty

        swings = list(self._swings[asset].get(tf, []))
        current = self._current.get(asset, {}).get(tf)

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
        """Dashboard-friendly status — nested by asset then timeframe."""
        result = {}
        for asset in ASSETS:
            asset_result = {}
            for tf in PERIODS:
                candles = self._candles[asset][tf]
                current = self._current[asset][tf]
                structure = self.get_structure(asset, tf)
                asset_result[tf] = {
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
            result[asset] = asset_result
        return result

    def get_warmup_status(self) -> dict:
        """Warm-up progress — how close each asset/timeframe is to structure detection.

        Structure detection needs >=3 closed candles (for swings) and >=5 (for trend).
        Returns per-asset readiness and overall warm-up percentage.
        """
        # Minutes needed for structure detection per timeframe
        tf_struct_candles = {"5m": 5, "15m": 5, "1h": 5}
        tf_minutes = {"5m": 25, "15m": 75, "1h": 300}  # 5 candles worth

        assets_status = {}
        total_ready = 0
        total_checks = 0

        for asset in ASSETS:
            asset_info = {}
            for tf in PERIODS:
                candle_count = len(self._candles[asset][tf])
                needed = tf_struct_candles.get(tf, 5)
                ready = candle_count >= needed
                structure = self.get_structure(asset, tf)
                has_trend = structure.get("trend") != "neutral"
                has_bos = structure.get("bos") is not None

                asset_info[tf] = {
                    "candles": candle_count,
                    "needed": needed,
                    "ready": ready,
                    "has_trend": has_trend,
                    "has_bos": has_bos,
                }
                total_checks += 1
                if ready:
                    total_ready += 1

            assets_status[asset] = asset_info

        pct = round(total_ready / total_checks * 100) if total_checks > 0 else 0
        warming = pct < 100

        return {
            "warming_up": warming,
            "progress_pct": pct,
            "assets": assets_status,
        }

    def reset_spread_history(self) -> None:
        """Compat — called from engine per-slot. No-op here."""
        pass
