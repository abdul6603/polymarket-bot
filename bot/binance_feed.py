from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

from bot.config import Config
from bot.price_cache import PriceCache

log = logging.getLogger(__name__)

# Binance symbol -> our internal asset name
SYMBOL_MAP = {
    "btcusdt": "bitcoin",
    "ethusdt": "ethereum",
    "solusdt": "solana",
    "xrpusdt": "xrp",
}

# Trade streams (individual trades for price/candle building)
TRADE_STREAMS = "/".join(f"{s}@trade" for s in SYMBOL_MAP)
# Depth streams (top 5 order book levels, updated every 1s)
DEPTH_STREAMS = "/".join(f"{s}@depth5@1000ms" for s in SYMBOL_MAP)
# Combined streams
STREAMS = f"{TRADE_STREAMS}/{DEPTH_STREAMS}"


class BinanceFeed:
    """Real-time trade + order book feed from Binance public WebSocket."""

    def __init__(self, cfg: Config, price_cache: PriceCache):
        self.cfg = cfg
        self._cache = price_cache
        self._ws = None
        self._running = False
        self._task: asyncio.Task | None = None
        # Use global Binance WS (stream.binance.com) — .us WS blocks non-US IPs
        # REST API stays on api.binance.us (works from any IP)
        base = getattr(cfg, "binance_ws_url", "wss://stream.binance.com:9443")
        self._url = f"{base}/stream?streams={STREAMS}"

        # Order book depth: asset -> {"bids": [[price, qty], ...], "asks": [...], "timestamp": float}
        self.depth: dict[str, dict[str, Any]] = {}

    async def start(self) -> None:
        """Start WS in a dedicated thread with its own event loop.

        The main asyncio loop is blocked by synchronous _tick() calls,
        starving coroutines. Own thread = unblocked WS connection.
        """
        self._running = True
        self._thread = threading.Thread(
            target=self._thread_entry, daemon=True, name="binance-ws",
        )
        self._thread.start()

    def _thread_entry(self) -> None:
        """Thread entry — create private event loop and run WS."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._run_loop())
        finally:
            self._loop.close()

    async def stop(self) -> None:
        self._running = False
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await self._connect()
            except asyncio.CancelledError:
                return
            except Exception:
                log.exception("Binance WS error, reconnecting in 5s")
                await asyncio.sleep(5)

    async def _connect(self) -> None:
        log.info("Connecting to Binance WS: %s", self._url[:80])
        async with websockets.connect(
            self._url, ping_interval=20, ping_timeout=10,
            open_timeout=30, close_timeout=5,
        ) as ws:
            self._ws = ws
            self._last_msg_ts = time.time()
            log.info("Binance WebSocket connected (trade + depth5)")
            try:
                async for raw in ws:
                    self._last_msg_ts = time.time()
                    self._handle_message(raw)
                    # Stale watchdog: if no message for 60s, force reconnect
                    if time.time() - self._last_msg_ts > 60:
                        log.warning("Binance WS stale (>60s no data), forcing reconnect")
                        break
            except ConnectionClosed:
                log.warning("Binance WS connection closed, will reconnect")

    def _handle_message(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        stream = msg.get("stream", "")
        data = msg.get("data")
        if not data:
            return

        if "@depth" in stream:
            self._handle_depth(data, stream)
        else:
            self._handle_trade(data)

    def _handle_trade(self, data: dict) -> None:
        """Process individual trade tick."""
        symbol = (data.get("s") or "").lower()
        asset = SYMBOL_MAP.get(symbol)
        if not asset:
            return

        try:
            price = float(data["p"])
            volume = float(data["q"])
            timestamp = data["T"] / 1000.0  # Binance sends ms
        except (KeyError, ValueError, TypeError):
            return

        self._cache.update_tick(asset, price, volume, timestamp)

    def _handle_depth(self, data: dict, stream: str) -> None:
        """Process order book depth snapshot (top 5 levels)."""
        # Stream name: "btcusdt@depth5@1000ms"
        symbol = stream.split("@")[0]
        asset = SYMBOL_MAP.get(symbol)
        if not asset:
            return

        bids = data.get("bids", [])
        asks = data.get("asks", [])

        self.depth[asset] = {
            "bids": bids,  # [[price_str, qty_str], ...]
            "asks": asks,
            "timestamp": time.time(),
        }

    def get_depth(self, asset: str) -> dict[str, Any] | None:
        """Get latest order book depth for an asset."""
        d = self.depth.get(asset)
        if not d:
            return None
        # Stale check: depth older than 10s is useless
        if time.time() - d["timestamp"] > 10:
            return None
        return d

    def get_depth_summary(self) -> dict[str, dict]:
        """Get depth summary for all assets (for dashboard)."""
        now = time.time()
        result = {}
        for asset, d in self.depth.items():
            age = now - d["timestamp"]
            if age > 30:
                continue
            bids = d.get("bids", [])
            asks = d.get("asks", [])
            bid_depth_usd = sum(float(b[0]) * float(b[1]) for b in bids) if bids else 0
            ask_depth_usd = sum(float(a[0]) * float(a[1]) for a in asks) if asks else 0
            total = bid_depth_usd + ask_depth_usd
            imbalance = (bid_depth_usd - ask_depth_usd) / total if total > 0 else 0
            result[asset] = {
                "bid_depth_usd": round(bid_depth_usd, 2),
                "ask_depth_usd": round(ask_depth_usd, 2),
                "imbalance": round(imbalance, 4),
                "best_bid": float(bids[0][0]) if bids else 0,
                "best_ask": float(asks[0][0]) if asks else 0,
                "spread_pct": round((float(asks[0][0]) - float(bids[0][0])) / float(bids[0][0]) * 100, 4) if bids and asks else 0,
                "levels": len(bids),
                "age_s": round(age, 1),
            }
        return result
