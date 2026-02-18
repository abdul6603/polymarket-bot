from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

from bot.config import Config

log = logging.getLogger(__name__)

# Binance Futures symbol -> our internal asset name
FUTURES_SYMBOL_MAP = {
    "BTCUSDT": "bitcoin",
    "ETHUSDT": "ethereum",
    "SOLUSDT": "solana",
    "XRPUSDT": "xrp",
}

# Build combined stream URL
# Format: btcusdt@forceOrder/ethusdt@forceOrder/.../btcusdt@markPrice/ethusdt@markPrice/...
LIQUIDATION_STREAMS = "/".join(f"{s.lower()}@forceOrder" for s in FUTURES_SYMBOL_MAP)
MARK_PRICE_STREAMS = "/".join(f"{s.lower()}@markPrice" for s in FUTURES_SYMBOL_MAP)
STREAMS = f"{LIQUIDATION_STREAMS}/{MARK_PRICE_STREAMS}"

# Binance Futures WebSocket — binance.com only (no futures on Binance.US)
# Requires VPN from geo-blocked regions. Falls back gracefully with backoff.
_FUTURES_WS_URL = "wss://fstream.binance.com"
_MAX_BACKOFF = 300  # 5 min max between retries when geo-blocked

# Cascade detection thresholds
CASCADE_USD_THRESHOLD = 500_000  # $500K in 5 minutes
CASCADE_EVENT_THRESHOLD = 10  # 10 events in 2 minutes
LIQUIDATION_RETENTION_SECONDS = 300  # 5 minutes


