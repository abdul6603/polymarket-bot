"""Order Book Imbalance Signal — predictive direction from Binance order flow.

Streams Binance.US BTC/USDT order book via WebSocket.
Calculates bid/ask volume imbalance to detect buying/selling pressure
BEFORE the price moves — giving the snipe engine earlier entry timing.

Imbalance = (bid_vol - ask_vol) / (bid_vol + ask_vol)
  +0.30 = strong buy pressure (65% bids) → BTC likely UP
  -0.30 = strong sell pressure (65% asks) → BTC likely DOWN
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass

log = logging.getLogger("garves.snipe")

# Binance.US WebSocket — top 20 levels, 100ms updates
WS_URL = "wss://stream.binance.us:9443/ws/btcusdt@depth20@100ms"

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
    bid_volume: float       # Total BTC on bid side (top 10 levels)
    ask_volume: float       # Total BTC on ask side (top 10 levels)
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
    """Streams Binance BTC/USDT order book and generates imbalance signals."""

    def __init__(self):
        self._readings: deque[ImbalanceReading] = deque(maxlen=MAX_HISTORY)
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._connected = False
        self._last_update = 0.0
        self._reconnect_delay = RECONNECT_DELAY_BASE

        # Latest raw data from WebSocket (updated at ~100ms by WS, sampled at 2s)
        self._latest_bids: list = []
        self._latest_asks: list = []
        self._ws_lock = threading.Lock()

    def start(self) -> None:
        """Start the WebSocket streaming thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="orderbook-ws")
        self._thread.start()
        log.info("[ORDERBOOK] Started Binance.US BTC/USDT order book stream")

    def stop(self) -> None:
        """Stop the WebSocket thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        log.info("[ORDERBOOK] Stopped")

    def get_signal(self) -> ImbalanceSignal | None:
        """Get current imbalance signal. Returns None if no clear signal."""
        with self._lock:
            if len(self._readings) < SUSTAINED_TICKS_REQUIRED:
                return None

            # Check staleness
            if time.time() - self._last_update > STALE_THRESHOLD:
                return None

            latest = self._readings[-1]

            # Check if imbalance exceeds threshold
            if abs(latest.imbalance) < IMBALANCE_THRESHOLD:
                return None

            # Check sustained direction
            sustained = 0
            direction = latest.direction
            for r in reversed(self._readings):
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
        return self._connected and (time.time() - self._last_update < STALE_THRESHOLD)

    def get_latest_reading(self) -> ImbalanceReading | None:
        """Get latest reading for logging."""
        with self._lock:
            return self._readings[-1] if self._readings else None

    def get_status(self) -> dict:
        """Dashboard-friendly status."""
        reading = self.get_latest_reading()
        signal = self.get_signal()
        return {
            "connected": self.is_connected,
            "readings": len(self._readings),
            "latest_imbalance": round(reading.imbalance, 4) if reading else None,
            "latest_direction": reading.direction if reading else None,
            "signal_active": signal is not None,
            "signal_direction": signal.direction if signal else None,
            "signal_strength": signal.strength if signal else None,
            "signal_sustained": signal.sustained_ticks if signal else None,
        }

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
        """Connect to WebSocket and stream order book data."""
        import websocket

        ws = websocket.create_connection(WS_URL, timeout=10)
        self._connected = True
        self._reconnect_delay = RECONNECT_DELAY_BASE
        log.info("[ORDERBOOK] Connected to Binance.US WebSocket")

        last_sample = 0.0
        try:
            while self._running:
                raw = ws.recv()
                data = json.loads(raw)

                # Store latest data (updated at ~100ms)
                with self._ws_lock:
                    self._latest_bids = data.get("bids", [])
                    self._latest_asks = data.get("asks", [])

                # Sample at SAMPLE_INTERVAL
                now = time.time()
                if now - last_sample >= SAMPLE_INTERVAL:
                    last_sample = now
                    self._process_snapshot()
        finally:
            ws.close()

    def _process_snapshot(self) -> None:
        """Process current order book snapshot into an imbalance reading."""
        with self._ws_lock:
            bids = self._latest_bids[:]
            asks = self._latest_asks[:]

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
            self._readings.append(reading)
            self._last_update = time.time()

    def reset(self) -> None:
        """Clear readings for new window."""
        with self._lock:
            self._readings.clear()
