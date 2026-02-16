from __future__ import annotations

import asyncio
import json
import logging

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
}

STREAMS = "/".join(f"{s}@trade" for s in SYMBOL_MAP)


class BinanceFeed:
    """Real-time trade feed from Binance public WebSocket (no API key needed)."""

    def __init__(self, cfg: Config, price_cache: PriceCache):
        self.cfg = cfg
        self._cache = price_cache
        self._ws = None
        self._running = False
        self._task: asyncio.Task | None = None
        base = getattr(cfg, "binance_ws_url", "wss://stream.binance.us:9443")
        self._url = f"{base}/stream?streams={STREAMS}"

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._running = False
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
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
        async with websockets.connect(self._url, ping_interval=20) as ws:
            self._ws = ws
            log.info("Binance WebSocket connected")
            try:
                async for raw in ws:
                    self._handle_message(raw)
            except ConnectionClosed:
                log.warning("Binance WS connection closed")

    def _handle_message(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        # Combined stream format: {"stream": "btcusdt@trade", "data": {...}}
        data = msg.get("data")
        if not data:
            return

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
