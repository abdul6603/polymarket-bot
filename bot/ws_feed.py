from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field

import websockets
from websockets.exceptions import ConnectionClosed

from bot.config import Config

log = logging.getLogger(__name__)


def _ws_is_closed(ws) -> bool:
    """Check if a websocket is closed, compatible with websockets 12-14."""
    if hasattr(ws, "closed"):
        return ws.closed  # websockets <14
    # websockets 14+: check state or connection attribute
    try:
        return ws.close_code is not None
    except Exception:
        return True


@dataclass
class PriceSnapshot:
    token_id: str
    price: float
    timestamp: float


@dataclass
class OrderbookSnapshot:
    buy_pressure: float
    sell_pressure: float
    best_bid: float
    best_ask: float
    spread: float


class MarketFeed:
    """Async WebSocket feed for real-time Polymarket orderbook data."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.ws_url = cfg.ws_url
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._token_ids: list[str] = []
        self._prices: dict[str, deque[PriceSnapshot]] = {}
        self._running = False
        self._task: asyncio.Task | None = None
        self._ping_task: asyncio.Task | None = None
        self._orderbooks: dict[str, OrderbookSnapshot] = {}

    @property
    def latest_orderbook(self) -> dict[str, OrderbookSnapshot]:
        """Get the latest orderbook snapshot for each token."""
        return dict(self._orderbooks)

    @property
    def latest_price(self) -> dict[str, float | None]:
        """Get the latest price for each subscribed token."""
        result: dict[str, float | None] = {}
        for tid, history in self._prices.items():
            result[tid] = history[-1].price if history else None
        return result

    def price_history(self, token_id: str) -> list[PriceSnapshot]:
        return list(self._prices.get(token_id, []))

    async def subscribe(self, token_ids: list[str]) -> None:
        """Update subscriptions to a new set of token IDs."""
        self._token_ids = token_ids
        for tid in token_ids:
            if tid not in self._prices:
                self._prices[tid] = deque(maxlen=100)

        if self._ws and not _ws_is_closed(self._ws):
            await self._send_subscribe()

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
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
        while self._running:
            try:
                await self._connect()
            except asyncio.CancelledError:
                return
            except Exception:
                log.exception("WebSocket error, reconnecting in 5s")
                await asyncio.sleep(5)

    async def _connect(self) -> None:
        log.info("Connecting to WS: %s", self.ws_url)
        async with websockets.connect(self.ws_url, ping_interval=None) as ws:
            self._ws = ws
            log.info("WebSocket connected")

            if self._token_ids:
                await self._send_subscribe()

            self._ping_task = asyncio.create_task(self._ping_loop(ws))

            try:
                async for raw in ws:
                    self._handle_message(raw)
            finally:
                if self._ping_task and not self._ping_task.done():
                    self._ping_task.cancel()

    async def _send_subscribe(self) -> None:
        if not self._ws or _ws_is_closed(self._ws):
            return
        for token_id in self._token_ids:
            msg = json.dumps({
                "type": "market",
                "assets_ids": [token_id],
            })
            await self._ws.send(msg)
            log.debug("Subscribed to token %s", token_id[:16])

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

        # WS may send arrays of events — process each individually
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    self._process_event(item)
            return

        if isinstance(data, dict):
            self._process_event(data)

    def _process_event(self, data: dict) -> None:
        # Handle price update events from the market channel
        event_type = data.get("event_type") or data.get("type", "")

        if event_type in ("book", "price_change", "last_trade_price"):
            token_id = data.get("asset_id") or data.get("market")
            price = data.get("price") or data.get("last_traded_price")
            if token_id and price is not None:
                snap = PriceSnapshot(
                    token_id=token_id,
                    price=float(price),
                    timestamp=time.time(),
                )
                if token_id not in self._prices:
                    self._prices[token_id] = deque(maxlen=100)
                self._prices[token_id].append(snap)

        # Parse orderbook depth from "book" events
        if event_type == "book":
            token_id = data.get("asset_id") or data.get("market")
            bids = data.get("bids", [])
            asks = data.get("asks", [])
            if token_id and (bids or asks):
                self._update_orderbook(token_id, bids, asks)

    def _update_orderbook(self, token_id: str, bids: list, asks: list) -> None:
        """Parse bids/asks arrays and compute buy/sell pressure from top 5 levels."""
        try:
            # Each level is typically {"price": "0.55", "size": "100"} or [price, size]
            def _parse_levels(levels: list, n: int = 5) -> list[tuple[float, float]]:
                parsed = []
                for lvl in levels[:n]:
                    if isinstance(lvl, dict):
                        p = float(lvl.get("price", 0))
                        s = float(lvl.get("size", 0))
                    elif isinstance(lvl, (list, tuple)) and len(lvl) >= 2:
                        p, s = float(lvl[0]), float(lvl[1])
                    else:
                        continue
                    parsed.append((p, s))
                return parsed

            bid_levels = _parse_levels(bids)
            ask_levels = _parse_levels(asks)

            buy_pressure = sum(p * s for p, s in bid_levels)
            sell_pressure = sum(p * s for p, s in ask_levels)
            best_bid = bid_levels[0][0] if bid_levels else 0.0
            best_ask = ask_levels[0][0] if ask_levels else 0.0
            spread = best_ask - best_bid if best_bid and best_ask else 0.0

            self._orderbooks[token_id] = OrderbookSnapshot(
                buy_pressure=buy_pressure,
                sell_pressure=sell_pressure,
                best_bid=best_bid,
                best_ask=best_ask,
                spread=spread,
            )
        except (ValueError, TypeError, IndexError):
            pass
