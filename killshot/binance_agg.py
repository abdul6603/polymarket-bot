"""Binance @aggTrade WebSocket — leading price indicator.

Connects to Binance.US aggregate trade stream for real-time tick data.
Computes volume-weighted price delta over a 30-second rolling window.
Binance leads Chainlink by 2-5 seconds — use as a confidence booster.

Usage:
    feed = BinanceAggWS(["bitcoin", "ethereum"])
    feed.start()
    sig = feed.get_signal("bitcoin")  # -> (delta_pct, volume_usd, confidence)
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections import deque

import websockets

log = logging.getLogger("killshot.binance_agg")

BINANCE_US_WS = "wss://stream.binance.us:9443/ws"

ASSET_TO_SYMBOL = {
    "bitcoin": "btcusdt",
    "ethereum": "ethusdt",
    "solana": "solusdt",
    "xrp": "xrpusdt",
}

SYMBOL_TO_ASSET = {v: k for k, v in ASSET_TO_SYMBOL.items()}

WINDOW_SECONDS = 30


class BinanceAggWS:
    """Real-time aggregate trade feed from Binance.US."""

    def __init__(self, assets: list[str] | None = None):
        self._assets = assets or list(ASSET_TO_SYMBOL.keys())
        self._trades: dict[str, deque] = {a: deque(maxlen=5000) for a in self._assets}
        self._running = False
        self._connected = False
        self._thread: threading.Thread | None = None
        self._update_count = 0

    def get_signal(self, asset: str) -> tuple[float, float, float] | None:
        """Get leading indicator signal for an asset.

        Returns (delta_pct, volume_usd, confidence) or None.
        - delta_pct: volume-weighted price change over 30s window
        - volume_usd: total USD volume in window
        - confidence: 0-1 based on trade count
        """
        asset = asset.lower()
        trades = self._trades.get(asset)
        if not trades or len(trades) < 5:
            return None

        now = time.time()
        cutoff = now - WINDOW_SECONDS

        # Prune old trades
        while trades and trades[0][0] < cutoff:
            trades.popleft()

        if len(trades) < 5:
            return None

        # Split into early/late halves for VWAP comparison
        items = list(trades)
        half = len(items) // 2
        early = items[:half]
        late = items[half:]

        early_vol = sum(t[2] for t in early)
        late_vol = sum(t[2] for t in late)

        if early_vol < 1e-10 or late_vol < 1e-10:
            return None

        early_vwap = sum(t[1] * t[2] for t in early) / early_vol
        late_vwap = sum(t[1] * t[2] for t in late) / late_vol

        if early_vwap <= 0:
            return None

        delta_pct = (late_vwap - early_vwap) / early_vwap
        volume_usd = sum(t[1] * t[2] for t in items)
        confidence = min(len(items) / 100.0, 1.0)

        return (delta_pct, volume_usd, confidence)

    def start(self) -> None:
        """Start the WebSocket feed in a daemon thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="binance-agg",
        )
        self._thread.start()
        log.info("[BINANCE-AGG] Started feed for %s", ", ".join(self._assets))

    def stop(self) -> None:
        self._running = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def _run_loop(self) -> None:
        """Run the async WebSocket in its own event loop."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        while self._running:
            try:
                loop.run_until_complete(self._connect())
            except Exception as e:
                log.warning("[BINANCE-AGG] Connection error: %s", str(e)[:100])
            if self._running:
                time.sleep(5)

    async def _connect(self) -> None:
        """Connect to Binance.US and stream aggregate trades."""
        symbols = [ASSET_TO_SYMBOL[a] for a in self._assets if a in ASSET_TO_SYMBOL]
        if not symbols:
            return

        streams = "/".join(f"{s}@aggTrade" for s in symbols)
        url = f"{BINANCE_US_WS}/{streams}"

        try:
            async with websockets.connect(
                url,
                ping_interval=20,
                ping_timeout=30,
                close_timeout=5,
            ) as ws:
                self._connected = True
                self._update_count = 0
                log.info("[BINANCE-AGG] Connected — streaming %d symbols", len(symbols))

                while self._running:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=30)
                        self._handle_message(raw)
                    except asyncio.TimeoutError:
                        await ws.ping()
                    except websockets.ConnectionClosed:
                        log.warning("[BINANCE-AGG] Connection closed — reconnecting")
                        break

        except Exception as e:
            log.warning("[BINANCE-AGG] Error: %s", str(e)[:100])
        finally:
            self._connected = False

    def _handle_message(self, raw: str) -> None:
        """Process incoming aggTrade message.

        Combined stream format: {"stream": "btcusdt@aggTrade", "data": {...}}
        Single stream format: {"s": "BTCUSDT", "p": "65432.10", "q": "0.5", ...}
        """
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return

        # Combined stream wraps in "data"
        if "data" in data:
            data = data["data"]

        symbol = data.get("s", "").lower()
        asset = SYMBOL_TO_ASSET.get(symbol)
        if not asset:
            return

        try:
            price = float(data["p"])
            qty = float(data["q"])
            ts = time.time()
            self._trades[asset].append((ts, price, qty))
            self._update_count += 1

            if self._update_count <= 3 or self._update_count % 5000 == 0:
                log.info(
                    "[BINANCE-AGG] %s $%.2f × %.4f (update #%d)",
                    asset.upper(), price, qty, self._update_count,
                )
        except (KeyError, ValueError):
            pass
