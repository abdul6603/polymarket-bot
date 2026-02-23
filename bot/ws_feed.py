"""Polymarket CLOB WebSocket feed — real-time orderbook + price data.

Hardened for NordVPN Qatar + Polymarket CDN environment where active
connections get killed after ~60-120 seconds regardless of traffic.

Strategy: PROACTIVE RECONNECT using threading.Timer (OS thread).
  1. A threading.Timer runs OUTSIDE the asyncio event loop
  2. After 48s it closes the raw TCP socket, forcing ws.recv() to fail
  3. This is immune to event loop blocking by signal analysis / LLM calls
  4. The asyncio event loop picks up the broken connection and reconnects

Why not asyncio.sleep/wait_for? Because Garves' signal analysis pipeline
blocks the event loop for 10-40 seconds (sync CoinGlass API calls, LLM
synthesis). During that time, no asyncio timers or coroutines can execute.
Only a real OS thread can guarantee the timer fires on time.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import threading
import time
from collections import deque
from dataclasses import dataclass

import websockets
from websockets.exceptions import ConnectionClosed

from bot.config import Config

log = logging.getLogger(__name__)

# ── Proactive reconnect ─────────────────────────────────────
# CDN kills connections at ~60-120s. threading.Timer closes at 48s.

LIFETIME_TARGET_S = 48     # threading.Timer fires at 48 seconds
LIFETIME_HARD_CAP_S = 58   # Asyncio fallback if timer somehow fails

# ── Reconnection tuning ─────────────────────────────────────

BACKOFF_BASE_S = 1.0       # First retry after ~1 second
BACKOFF_MULTIPLIER = 1.6   # Each retry: delay *= 1.6
BACKOFF_MAX_S = 60.0       # Cap at 60 seconds
JITTER_MAX_S = 1.0         # Random 0-1s added to each delay

# ── Keepalive (safe levels — aggressive pings trigger CDN rate limits)

WS_PING_INTERVAL_S = 15    # Protocol ping every 15 seconds
WS_PING_TIMEOUT_S = 8      # Close if no pong within 8 seconds
APP_KEEPALIVE_S = 15.0      # Send JSON ping if silent for 15 seconds

# ── Stale-data detection ─────────────────────────────────────

STALE_DATA_TIMEOUT_S = 25 * 60  # 25 min with zero messages → force reconnect

# ── Alerting ─────────────────────────────────────────────────

ALERT_COOLDOWN_S = 15 * 60       # Max 1 Telegram alert per 15 minutes
DEGRADED_WINDOW_S = 15 * 60      # Check disconnects within this window
DEGRADED_THRESHOLD = 5            # 5+ UNEXPECTED disconnects in window → DEGRADED

# ── Connection status (module-level for dashboard access) ────

_connection_status: str = "DISCONNECTED"
_status_detail: str = ""
_reconnect_count_today: int = 0
_proactive_reconnect_count: int = 0
_last_connected_at: float = 0.0
_last_message_at: float = 0.0


CLOB_STATUS_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "clob_status.json")


def get_clob_status() -> dict:
    """Dashboard-friendly connection status. Called by route handlers."""
    now = time.time()
    return {
        "status": _connection_status,
        "detail": _status_detail,
        "reconnects_today": _reconnect_count_today,
        "proactive_reconnects": _proactive_reconnect_count,
        "last_connected": _last_connected_at,
        "last_message": _last_message_at,
        "silence_s": round(now - _last_message_at, 1) if _last_message_at > 0 else 0,
        "uptime_s": round(now - _last_connected_at, 1) if _last_connected_at > 0 and _connection_status == "CONNECTED" else 0,
    }


def _write_clob_status() -> None:
    """Persist CLOB status to file for dashboard (separate process)."""
    try:
        data = get_clob_status()
        data["written_at"] = time.time()
        with open(CLOB_STATUS_FILE, "w") as f:
            json.dump(data, f)
    except Exception:
        pass


def _ws_is_closed(ws) -> bool:
    """Check if a websocket is closed, compatible with websockets 12-14."""
    if hasattr(ws, "closed"):
        return ws.closed
    try:
        return ws.close_code is not None
    except Exception:
        return True


# ── Telegram alerting ────────────────────────────────────────

def _send_telegram_alert(text: str) -> None:
    """Send alert via Telegram. Best-effort, never raises."""
    try:
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        if not token or not chat_id:
            return
        import httpx
        httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
    except Exception:
        pass


# ── Data classes ─────────────────────────────────────────────

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


# ── MarketFeed ───────────────────────────────────────────────

class MarketFeed:
    """Async WebSocket feed for real-time Polymarket orderbook data.

    Core strategy: threading.Timer proactive reconnect.

    The asyncio event loop gets blocked by Garves' signal analysis pipeline
    (sync CoinGlass API calls, LLM synthesis) for 10-40 seconds. During this
    time, no asyncio timers or coroutines can execute.

    Solution: a threading.Timer runs in a separate OS thread. After 48 seconds
    it closes the raw TCP transport, forcing ws.recv() to raise ConnectionClosed.
    The asyncio code picks up the broken connection and reconnects immediately.

    This is guaranteed to fire within ~1ms of the 48s deadline, regardless of
    what the asyncio event loop is doing.
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.ws_url = cfg.ws_url
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._token_ids: list[str] = []
        self._prices: dict[str, deque[PriceSnapshot]] = {}
        self._running = False
        self._task: asyncio.Task | None = None
        self._orderbooks: dict[str, OrderbookSnapshot] = {}

        # Reconnection state
        self._backoff_s = BACKOFF_BASE_S
        self._reconnect_attempts = 0
        self._disconnect_times: deque[float] = deque(maxlen=50)
        self._last_alert_at = 0.0
        self._got_data_this_session = False
        self._session_start = 0.0
        self._proactive_close = False
        self._lifetime_timer: threading.Timer | None = None

    # ── Public properties (unchanged API) ────────────────────

    @property
    def latest_orderbook(self) -> dict[str, OrderbookSnapshot]:
        return dict(self._orderbooks)

    @property
    def latest_price(self) -> dict[str, float | None]:
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

    # ── Lifecycle ────────────────────────────────────────────

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._running = False
        self._cancel_lifetime_timer()
        if self._ws and not _ws_is_closed(self._ws):
            await self._ws.close()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    # ── Threading.Timer lifecycle ────────────────────────────

    def _start_lifetime_timer(self, ws) -> None:
        """Start a real OS thread timer that closes the TCP socket after 48s.

        This runs OUTSIDE the asyncio event loop, so it's immune to
        event loop blocking by signal analysis / LLM calls.
        """
        self._cancel_lifetime_timer()

        def _force_close():
            """Called from timer thread after LIFETIME_TARGET_S seconds.

            Closes the raw socket file descriptor — works from any thread,
            immune to asyncio event loop blocking. This causes ws.recv()
            to fail immediately with an OS-level error.
            """
            import socket as socket_mod
            global _proactive_reconnect_count
            try:
                duration = time.time() - self._session_start
                _proactive_reconnect_count += 1
                self._proactive_close = True
                log.warning(
                    "Proactive reconnect triggered after %.0fs (total: %d) [thread]",
                    duration, _proactive_reconnect_count,
                )
                # Get the raw socket and close it at OS level.
                # This is thread-safe and doesn't need the event loop.
                transport = ws.transport
                if transport:
                    sock = transport.get_extra_info("socket")
                    if sock and isinstance(sock, socket_mod.socket):
                        try:
                            sock.shutdown(socket_mod.SHUT_RDWR)
                        except OSError:
                            pass
                        sock.close()
                        log.warning("Proactive: raw socket closed")
                    else:
                        # Fallback: try to get the fd and close it
                        try:
                            fd = transport.get_extra_info("socket")
                            if fd:
                                fd.close()
                        except Exception:
                            pass
                        log.warning("Proactive: fallback close attempted")
                else:
                    log.warning("Proactive: no transport available")
            except Exception as e:
                self._proactive_close = True
                log.warning("Proactive timer error: %s", str(e)[:150])
            _write_clob_status()

        self._lifetime_timer = threading.Timer(LIFETIME_TARGET_S, _force_close)
        self._lifetime_timer.daemon = True
        self._lifetime_timer.start()
        log.info(
            "Lifetime timer started (target %ds, hard cap %ds) [thread]",
            LIFETIME_TARGET_S, LIFETIME_HARD_CAP_S,
        )

    def _cancel_lifetime_timer(self) -> None:
        """Cancel the lifetime timer if running."""
        if self._lifetime_timer is not None:
            self._lifetime_timer.cancel()
            self._lifetime_timer = None

    # ── Core loop ────────────────────────────────────────────

    async def _run_loop(self) -> None:
        global _connection_status, _status_detail

        while self._running:
            try:
                await self._connect()

                # Proactive close — reconnect immediately, no backoff
                if self._proactive_close:
                    self._proactive_close = False
                    continue

            except asyncio.CancelledError:
                return
            except Exception as e:
                err_msg = str(e)[:120]

                # Proactive close that raised an exception — still skip backoff
                if self._proactive_close:
                    self._proactive_close = False
                    log.debug("Proactive reconnect (exception path): %s", err_msg)
                    continue

                # Unexpected disconnect — apply backoff
                self._on_disconnect(err_msg)

                jitter = random.uniform(0, JITTER_MAX_S)
                delay = self._backoff_s + jitter

                now = time.time()
                silence = now - _last_message_at if _last_message_at > 0 else 0
                duration = now - self._session_start if self._session_start > 0 else 0

                log.warning(
                    "WebSocket disconnected: %s | "
                    "session=%.0fs silence=%.0fs | "
                    "reconnecting in %.1fs (attempt %d) | "
                    "Suspected VPN/CDN kill",
                    err_msg, duration, silence, delay, self._reconnect_attempts,
                )

                _connection_status = "CONNECTING"
                _status_detail = f"Reconnecting in {delay:.0f}s (attempt {self._reconnect_attempts})"

                await asyncio.sleep(delay)
                self._backoff_s = min(self._backoff_s * BACKOFF_MULTIPLIER, BACKOFF_MAX_S)

    async def _connect(self) -> None:
        global _connection_status, _status_detail, _last_connected_at, _last_message_at, _proactive_reconnect_count

        _connection_status = "CONNECTING"
        log.info("Connecting to WS: %s", self.ws_url)

        async with websockets.connect(
            self.ws_url,
            ping_interval=WS_PING_INTERVAL_S,
            ping_timeout=WS_PING_TIMEOUT_S,
            close_timeout=5,
            open_timeout=10,
        ) as ws:
            self._ws = ws
            self._got_data_this_session = False
            self._session_start = time.time()
            _last_connected_at = self._session_start

            log.info("WebSocket connected (ping=%ds/%ds)", WS_PING_INTERVAL_S, WS_PING_TIMEOUT_S)
            _connection_status = "CONNECTED"
            _status_detail = ""
            _write_clob_status()

            if self._token_ids:
                await self._send_subscribe()

            # ── Start lifetime timer (OS thread — immune to event loop blocking) ──
            self._start_lifetime_timer(ws)

            # Deadline for asyncio fallback (in case timer thread somehow fails)
            hard_deadline = self._session_start + LIFETIME_HARD_CAP_S

            # Light keepalive in background
            keepalive_task = asyncio.create_task(self._app_keepalive_loop(ws))

            try:
                async for raw in ws:
                    _last_message_at = time.time()

                    # ── Asyncio fallback: hard cap check on each message ──
                    if _last_message_at >= hard_deadline:
                        _proactive_reconnect_count += 1
                        self._proactive_close = True
                        duration = _last_message_at - self._session_start
                        log.info(
                            "Hard cap reached (%.0fs > %ds) — force closing (total: %d)",
                            duration, LIFETIME_HARD_CAP_S, _proactive_reconnect_count,
                        )
                        await ws.close(1000, "hard_cap")
                        return

                    if not self._got_data_this_session:
                        self._got_data_this_session = True
                        self._backoff_s = BACKOFF_BASE_S
                        self._reconnect_attempts = 0

                    self._handle_message(raw)

            finally:
                self._cancel_lifetime_timer()
                keepalive_task.cancel()
                try:
                    await keepalive_task
                except asyncio.CancelledError:
                    pass

    # ── Light keepalive ──────────────────────────────────────

    async def _app_keepalive_loop(self, ws) -> None:
        """Light application-level keepalive — JSON ping every 15s if silent."""
        try:
            while not _ws_is_closed(ws):
                await asyncio.sleep(APP_KEEPALIVE_S)
                _write_clob_status()
                silence = time.time() - _last_message_at if _last_message_at > 0 else APP_KEEPALIVE_S + 1
                if silence >= APP_KEEPALIVE_S:
                    try:
                        await ws.send(json.dumps({"type": "ping"}))
                    except ConnectionClosed:
                        return
        except asyncio.CancelledError:
            pass

    # ── Disconnect handling + alerting ───────────────────────

    def _on_disconnect(self, reason: str) -> None:
        """Handle UNEXPECTED disconnects only. Proactive closes skip this."""
        global _connection_status, _status_detail, _reconnect_count_today

        now = time.time()
        self._reconnect_attempts += 1
        _reconnect_count_today += 1
        self._disconnect_times.append(now)

        cutoff = now - DEGRADED_WINDOW_S
        recent_disconnects = sum(1 for t in self._disconnect_times if t > cutoff)

        if recent_disconnects >= DEGRADED_THRESHOLD:
            _connection_status = "DEGRADED"
            _status_detail = f"{recent_disconnects} unexpected drops in 15min"
            log.error(
                "DEGRADED: %d unexpected disconnects in 15 min (total today: %d)",
                recent_disconnects, _reconnect_count_today,
            )
            self._maybe_send_alert(
                f"DEGRADED: {recent_disconnects} unexpected disconnects in 15 min\n"
                f"Reason: {reason}\nTotal today: {_reconnect_count_today}",
            )
        else:
            _connection_status = "CONNECTING"
            _status_detail = reason
        _write_clob_status()

    def _maybe_send_alert(self, detail: str) -> None:
        """Send Telegram alert with 15-minute cooldown to avoid spam."""
        now = time.time()
        if now - self._last_alert_at < ALERT_COOLDOWN_S:
            return
        self._last_alert_at = now
        _send_telegram_alert(
            f"*Garves CLOB WebSocket*\n{detail}\nBackoff: {self._backoff_s:.1f}s"
        )

    # ── Subscribe ────────────────────────────────────────────

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

    # ── Message handling (UNTOUCHED — same logic as before) ──

    def _handle_message(self, raw: str) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return

        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    self._process_event(item)
            return

        if isinstance(data, dict):
            self._process_event(data)

    def _process_event(self, data: dict) -> None:
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

        if event_type == "book":
            token_id = data.get("asset_id") or data.get("market")
            bids = data.get("bids", [])
            asks = data.get("asks", [])
            if token_id and (bids or asks):
                self._update_orderbook(token_id, bids, asks)

    def _update_orderbook(self, token_id: str, bids: list, asks: list) -> None:
        try:
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

            try:
                from bot.poly_flow import get_flow_tracker
                get_flow_tracker().record_snapshot(
                    token_id=token_id,
                    buy_pressure=buy_pressure,
                    sell_pressure=sell_pressure,
                    best_bid=best_bid,
                    best_ask=best_ask,
                    spread=spread,
                )
            except Exception:
                pass
        except (ValueError, TypeError, IndexError):
            pass
