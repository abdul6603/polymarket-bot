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

# ── Reconnection tuning ─────────────────────────────────────
BACKOFF_BASE_S = 1.0
BACKOFF_MULTIPLIER = 1.5
BACKOFF_MAX_S = 30.0
JITTER_MAX_S = 1.0

# ── Fallback WS streams ─────────────────────────────────────
WS_STREAMS = [
    "wss://stream.binance.com:9443",   # Primary
    "wss://stream.binance.com:443",    # Alternate port
]

# ── Health status (module-level for dashboard access) ────────
_binance_status: str = "DISCONNECTED"
_binance_last_msg_at: float = 0.0
_binance_reconnect_count: int = 0
_binance_stream_url: str = ""

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
    }


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
    """Real-time trade + order book feed from Binance public WebSocket."""

    def __init__(self, cfg: Config, price_cache: PriceCache):
        self.cfg = cfg
        self._cache = price_cache
        self._ws = None
        self._running = False
        self._task: asyncio.Task | None = None
        # Use global Binance WS (stream.binance.com) — .us WS blocks non-US IPs
        # REST API stays on api.binance.us (works from any IP)
        self._base_url = getattr(cfg, "binance_ws_url", WS_STREAMS[0])
        self._stream_index = 0  # Index into WS_STREAMS for fallback
        self._consecutive_failures = 0

        # Order book depth: asset -> {"bids": [[price, qty], ...], "asks": [...], "timestamp": float}
        self.depth: dict[str, dict[str, Any]] = {}

        # Reconnection state
        self._backoff_s = BACKOFF_BASE_S
        self._pool_recreate_count = 0
        self._last_status_write = 0.0

    def _build_url(self) -> str:
        """Build WS URL from current base."""
        return f"{self._base_url}/stream?streams={STREAMS}"

    async def start(self) -> None:
        """Start WS in a dedicated thread with its own event loop."""
        self._running = True
        self._thread = threading.Thread(
            target=self._thread_entry, daemon=True, name="binance-ws",
        )
        self._thread.start()

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

            # Backoff before retry
            delay = min(self._backoff_s, BACKOFF_MAX_S) + random.uniform(0, JITTER_MAX_S)
            _binance_status = "RECONNECTING"
            _write_binance_status()
            log.warning("Binance WS thread sleeping %.1fs before restart", delay)
            time.sleep(delay)
            self._backoff_s = min(self._backoff_s * BACKOFF_MULTIPLIER, BACKOFF_MAX_S)

        log.info("Binance WS thread exited (running=False)")

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

    async def stop(self) -> None:
        self._running = False
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
                # Successful session — reset backoff and failure counter
                self._backoff_s = BACKOFF_BASE_S
                self._consecutive_failures = 0
            except asyncio.CancelledError:
                return
            except Exception:
                self._consecutive_failures += 1
                _binance_reconnect_count += 1

                # Switch to alternate stream after 3 consecutive failures
                if self._consecutive_failures >= 3 and len(WS_STREAMS) > 1:
                    self._stream_index = (self._stream_index + 1) % len(WS_STREAMS)
                    self._base_url = WS_STREAMS[self._stream_index]
                    log.warning(
                        "Binance WS switching to alternate stream: %s (after %d failures)",
                        self._base_url, self._consecutive_failures,
                    )
                    self._consecutive_failures = 0

                _binance_status = "CONNECTING"
                _binance_stream_url = self._base_url
                _write_binance_status()

                delay = min(self._backoff_s, BACKOFF_MAX_S) + random.uniform(0, JITTER_MAX_S)
                log.warning(
                    "Binance WS error, reconnecting in %.1fs (attempt backoff=%.1fs)",
                    delay, self._backoff_s,
                )
                await asyncio.sleep(delay)
                self._backoff_s = min(self._backoff_s * BACKOFF_MULTIPLIER, BACKOFF_MAX_S)

    async def _watchdog(self, ws) -> None:
        """Independent watchdog — closes WS if no data for 25s."""
        while True:
            await asyncio.sleep(10)
            age = time.time() - self._last_msg_ts
            if age > 25:
                log.warning("[BINANCE-STALE] No data for %.0fs, forcing reconnect", age)
                await ws.close()
                return

    async def _connect(self) -> None:
        global _binance_status, _binance_last_msg_at, _binance_stream_url

        url = self._build_url()
        _binance_stream_url = self._base_url
        _binance_status = "CONNECTING"
        log.info("Connecting to Binance WS: %s", url[:80])

        async with websockets.connect(
            url, ping_interval=20, ping_timeout=10,
            open_timeout=15, close_timeout=5,
        ) as ws:
            self._ws = ws
            self._last_msg_ts = time.time()
            self._msg_count = 0
            _binance_status = "CONNECTED"
            log.info("Binance WebSocket connected (trade + depth5) via %s", self._base_url)
            _write_binance_status()

            watchdog = asyncio.ensure_future(self._watchdog(ws))
            try:
                async for raw in ws:
                    now = time.time()
                    self._last_msg_ts = now
                    _binance_last_msg_at = now
                    self._msg_count += 1

                    # Reset backoff on first message (connection confirmed working)
                    if self._msg_count == 1:
                        self._backoff_s = BACKOFF_BASE_S
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
