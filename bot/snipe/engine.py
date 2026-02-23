"""Snipe Engine v7 — 5m BTC binary scalper with 10-component scoring.

State machine:
  IDLE -> TRACKING -> ARMED -> EXECUTING -> COOLDOWN -> IDLE

Runs in its own background thread (independent of Garves's main event loop).
Ticks every 2s. BTC only — deepest order book, most reliable fills.

v7 upgrade: Combines Binance L2 orderflow + Polymarket CLOB orderbook +
SMC structure (BOS/CHoCH on 5m/15m candles) into a 0-100 signal score.
Only executes when score >= threshold (default 75).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from enum import Enum
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from bot.config import Config
from bot.price_cache import PriceCache
from bot.snipe.window_tracker import WindowTracker
from bot.snipe.delta_signal import DeltaSignal
from bot.snipe.pyramid_executor import PyramidExecutor
from bot.snipe.orderbook_signal import OrderBookSignal
from bot.snipe.candle_store import CandleStore
from bot.snipe.signal_scorer import SignalScorer
from bot.snipe import clob_book
from bot.snipe.fill_simulator import estimate_fill

log = logging.getLogger("garves.snipe")

ET = ZoneInfo("America/New_York")

# Tick interval in seconds — 8s gives ~22 ticks per 180s snipe zone
SNIPE_TICK_INTERVAL = 2  # 2s ticks for T-30s precision

# Weekend pre-futures: low vol, lower threshold to catch small moves
# Futures active (weekdays + weekend after 6PM ET): higher threshold for real moves only
WEEKEND_PREFUTURES_THRESHOLD = 0.0007  # 0.070%
FUTURES_THRESHOLD = 0.0008             # 0.080%

# Minimum CLOB implied price to enter — don't buy tokens the market says are <40% likely.
# A $0.09 DOWN token = market says 9% chance of DOWN. That's fighting smart money.
# Whale enters near $0.45-$0.55 (50/50 odds). Floor at $0.40 keeps us honest.
MIN_IMPLIED_PRICE = 0.40

# Delta confirmation threshold — lower than standalone because imbalance provides direction
DELTA_CONFIRM_THRESHOLD = 0.0005  # 0.05% confirms imbalance direction


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
        self._delta_threshold = delta_threshold
        self._delta_signals: dict[str, DeltaSignal] = {}  # Per-asset signal trackers
        self.pyramid = PyramidExecutor(
            cfg, clob_client, dry_run=dry_run,
            budget_per_window=25.0,
        )
        self._orderbook = OrderBookSignal()
        self._orderbook.start()

        # v7: Scoring engine + candle structure + CLOB bridge
        self._candle_store = CandleStore()
        self._scorer = SignalScorer(threshold=75)

        self._state = SnipeState.IDLE
        self._current_window_id: str = ""
        self._cooldown_until = 0.0
        self._executing_since = 0.0  # Track when we entered EXECUTING
        self._base_budget = 25.0  # Conservative: protect the $188
        self._escalated_budget = 40.0  # After 3 consecutive wins
        self._consecutive_wins = 0
        self._stats = {
            "signals": 0, "trades": 0, "wins": 0,
            "losses": 0, "pnl": 0.0, "total_invested": 0.0,
        }

        self._status_file = Path(__file__).parent.parent.parent / "data" / "snipe_status.json"
        self.enabled = getattr(cfg, "snipe_enabled", True)

    def _effective_threshold(self) -> float:
        """Dynamic threshold based on CME futures session.

        Futures closed (weekend): Friday 4:30PM ET → Sunday 6:00PM ET → 0.070%
        Futures open: Sunday 6:00PM ET → Friday 4:30PM ET → 0.080%
        """
        now = datetime.now(ET)
        day = now.weekday()  # 0=Mon, 4=Fri, 5=Sat, 6=Sun
        hour_min = now.hour * 100 + now.minute  # e.g. 1630 = 4:30PM

        # Weekend = futures closed
        if day == 5:  # Saturday — always weekend
            return WEEKEND_PREFUTURES_THRESHOLD
        if day == 4 and hour_min >= 1630:  # Friday after 4:30PM
            return WEEKEND_PREFUTURES_THRESHOLD
        if day == 6 and hour_min < 1800:  # Sunday before 6PM
            return WEEKEND_PREFUTURES_THRESHOLD

        return FUTURES_THRESHOLD

    def _get_signal(self, asset: str) -> DeltaSignal:
        """Get or create per-asset delta signal tracker with dynamic threshold."""
        threshold = self._effective_threshold()
        if asset not in self._delta_signals:
            self._delta_signals[asset] = DeltaSignal(threshold=threshold)
        else:
            # Update threshold if it changed (weekend <-> weekday transition)
            self._delta_signals[asset]._threshold = threshold
        return self._delta_signals[asset]

    async def run_loop(self, shutdown_event: asyncio.Event) -> None:
        """Snipe loop — runs in its own thread to avoid starvation from main event loop.

        The taker loop blocks the asyncio event loop for 60-100s per cycle
        doing synchronous HTTP calls. By running snipe in a separate thread,
        it ticks every 15s regardless of taker activity.
        """
        if not self.enabled:
            log.info("[SNIPE] Engine disabled")
            return

        threshold = self._effective_threshold()
        is_weekend = datetime.now(ET).weekday() >= 5
        log.info(
            "[SNIPE] Engine v7 started | budget=$%.0f/window | threshold=%.3f%% (%s) | "
            "score_threshold=%d | tick=%ds | dry_run=%s | orderbook=%s",
            self.pyramid._budget, threshold * 100,
            "weekend" if is_weekend else "weekday",
            self._scorer.threshold, SNIPE_TICK_INTERVAL, self._dry_run,
            "connected" if self._orderbook.is_connected else "disconnected",
        )

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._thread_loop, shutdown_event)

    def _thread_loop(self, shutdown_event: asyncio.Event) -> None:
        """Blocking loop running in a background thread, ticks every 15s."""
        while not shutdown_event.is_set():
            try:
                self.tick()
                self._save_status()
            except Exception as e:
                log.warning("[SNIPE] Tick error: %s", str(e)[:200])

            # Sleep in 1s increments so we can respond to shutdown quickly
            for _ in range(SNIPE_TICK_INTERVAL):
                if shutdown_event.is_set():
                    break
                time.sleep(1)

        log.info("[SNIPE] Engine stopped")

    def tick(self) -> None:
        """Single tick of the snipe state machine."""
        now = time.time()

        # Feed candle store every tick (needs continuous data for 5m/15m candles)
        btc_price = self._cache.get_price("bitcoin")
        if btc_price:
            self._candle_store.feed_tick(btc_price)

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
                self._delta_signals.clear()  # Reset all per-asset trackers
                self._scorer.reset_spread_history()  # Fresh spread tracking
                threshold = self._effective_threshold()
                log.info(
                    "[SNIPE] IDLE -> TRACKING (T-%.0fs, delta_thresh=%.3f%%, score_thresh=%d)",
                    remaining, threshold * 100, self._scorer.threshold,
                )
                return

    def _on_tracking(self) -> None:
        """Evaluate BTC windows with 10-component scoring engine (v7)."""
        now = time.time()

        # Fetch LIVE BTC price (PriceCache is stale between taker ticks)
        live_btc = self._fetch_btc_price()
        if live_btc:
            self._candle_store.feed_tick(live_btc)

        candidates = []
        for w in self.window_tracker.all_active_windows():
            if w.traded or w.asset != "bitcoin":
                continue
            remaining = w.end_ts - now
            if remaining <= 0 or remaining > 190:
                continue
            price = live_btc if live_btc else self._cache.get_price(w.asset)
            if not price or w.open_price <= 0:
                continue
            delta = (price - w.open_price) / w.open_price
            candidates.append((w, price, delta, remaining))

        if not candidates:
            self._state = SnipeState.IDLE
            return

        candidates.sort(key=lambda x: abs(x[2]), reverse=True)

        # Log top candidates
        remaining_top = candidates[0][3]
        if remaining_top <= 185:
            parts = [f"{w.asset[:3].upper()}={d*100:+.4f}%" for w, p, d, r in candidates]
            log.info(
                "[SNIPE] T-%.0fs | Best: %s $%.2f | delta=%+.4f%% | All: %s",
                remaining_top, candidates[0][0].asset.upper(), candidates[0][1],
                candidates[0][2] * 100, " ".join(parts),
            )

        # Gather Binance L2 data (shared across all candidates)
        ob_signal = self._orderbook.get_signal() if self._orderbook.is_connected else None
        ob_reading = self._orderbook.get_latest_reading() if self._orderbook.is_connected else None

        # Gather SMC structure (shared — all candidates are BTC)
        structure_5m = self._candle_store.get_structure("5m")
        structure_15m = self._candle_store.get_structure("15m")

        from bot.snipe.pyramid_executor import WAVES

        for window, price, delta, remaining in candidates:
            # Timing guard
            if remaining > 180 or remaining < 5:
                continue

            # Track delta direction (keeps DeltaSignal's history for reversal detection)
            sig_tracker = self._get_signal(window.asset)
            sig_tracker.evaluate(price, window.open_price, remaining)

            # Pre-filter: minimum delta to even consider scoring
            abs_delta = abs(delta)
            if abs_delta < DELTA_CONFIRM_THRESHOLD:
                continue

            direction = "up" if delta > 0 else "down"

            # Count sustained ticks in same direction
            sustained = 0
            for d in reversed(sig_tracker._recent_dirs):
                if d == direction:
                    sustained += 1
                else:
                    break

            # Token IDs for target and opposite
            token_id = window.up_token_id if direction == "up" else window.down_token_id
            opp_token_id = window.down_token_id if direction == "up" else window.up_token_id

            # Fetch CLOB implied price
            implied = self._fetch_implied_price(window.market_id, token_id)
            if not implied:
                log.info("[SNIPE] %s: no implied price, trying next", window.asset.upper())
                continue

            # Price floor
            if implied < MIN_IMPLIED_PRICE:
                log.info(
                    "[SNIPE] %s: CLOB $%.3f < floor $%.2f, skipping",
                    window.asset.upper(), implied, MIN_IMPLIED_PRICE,
                )
                continue

            # Cap check
            max_cap = WAVES[0][2]
            for _, _, cap, fire_below in WAVES:
                if remaining <= fire_below:
                    max_cap = cap
            if implied > max_cap:
                log.info("[SNIPE] %s: CLOB $%.3f > cap $%.2f, trying next",
                         window.asset.upper(), implied, max_cap)
                continue

            # Get CLOB orderbook data for both tokens
            target_book = clob_book.get_orderbook(token_id)
            opp_book = clob_book.get_orderbook(opp_token_id)

            # ── SCORE all 10 components ──
            score_result = self._scorer.score(
                direction=direction,
                delta_pct=abs_delta * 100,  # Convert to percent (0.12 = 0.12%)
                sustained_ticks=sustained,
                ob_imbalance=ob_reading.imbalance if ob_reading else None,
                ob_strength=ob_signal.strength if ob_signal else None,
                clob_book=target_book,
                clob_book_opposite=opp_book,
                structure_5m=structure_5m,
                structure_15m=structure_15m,
                remaining_s=remaining,
                implied_price=implied,
            )

            if not score_result.should_trade:
                continue  # Score logged by scorer itself

            # ── Fill simulation ──
            shares_est = int(self.pyramid._budget / max_cap)
            fill_est = estimate_fill(token_id, max_cap, shares_est)
            log.info("[SNIPE] FILL SIM: %s", fill_est.detail)

            self._stats["signals"] += 1
            log.info(
                "[SNIPE] SIGNAL: %s %s | score=%.0f/100 | delta=%+.4f%% | "
                "sustained=%d | T-%.0fs | $%.2f (open $%.2f)",
                window.asset.upper(), direction.upper(), score_result.total_score,
                delta * 100, sustained, remaining, price, window.open_price,
            )

            # ── Execute ──
            self._current_window_id = window.market_id
            self.pyramid.start_position(window.market_id, direction, window.open_price, window.asset)

            result = self.pyramid.execute_wave(1, token_id, implied)
            if result:
                log.info(
                    "[SNIPE] GTC FILLED | T-%.0fs | %s %s | $%.2f | %.0f shares @ $%.3f | score=%.0f",
                    remaining, window.asset.upper(), direction.upper(),
                    result.size_usd, result.shares, result.price, score_result.total_score,
                )
                self._state = SnipeState.EXECUTING
                self._executing_since = time.time()
                self.window_tracker.mark_traded(window.market_id)
                log.info("[SNIPE] TRACKING -> EXECUTING (GTC filled)")
                return
            elif self.pyramid.has_pending_order:
                self._state = SnipeState.ARMED
                self.window_tracker.mark_traded(window.market_id)
                log.info("[SNIPE] TRACKING -> ARMED (GTC resting, score=%.0f)", score_result.total_score)
                return
            else:
                log.warning("[SNIPE] GTC failed on %s — trying next", window.asset.upper())
                self.pyramid.close_position()
                continue

    def _on_armed(self) -> None:
        """Monitor resting GTC order for fills. Cancel at T-5s or on reversal."""
        window = self.window_tracker.get_window(self._current_window_id)
        if not window:
            self.pyramid.cancel_pending_order()
            self._finish_trade()
            return

        now = time.time()
        remaining = window.end_ts - now

        if remaining <= 0:
            self.pyramid.cancel_pending_order()
            self._finish_trade()
            return

        # Cancel at T-5s — don't hold unfilled orders into resolution
        if remaining <= 5:
            # Check for partial fills before cancelling
            partial = self.pyramid.finalize_partial_fill()
            self.pyramid.cancel_pending_order()
            if self.pyramid.has_active_position and self.pyramid.waves_fired > 0:
                self._state = SnipeState.EXECUTING
                log.info("[SNIPE] T-5s: have fills, holding through resolution")
            else:
                self.pyramid.close_position()
                self._state = SnipeState.IDLE
                log.info("[SNIPE] T-5s: no fills, order cancelled")
            return

        # Poll for order fill
        if self.pyramid.has_pending_order:
            fill = self.pyramid.poll_pending_order()
            if fill:
                log.info(
                    "[SNIPE] GTC FILLED | T-%.0fs | %s %s | $%.2f | %.0f shares",
                    remaining, window.asset.upper(), fill.direction.upper(),
                    fill.size_usd, fill.shares,
                )
                self._state = SnipeState.EXECUTING
                self.window_tracker.mark_traded(window.market_id)
                self._executing_since = time.time()
                log.info("[SNIPE] ARMED -> EXECUTING (GTC filled)")
                return
        elif not self.pyramid.has_active_position:
            self._state = SnipeState.IDLE
            return

        # Update delta tracking + log + check reversal
        live_btc = self._fetch_btc_price()
        if live_btc and self.pyramid.has_active_position and window.open_price > 0:
            direction = self.pyramid.active_direction
            delta = (live_btc - window.open_price) / window.open_price
            current_dir = "up" if delta > 0 else "down"

            # Update signal tracker for reversal detection
            sig = self._get_signal(window.asset)
            sig._recent_dirs.append(current_dir)

            ob_reading = self._orderbook.get_latest_reading() if hasattr(self, "_orderbook") else None
            ob_str = f" | OB={ob_reading.imbalance:+.3f}" if ob_reading else ""
            log.info(
                "[SNIPE] ARMED T-%.0fs | %s $%.2f | delta=%+.4f%% (%s)%s",
                remaining, window.asset.upper(), live_btc, delta * 100, current_dir.upper(), ob_str,
            )

            # Reversal: direction flipped with strong sustained opposition
            if current_dir != direction and abs(delta) > self._effective_threshold():
                reversal_count = 0
                for d in reversed(sig._recent_dirs):
                    if d == current_dir:
                        reversal_count += 1
                    else:
                        break
                if reversal_count >= 3:
                    log.warning(
                        "[SNIPE] REVERSAL %s->%s (delta=%+.3f%%, %d ticks) | Cancelling GTC",
                        direction.upper(), current_dir.upper(), delta * 100, reversal_count,
                    )
                    partial = self.pyramid.finalize_partial_fill()
                    self.pyramid.cancel_pending_order()
                    if self.pyramid.waves_fired > 0:
                        # We have partial fills — hold through resolution
                        self._state = SnipeState.EXECUTING
                        log.info("[SNIPE] Reversal but have fills — holding")
                    else:
                        self.pyramid.close_position()
                        self._state = SnipeState.IDLE
                    return


    def _on_executing(self) -> None:
        """Wait for resolution. Paper mode: resolve via BTC price. Live: check CLOB API."""
        now = time.time()
        market_id = self._current_window_id
        elapsed = now - self._executing_since if self._executing_since > 0 else 0

        # Check window end time if still in tracker
        window = self.window_tracker.get_window(market_id)
        if window:
            remaining = window.end_ts - now
            if remaining > -30:
                return  # Wait for window to finish + 30s settle time

        # Need at least 30s after entering EXECUTING
        if elapsed < 30:
            return

        # Paper mode: resolve using BTC price comparison
        if self._dry_run and self.pyramid.has_active_position:
            live_btc = self._fetch_btc_price()
            if live_btc and self.pyramid._active_position:
                open_price = self.pyramid._active_position.open_price
                delta = (live_btc - open_price) / open_price if open_price > 0 else 0
                resolved_dir = "up" if delta > 0 else "down"
                result = self.pyramid.close_position(resolved_dir)
                if result:
                    self._record_outcome(result)
                self._cooldown_until = now + 30
                self._state = SnipeState.COOLDOWN
                log.info(
                    "[SNIPE] PAPER RESOLVED: %s (BTC $%.0f vs open $%.0f, delta=%+.3f%%)",
                    resolved_dir.upper(), live_btc, open_price, delta * 100,
                )
                return

        # Live mode: Check CLOB for actual resolution
        resolved_dir = self._fetch_resolution(market_id)
        if resolved_dir:
            result = self.pyramid.close_position(resolved_dir)
            if result:
                self._record_outcome(result)
            self._cooldown_until = now + 30
            self._state = SnipeState.COOLDOWN
            log.info("[SNIPE] EXECUTING -> COOLDOWN (resolved=%s)", resolved_dir.upper())
            return

        # Timeout: 10 min after entering EXECUTING
        if elapsed >= 600:
            log.warning("[SNIPE] Resolution timeout for %s (%.0fs elapsed)", market_id[:12], elapsed)
            self.pyramid.close_position()
            self._cooldown_until = now + 30
            self._state = SnipeState.COOLDOWN
            return

    def _finish_trade(self) -> None:
        """Clean up current trade and go to cooldown."""
        market_id = self._current_window_id
        resolved_dir = self._fetch_resolution(market_id) or "" if market_id else ""

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

    def _fetch_btc_price(self) -> float | None:
        """Fetch live BTC price directly — bypasses stale PriceCache."""
        try:
            resp = requests.get(
                "https://api.binance.us/api/v3/ticker/price?symbol=BTCUSDT",
                timeout=5,
            )
            if resp.status_code == 200:
                return float(resp.json()["price"])
        except Exception:
            pass
        # Fallback to cache
        return self._cache.get_price("bitcoin")

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

    def _compute_success_rate_50(self) -> float | None:
        """Compute win rate from last 50 resolved trades."""
        history = self.pyramid.get_history(50)
        if not history:
            return None
        wins = sum(1 for t in history if t.get("won") is True)
        resolved = sum(1 for t in history if t.get("won") is not None)
        if resolved == 0:
            return None
        return round(wins / resolved * 100, 1)

    def get_status(self) -> dict:
        """Dashboard-friendly status."""
        threshold = self._effective_threshold()
        is_weekend = datetime.now(ET).weekday() >= 5
        return {
            "enabled": self.enabled,
            "state": self._state.value,
            "dry_run": self._dry_run,
            "budget_per_window": self.pyramid._budget,
            "budget_base": self._base_budget,
            "budget_escalated": self._escalated_budget,
            "consecutive_wins": self._consecutive_wins,
            "delta_threshold_pct": round(threshold * 100, 3),
            "threshold_mode": "weekend" if is_weekend else "weekday",
            "tick_interval_s": SNIPE_TICK_INTERVAL,
            "stats": self._stats.copy(),
            "window": self.window_tracker.get_status(),
            "position": self.pyramid.get_status(),
            "history": self.pyramid.get_history(10),
            "orderbook": self._orderbook.get_status() if hasattr(self, "_orderbook") else None,
            # v7: scoring engine + candle structure + latency
            "scorer": self._scorer.get_status(),
            "candles": self._candle_store.get_status(),
            "success_rate_50": self._compute_success_rate_50(),
            "avg_latency_ms": self.pyramid.get_avg_latency_ms(),
            "timestamp": time.time(),
        }
