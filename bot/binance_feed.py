"""Binance WebSocket feed — real-time trade + orderbook depth for BTC, ETH, SOL, XRP.

Hardened for VPN (Amsterdam) environment with aggressive reconnection,
multiple fallback endpoints, health monitoring, and REST fallback.

Uses global Binance WS (stream.binance.com) — .us WS blocks non-US IPs.
REST API stays on api.binance.us (works from any IP).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
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

# ── Reconnection tuning (aggressive with jitter) ─────────────
BACKOFF_STEPS = [2.0, 5.0, 12.0, 25.0, 60.0, 90.0]  # Explicit steps
BACKOFF_MAX_S = 90.0
JITTER_MAX_S = 2.0

# ── WebSocket parameters ─────────────────────────────────────
WS_OPEN_TIMEOUT_S = 60     # Wait up to 60s for connection (VPN latency)
WS_PING_INTERVAL_S = 15    # Protocol ping every 15 seconds
WS_PING_TIMEOUT_S = 30     # Close if no pong within 30 seconds
STALE_DATA_TIMEOUT_S = 35  # 35s no data → force reconnect
STALE_CHECK_INTERVAL_S = 5 # Health check frequency

# ── REST fallback ─────────────────────────────────────────────
REST_FALLBACK_AFTER_S = 45       # Switch to REST after 45s WS downtime
REST_POLL_INTERVAL_S = 8         # Poll REST every 8 seconds
REST_CONFIDENCE_PENALTY = 20     # Subtract 20 from scores during fallback
BINANCE_REST_BASE = "https://api.binance.us/api/v3"

# ── Fallback WS endpoints (multiple regions) ─────────────────
WS_ENDPOINTS = [
    "wss://stream.binance.com:9443",   # Primary global
    "wss://stream.binance.com:443",    # Alternate port
    "wss://stream.binance.com:9443",   # Retry primary
]

# ── Health status (module-level for dashboard access) ────────
_binance_status: str = "DISCONNECTED"
_binance_last_msg_at: float = 0.0
_binance_reconnect_count: int = 0
_binance_stream_url: str = ""
_binance_rest_fallback: bool = False
_binance_ws_down_since: float = 0.0

BINANCE_STATUS_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "binance_status.json")


def get_binance_status() -> dict:
    """Dashboard-friendly connection status."""
    now = time.time()
    return {
        "status": _binance_status,
        "last_message": _binance_last_msg_at,
        "silence_s": round(now - _binance_last_msg_at, 1) if _binance_last_msg_at > 0 else 0,
        "reconnect_count": _binance_reconnect_count,
        "stream_url": _binance_stream_url,
        "rest_fallback": _binance_rest_fallback,
        "ws_down_since": _binance_ws_down_since,
    }


def get_binance_confidence_penalty() -> int:
    """Return score penalty when Binance WS is in REST fallback mode."""
    return REST_CONFIDENCE_PENALTY if _binance_rest_fallback else 0


def _write_binance_status() -> None:
    """Persist Binance status to file for dashboard (separate process)."""
    try:
        data = get_binance_status()
        data["written_at"] = time.time()
        with open(BINANCE_STATUS_FILE, "w") as f:
            json.dump(data, f)
    except Exception:
        pass


class BinanceFeed:
    """Real-time trade + order book feed from Binance public WebSocket.

    Hardened with:
    - 60s open timeout for VPN latency
    - 15s ping / 30s pong timeout
    - 35s stale data → force reconnect
    - Exponential backoff: 2s, 5s, 12s, 25s, 60s, max 90s
    - Multiple fallback endpoints with auto-rotation
    - Health monitor thread (independent of asyncio event loop)
    - REST fallback after 45s WS downtime (-20 score penalty)
    """

    def __init__(self, cfg: Config, price_cache: PriceCache):
        self.cfg = cfg
        self._cache = price_cache
        self._ws = None
        self._running = False
        self._task: asyncio.Task | None = None

        # Endpoint management
        self._endpoints = list(WS_ENDPOINTS)
        self._endpoint_index = 0
        self._base_url = self._endpoints[0]
        self._consecutive_failures = 0
        self._reconnect_attempts = 0

        # Order book depth: asset -> {"bids": [[price, qty], ...], "asks": [...], "timestamp": float}
        self.depth: dict[str, dict[str, Any]] = {}

        # Reconnection state
        self._backoff_s = BACKOFF_STEPS[0]
        self._pool_recreate_count = 0
        self._last_status_write = 0.0
        self._last_msg_ts = 0.0
        self._msg_count = 0

        # REST fallback
        self._rest_fallback_running = False
        self._rest_fallback_thread: threading.Thread | None = None

        # Health monitor
        self._health_thread: threading.Thread | None = None

    def _build_url(self) -> str:
        """Build WS URL from current base."""
        return f"{self._base_url}/stream?streams={STREAMS}"

    def _get_backoff(self) -> float:
        """Get backoff delay from explicit step list with jitter."""
        idx = min(self._reconnect_attempts, len(BACKOFF_STEPS) - 1)
        base = BACKOFF_STEPS[idx]
        jitter = random.uniform(0, JITTER_MAX_S)
        return base + jitter

    def _next_endpoint(self) -> str:
        """Rotate to next Binance WS endpoint."""
        global _binance_stream_url
        self._endpoint_index = (self._endpoint_index + 1) % len(self._endpoints)
        self._base_url = self._endpoints[self._endpoint_index]
        _binance_stream_url = self._base_url
        return self._base_url

    async def start(self) -> None:
        """Start WS in a dedicated thread with its own event loop."""
        self._running = True
        self._thread = threading.Thread(
            target=self._thread_entry, daemon=True, name="binance-ws",
        )
        self._thread.start()
        self._start_health_monitor()

    def _thread_entry(self) -> None:
        """Thread entry — self-healing: restarts _run_loop on crash."""
        global _binance_status
        while self._running:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            try:
                self._loop.run_until_complete(self._run_loop())
            except Exception:
                log.exception("Binance WS thread crashed — restarting with backoff")
            finally:
                self._loop.close()

            if not self._running:
                break

            delay = self._get_backoff()
            _binance_status = "RECONNECTING"
            _write_binance_status()
            log.warning("Binance WS thread sleeping %.1fs before restart (attempt %d)",
                        delay, self._reconnect_attempts)
            time.sleep(delay)

        log.info("Binance WS thread exited (running=False)")

    # ── Health monitor (OS thread) ────────────────────────────

    def _start_health_monitor(self) -> None:
        """OS thread that monitors Binance WS health independently."""
        if self._health_thread and self._health_thread.is_alive():
            return
        self._health_thread = threading.Thread(
            target=self._health_monitor_loop, daemon=True, name="binance-health",
        )
        self._health_thread.start()

    def _health_monitor_loop(self) -> None:
        """Runs in OS thread — monitors data freshness, manages REST fallback."""
        global _binance_rest_fallback, _binance_ws_down_since, _binance_status

        while self._running:
            time.sleep(STALE_CHECK_INTERVAL_S)
            now = time.time()

            # Check data staleness
            if self._last_msg_ts > 0:
                silence = now - self._last_msg_ts
            else:
                silence = 0

            # Force WS reconnect if connected but stale (35s)
            if silence >= STALE_DATA_TIMEOUT_S and _binance_status == "CONNECTED":
                log.warning(
                    "[BINANCE-HEALTH] Stale data: %.0fs no messages — forcing reconnect",
                    silence,
                )
                if self._ws:
                    try:
                        asyncio.run_coroutine_threadsafe(
                            self._ws.close(), self._loop
                        )
                    except Exception:
                        pass

            # Track WS downtime for REST fallback
            if _binance_status not in ("CONNECTED",):
                if _binance_ws_down_since <= 0:
                    _binance_ws_down_since = now
                down_duration = now - _binance_ws_down_since

                if down_duration >= REST_FALLBACK_AFTER_S and not _binance_rest_fallback:
                    log.warning(
                        "[BINANCE-HEALTH] WS down %.0fs > %ds — activating REST fallback (polling every %ds, -%d score penalty)",
                        down_duration, REST_FALLBACK_AFTER_S, REST_POLL_INTERVAL_S, REST_CONFIDENCE_PENALTY,
                    )
                    _binance_rest_fallback = True
                    _binance_status = "REST_FALLBACK"
                    _write_binance_status()
                    self._start_rest_fallback()
            else:
                if _binance_rest_fallback:
                    log.info("[BINANCE-HEALTH] WS recovered — stopping REST fallback")
                    _binance_rest_fallback = False
                    self._stop_rest_fallback()
                    _write_binance_status()
                _binance_ws_down_since = 0.0

    # ── REST fallback ─────────────────────────────────────────

    def _start_rest_fallback(self) -> None:
        """Start REST polling thread as fallback when WS is down."""
        if self._rest_fallback_running:
            return
        self._rest_fallback_running = True
        self._rest_fallback_thread = threading.Thread(
            target=self._rest_fallback_loop, daemon=True, name="binance-rest-fallback",
        )
        self._rest_fallback_thread.start()

    def _stop_rest_fallback(self) -> None:
        self._rest_fallback_running = False

    def _rest_fallback_loop(self) -> None:
        """Poll Binance.US REST API every 8s for price + depth data."""
        global _binance_last_msg_at, _binance_status
        import httpx

        symbols = list(SYMBOL_MAP.keys())
        log.info("[BINANCE-REST] REST fallback started — polling every %ds for %d symbols",
                 REST_POLL_INTERVAL_S, len(symbols))

        while self._rest_fallback_running and self._running:
            for symbol in symbols:
                if not self._rest_fallback_running:
                    break
                asset = SYMBOL_MAP[symbol]
                upper_sym = symbol.upper()

                # Fetch ticker price
                try:
                    resp = httpx.get(
                        f"{BINANCE_REST_BASE}/ticker/price",
                        params={"symbol": upper_sym},
                        timeout=10,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        price = float(data.get("price", 0))
                        if price > 0:
                            self._cache.update_tick(asset, price, 0.0, time.time())
                            _binance_last_msg_at = time.time()
                except Exception as e:
                    log.debug("[BINANCE-REST] Price poll failed for %s: %s", symbol, str(e)[:80])

                # Fetch depth (top 5)
                try:
                    resp = httpx.get(
                        f"{BINANCE_REST_BASE}/depth",
                        params={"symbol": upper_sym, "limit": 5},
                        timeout=10,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        self.depth[asset] = {
                            "bids": data.get("bids", []),
                            "asks": data.get("asks", []),
                            "timestamp": time.time(),
                        }
                except Exception as e:
                    log.debug("[BINANCE-REST] Depth poll failed for %s: %s", symbol, str(e)[:80])

            _binance_status = "REST_FALLBACK"
            _write_binance_status()
            time.sleep(REST_POLL_INTERVAL_S)

        log.info("[BINANCE-REST] REST fallback stopped")

    # ── Thread lifecycle ──────────────────────────────────────

    def ensure_alive(self) -> None:
        """Restart WS thread if it died. Call from main tick loop."""
        if not self._running:
            return
        if not hasattr(self, '_thread') or not self._thread.is_alive():
            log.warning("Binance WS thread dead, restarting")
            self._thread = threading.Thread(
                target=self._thread_entry, daemon=True, name="binance-ws",
            )
            self._thread.start()
        # Also ensure health monitor is alive
        if not self._health_thread or not self._health_thread.is_alive():
            self._start_health_monitor()

    async def stop(self) -> None:
        self._running = False
        self._stop_rest_fallback()
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass

    async def _run_loop(self) -> None:
        global _binance_status, _binance_reconnect_count, _binance_stream_url

        while self._running:
            try:
                await self._connect()
                # Successful session — reset
                self._reconnect_attempts = 0
                self._consecutive_failures = 0
            except asyncio.CancelledError:
                return
            except Exception:
                self._consecutive_failures += 1
                self._reconnect_attempts += 1
                _binance_reconnect_count += 1

                # Rotate endpoint after 2 consecutive failures
                if self._consecutive_failures >= 2:
                    old_url = self._base_url
                    new_url = self._next_endpoint()
                    self._consecutive_failures = 0
                    if old_url != new_url:
                        log.warning(
                            "[BINANCE-WS] Rotating endpoint: %s → %s (after failures)",
                            old_url[:40], new_url[:40],
                        )

                _binance_status = "CONNECTING"
                _binance_stream_url = self._base_url
                _write_binance_status()

                delay = self._get_backoff()
                log.warning(
                    "Binance WS error, reconnecting in %.1fs (attempt %d, step %d/%d, endpoint=%s)",
                    delay, self._reconnect_attempts,
                    min(self._reconnect_attempts, len(BACKOFF_STEPS)),
                    len(BACKOFF_STEPS),
                    self._base_url[:40],
                )
                await asyncio.sleep(delay)

    async def _watchdog(self, ws) -> None:
        """Independent watchdog — closes WS if no data for 35s."""
        while True:
            await asyncio.sleep(STALE_CHECK_INTERVAL_S)
            if self._last_msg_ts <= 0:
                continue
            age = time.time() - self._last_msg_ts
            if age > STALE_DATA_TIMEOUT_S:
                log.warning("[BINANCE-STALE] No data for %.0fs (threshold=%ds), forcing reconnect",
                            age, STALE_DATA_TIMEOUT_S)
                await ws.close()
                return

    async def _connect(self) -> None:
        global _binance_status, _binance_last_msg_at, _binance_stream_url

        url = self._build_url()
        _binance_stream_url = self._base_url
        _binance_status = "CONNECTING"
        log.info("Connecting to Binance WS: %s (open_timeout=%ds, ping=%ds/%ds)",
                 url[:80], WS_OPEN_TIMEOUT_S, WS_PING_INTERVAL_S, WS_PING_TIMEOUT_S)

        async with websockets.connect(
            url,
            ping_interval=WS_PING_INTERVAL_S,
            ping_timeout=WS_PING_TIMEOUT_S,
            open_timeout=WS_OPEN_TIMEOUT_S,
            close_timeout=5,
        ) as ws:
            self._ws = ws
            self._last_msg_ts = time.time()
            self._msg_count = 0
            _binance_status = "CONNECTED"
            log.info("Binance WebSocket connected (trade + depth5, ping=%ds/%ds) via %s",
                     WS_PING_INTERVAL_S, WS_PING_TIMEOUT_S, self._base_url)
            _write_binance_status()

            watchdog = asyncio.ensure_future(self._watchdog(ws))
            try:
                async for raw in ws:
                    now = time.time()
                    self._last_msg_ts = now
                    _binance_last_msg_at = now
                    self._msg_count += 1

                    # Reset on first message (connection confirmed working)
                    if self._msg_count == 1:
                        self._reconnect_attempts = 0
                        self._consecutive_failures = 0

                    self._handle_message(raw)

                    # Periodic status write (every 60s)
                    if now - self._last_status_write > 60:
                        self._last_status_write = now
                        _write_binance_status()
            except ConnectionClosed:
                log.warning("Binance WS closed (received %d msgs), will reconnect", self._msg_count)
            finally:
                watchdog.cancel()

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
