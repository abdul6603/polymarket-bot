"""Polymarket CLOB WebSocket feed — real-time orderbook + price data.

Hardened for NordVPN (Amsterdam) + Polymarket CDN environment where active
connections get killed after ~60-120 seconds regardless of traffic.

Strategy: PROACTIVE RECONNECT using threading.Timer (OS thread).
  1. A threading.Timer runs OUTSIDE the asyncio event loop
  2. After 48s it closes the raw TCP socket, forcing ws.recv() to fail
  3. This is immune to event loop blocking by signal analysis / LLM calls
  4. The asyncio event loop picks up the broken connection and reconnects

REST FALLBACK: If WS is down >45s, switches to REST polling (every 8s)
with a -20 confidence penalty on all scores sourced from fallback data.

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

# ── Reconnection tuning (aggressive with jitter) ─────────────

BACKOFF_STEPS = [2.0, 5.0, 12.0, 25.0, 60.0, 90.0]  # Explicit steps
BACKOFF_MAX_S = 90.0       # Cap at 90 seconds
JITTER_MAX_S = 2.0         # Random 0-2s added to each delay

# ── Keepalive ─────────────────────────────────────────────────

WS_PING_INTERVAL_S = 15    # Protocol ping every 15 seconds
WS_PING_TIMEOUT_S = 30     # Close if no pong within 30 seconds
WS_OPEN_TIMEOUT_S = 60     # Wait up to 60s for connection open (VPN latency)
APP_KEEPALIVE_S = 15.0     # Send JSON ping if silent for 15 seconds

# ── Stale-data detection ─────────────────────────────────────

STALE_DATA_TIMEOUT_S = 35  # 35s with zero messages → force reconnect
STALE_CHECK_INTERVAL_S = 5 # Check every 5 seconds

# ── REST fallback ─────────────────────────────────────────────

REST_FALLBACK_AFTER_S = 45       # Switch to REST after 45s WS downtime
REST_POLL_INTERVAL_S = 8         # Poll REST every 8 seconds
REST_CONFIDENCE_PENALTY = 20     # Subtract 20 points from scores during fallback
CLOB_REST_BASE = "https://clob.polymarket.com"

# ── Multiple endpoints for failover ──────────────────────────

CLOB_WS_ENDPOINTS = [
    "wss://ws-subscriptions-clob.polymarket.com/ws/market",
    "wss://ws-subscriptions-clob.polymarket.com/ws/market",  # Same host, retry
]

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
_rest_fallback_active: bool = False
_current_endpoint: str = ""
_ws_down_since: float = 0.0


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
        "rest_fallback": _rest_fallback_active,
        "endpoint": _current_endpoint,
        "ws_down_since": _ws_down_since,
    }


def get_confidence_penalty() -> int:
    """Return score penalty when WS is in REST fallback mode.
    Sniper/signals should subtract this from confidence scores."""
    return REST_CONFIDENCE_PENALTY if _rest_fallback_active else 0


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

    REST Fallback: If WS stays down >45s, a background thread polls REST API
    every 8s for orderbook/price data. All data sourced from REST gets a -20
    confidence penalty applied to scores.
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
        self._reconnect_attempts = 0
        self._disconnect_times: deque[float] = deque(maxlen=50)
        self._last_alert_at = 0.0
        self._got_data_this_session = False
        self._session_start = 0.0
        self._proactive_close = False
        self._lifetime_timer: threading.Timer | None = None

        # Endpoint rotation
        self._endpoint_index = 0
        self._endpoints = list(CLOB_WS_ENDPOINTS)
        if self.ws_url not in self._endpoints:
            self._endpoints.insert(0, self.ws_url)

        # REST fallback
        self._rest_fallback_thread: threading.Thread | None = None
        self._rest_fallback_running = False

        # Health monitor thread
        self._health_thread: threading.Thread | None = None

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
        self._start_health_monitor()

    async def stop(self) -> None:
        self._running = False
        self._stop_rest_fallback()
        self._cancel_lifetime_timer()
        self._stop_health_monitor()
        if self._ws and not _ws_is_closed(self._ws):
            await self._ws.close()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    # ── Health monitor thread ─────────────────────────────────

    def _start_health_monitor(self) -> None:
        """OS thread that monitors WS health and forces reconnect if stale."""
        if self._health_thread and self._health_thread.is_alive():
            return
        self._health_thread = threading.Thread(
            target=self._health_monitor_loop, daemon=True, name="clob-health",
        )
        self._health_thread.start()

    def _stop_health_monitor(self) -> None:
        self._health_thread = None

    def _health_monitor_loop(self) -> None:
        """Runs in OS thread — checks data freshness every 5s.
        If no message for 35s → force reconnect.
        If WS down >45s → start REST fallback.
        """
        global _rest_fallback_active, _ws_down_since

        while self._running:
            time.sleep(STALE_CHECK_INTERVAL_S)
            now = time.time()

            # Check data staleness
            if _last_message_at > 0:
                silence = now - _last_message_at
            elif _last_connected_at > 0:
                silence = now - _last_connected_at
            else:
                silence = 0

            # Force reconnect if stale (35s no data while supposedly connected)
            if silence >= STALE_DATA_TIMEOUT_S and _connection_status == "CONNECTED":
                log.warning(
                    "[CLOB-HEALTH] Stale data: %.0fs no messages — forcing reconnect",
                    silence,
                )
                self._proactive_close = True
                if self._ws:
                    try:
                        import socket as socket_mod
                        transport = self._ws.transport
                        if transport:
                            sock = transport.get_extra_info("socket")
                            if sock and isinstance(sock, socket_mod.socket):
                                try:
                                    sock.shutdown(socket_mod.SHUT_RDWR)
                                except OSError:
                                    pass
                                sock.close()
                    except Exception:
                        pass

            # Track WS downtime for REST fallback
            if _connection_status not in ("CONNECTED",):
                if _ws_down_since <= 0:
                    _ws_down_since = now
                down_duration = now - _ws_down_since

                # Start REST fallback if WS down >45s
                if down_duration >= REST_FALLBACK_AFTER_S and not _rest_fallback_active:
                    log.warning(
                        "[CLOB-HEALTH] WS down %.0fs > %ds — activating REST fallback (polling every %ds, -20 score penalty)",
                        down_duration, REST_FALLBACK_AFTER_S, REST_POLL_INTERVAL_S,
                    )
                    _rest_fallback_active = True
                    _write_clob_status()
                    self._start_rest_fallback()
            else:
                # WS is up — stop REST fallback
                if _rest_fallback_active:
                    log.info("[CLOB-HEALTH] WS recovered — stopping REST fallback")
                    _rest_fallback_active = False
                    self._stop_rest_fallback()
                    _write_clob_status()
                _ws_down_since = 0.0

    # ── REST fallback ─────────────────────────────────────────

    def _start_rest_fallback(self) -> None:
        """Start REST polling thread as fallback when WS is down."""
        if self._rest_fallback_running:
            return
        self._rest_fallback_running = True
        self._rest_fallback_thread = threading.Thread(
            target=self._rest_fallback_loop, daemon=True, name="clob-rest-fallback",
        )
        self._rest_fallback_thread.start()

    def _stop_rest_fallback(self) -> None:
        """Stop REST polling thread."""
        self._rest_fallback_running = False

    def _rest_fallback_loop(self) -> None:
        """Poll CLOB REST API every 8s for orderbook data."""
        global _last_message_at, _connection_status, _status_detail
        import httpx

        log.info("[CLOB-REST] REST fallback started — polling every %ds", REST_POLL_INTERVAL_S)
        while self._rest_fallback_running and self._running:
            for token_id in list(self._token_ids):
                if not self._rest_fallback_running:
                    break
                try:
                    resp = httpx.get(
                        f"{CLOB_REST_BASE}/book",
                        params={"token_id": token_id},
                        timeout=10,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        bids = data.get("bids", [])
                        asks = data.get("asks", [])
                        if bids or asks:
                            self._update_orderbook(token_id, bids, asks)
                            # Update price from best bid/ask midpoint
                            if bids and asks:
                                try:
                                    best_bid = float(bids[0].get("price", 0) if isinstance(bids[0], dict) else bids[0][0])
                                    best_ask = float(asks[0].get("price", 0) if isinstance(asks[0], dict) else asks[0][0])
                                    mid = (best_bid + best_ask) / 2
                                    if mid > 0:
                                        snap = PriceSnapshot(token_id=token_id, price=mid, timestamp=time.time())
                                        if token_id not in self._prices:
                                            self._prices[token_id] = deque(maxlen=100)
                                        self._prices[token_id].append(snap)
                                except (ValueError, TypeError, IndexError):
                                    pass
                            _last_message_at = time.time()
                            _connection_status = "REST_FALLBACK"
                            _status_detail = f"Polling REST every {REST_POLL_INTERVAL_S}s (-{REST_CONFIDENCE_PENALTY} penalty)"
                except Exception as e:
                    log.debug("[CLOB-REST] REST poll failed for %s: %s", token_id[:16], str(e)[:80])

            _write_clob_status()
            time.sleep(REST_POLL_INTERVAL_S)

        log.info("[CLOB-REST] REST fallback stopped")

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

    # ── Endpoint rotation ─────────────────────────────────────

    def _next_endpoint(self) -> str:
        """Rotate to next CLOB WS endpoint."""
        global _current_endpoint
        self._endpoint_index = (self._endpoint_index + 1) % len(self._endpoints)
        url = self._endpoints[self._endpoint_index]
        _current_endpoint = url
        return url

    def _get_backoff(self) -> float:
        """Get backoff delay from explicit step list with jitter."""
        idx = min(self._reconnect_attempts, len(BACKOFF_STEPS) - 1)
        base = BACKOFF_STEPS[idx]
        jitter = random.uniform(0, JITTER_MAX_S)
        return base + jitter

    # ── Core loop ────────────────────────────────────────────

    async def _run_loop(self) -> None:
        global _connection_status, _status_detail, _current_endpoint

        _current_endpoint = self.ws_url

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

                delay = self._get_backoff()

                now = time.time()
                silence = now - _last_message_at if _last_message_at > 0 else 0
                duration = now - self._session_start if self._session_start > 0 else 0

                # Rotate endpoint on repeated failures
                if self._reconnect_attempts >= 2 and self._reconnect_attempts % 2 == 0:
                    old_url = _current_endpoint
                    new_url = self._next_endpoint()
                    if old_url != new_url:
                        log.warning("[CLOB-WS] Rotating endpoint: %s → %s", old_url[:50], new_url[:50])

                log.warning(
                    "WebSocket disconnected: %s | "
                    "session=%.0fs silence=%.0fs | "
                    "reconnecting in %.1fs (attempt %d, backoff step %d/%d) | "
                    "endpoint=%s | Suspected VPN/CDN kill",
                    err_msg, duration, silence, delay,
                    self._reconnect_attempts,
                    min(self._reconnect_attempts, len(BACKOFF_STEPS)),
                    len(BACKOFF_STEPS),
                    _current_endpoint[:50],
                )

                _connection_status = "CONNECTING"
                _status_detail = f"Reconnecting in {delay:.0f}s (attempt {self._reconnect_attempts})"
                _write_clob_status()

                await asyncio.sleep(delay)

    async def _connect(self) -> None:
        global _connection_status, _status_detail, _last_connected_at, _last_message_at, _proactive_reconnect_count, _current_endpoint

        _connection_status = "CONNECTING"
        url = _current_endpoint or self.ws_url
        log.info("Connecting to CLOB WS: %s (open_timeout=%ds, ping=%ds/%ds)",
                 url[:60], WS_OPEN_TIMEOUT_S, WS_PING_INTERVAL_S, WS_PING_TIMEOUT_S)

        async with websockets.connect(
            url,
            ping_interval=WS_PING_INTERVAL_S,
            ping_timeout=WS_PING_TIMEOUT_S,
            close_timeout=5,
            open_timeout=WS_OPEN_TIMEOUT_S,
        ) as ws:
            self._ws = ws
            self._got_data_this_session = False
            self._session_start = time.time()
            _last_connected_at = self._session_start

            log.info("CLOB WebSocket connected (ping=%ds/%ds, open_timeout=%ds)",
                     WS_PING_INTERVAL_S, WS_PING_TIMEOUT_S, WS_OPEN_TIMEOUT_S)
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
            # Stale-data monitor: detect zombie connections
            stale_monitor_task = asyncio.create_task(self._stale_data_monitor(ws))

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
                        self._reconnect_attempts = 0

                    self._handle_message(raw)

            finally:
                self._cancel_lifetime_timer()
                keepalive_task.cancel()
                stale_monitor_task.cancel()
                try:
                    await keepalive_task
                except asyncio.CancelledError:
                    pass
                try:
                    await stale_monitor_task
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

    async def _stale_data_monitor(self, ws) -> None:
        """Detect zombie connections: WS appears connected but CDN sends no data.
        Checks every 5s, triggers at 35s silence."""
        try:
            while not _ws_is_closed(ws):
                await asyncio.sleep(STALE_CHECK_INTERVAL_S)
                if _last_message_at <= 0:
                    continue
                silence = time.time() - _last_message_at
                if silence >= STALE_DATA_TIMEOUT_S:
                    log.warning(
                        "CLOB WS zombie detected: connected but %.0fs without data (threshold=%ds) — forcing reconnect",
                        silence, STALE_DATA_TIMEOUT_S,
                    )
                    self._proactive_close = True
                    try:
                        await ws.close(1000, "stale_data")
                    except Exception:
                        pass
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
            f"*Garves CLOB WebSocket*\n{detail}\nBackoff step: {min(self._reconnect_attempts, len(BACKOFF_STEPS))}/{len(BACKOFF_STEPS)}"
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
            try:
                await self._ws.send(msg)
                log.debug("Subscribed to token %s", token_id[:16])
            except (ConnectionClosed, OSError) as e:
                log.warning("Subscribe send failed for %s: %s — closing for reconnect", token_id[:16], str(e)[:80])
                try:
                    await self._ws.close()
                except Exception:
                    pass
                return

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
