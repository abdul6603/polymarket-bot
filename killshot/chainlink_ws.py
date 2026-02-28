"""Real-time Chainlink price feed via Polymarket RTDS WebSocket.

This is the EXACT price Polymarket uses to resolve crypto Up/Down markets.
Sub-second updates, zero cost, zero auth.

Key discovery: subscribing WITH filters only sends one historical batch.
Subscribing WITHOUT filters sends continuous real-time updates.

Usage:
    feed = ChainlinkWS()
    feed.start()
    price = feed.get_price("bitcoin")  # -> 65432.10
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from typing import Optional

import websockets

log = logging.getLogger("killshot.chainlink_ws")

RTDS_URL = "wss://ws-live-data.polymarket.com"

# Map RTDS symbol names to our asset names
SYMBOL_TO_ASSET = {
    "btc/usd": "bitcoin",
    "eth/usd": "ethereum",
    "sol/usd": "solana",
    "xrp/usd": "xrp",
}


class ChainlinkWS:
    """Real-time Chainlink prices from Polymarket's RTDS WebSocket."""

    def __init__(self):
        self._prices: dict[str, float] = {}
        self._timestamps: dict[str, float] = {}
        self._running = False
        self._connected = False
        self._update_count = 0
        self._thread: Optional[threading.Thread] = None

    def get_price(self, asset: str) -> Optional[float]:
        """Get latest Chainlink price. Returns None if no data yet."""
        return self._prices.get(asset.lower())

    def get_price_age(self, asset: str) -> float:
        """Seconds since last update. inf if never updated."""
        ts = self._timestamps.get(asset.lower())
        if ts is None:
            return float("inf")
        return time.time() - ts

    def start(self) -> None:
        """Start the WebSocket feed in a daemon thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        log.info("[CHAINLINK-WS] Started real-time feed thread")

    def stop(self) -> None:
        self._running = False

    def _run_loop(self) -> None:
        """Run the async WebSocket in its own event loop."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        while self._running:
            try:
                loop.run_until_complete(self._connect())
            except Exception as e:
                log.warning("[CHAINLINK-WS] Connection error: %s", str(e)[:100])
            if self._running:
                time.sleep(5)

    async def _connect(self) -> None:
        """Connect to RTDS and stream all crypto prices."""
        try:
            async with websockets.connect(
                RTDS_URL,
                ping_interval=20,
                ping_timeout=30,
                close_timeout=5,
            ) as ws:
                # Subscribe WITHOUT filters — this gives continuous real-time updates
                subscribe_msg = {
                    "action": "subscribe",
                    "subscriptions": [{
                        "topic": "crypto_prices_chainlink",
                        "type": "*",
                    }],
                }
                await ws.send(json.dumps(subscribe_msg))
                self._connected = True
                self._update_count = 0
                log.info("[CHAINLINK-WS] Connected — streaming all assets")

                while self._running:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=30)
                        if not raw or not raw.strip():
                            continue
                        try:
                            msg = json.loads(raw)
                            self._handle_message(msg)
                        except (json.JSONDecodeError, ValueError):
                            pass
                    except asyncio.TimeoutError:
                        # No message for 30s — send ping to keep alive
                        await ws.ping()
                    except websockets.ConnectionClosed:
                        log.warning("[CHAINLINK-WS] Connection closed")
                        break

        except Exception as e:
            log.warning("[CHAINLINK-WS] Error: %s", str(e)[:100])

    def _handle_message(self, msg: dict) -> None:
        """Process incoming RTDS price message.

        Live update format:
        {
            "payload": {
                "symbol": "btc/usd",
                "value": 63574.043545479006,
                "full_accuracy_value": "63574043545479007000000",
                "timestamp": 1772272063000
            },
            "type": "update",
            "topic": "crypto_prices_chainlink"
        }
        """
        payload = msg.get("payload", {})

        # Live update format: payload.value + payload.symbol
        symbol = payload.get("symbol", "").lower()
        value = payload.get("value")

        if symbol and value is not None:
            asset = SYMBOL_TO_ASSET.get(symbol)
            if asset:
                try:
                    price = float(value)
                    self._prices[asset] = price
                    self._timestamps[asset] = time.time()
                    self._update_count += 1
                    if self._update_count <= 3 or self._update_count % 100 == 0:
                        log.info(
                            "[CHAINLINK-WS] %s $%.2f (update #%d)",
                            asset.upper(), price, self._update_count,
                        )
                except (ValueError, TypeError):
                    pass
            return

        # Historical batch format (initial burst): payload.data[]
        data = payload.get("data", [])
        if data and isinstance(data, list):
            latest = data[-1]
            batch_value = latest.get("value")
            if batch_value is not None:
                # Historical batches don't have symbol in each point,
                # but we can match by price range
                try:
                    price = float(batch_value)
                    # Assume BTC if price > 10000
                    if price > 10000:
                        self._prices["bitcoin"] = price
                        self._timestamps["bitcoin"] = time.time()
                        log.info("[CHAINLINK-WS] BTC $%.2f (from batch of %d)", price, len(data))
                except (ValueError, TypeError):
                    pass
