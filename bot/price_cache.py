from __future__ import annotations

import json
import logging
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

CANDLE_DIR = Path(__file__).parent.parent / "data" / "candles"
CANDLE_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class Candle:
    timestamp: float
    open: float
    high: float
    low: float
    close: float
    volume: float


class PriceCache:
    """Stores 1-minute OHLCV candles built from raw trade ticks."""

    def __init__(self, maxlen: int = 200):
        self._maxlen = maxlen
        # asset -> deque of completed 1m candles
        self._candles: dict[str, deque[Candle]] = {}
        # asset -> current building candle
        self._building: dict[str, Candle] = {}
        # asset -> minute bucket (floored timestamp)
        self._current_minute: dict[str, int] = {}
        # asset -> latest tick price
        self._latest_price: dict[str, float] = {}
        # Order flow delta tracking: buy vs sell volume (rolling window)
        self._buy_volume: dict[str, deque[float]] = {}   # per-minute buy vol
        self._sell_volume: dict[str, deque[float]] = {}   # per-minute sell vol
        self._current_buy: dict[str, float] = {}
        self._current_sell: dict[str, float] = {}
        self._prev_price: dict[str, float] = {}  # for tick-rule classification

    def preload_from_disk(self) -> None:
        """Load saved candles from disk so indicators can fire immediately."""
        for fpath in CANDLE_DIR.glob("*.jsonl"):
            asset = fpath.stem  # e.g. "bitcoin"
            candles = self.load_candles(asset)
            if not candles:
                continue
            # Only load the most recent candles up to maxlen
            recent = candles[-self._maxlen:]
            self._candles[asset] = deque(recent, maxlen=self._maxlen)
            # Set latest price from the last candle
            self._latest_price[asset] = recent[-1].close
            self._prev_price[asset] = recent[-1].close
            log.info("Preloaded %d candles for %s from disk", len(recent), asset)

    def update_tick(self, asset: str, price: float, volume: float, timestamp: float) -> None:
        """Ingest a raw trade tick and build 1-minute candles + track order flow."""
        # Classify as buy or sell using tick rule (uptick = buy, downtick = sell)
        prev = self._prev_price.get(asset)
        self._prev_price[asset] = price
        is_buy = price >= prev if prev is not None else True

        self._latest_price[asset] = price
        minute = int(timestamp // 60)

        if asset not in self._current_minute:
            self._current_minute[asset] = minute
            self._building[asset] = Candle(
                timestamp=minute * 60,
                open=price, high=price, low=price, close=price,
                volume=volume,
            )
            self._current_buy[asset] = volume if is_buy else 0.0
            self._current_sell[asset] = 0.0 if is_buy else volume
            return

        if minute > self._current_minute[asset]:
            # Finalize candle
            old = self._building[asset]
            if asset not in self._candles:
                self._candles[asset] = deque(maxlen=self._maxlen)
            self._candles[asset].append(old)

            # Finalize order flow for this minute
            if asset not in self._buy_volume:
                self._buy_volume[asset] = deque(maxlen=self._maxlen)
                self._sell_volume[asset] = deque(maxlen=self._maxlen)
            self._buy_volume[asset].append(self._current_buy.get(asset, 0))
            self._sell_volume[asset].append(self._current_sell.get(asset, 0))

            self._current_minute[asset] = minute
            self._building[asset] = Candle(
                timestamp=minute * 60,
                open=price, high=price, low=price, close=price,
                volume=volume,
            )
            self._current_buy[asset] = volume if is_buy else 0.0
            self._current_sell[asset] = 0.0 if is_buy else volume
        else:
            c = self._building[asset]
            c.high = max(c.high, price)
            c.low = min(c.low, price)
            c.close = price
            c.volume += volume
            if is_buy:
                self._current_buy[asset] = self._current_buy.get(asset, 0) + volume
            else:
                self._current_sell[asset] = self._current_sell.get(asset, 0) + volume

    def get_closes(self, asset: str, count: int) -> list[float]:
        """Return the last N close prices (completed candles + current building)."""
        candles = list(self._candles.get(asset, []))
        building = self._building.get(asset)
        if building:
            candles.append(building)
        return [c.close for c in candles[-count:]]

    def get_candles(self, asset: str, count: int) -> list[Candle]:
        """Return the last N Candle objects (completed + current building)."""
        candles = list(self._candles.get(asset, []))
        building = self._building.get(asset)
        if building:
            candles.append(building)
        return candles[-count:]

    def get_price(self, asset: str) -> float | None:
        """Return the latest spot price for an asset."""
        return self._latest_price.get(asset)

    def get_order_flow(self, asset: str, window: int = 30) -> tuple[float, float]:
        """Return (total_buy_volume, total_sell_volume) over last N minutes."""
        buys = list(self._buy_volume.get(asset, []))
        sells = list(self._sell_volume.get(asset, []))
        # Include current building minute
        buys.append(self._current_buy.get(asset, 0))
        sells.append(self._current_sell.get(asset, 0))
        return sum(buys[-window:]), sum(sells[-window:])

    def get_price_ago(self, asset: str, minutes: int) -> float | None:
        """Return close price from approximately N minutes ago."""
        candles = list(self._candles.get(asset, []))
        if not candles:
            return None
        idx = len(candles) - minutes
        if idx < 0:
            return candles[0].close
        return candles[min(idx, len(candles) - 1)].close

    def candle_count(self, asset: str) -> int:
        """Total candles available (completed + building)."""
        n = len(self._candles.get(asset, []))
        if asset in self._building:
            n += 1
        return n

    def save_candles(self) -> None:
        """Persist all candle data to disk for backtesting."""
        for asset, candle_deque in self._candles.items():
            fpath = CANDLE_DIR / f"{asset}.jsonl"
            # Load existing, merge, save (avoid duplicates by timestamp)
            existing = {}
            if fpath.exists():
                try:
                    with open(fpath) as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                c = json.loads(line)
                                existing[c["timestamp"]] = c
                            except (json.JSONDecodeError, KeyError):
                                continue  # skip corrupted lines
                except Exception as e:
                    log.warning("Failed to read existing candles for %s: %s", asset, e)
            # Add new candles
            for c in candle_deque:
                existing[c.timestamp] = asdict(c)
            # Write sorted by timestamp
            sorted_candles = sorted(existing.values(), key=lambda x: x["timestamp"])
            try:
                with open(fpath, "w") as f:
                    for c in sorted_candles:
                        f.write(json.dumps(c) + "\n")
                log.debug("Saved %d candles for %s", len(sorted_candles), asset)
            except Exception as e:
                log.error("Failed to write candles for %s: %s", asset, e)

    @staticmethod
    def load_candles(asset: str) -> list[Candle]:
        """Load historical candle data from disk."""
        fpath = CANDLE_DIR / f"{asset}.jsonl"
        if not fpath.exists():
            return []
        candles = []
        try:
            with open(fpath) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                        candles.append(Candle(**d))
                    except (json.JSONDecodeError, KeyError, TypeError):
                        continue
        except Exception as e:
            log.warning("Failed to load candles for %s: %s", asset, e)
            return []
        return sorted(candles, key=lambda c: c.timestamp)