class DerivativesFeed:
    """
    Read-only feed from Binance Futures for liquidation and funding rate data.

    This is NOT for trading on Binance — we only read public WebSocket streams
    for signal intelligence to feed into Garves' trading strategy.
    """

    def __init__(self, cfg: Config | None = None):
        """
        Initialize the derivatives feed.

        Args:
            cfg: Config object (for consistency with other feeds, but not used here)
        """
        self.cfg = cfg
        self._ws = None
        self._running = False
        self._task: asyncio.Task | None = None
        self._url = f"{_FUTURES_WS_URL}/stream?streams={STREAMS}"

        # Liquidation tracking: asset -> list of liquidation events
        # Each event: {"side": "SELL"|"BUY", "qty": float, "price": float, "usd_value": float, "timestamp": float}
        self.liquidations: dict[str, list[dict[str, Any]]] = {
            "bitcoin": [],
            "ethereum": [],
            "solana": [],
            "xrp": [],
        }

        # Funding rate: asset -> {"rate": float, "mark_price": float, "index_price": float, "timestamp": float}
        self.funding_rates: dict[str, dict[str, float]] = {}

        # Liquidation cascade summary: asset -> {
        #   "long_liq_usd_5m": float,
        #   "short_liq_usd_5m": float,
        #   "cascade_detected": bool,
        #   "cascade_direction": str,  # "up" (bullish) | "down" (bearish) | ""
        #   "last_cascade_time": float,
        # }
        _default_liq = {
            "long_liq_usd_5m": 0.0,
            "short_liq_usd_5m": 0.0,
            "cascade_detected": False,
            "cascade_direction": "",
            "last_cascade_time": 0.0,
        }
        self.liq_summary: dict[str, dict[str, Any]] = {
            asset: dict(_default_liq) for asset in self.liquidations
        }

    async def start(self) -> None:
        """Start the WebSocket feed."""
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        log.info("DerivativesFeed started")

    async def stop(self) -> None:
        """Stop the WebSocket feed."""
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
        log.info("DerivativesFeed stopped")

    async def _run_loop(self) -> None:
        """Main loop that reconnects on disconnect."""
        backoff = 5
        while self._running:
            try:
                await self._connect()
                backoff = 5  # Reset on successful connection
            except asyncio.CancelledError:
                return
            except Exception:
                log.warning("Derivatives WS error, reconnecting in %ds (geo-block likely)", backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF)

    async def _connect(self) -> None:
        """Connect to Binance Futures WebSocket and handle messages."""
        log.info("Connecting to Binance Futures WS: %s", self._url[:80])
        async with websockets.connect(self._url, ping_interval=20) as ws:
            self._ws = ws
            log.info("Binance Futures WebSocket connected")
            try:
                async for raw in ws:
                    self._handle_message(raw)
            except ConnectionClosed:
                log.warning("Binance Futures WS connection closed")

    def _handle_message(self, raw: str) -> None:
        """Parse and route WebSocket messages."""
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        stream = msg.get("stream", "")
        data = msg.get("data")
        if not data:
            return

        event_type = data.get("e")

        if event_type == "forceOrder":
            self._handle_liquidation(data)
        elif event_type == "markPriceUpdate":
            self._handle_mark_price(data)

    def _handle_liquidation(self, data: dict[str, Any]) -> None:
        """
        Process liquidation event.

        Example data:
        {
          "e": "forceOrder",
          "E": 1234567890,
          "o": {
            "s": "BTCUSDT",
            "S": "SELL",  # SELL = long liquidated (bearish), BUY = short liquidated (bullish)
            "q": "0.014",
            "p": "95000.00",
            "ap": "94500.00",
            "X": "FILLED",
            "T": 1234567890
          }
        }
        """
        order = data.get("o", {})
        symbol = order.get("s")
        asset = FUTURES_SYMBOL_MAP.get(symbol)
        if not asset:
            return

        try:
            side = order["S"]  # "SELL" or "BUY"
            qty = float(order["q"])
            price = float(order.get("ap", order.get("p", 0)))  # Use average price if available
            timestamp = order["T"] / 1000.0  # ms -> seconds
            usd_value = qty * price
        except (KeyError, ValueError, TypeError):
            log.debug("Failed to parse liquidation data: %s", order)
            return

        # Store liquidation event
        event = {
            "side": side,
            "qty": qty,
            "price": price,
            "usd_value": usd_value,
            "timestamp": timestamp,
        }
        self.liquidations[asset].append(event)

        log.debug(
            "Liquidation: %s %s %.4f @ $%.2f ($%.0f)",
            asset.upper(),
            side,
            qty,
            price,
            usd_value,
        )

        # Clean up old liquidations (older than 5 minutes)
        self._cleanup_old_liquidations(asset)

        # Update cascade detection
        self._detect_cascade(asset)

    def _handle_mark_price(self, data: dict[str, Any]) -> None:
        """
        Process mark price update (includes funding rate).

        Example data:
        {
          "e": "markPriceUpdate",
          "E": 1234567890,
          "s": "BTCUSDT",
          "p": "95000.50",
          "i": "95100.00",
          "r": "0.00010000",
          "T": 1234567890
        }
        """
        symbol = data.get("s")
        asset = FUTURES_SYMBOL_MAP.get(symbol)
        if not asset:
            return

        try:
            mark_price = float(data["p"])
            index_price = float(data["i"])
            funding_rate = float(data["r"])
            timestamp = data["E"] / 1000.0  # ms -> seconds
        except (KeyError, ValueError, TypeError):
            log.debug("Failed to parse mark price data: %s", data)
            return

        self.funding_rates[asset] = {
            "rate": funding_rate,
            "mark_price": mark_price,
            "index_price": index_price,
            "timestamp": timestamp,
        }

        log.debug(
            "Funding rate: %s %.6f%% (mark: $%.2f, index: $%.2f)",
            asset.upper(),
            funding_rate * 100,
            mark_price,
            index_price,
        )

    def _cleanup_old_liquidations(self, asset: str) -> None:
        """Remove liquidation events older than 5 minutes."""
        now = time.time()
        cutoff = now - LIQUIDATION_RETENTION_SECONDS
        self.liquidations[asset] = [
            event for event in self.liquidations[asset]
            if event["timestamp"] >= cutoff
        ]

    def _detect_cascade(self, asset: str) -> None:
        """
        Detect liquidation cascades.

        A cascade is detected when:
        - More than $500K in liquidations for one side within 5 minutes
        - OR more than 10 individual liquidation events for one side within 2 minutes

        Long cascade (SELL liquidations) → "down" (bearish)
        Short cascade (BUY liquidations) → "up" (bullish)
        """
        now = time.time()
        events = self.liquidations[asset]

        # Calculate 5-minute totals
        long_liq_usd_5m = sum(
            e["usd_value"] for e in events if e["side"] == "SELL"
        )
        short_liq_usd_5m = sum(
            e["usd_value"] for e in events if e["side"] == "BUY"
        )

        # Count events in last 2 minutes
        two_min_cutoff = now - 120
        long_events_2m = sum(
            1 for e in events if e["side"] == "SELL" and e["timestamp"] >= two_min_cutoff
        )
        short_events_2m = sum(
            1 for e in events if e["side"] == "BUY" and e["timestamp"] >= two_min_cutoff
        )

        # Update summary
        summary = self.liq_summary[asset]
        summary["long_liq_usd_5m"] = long_liq_usd_5m
        summary["short_liq_usd_5m"] = short_liq_usd_5m

        # Detect cascade
        long_cascade = (
            long_liq_usd_5m > CASCADE_USD_THRESHOLD
            or long_events_2m > CASCADE_EVENT_THRESHOLD
        )
        short_cascade = (
            short_liq_usd_5m > CASCADE_USD_THRESHOLD
            or short_events_2m > CASCADE_EVENT_THRESHOLD
        )

        # Determine direction (stronger side wins)
        cascade_detected = long_cascade or short_cascade
        cascade_direction = ""

        if cascade_detected:
            if long_cascade and not short_cascade:
                cascade_direction = "down"  # Long liquidations = bearish
            elif short_cascade and not long_cascade:
                cascade_direction = "up"  # Short liquidations = bullish
            elif long_liq_usd_5m > short_liq_usd_5m:
                cascade_direction = "down"
            else:
                cascade_direction = "up"

            # Log cascade detection (only if new or direction changed)
            if (
                not summary["cascade_detected"]
                or summary["cascade_direction"] != cascade_direction
            ):
                log.info(
                    "🚨 Liquidation cascade detected: %s → %s | Long: $%.0f (%d events/2m) | Short: $%.0f (%d events/2m)",
                    asset.upper(),
                    cascade_direction.upper(),
                    long_liq_usd_5m,
                    long_events_2m,
                    short_liq_usd_5m,
                    short_events_2m,
                )
                summary["last_cascade_time"] = now

        summary["cascade_detected"] = cascade_detected
        summary["cascade_direction"] = cascade_direction

    def get_status(self) -> dict[str, Any]:
        """
        Get current status for dashboard display.

        Returns:
            Dict with connection status, funding rates, and liquidation summaries.
        """
        return {
            "connected": self._running and self._ws is not None,
            "funding_rates": self.funding_rates.copy(),
            "liquidations": {
                asset: {
                    "long_liq_usd_5m": summary["long_liq_usd_5m"],
                    "short_liq_usd_5m": summary["short_liq_usd_5m"],
                    "cascade_detected": summary["cascade_detected"],
                    "cascade_direction": summary["cascade_direction"],
                    "event_count": len(self.liquidations[asset]),
                    "last_cascade_time": summary["last_cascade_time"],
                }
                for asset, summary in self.liq_summary.items()
            },
        }
