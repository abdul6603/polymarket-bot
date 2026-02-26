"""Order Book Imbalance Signal — predictive direction from Binance order flow.

Streams Binance.US BTC/ETH/SOL/XRP order books via combined WebSocket.
Calculates bid/ask volume imbalance to detect buying/selling pressure
BEFORE the price moves — giving the snipe engine earlier entry timing.

v8: Multi-asset combined stream for BTC, ETH, SOL, XRP.
Uses the combined stream format: /stream?streams=...

Imbalance = (bid_vol - ask_vol) / (bid_vol + ask_vol)
  +0.30 = strong buy pressure (65% bids) → likely UP
  -0.30 = strong sell pressure (65% asks) → likely DOWN
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass

log = logging.getLogger("garves.snipe")

# Binance.US combined WebSocket — top 20 levels, 100ms updates, all 4 assets
STREAM_NAMES = [
    "btcusdt@depth20@100ms",
    "ethusdt@depth20@100ms",
    "solusdt@depth20@100ms",
    "xrpusdt@depth20@100ms",
]
WS_URL = "wss://stream.binance.com:9443/stream?streams=" + "/".join(STREAM_NAMES)

# Map stream symbol prefix to asset name
SYMBOL_TO_ASSET = {
    "btcusdt": "bitcoin",
    "ethusdt": "ethereum",
    "solusdt": "solana",
    "xrpusdt": "xrp",
}
ASSETS = tuple(SYMBOL_TO_ASSET.values())

# Imbalance thresholds
IMBALANCE_THRESHOLD = 0.25       # |imbalance| > 0.25 to signal direction
SUSTAINED_TICKS_REQUIRED = 3     # 3 consecutive ticks same direction (~6s at 2s sampling)
SAMPLE_INTERVAL = 2.0            # Sample every 2s (matches engine tick)
MAX_HISTORY = 30                 # Keep last 30 readings (~60s)
RECONNECT_DELAY_BASE = 2         # Base reconnect delay (doubles on failure, caps at 30s)
RECONNECT_DELAY_MAX = 30
STALE_THRESHOLD = 10.0           # Data is stale if no update in 10s


@dataclass
class ImbalanceReading:
    """Single order book imbalance snapshot."""
    timestamp: float
    bid_volume: float       # Total volume on bid side (top 10 levels)
    ask_volume: float       # Total volume on ask side (top 10 levels)
    imbalance: float        # -1.0 to +1.0
    direction: str          # "up" or "down"
    best_bid: float         # Best bid price
    best_ask: float         # Best ask price


@dataclass
class ImbalanceSignal:
    """Signal from order book imbalance analysis."""
    direction: str          # "up" or "down"
    strength: float         # 0.0 to 1.0 (normalized imbalance magnitude)
    sustained_ticks: int    # How many consecutive ticks same direction
    imbalance: float        # Raw imbalance value
    bid_volume: float
    ask_volume: float


class OrderBookSignal:
    """Streams Binance multi-asset order books and generates imbalance signals."""

    def __init__(self):
        # Per-asset storage
        self._readings: dict[str, deque[ImbalanceReading]] = {
            a: deque(maxlen=MAX_HISTORY) for a in ASSETS
        }
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._connected = False
        self._last_update: dict[str, float] = {a: 0.0 for a in ASSETS}
        self._reconnect_delay = RECONNECT_DELAY_BASE

        # Latest raw data from WebSocket per asset (updated at ~100ms by WS)
        self._latest_bids: dict[str, list] = {a: [] for a in ASSETS}
        self._latest_asks: dict[str, list] = {a: [] for a in ASSETS}
        self._ws_lock = threading.Lock()

    def start(self) -> None:
        """Start the WebSocket streaming thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="orderbook-ws")
        self._thread.start()
        log.info("[ORDERBOOK] Started Binance order book stream (4 assets)")

    def stop(self) -> None:
        """Stop the WebSocket thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        log.info("[ORDERBOOK] Stopped")

    def get_signal(self, asset: str = "bitcoin") -> ImbalanceSignal | None:
        """Get current imbalance signal for an asset. Returns None if no clear signal."""
        with self._lock:
            readings = self._readings.get(asset)
            if not readings or len(readings) < SUSTAINED_TICKS_REQUIRED:
                return None

            # Check staleness
            if time.time() - self._last_update.get(asset, 0) > STALE_THRESHOLD:
                return None

            latest = readings[-1]

            # Check if imbalance exceeds threshold
            if abs(latest.imbalance) < IMBALANCE_THRESHOLD:
                return None

            # Check sustained direction
            sustained = 0
            direction = latest.direction
            for r in reversed(readings):
                if r.direction == direction and abs(r.imbalance) >= IMBALANCE_THRESHOLD * 0.7:
                    sustained += 1
                else:
                    break

            if sustained < SUSTAINED_TICKS_REQUIRED:
                return None

            # Normalize strength: 0.25 → 0.0, 0.50 → 0.5, 0.75+ → 1.0
            strength = min(1.0, max(0.0, (abs(latest.imbalance) - IMBALANCE_THRESHOLD) / 0.50))

            return ImbalanceSignal(
                direction=direction,
                strength=round(strength, 3),
                sustained_ticks=sustained,
                imbalance=round(latest.imbalance, 4),
                bid_volume=round(latest.bid_volume, 4),
                ask_volume=round(latest.ask_volume, 4),
            )

    @property
    def is_connected(self) -> bool:
        # Connected if any asset has recent data
        now = time.time()
        return self._connected and any(
            now - ts < STALE_THRESHOLD for ts in self._last_update.values()
        )

    def get_latest_reading(self, asset: str = "bitcoin") -> ImbalanceReading | None:
        """Get latest reading for an asset."""
        with self._lock:
            readings = self._readings.get(asset)
            return readings[-1] if readings else None

    def get_status(self) -> dict:
        """Dashboard-friendly status."""
        result = {"connected": self.is_connected, "assets": {}}
        for asset in ASSETS:
            reading = self.get_latest_reading(asset)
            signal = self.get_signal(asset)
            result["assets"][asset] = {
                "readings": len(self._readings.get(asset, [])),
                "latest_imbalance": round(reading.imbalance, 4) if reading else None,
                "latest_direction": reading.direction if reading else None,
                "signal_active": signal is not None,
                "signal_direction": signal.direction if signal else None,
                "signal_strength": signal.strength if signal else None,
                "signal_sustained": signal.sustained_ticks if signal else None,
            }
        return result

    def _run_loop(self) -> None:
        """Main loop: connect, stream, sample, reconnect on failure."""
        while self._running:
            try:
                self._stream()
            except Exception as e:
                self._connected = False
                if self._running:
                    log.warning(
                        "[ORDERBOOK] Disconnected: %s — reconnecting in %ds",
                        str(e)[:100], self._reconnect_delay,
                    )
                    time.sleep(self._reconnect_delay)
                    self._reconnect_delay = min(self._reconnect_delay * 2, RECONNECT_DELAY_MAX)

    def _stream(self) -> None:
        """Connect to combined WebSocket and stream order book data."""
        import websocket

        ws = websocket.create_connection(WS_URL, timeout=10)
        self._connected = True
        self._reconnect_delay = RECONNECT_DELAY_BASE
        log.info("[ORDERBOOK] Connected to Binance combined WebSocket (4 assets)")

        last_sample: dict[str, float] = {a: 0.0 for a in ASSETS}
        try:
            while self._running:
                raw = ws.recv()
                data = json.loads(raw)

                # Combined stream format: {"stream": "btcusdt@depth20@100ms", "data": {...}}
                stream_name = data.get("stream", "")
                payload = data.get("data", data)  # Fallback for single-stream format

                # Extract symbol from stream name
                symbol = stream_name.split("@")[0] if "@" in stream_name else ""
                asset = SYMBOL_TO_ASSET.get(symbol)
                if not asset:
                    continue

                # Store latest data
                with self._ws_lock:
                    self._latest_bids[asset] = payload.get("bids", [])
                    self._latest_asks[asset] = payload.get("asks", [])

                # Sample at SAMPLE_INTERVAL per asset
                now = time.time()
                if now - last_sample.get(asset, 0) >= SAMPLE_INTERVAL:
                    last_sample[asset] = now
                    self._process_snapshot(asset)
        finally:
            ws.close()

    def _process_snapshot(self, asset: str) -> None:
        """Process current order book snapshot for an asset into an imbalance reading."""
        with self._ws_lock:
            bids = self._latest_bids[asset][:]
            asks = self._latest_asks[asset][:]

        if not bids or not asks:
            return

        # Sum top 10 levels volume
        bid_vol = sum(float(b[1]) for b in bids[:10])
        ask_vol = sum(float(a[1]) for a in asks[:10])
        total = bid_vol + ask_vol

        if total <= 0:
            return

        imbalance = (bid_vol - ask_vol) / total
        direction = "up" if imbalance > 0 else "down"
        best_bid = float(bids[0][0]) if bids else 0
        best_ask = float(asks[0][0]) if asks else 0

        reading = ImbalanceReading(
            timestamp=time.time(),
            bid_volume=bid_vol,
            ask_volume=ask_vol,
            imbalance=imbalance,
            direction=direction,
            best_bid=best_bid,
            best_ask=best_ask,
        )

        with self._lock:
            self._readings[asset].append(reading)
            self._last_update[asset] = time.time()

    def reset(self, asset: str | None = None) -> None:
        """Clear readings. If asset=None, clear all."""
        with self._lock:
            if asset:
                if asset in self._readings:
                    self._readings[asset].clear()
            else:
                for a in ASSETS:
                    self._readings[a].clear()
