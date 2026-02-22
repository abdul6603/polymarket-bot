"""Snipe Engine — orchestrates the 5m multi-asset snipe strategy.

State machine:
  IDLE -> TRACKING -> ARMED -> EXECUTING -> COOLDOWN -> IDLE

Runs as a concurrent async task alongside Garves's taker/maker loops.
Completely isolated: own budget, own position tracking, own trades file.
Assets: BTC, ETH, SOL, XRP — whichever shows a delta lock first.
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
    """Main 5m multi-asset snipe orchestrator."""

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
        self._base_budget = budget_per_window
        self._escalated_budget = 75.0  # After 3 consecutive wins
        self._consecutive_wins = 0
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
        """Wait for windows to enter snipe zone, then start tracking all assets."""
        now = time.time()
        for w in self.window_tracker.all_active_windows():
            if w.traded:
                continue
            remaining = w.end_ts - now
            # Enter TRACKING once any window is within snipe zone
            if 30 < remaining <= 190:
                self._state = SnipeState.TRACKING
                self.delta_signal.reset()
                log.info("[SNIPE] IDLE -> TRACKING (scanning all assets, T-%.0fs)", remaining)
                return

    def _on_tracking(self) -> None:
        """Scan ALL assets each tick, try best delta first, fall back to others on no-liquidity."""
        now = time.time()

        # Find all tradeable windows in snipe zone
        candidates = []
        for w in self.window_tracker.all_active_windows():
            if w.traded:
                continue
            remaining = w.end_ts - now
            if remaining <= 0:
                continue
            if remaining > 190:
                continue
            price = self._cache.get_price(w.asset)
            if not price or w.open_price <= 0:
                continue
            delta = (price - w.open_price) / w.open_price
            candidates.append((w, price, delta, remaining))

        if not candidates:
            self._state = SnipeState.IDLE
            return

        # Sort by absolute delta (biggest mover first)
        candidates.sort(key=lambda x: abs(x[2]), reverse=True)

        # Log all assets in snipe zone for visibility
        remaining_top = candidates[0][3]
        if remaining_top <= 185:
            parts = []
            for w, p, d, r in candidates:
                parts.append(f"{w.asset[:3].upper()}={d*100:+.4f}%")
            log.info(
                "[SNIPE] T-%.0fs | Best: %s $%.2f | delta=%+.4f%% | All: %s",
                remaining_top, candidates[0][0].asset.upper(), candidates[0][1],
                candidates[0][2] * 100, " ".join(parts),
            )

        # Try each candidate in order (biggest delta first) — fall back on no-liquidity
        from bot.snipe.pyramid_executor import WAVES
        for window, price, delta, remaining in candidates:
            self._current_window_id = window.market_id

            signal = self.delta_signal.evaluate(price, window.open_price, remaining)
            if not signal:
                return  # Delta not strong enough on best candidate — wait

            direction = signal.direction
            token_id = window.up_token_id if direction == "up" else window.down_token_id

            implied = self._fetch_implied_price(window.market_id, token_id)
            if not implied:
                log.info("[SNIPE] %s: no implied price, trying next", window.asset.upper())
                continue

            max_cap = WAVES[0][2]
            for _, _, cap, fire_below in WAVES:
                if remaining <= fire_below:
                    max_cap = cap
            if implied > max_cap:
                log.info("[SNIPE] %s: CLOB $%.3f > cap $%.2f, trying next", window.asset.upper(), implied, max_cap)
                continue

            self._stats["signals"] += 1
            log.info(
                "[SNIPE] SIGNAL: %s %s | delta=%+.4f%% | conf=%.2f | "
                "sustained=%d | T-%.0fs | $%.2f (open $%.2f)",
                window.asset.upper(), signal.direction.upper(), signal.delta_pct,
                signal.confidence, signal.sustained_ticks, remaining, price, window.open_price,
            )

            # Start pyramid and fire waves immediately
            self.pyramid.start_position(window.market_id, signal.direction, window.open_price, window.asset)
            self._state = SnipeState.ARMED
            log.info("[SNIPE] TRACKING -> ARMED on %s (implied=$%.3f) — firing waves now", window.asset.upper(), implied)
            self._on_armed()

            # If wave 1 filled, we're done. If not (no liquidity), _on_armed set us back to IDLE.
            if self._state != SnipeState.IDLE:
                return
            # Still IDLE = wave 1 failed, try next asset
            log.info("[SNIPE] Falling back to next asset...")

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
            log.warning("[SNIPE] ARMED but no implied price | T-%.0fs | %s %s", remaining, window.asset.upper(), direction.upper())
            return

        # Check and fire each wave
        wave1_failed = False
        for wave_num in (1, 2, 3):
            if self.pyramid.should_fire_wave(wave_num, remaining, implied):
                # Verify delta still holds for waves 2 and 3
                if wave_num > 1:
                    asset_price = self._cache.get_price(window.asset)
                    if asset_price and window.open_price > 0:
                        abs_delta = abs((asset_price - window.open_price) / window.open_price)
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
                elif wave_num == 1:
                    wave1_failed = True

        # Wave 1 failed (no liquidity) — abandon this asset, try others
        if wave1_failed and self.pyramid.waves_fired == 0:
            log.warning("[SNIPE] Wave 1 failed (no liquidity) on %s — skipping", window.asset.upper())
            self.window_tracker.mark_traded(window.market_id)
            self.pyramid.close_position()
            self._state = SnipeState.IDLE
            return

        # All 3 waves done OR time running out with fills -> wait for resolution
        if self.pyramid.waves_fired >= 3:
            self._state = SnipeState.EXECUTING
            self.window_tracker.mark_traded(window.market_id)
            log.info("[SNIPE] ARMED -> EXECUTING (all 3 waves filled)")
        elif remaining < 30 and self.pyramid.waves_fired > 0:
            self._state = SnipeState.EXECUTING
            self.window_tracker.mark_traded(window.market_id)
            log.info("[SNIPE] ARMED -> EXECUTING (%d waves filled, T-%.0fs)", self.pyramid.waves_fired, remaining)

    def _on_executing(self) -> None:
        """Wait for CLOB resolution — don't guess from spot price."""
        window = self.window_tracker.get_window(self._current_window_id)
        if not window:
            self._finish_trade()
            return

        now = time.time()
        remaining = window.end_ts - now

        # Wait at least 30s after window end for CLOB to resolve
        if remaining > -30:
            return

        # Check CLOB for actual resolution
        resolved_dir = self._fetch_resolution(window.market_id)
        if resolved_dir:
            result = self.pyramid.close_position(resolved_dir)
            if result:
                self._record_outcome(result)
            self._cooldown_until = now + 30
            self._state = SnipeState.COOLDOWN
            log.info("[SNIPE] EXECUTING -> COOLDOWN (resolved=%s)", resolved_dir.upper())
            return

        # Timeout: 10 min after window end, give up
        if remaining <= -600:
            log.warning("[SNIPE] Resolution timeout for %s, closing as unknown", window.market_id[:12])
            self.pyramid.close_position()
            self._cooldown_until = now + 30
            self._state = SnipeState.COOLDOWN
            return

    def _finish_trade(self) -> None:
        """Clean up current trade and go to cooldown."""
        window = self.window_tracker.get_window(self._current_window_id)
        resolved_dir = ""
        if window:
            resolved_dir = self._fetch_resolution(window.market_id) or ""

        result = self.pyramid.close_position(resolved_dir)
        if result:
            self._record_outcome(result)

        self._cooldown_until = time.time() + 30
        self._state = SnipeState.COOLDOWN

    def _fetch_resolution(self, market_id: str) -> str | None:
        """Check CLOB API for actual market resolution. Returns 'up', 'down', or None."""
        try:
            from bot.http_session import get_session
            resp = get_session().get(
                f"{self._cfg.clob_host}/markets/{market_id}",
                timeout=10,
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            if not data.get("closed"):
                return None
            for t in data.get("tokens", []):
                outcome_label = (t.get("outcome") or "").lower()
                if t.get("winner", False):
                    if outcome_label in ("up", "yes"):
                        return "up"
                    elif outcome_label in ("down", "no"):
                        return "down"
            # Fallback: check final prices
            for t in data.get("tokens", []):
                outcome_label = (t.get("outcome") or "").lower()
                price = float(t.get("price", 0))
                if price > 0.9:
                    if outcome_label in ("up", "yes"):
                        return "up"
                    elif outcome_label in ("down", "no"):
                        return "down"
        except Exception:
            pass
        return None

    def _record_outcome(self, result: dict) -> None:
        """Record trade outcome in stats and publish to event bus."""
        self._stats["trades"] += 1
        self._stats["total_invested"] += result.get("total_size_usd", 0)

        if result.get("won") is True:
            self._stats["wins"] += 1
            self._consecutive_wins += 1
        elif result.get("won") is False:
            self._stats["losses"] += 1
            self._consecutive_wins = 0
        self._stats["pnl"] += result.get("pnl_usd", 0)

        # Budget escalation: $50 → $75 after 3 consecutive wins
        if self._consecutive_wins >= 3:
            self.pyramid._budget = self._escalated_budget
            log.info("[SNIPE] Budget ESCALATED to $%.0f (streak=%d)", self._escalated_budget, self._consecutive_wins)
        else:
            self.pyramid._budget = self._base_budget

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
                asset_name = result.get("asset", "BTC").upper()
                msg = (
                    f"GARVES SNIPE [{emoji}]\n\n"
                    f"{result['direction'].upper()} {asset_name} 5m\n"
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
                log.warning("[SNIPE] Implied price fetch failed: HTTP %d for %s", resp.status_code, market_id[:16])
                return None
            data = resp.json()
            for t in data.get("tokens", []):
                if t.get("token_id") == token_id:
                    price = t.get("price")
                    if price is not None:
                        return float(price)
            log.warning("[SNIPE] Token %s not found in market %s (tokens: %d)", token_id[:16], market_id[:16], len(data.get("tokens", [])))
        except Exception as e:
            log.warning("[SNIPE] Implied price error: %s", str(e)[:150])
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
            "budget_base": self._base_budget,
            "budget_escalated": self._escalated_budget,
            "consecutive_wins": self._consecutive_wins,
            "delta_threshold_pct": round(self.delta_signal._threshold * 100, 3),
            "stats": self._stats.copy(),
            "window": self.window_tracker.get_status(),
            "position": self.pyramid.get_status(),
            "history": self.pyramid.get_history(10),
            "timestamp": time.time(),
        }
