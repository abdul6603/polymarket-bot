"""Snipe Engine — orchestrates the 5m BTC snipe strategy.

State machine:
  IDLE -> TRACKING -> ARMED -> EXECUTING -> COOLDOWN -> IDLE

Runs as a concurrent async task alongside Garves's taker/maker loops.
Completely isolated: own budget, own position tracking, own trades file.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from enum import Enum
from pathlib import Path

from bot.config import Config
from bot.price_cache import PriceCache
from bot.snipe.window_tracker import WindowTracker
from bot.snipe.delta_signal import DeltaSignal
from bot.snipe.pyramid_executor import PyramidExecutor

log = logging.getLogger("garves.snipe")


class SnipeState(Enum):
    IDLE = "idle"
    TRACKING = "tracking"
    ARMED = "armed"
    EXECUTING = "executing"
    COOLDOWN = "cooldown"


class SnipeEngine:
    """Main 5m BTC snipe orchestrator."""

    def __init__(
        self,
        cfg: Config,
        price_cache: PriceCache,
        clob_client,
        dry_run: bool = True,
        budget_per_window: float = 50.0,
        delta_threshold: float = 0.0008,
    ):
        self._cfg = cfg
        self._cache = price_cache
        self._dry_run = dry_run

        self.window_tracker = WindowTracker(cfg, price_cache)
        self.delta_signal = DeltaSignal(threshold=delta_threshold)
        self.pyramid = PyramidExecutor(
            cfg, clob_client, dry_run=dry_run,
            budget_per_window=budget_per_window,
        )

        self._state = SnipeState.IDLE
        self._current_window_id: str = ""
        self._cooldown_until = 0.0
        self._stats = {
            "signals": 0, "trades": 0, "wins": 0,
            "losses": 0, "pnl": 0.0, "total_invested": 0.0,
        }

        self._status_file = Path(__file__).parent.parent.parent / "data" / "snipe_status.json"
        self.enabled = getattr(cfg, "snipe_enabled", True)

    async def run_loop(self, shutdown_event: asyncio.Event) -> None:
        """Main snipe loop — ticks every 5s."""
        if not self.enabled:
            log.info("[SNIPE] Engine disabled")
            return

        log.info(
            "[SNIPE] Engine started | budget=$%.0f/window | threshold=%.2f%% | dry_run=%s",
            self.pyramid._budget, self.delta_signal._threshold * 100, self._dry_run,
        )

        while not shutdown_event.is_set():
            try:
                self.tick()
                self._save_status()
            except Exception as e:
                log.warning("[SNIPE] Tick error: %s", str(e)[:200])

            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass

        log.info("[SNIPE] Engine stopped")

    def tick(self) -> None:
        """Single tick of the snipe state machine."""
        now = time.time()

        if self._state == SnipeState.IDLE:
            self._on_idle()
        elif self._state == SnipeState.TRACKING:
            self._on_tracking()
        elif self._state == SnipeState.ARMED:
            self._on_armed()
        elif self._state == SnipeState.EXECUTING:
            self._on_executing()
        elif self._state == SnipeState.COOLDOWN:
            if now > self._cooldown_until:
                self._state = SnipeState.IDLE
                log.info("[SNIPE] Cooldown done -> IDLE")

    def _on_idle(self) -> None:
        """Look for a tradeable 5m window."""
        window = self.window_tracker.get_active_window()
        if not window:
            return

        now = time.time()
        remaining = window.end_ts - now
        if remaining > 300 or remaining < 30:
            return

        self._current_window_id = window.market_id
        self.delta_signal.reset()
        self._state = SnipeState.TRACKING
        log.info(
            "[SNIPE] IDLE -> TRACKING: %s (T-%.0fs, BTC open=$%.2f)",
            window.market_id[:12], remaining, window.open_price,
        )

    def _on_tracking(self) -> None:
        """Monitor BTC delta, waiting for signal to arm."""
        window = self.window_tracker.get_window(self._current_window_id)
        if not window:
            self._state = SnipeState.IDLE
            return

        now = time.time()
        remaining = window.end_ts - now

        if remaining <= 0:
            log.info("[SNIPE] Window expired while tracking")
            self._state = SnipeState.IDLE
            return

        btc_price = self._cache.get_price("bitcoin")
        if not btc_price:
            return

        # Log delta every tick for visibility
        delta = (btc_price - window.open_price) / window.open_price * 100
        if remaining <= 185:
            log.info(
                "[SNIPE] T-%.0fs | BTC $%.2f | delta=%+.4f%% (open=$%.2f)",
                remaining, btc_price, delta, window.open_price,
            )

        signal = self.delta_signal.evaluate(btc_price, window.open_price, remaining)
        if not signal:
            return

        self._stats["signals"] += 1
        log.info(
            "[SNIPE] SIGNAL: %s | delta=%+.4f%% | conf=%.2f | "
            "sustained=%d | T-%.0fs | BTC $%.2f (open $%.2f)",
            signal.direction.upper(), signal.delta_pct, signal.confidence,
            signal.sustained_ticks, remaining, btc_price, window.open_price,
        )

        # Determine target token
        token_id = window.up_token_id if signal.direction == "up" else window.down_token_id

        # Check CLOB implied price
        implied = self._fetch_implied_price(window.market_id, token_id)
        if not implied:
            log.info("[SNIPE] Signal but no implied price available")
            return

        if implied > 0.60:
            log.info("[SNIPE] Signal but CLOB price too high: $%.3f (Wave 1 cap $0.60)", implied)
            return

        # Start pyramid
        self.pyramid.start_position(window.market_id, signal.direction, window.open_price)
        self._state = SnipeState.ARMED
        log.info("[SNIPE] TRACKING -> ARMED (implied=$%.3f)", implied)

    def _on_armed(self) -> None:
        """Execute pyramid waves as timing conditions are met."""
        window = self.window_tracker.get_window(self._current_window_id)
        if not window:
            self._finish_trade()
            return

        now = time.time()
        remaining = window.end_ts - now

        if remaining <= 0:
            self._finish_trade()
            return

        if not self.pyramid.has_active_position:
            self._state = SnipeState.IDLE
            return

        direction = self.pyramid.active_direction
        token_id = window.up_token_id if direction == "up" else window.down_token_id

        implied = self._fetch_implied_price(window.market_id, token_id)
        if not implied:
            return

        # Check and fire each wave
        for wave_num in (1, 2, 3):
            if self.pyramid.should_fire_wave(wave_num, remaining, implied):
                # Verify delta still holds for waves 2 and 3 (escalating threshold)
                if wave_num > 1:
                    btc_price = self._cache.get_price("bitcoin")
                    if btc_price and window.open_price > 0:
                        abs_delta = abs((btc_price - window.open_price) / window.open_price)
                        wave_threshold = self.delta_signal.get_wave_threshold(wave_num)
                        if abs_delta < wave_threshold:
                            log.info(
                                "[SNIPE] Wave %d delta check failed: %.4f%% < %.4f%%",
                                wave_num, abs_delta * 100, wave_threshold * 100,
                            )
                            continue

                result = self.pyramid.execute_wave(wave_num, token_id, implied)
                if result:
                    log.info("[SNIPE] Wave %d FIRED | T-%.0fs | price=$%.3f", wave_num, remaining, implied)

        # All 3 waves done -> wait for resolution
        if self.pyramid.waves_fired >= 3:
            self._state = SnipeState.EXECUTING
            self.window_tracker.mark_traded(window.market_id)
            log.info("[SNIPE] ARMED -> EXECUTING (all 3 waves placed)")

    def _on_executing(self) -> None:
        """Waiting for window to resolve."""
        window = self.window_tracker.get_window(self._current_window_id)
        if not window:
            self._finish_trade()
            return

        now = time.time()
        remaining = window.end_ts - now

        if remaining <= -5:
            # Window ended — determine outcome from BTC price
            btc_price = self._cache.get_price("bitcoin")
            if btc_price and window.open_price > 0:
                actual_dir = "up" if btc_price > window.open_price else "down"
                result = self.pyramid.close_position(actual_dir)
                if result:
                    self._record_outcome(result)
            else:
                self.pyramid.close_position()

            self._cooldown_until = now + 30
            self._state = SnipeState.COOLDOWN
            log.info("[SNIPE] EXECUTING -> COOLDOWN")

    def _finish_trade(self) -> None:
        """Clean up current trade and go to cooldown."""
        # Try to determine outcome from BTC price
        window = self.window_tracker.get_window(self._current_window_id)
        resolved_dir = ""
        if window:
            btc_price = self._cache.get_price("bitcoin")
            if btc_price and window.open_price > 0:
                resolved_dir = "up" if btc_price > window.open_price else "down"

        result = self.pyramid.close_position(resolved_dir)
        if result:
            self._record_outcome(result)

        self._cooldown_until = time.time() + 30
        self._state = SnipeState.COOLDOWN

    def _record_outcome(self, result: dict) -> None:
        """Record trade outcome in stats and publish to event bus."""
        self._stats["trades"] += 1
        self._stats["total_invested"] += result.get("total_size_usd", 0)

        if result.get("won") is True:
            self._stats["wins"] += 1
        elif result.get("won") is False:
            self._stats["losses"] += 1
        self._stats["pnl"] += result.get("pnl_usd", 0)

        # Publish to event bus
        try:
            from shared.events import publish
            publish(
                agent="garves",
                event_type="snipe_trade_resolved",
                data=result,
                summary=(
                    f"Snipe {result['direction'].upper()} "
                    f"${result['total_size_usd']:.2f} -> "
                    f"PnL ${result['pnl_usd']:+.2f}"
                ),
            )
        except Exception:
            pass

        # Telegram alert
        try:
            import os
            tg_token = os.environ.get("TG_BOT_TOKEN", "")
            tg_chat = os.environ.get("TG_CHAT_ID", "")
            if tg_token and tg_chat:
                import requests
                won = result.get("won")
                emoji = "W" if won else "L" if won is False else "?"
                msg = (
                    f"GARVES SNIPE [{emoji}]\n\n"
                    f"{result['direction'].upper()} BTC 5m\n"
                    f"Waves: {result['waves']} | Invested: ${result['total_size_usd']:.2f}\n"
                    f"Avg Entry: ${result['avg_entry']:.3f}\n"
                    f"PnL: ${result['pnl_usd']:+.2f}\n"
                    f"Running: {self._stats['wins']}W-{self._stats['losses']}L "
                    f"(${self._stats['pnl']:+.2f})"
                )
                requests.post(
                    f"https://api.telegram.org/bot{tg_token}/sendMessage",
                    json={"chat_id": tg_chat, "text": msg},
                    timeout=10,
                )
        except Exception:
            pass

    def _fetch_implied_price(self, market_id: str, token_id: str) -> float | None:
        """Fetch current CLOB implied price for a token."""
        try:
            from bot.http_session import get_session
            resp = get_session().get(
                f"{self._cfg.clob_host}/markets/{market_id}",
                timeout=5,
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            for t in data.get("tokens", []):
                if t.get("token_id") == token_id:
                    price = t.get("price")
                    if price is not None:
                        return float(price)
        except Exception:
            pass
        return None

    def _save_status(self) -> None:
        """Write status to disk for dashboard."""
        try:
            status = self.get_status()
            self._status_file.parent.mkdir(parents=True, exist_ok=True)
            self._status_file.write_text(json.dumps(status, indent=2))
        except Exception:
            pass

    def get_status(self) -> dict:
        """Dashboard-friendly status."""
        return {
            "enabled": self.enabled,
            "state": self._state.value,
            "dry_run": self._dry_run,
            "budget_per_window": self.pyramid._budget,
            "delta_threshold_pct": round(self.delta_signal._threshold * 100, 3),
            "stats": self._stats.copy(),
            "window": self.window_tracker.get_status(),
            "position": self.pyramid.get_status(),
            "history": self.pyramid.get_history(10),
            "timestamp": time.time(),
        }
