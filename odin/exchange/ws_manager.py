"""Hyperliquid WebSocket manager — real-time price feeds and order updates.

Bridges the SDK's threading-based WebSocket to Odin's asyncio event loop.
All callbacks push events into an asyncio.Queue for safe cross-thread dispatch.

Subscriptions:
  - allMids: real-time mid prices for all coins (replaces REST polling)
  - l2Book: orderbook depth for active symbols (dynamic subscribe/unsub)
  - userFills: fill notifications for limit orders
  - orderUpdates: order status changes (filled, cancelled, etc.)
"""
from __future__ import annotations

import asyncio
import logging
import time
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from hyperliquid.info import Info
from hyperliquid.utils import constants

from odin.config import OdinConfig

log = logging.getLogger("odin.ws")


@dataclass
class WSEvent:
    """Event pushed from WS thread to asyncio queue."""
    channel: str
    data: Any
    timestamp: float = 0.0

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()


class OdinWSManager:
    """Manages Hyperliquid WebSocket connection with asyncio bridge.

    Architecture:
      - SDK Info(skip_ws=False) creates a WebsocketManager (threading.Thread)
      - Callbacks run on the WS thread
      - We push WSEvent objects into an asyncio.Queue via loop.call_soon_threadsafe
      - Odin's asyncio loop reads from the queue and dispatches events
    """

    def __init__(self, cfg: OdinConfig, loop: Optional[asyncio.AbstractEventLoop] = None):
        self._cfg = cfg
        self._loop = loop
        self._info: Optional[Info] = None
        self._running = False
        self._connected = False

        # Event queue: WS thread → asyncio loop
        self._queue: asyncio.Queue[WSEvent] = asyncio.Queue(maxsize=5000)

        # Cached state (updated on every WS tick)
        self._mid_prices: dict[str, float] = {}
        self._mid_prices_lock = threading.Lock()
        self._last_mid_tick: float = 0.0

        self._l2_books: dict[str, dict] = {}
        self._l2_lock = threading.Lock()

        # Track subscriptions for reconnect
        self._book_subs: set[str] = set()  # coins with active l2Book subs
        self._sub_ids: dict[str, int] = {}  # identifier → subscription_id

        # Account address for user subscriptions
        self._account_address = cfg.hl_account_address
        if not self._account_address and cfg.hl_secret_key:
            import eth_account
            wallet = eth_account.Account.from_key(cfg.hl_secret_key)
            self._account_address = wallet.address

        # Reconnect state
        self._reconnect_count = 0
        self._last_connect_attempt: float = 0.0

        # Health
        self._start_time: float = 0.0
        self._events_received: int = 0

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def queue(self) -> asyncio.Queue[WSEvent]:
        return self._queue

    def start(self, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        """Start WebSocket connection and subscribe to feeds."""
        if loop:
            self._loop = loop
        if not self._loop:
            self._loop = asyncio.get_event_loop()

        self._running = True
        self._start_time = time.time()
        self._connect()

    def _connect(self) -> None:
        """Create Info client with WS enabled and subscribe."""
        self._last_connect_attempt = time.time()
        base_url = (
            constants.TESTNET_API_URL if self._cfg.hl_testnet
            else constants.MAINNET_API_URL
        )

        try:
            # Create Info with WS enabled
            self._info = Info(base_url, skip_ws=False)
            self._connected = True
            log.info("[WS] Connected to %s", base_url)

            # Subscribe to allMids (real-time prices for all coins)
            sub_id = self._info.subscribe(
                {"type": "allMids"},
                self._on_all_mids,
            )
            self._sub_ids["allMids"] = sub_id
            log.info("[WS] Subscribed: allMids")

            # Subscribe to user fills if we have an account
            if self._account_address:
                sub_id = self._info.subscribe(
                    {"type": "userFills", "user": self._account_address},
                    self._on_user_fills,
                )
                self._sub_ids["userFills"] = sub_id
                log.info("[WS] Subscribed: userFills")

                sub_id = self._info.subscribe(
                    {"type": "orderUpdates", "user": self._account_address},
                    self._on_order_updates,
                )
                self._sub_ids["orderUpdates"] = sub_id
                log.info("[WS] Subscribed: orderUpdates")

            # Re-subscribe L2 books for previously subscribed coins
            for coin in list(self._book_subs):
                self._subscribe_book_internal(coin)

            self._reconnect_count += 1 if self._reconnect_count > 0 else 0

        except Exception as e:
            self._connected = False
            log.error("[WS] Connection failed: %s", str(e)[:200])

    def stop(self) -> None:
        """Clean shutdown."""
        self._running = False
        self._connected = False
        if self._info:
            try:
                self._info.disconnect_websocket()
            except Exception as e:
                log.debug("[WS] Disconnect error: %s", str(e)[:100])
            self._info = None
        log.info("[WS] Stopped (events received: %d)", self._events_received)

    # ── Dynamic Subscriptions ──

    def subscribe_book(self, coin: str) -> None:
        """Subscribe to L2 orderbook for a coin."""
        coin = coin.replace("USDT", "").upper()
        if coin in self._book_subs:
            return
        self._book_subs.add(coin)
        if self._connected and self._info:
            self._subscribe_book_internal(coin)

    def unsubscribe_book(self, coin: str) -> None:
        """Unsubscribe from L2 orderbook for a coin."""
        coin = coin.replace("USDT", "").upper()
        self._book_subs.discard(coin)
        key = f"l2Book:{coin}"
        sub_id = self._sub_ids.pop(key, None)
        if sub_id is not None and self._info:
            try:
                self._info.unsubscribe(
                    {"type": "l2Book", "coin": coin},
                    sub_id,
                )
            except Exception:
                pass
        with self._l2_lock:
            self._l2_books.pop(coin, None)

    def _subscribe_book_internal(self, coin: str) -> None:
        """Internal: subscribe to l2Book for a coin."""
        if not self._info:
            return
        try:
            sub_id = self._info.subscribe(
                {"type": "l2Book", "coin": coin},
                self._on_l2_book,
            )
            self._sub_ids[f"l2Book:{coin}"] = sub_id
            log.debug("[WS] Subscribed: l2Book:%s", coin)
        except Exception as e:
            log.warning("[WS] l2Book subscribe error for %s: %s", coin, str(e)[:100])

    # ── Cached Getters ──

    def get_mid(self, coin: str) -> float:
        """Get latest cached mid price for a coin (thread-safe)."""
        coin = coin.replace("USDT", "").upper()
        with self._mid_prices_lock:
            return self._mid_prices.get(coin, 0.0)

    def get_all_mids(self) -> dict[str, float]:
        """Get all cached mid prices (thread-safe copy)."""
        with self._mid_prices_lock:
            return dict(self._mid_prices)

    def get_book(self, coin: str) -> dict:
        """Get latest cached L2 book for a coin (thread-safe)."""
        coin = coin.replace("USDT", "").upper()
        with self._l2_lock:
            return self._l2_books.get(coin, {"bids": [], "asks": []})

    @property
    def last_tick_age(self) -> float:
        """Seconds since last allMids tick (staleness indicator)."""
        if self._last_mid_tick == 0:
            return float("inf")
        return time.time() - self._last_mid_tick

    # ── WS Callbacks (run on WS thread) ──

    def _on_all_mids(self, msg: dict) -> None:
        """allMids tick — update cached prices and push to asyncio queue."""
        try:
            data = msg.get("data", {})
            mids = data.get("mids", {})
            if not mids:
                return

            # Update cache (thread-safe)
            with self._mid_prices_lock:
                for coin, price_str in mids.items():
                    try:
                        self._mid_prices[coin] = float(price_str)
                    except (ValueError, TypeError):
                        pass
                self._last_mid_tick = time.time()

            self._events_received += 1

            # Push to asyncio queue
            self._push_event(WSEvent(
                channel="allMids",
                data=self._mid_prices.copy(),
            ))
        except Exception as e:
            log.debug("[WS] allMids callback error: %s", str(e)[:100])

    def _on_l2_book(self, msg: dict) -> None:
        """l2Book tick — update cached orderbook."""
        try:
            data = msg.get("data", {})
            coin = data.get("coin", "")
            if not coin:
                return

            levels = data.get("levels", [[], []])
            book = {
                "bids": [
                    {"price": float(b["px"]), "qty": float(b["sz"])}
                    for b in levels[0][:10]
                ] if levels[0] else [],
                "asks": [
                    {"price": float(a["px"]), "qty": float(a["sz"])}
                    for a in levels[1][:10]
                ] if levels[1] else [],
                "time": data.get("time", 0),
            }

            with self._l2_lock:
                self._l2_books[coin] = book

            self._events_received += 1

            self._push_event(WSEvent(channel="l2Book", data={"coin": coin, **book}))
        except Exception as e:
            log.debug("[WS] l2Book callback error: %s", str(e)[:100])

    def _on_user_fills(self, msg: dict) -> None:
        """userFills tick — order filled notification."""
        try:
            data = msg.get("data", {})
            self._events_received += 1
            self._push_event(WSEvent(channel="userFills", data=data))
            log.info("[WS] Fill received: %s", str(data)[:200])
        except Exception as e:
            log.debug("[WS] userFills callback error: %s", str(e)[:100])

    def _on_order_updates(self, msg: dict) -> None:
        """orderUpdates tick — order status change."""
        try:
            data = msg.get("data", {})
            self._events_received += 1
            self._push_event(WSEvent(channel="orderUpdates", data=data))
            log.info("[WS] Order update: %s", str(data)[:200])
        except Exception as e:
            log.debug("[WS] orderUpdates callback error: %s", str(e)[:100])

    def _push_event(self, event: WSEvent) -> None:
        """Push event from WS thread into asyncio queue (thread-safe)."""
        if not self._loop or not self._running:
            return
        try:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, event)
        except asyncio.QueueFull:
            pass  # Drop oldest-style: queue is bounded, consumer should keep up
        except RuntimeError:
            pass  # Loop closed

    # ── Health Check / Reconnect ──

    async def health_check(self) -> bool:
        """Check WS health, reconnect if stale. Call periodically from asyncio."""
        if not self._running:
            return False

        # Check if we're getting ticks
        if self.last_tick_age > 30:
            log.warning("[WS] Stale: no tick for %.0fs — reconnecting", self.last_tick_age)
            self._reconnect()
            return False

        return self._connected

    def _reconnect(self) -> None:
        """Tear down and reconnect."""
        delay = self._cfg.ws_reconnect_delay
        elapsed = time.time() - self._last_connect_attempt
        if elapsed < delay:
            return  # Don't reconnect too fast

        log.info("[WS] Reconnecting (attempt #%d)...", self._reconnect_count + 1)
        self._connected = False

        # Tear down old connection
        if self._info:
            try:
                self._info.disconnect_websocket()
            except Exception:
                pass
            self._info = None

        self._reconnect_count += 1
        self._connect()

    # ── Status ──

    def get_status(self) -> dict:
        """Status dict for dashboard."""
        return {
            "connected": self._connected,
            "last_tick_age_s": round(self.last_tick_age, 1) if self._last_mid_tick > 0 else -1,
            "events_received": self._events_received,
            "reconnect_count": self._reconnect_count,
            "book_subscriptions": sorted(self._book_subs),
            "cached_coins": len(self._mid_prices),
            "uptime_s": round(time.time() - self._start_time, 0) if self._start_time else 0,
            "queue_size": self._queue.qsize(),
        }
