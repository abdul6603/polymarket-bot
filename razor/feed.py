"""WebSocket real-time price feed for Razor — sub-second price updates."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass

import websockets
from websockets.exceptions import ConnectionClosed

from razor.config import RazorConfig

log = logging.getLogger(__name__)


def _ws_is_closed(ws) -> bool:
    """Check if a websocket is closed, compatible with websockets 12-14."""
    if hasattr(ws, "closed"):
        return ws.closed
    try:
        return ws.close_code is not None
    except Exception:
        return True


class RazorFeed:
    """Async WebSocket feed for real-time Polymarket price data.

    Maintains in-memory price/bid/ask dicts for microsecond-latency reads.
    """

    def __init__(self, cfg: RazorConfig):
        self.cfg = cfg
        self._ws_url = cfg.ws_url
        self._ws = None
        self._running = False
        self._task: asyncio.Task | None = None
        self._ping_task: asyncio.Task | None = None

        # In-memory price state — the hot path
        self.prices: dict[str, float] = {}
        self.best_bids: dict[str, float] = {}
        self.best_asks: dict[str, float] = {}
        self._last_update: dict[str, float] = {}

        # Token subscriptions
        self._token_ids: list[str] = []
        self._subscribe_lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return self._ws is not None and not _ws_is_closed(self._ws)

    @property
    def token_count(self) -> int:
        return len(self._token_ids)

    async def subscribe(self, token_ids: list[str]) -> None:
        """Subscribe to price updates for given token IDs."""
        async with self._subscribe_lock:
            new_ids = [tid for tid in token_ids if tid not in self._token_ids]
            if not new_ids:
                return
            self._token_ids.extend(new_ids)
            if self.connected:
                await self._send_subscribe(new_ids)
            log.info("Subscribed to %d new tokens (total: %d)", len(new_ids), len(self._token_ids))

    def get_pair_prices(self, token_a: str, token_b: str) -> tuple[float, float, float, float]:
        """Get prices for a binary pair. Returns (price_a, price_b, bid_a, bid_b).

        Microsecond read from in-memory dicts.
        """
        return (
            self.prices.get(token_a, 0.0),
            self.prices.get(token_b, 0.0),
            self.best_bids.get(token_a, 0.0),
            self.best_bids.get(token_b, 0.0),
        )

    def token_age(self, token_id: str) -> float:
        """Seconds since last price update for this token."""
        last = self._last_update.get(token_id, 0.0)
        return time.time() - last if last > 0 else float("inf")

    async def start(self) -> None:
        """Start the WebSocket connection loop."""
        self._running = True
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        """Graceful shutdown."""
        self._running = False
        if self._ping_task and not self._ping_task.done():
            self._ping_task.cancel()
            try:
                await self._ping_task
            except asyncio.CancelledError:
                pass
        if self._ws and not _ws_is_closed(self._ws):
            await self._ws.close()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run_loop(self) -> None:
        """Reconnect loop with backoff."""
        while self._running:
            try:
                await self._connect()
            except asyncio.CancelledError:
                return
            except Exception:
                log.exception("WebSocket error, reconnecting in 5s")
                await asyncio.sleep(5)

    async def _connect(self) -> None:
        log.info("Connecting to WS: %s", self._ws_url)
        async with websockets.connect(self._ws_url, ping_interval=None) as ws:
            self._ws = ws
            log.info("WebSocket connected")
            if self._token_ids:
                await self._send_subscribe(self._token_ids)
            self._ping_task = asyncio.create_task(self._ping_loop(ws))
            try:
                async for raw in ws:
                    self._handle_message(raw)
            finally:
                if self._ping_task and not self._ping_task.done():
                    self._ping_task.cancel()

    async def _send_subscribe(self, token_ids: list[str]) -> None:
        """Send subscription messages for token IDs."""
        if not self._ws or _ws_is_closed(self._ws):
            return
        # Batch subscribe — send one message per token (Polymarket WS protocol)
        for token_id in token_ids:
            msg = json.dumps({
                "type": "market",
                "assets_ids": [token_id],
            })
            await self._ws.send(msg)
        log.debug("Sent subscribe for %d tokens", len(token_ids))

    async def _ping_loop(self, ws) -> None:
        try:
            while not _ws_is_closed(ws):
                await asyncio.sleep(10)
                await ws.ping()
        except (asyncio.CancelledError, ConnectionClosed):
            pass

    def _handle_message(self, raw: str) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    self._process_event(item)
        elif isinstance(data, dict):
            self._process_event(data)

    def _process_event(self, data: dict) -> None:
        event_type = data.get("event_type") or data.get("type", "")

        if event_type in ("book", "price_change", "last_trade_price"):
            token_id = data.get("asset_id") or data.get("market")
            price = data.get("price") or data.get("last_traded_price")
            if token_id and price is not None:
                try:
                    self.prices[token_id] = float(price)
                    self._last_update[token_id] = time.time()
                except (ValueError, TypeError):
                    pass

        if event_type == "book":
            token_id = data.get("asset_id") or data.get("market")
            if not token_id:
                return
            bids = data.get("bids", [])
            asks = data.get("asks", [])
            if bids:
                try:
                    best = max(float(b["price"]) if isinstance(b, dict) else float(b[0])
                               for b in bids[:5])
                    self.best_bids[token_id] = best
                except (ValueError, TypeError, KeyError, IndexError):
                    pass
            if asks:
                try:
                    best = min(float(a["price"]) if isinstance(a, dict) else float(a[0])
                               for a in asks[:5])
                    self.best_asks[token_id] = best
                except (ValueError, TypeError, KeyError, IndexError):
                    pass
