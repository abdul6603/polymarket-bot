"""Real-time CLOB orderbook via Polymarket WebSocket.

Replaces REST polling (50-200ms per call) with a persistent WebSocket
connection that receives live orderbook updates. Provides instant
get_book() lookups (~0ms dict read) instead of HTTP round-trips.

Output format matches clob_book.get_orderbook() for drop-in replacement:
    {"best_bid", "best_ask", "buy_pressure", "sell_pressure", "spread"}

Usage:
    ws = ClobWS()
    ws.start()
    ws.subscribe("token_id")
    book = ws.get_book("token_id")  # instant dict lookup
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time

import websockets

log = logging.getLogger("killshot.clob_ws")

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


class ClobWS:
    """WebSocket client for real-time CLOB orderbook data.

    Runs in a daemon thread with its own event loop. Auto-reconnects
    on disconnect. Thread-safe — get_book() can be called from any thread.
    """

    def __init__(self):
        self._books: dict[str, dict] = {}        # token_id -> parsed book
        self._book_ts: dict[str, float] = {}     # token_id -> last update time
        self._subscribed: set[str] = set()        # currently subscribed
        self._pending_subs: set[str] = set()      # subscribe on (re)connect
        self._running = False
        self._connected = False
        self._ws = None
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._update_count = 0
        self._last_msg_at = 0.0

    # ── Public API ────────────────────────────────────────────

    def get_book(self, token_id: str) -> dict | None:
        """Get cached orderbook for a token. Returns None if no data.

        Returns dict with: best_bid, best_ask, buy_pressure, sell_pressure, spread.
        Same format as clob_book.get_orderbook() for drop-in replacement.
        """
        return self._books.get(token_id)

    def get_book_age(self, token_id: str) -> float:
        """Seconds since last book update. inf if never updated."""
        ts = self._book_ts.get(token_id)
        if ts is None:
            return float("inf")
        return time.time() - ts

    def subscribe(self, token_id: str) -> None:
        """Subscribe to orderbook updates for a token."""
        if token_id in self._subscribed:
            return
        self._pending_subs.add(token_id)
        if self._connected and self._loop:
            # Must resend ALL tokens — WS replaces subscription on each message
            all_desired = self._subscribed | self._pending_subs
            asyncio.run_coroutine_threadsafe(
                self._send_subscribe(all_desired), self._loop,
            )

    def update_subscriptions(self, token_ids: set[str]) -> None:
        """Update subscription set. Sends ALL desired tokens in one batch.

        Polymarket WS uses replacement semantics — each 'type: market' message
        replaces the entire subscription. Must send all tokens in one message.
        """
        new_tokens = token_ids - self._subscribed
        if not new_tokens:
            return
        self._pending_subs.update(new_tokens)
        all_desired = self._subscribed | self._pending_subs
        if self._connected and self._loop:
            asyncio.run_coroutine_threadsafe(
                self._send_subscribe(all_desired), self._loop,
            )

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def update_count(self) -> int:
        return self._update_count

    # ── Lifecycle ─────────────────────────────────────────────

    def start(self) -> None:
        """Start the WebSocket feed in a daemon thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="killshot-clob-ws",
        )
        self._thread.start()
        log.info("[CLOB-WS] Started orderbook feed thread")

    def stop(self) -> None:
        self._running = False

    # ── Internal ──────────────────────────────────────────────

    def _run_loop(self) -> None:
        """Run the async WebSocket in its own event loop."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        while self._running:
            try:
                self._loop.run_until_complete(self._connect())
            except Exception as e:
                log.warning("[CLOB-WS] Connection error: %s", str(e)[:120])
            self._connected = False
            if self._running:
                time.sleep(2)

    async def _connect(self) -> None:
        """Connect to CLOB WS and stream orderbook updates."""
        try:
            async with websockets.connect(
                WS_URL,
                ping_interval=20,
                ping_timeout=30,
                close_timeout=5,
                open_timeout=30,
            ) as ws:
                self._ws = ws
                self._connected = True
                log.info("[CLOB-WS] Connected to %s", WS_URL[:60])

                # Re-subscribe all known tokens on (re)connect
                all_tokens = self._subscribed | self._pending_subs
                if all_tokens:
                    await self._send_subscribe(all_tokens)

                while self._running:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=30)
                        self._last_msg_at = time.time()
                        self._handle_message(raw)
                    except asyncio.TimeoutError:
                        await ws.ping()
                    except websockets.ConnectionClosed:
                        log.warning("[CLOB-WS] Connection closed — reconnecting")
                        break

        except Exception as e:
            log.warning("[CLOB-WS] Error: %s", str(e)[:120])
        finally:
            self._connected = False

    async def _send_subscribe(self, token_ids) -> None:
        """Send ONE subscribe message with ALL token IDs.

        Polymarket WS replaces subscriptions on each 'type: market' message,
        so we must send all desired tokens in a single assets_ids array.
        """
        if not self._ws or not self._connected:
            return
        tokens = list(token_ids)
        if not tokens:
            return
        try:
            msg = json.dumps({
                "type": "market",
                "assets_ids": tokens,
            })
            await self._ws.send(msg)
            self._subscribed.update(tokens)
            self._pending_subs -= set(tokens)
            log.info("[CLOB-WS] Subscribed to %d tokens (batch)", len(tokens))
        except Exception as e:
            log.warning("[CLOB-WS] Subscribe error: %s", str(e)[:80])

    # ── Message parsing ───────────────────────────────────────

    def _handle_message(self, raw: str) -> None:
        """Process incoming WS message."""
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
        """Process a single WS event."""
        event_type = data.get("event_type") or data.get("type", "")

        if event_type == "book":
            token_id = data.get("asset_id") or data.get("market")
            bids = data.get("bids", [])
            asks = data.get("asks", [])
            if token_id and (bids or asks):
                self._update_book(token_id, bids, asks)

    def _update_book(self, token_id: str, bids: list, asks: list) -> None:
        """Parse and cache orderbook snapshot."""
        try:
            def _parse_level(lvl):
                if isinstance(lvl, dict):
                    return float(lvl.get("price", 0)), float(lvl.get("size", 0))
                elif isinstance(lvl, (list, tuple)) and len(lvl) >= 2:
                    return float(lvl[0]), float(lvl[1])
                return 0.0, 0.0

            parsed_bids = [_parse_level(b) for b in bids]
            parsed_asks = [_parse_level(a) for a in asks]

            # Sort: bids descending (best first), asks ascending (best first)
            parsed_bids.sort(key=lambda x: x[0], reverse=True)
            parsed_asks.sort(key=lambda x: x[0])

            # Filter zero prices
            parsed_bids = [(p, s) for p, s in parsed_bids if p > 0]
            parsed_asks = [(p, s) for p, s in parsed_asks if p > 0]

            best_bid = parsed_bids[0][0] if parsed_bids else 0.0
            best_ask = parsed_asks[0][0] if parsed_asks else 0.0
            buy_pressure = sum(p * s for p, s in parsed_bids[:5])
            sell_pressure = sum(p * s for p, s in parsed_asks[:5])
            spread = best_ask - best_bid if best_bid > 0 and best_ask > 0 else 0.0

            self._books[token_id] = {
                "best_bid": best_bid,
                "best_ask": best_ask,
                "buy_pressure": buy_pressure,
                "sell_pressure": sell_pressure,
                "spread": spread,
            }
            self._book_ts[token_id] = time.time()
            self._update_count += 1

            if self._update_count <= 3 or self._update_count % 500 == 0:
                log.info(
                    "[CLOB-WS] Book #%d: %s... bid=%.3f ask=%.3f spread=%.4f",
                    self._update_count, token_id[:16],
                    best_bid, best_ask, spread,
                )

        except (ValueError, TypeError, IndexError):
            pass
