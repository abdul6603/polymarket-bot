"""Snipe Engine v8 — Multi-Asset Hybrid Snipe with MTF Confirmation.

State machine per asset slot:
  IDLE -> TRACKING -> ARMED -> EXECUTING -> COOLDOWN -> IDLE

Runs in its own background thread (independent of Garves's main event loop).
Ticks every 2s. BTC/ETH/SOL/XRP — 4 independent state machines.

v8 upgrade: Multi-asset scanning with cross-asset correlation.
5m scanner detects direction, MTF gate confirms on 15m/1h structure,
executes on 15m/1h markets where real liquidity exists.

Max 3 concurrent positions. Correlation bonus when 3/4+ assets align.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
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
from bot.snipe.timing_learner import TimingLearner
from bot.snipe.timing_assistant import TimingAssistant
from bot.snipe.mtf_gate import check_mtf
from bot.snipe.correlation import evaluate_correlation

log = logging.getLogger("garves.snipe")

ET = ZoneInfo("America/New_York")

# Tick interval in seconds — 2s gives ~22 ticks per 180s snipe zone
SNIPE_TICK_INTERVAL = 2

# Weekend pre-futures: low vol, lower threshold to catch small moves
WEEKEND_PREFUTURES_THRESHOLD = 0.0007  # 0.070%
FUTURES_THRESHOLD = 0.0008             # 0.080%

# Minimum CLOB implied price to enter
MIN_IMPLIED_PRICE = 0.40

# Delta confirmation threshold
DELTA_CONFIRM_THRESHOLD = 0.0005  # 0.05%

# Liquidity Seeker thresholds
LIQ_SPREAD_MAX = 0.40
LIQ_ASK_MAX = 0.65
LIQ_DEPTH_MIN = 20
LIQ_MONITOR_S = 90
LIQ_SPREAD_COMPRESSION = 0.60

# Multi-asset config
ASSETS = ("bitcoin", "ethereum", "solana", "xrp")
BINANCE_SYMBOLS = {
    "bitcoin": "BTCUSDT",
    "ethereum": "ETHUSDT",
    "solana": "SOLUSDT",
    "xrp": "XRPUSDT",
}
ASSET_CONFIG = {
    "bitcoin":  {"threshold": 75, "budget": 25.0},
    "ethereum": {"threshold": 73, "budget": 25.0},
    "solana":   {"threshold": 72, "budget": 20.0},
    "xrp":      {"threshold": 72, "budget": 20.0},
}
MAX_CONCURRENT_POSITIONS = 3


class SnipeState(Enum):
    IDLE = "idle"
    TRACKING = "tracking"
    ARMED = "armed"
    EXECUTING = "executing"
    COOLDOWN = "cooldown"


@dataclass
class AssetSlot:
    """Per-asset state machine with independent executor."""
    asset: str
    state: SnipeState = SnipeState.IDLE
    current_window_id: str = ""
    executor: PyramidExecutor = field(default=None)
    scorer: SignalScorer = field(default=None)
    liquidity_ignited: bool = False
    liquidity_monitor_start: float = 0.0
    initial_spread: float = 0.98
    ignition_failures: dict = field(default_factory=dict)
    ignition_bypassed: bool = False  # True when 5m book dead, scoring proceeds via MTF
    cooldown_until: float = 0.0
    executing_since: float = 0.0
    last_score: float = 0.0
    last_direction: str = ""
    # MTF execution info
    exec_market_id: str = ""
    exec_end_ts: float = 0.0
    exec_timeframe: str = ""


class SnipeEngine:
    """Main multi-asset snipe orchestrator with MTF confirmation."""

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
        self._delta_signals: dict[str, DeltaSignal] = {}

        # Per-asset slots with independent executors + scorers
        self._slots: dict[str, AssetSlot] = {}
        for asset in ASSETS:
            ac = ASSET_CONFIG.get(asset, {"threshold": 75, "budget": 25.0})
            slot = AssetSlot(asset=asset)
            slot.executor = PyramidExecutor(
                cfg, clob_client, dry_run=dry_run,
                budget_per_window=ac["budget"],
            )
            slot.scorer = SignalScorer(threshold=65)
            self._slots[asset] = slot

        self._orderbook = OrderBookSignal()
        self._orderbook.start()

        # Candle structure (shared, but feed_tick per-asset is safe)
        self._candle_store = CandleStore()
        self._scorer = SignalScorer(threshold=65)  # Legacy compat

        # Thread pool for parallel per-asset ticking
        self._tick_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="snipe-tick")
        self._timing_lock = threading.Lock()

        # Execution preference: "15m" or "1h"
        self._exec_preference = "15m"

        self._base_budget = 25.0
        self._escalated_budget = 40.0
        self._consecutive_wins = 0
        self._stats = {
            "signals": 0, "trades": 0, "wins": 0,
            "losses": 0, "pnl": 0.0, "total_invested": 0.0,
        }
        self._correlation_result = None

        self._status_file = Path(__file__).parent.parent.parent / "data" / "snipe_status.json"
        self.enabled = getattr(cfg, "snipe_enabled", True)

        # Timing Assistant
        self._timing_learner = TimingLearner()
        self._timing_assistant = TimingAssistant(self._timing_learner)

    def _effective_threshold(self) -> float:
        """Dynamic threshold based on CME futures session."""
        now = datetime.now(ET)
        day = now.weekday()
        hour_min = now.hour * 100 + now.minute
        if day == 5:
            return WEEKEND_PREFUTURES_THRESHOLD
        if day == 4 and hour_min >= 1630:
            return WEEKEND_PREFUTURES_THRESHOLD
        if day == 6 and hour_min < 1800:
            return WEEKEND_PREFUTURES_THRESHOLD
        return FUTURES_THRESHOLD

    def _get_signal(self, asset: str) -> DeltaSignal:
        """Get or create per-asset delta signal tracker."""
        threshold = self._effective_threshold()
        if asset not in self._delta_signals:
            self._delta_signals[asset] = DeltaSignal(threshold=threshold)
        else:
            self._delta_signals[asset]._threshold = threshold
        return self._delta_signals[asset]

    def _active_position_count(self) -> int:
        """Count how many slots currently have active positions."""
        return sum(
            1 for s in self._slots.values()
            if s.state in (SnipeState.ARMED, SnipeState.EXECUTING)
        )

    async def run_loop(self, shutdown_event: asyncio.Event) -> None:
        """Snipe loop — runs in its own thread."""
        if not self.enabled:
            log.info("[SNIPE] Engine disabled")
            return

        threshold = self._effective_threshold()
        is_weekend = datetime.now(ET).weekday() >= 5
        log.info(
            "[SNIPE] Engine v8 started | multi-asset (BTC/ETH/SOL/XRP) | "
            "exec_pref=%s | threshold=%.3f%% (%s) | "
            "max_positions=%d | tick=%ds | dry_run=%s | orderbook=%s",
            self._exec_preference, threshold * 100,
            "weekend" if is_weekend else "weekday",
            MAX_CONCURRENT_POSITIONS, SNIPE_TICK_INTERVAL,
            self._dry_run,
            "connected" if self._orderbook.is_connected else "disconnected",
        )

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._thread_loop, shutdown_event)

    def _thread_loop(self, shutdown_event: asyncio.Event) -> None:
        """Blocking loop running in a background thread."""
        while not shutdown_event.is_set():
            try:
                self.tick()
                self._save_status()
            except Exception as e:
                log.warning("[SNIPE] Tick error: %s", str(e)[:200])

            for _ in range(SNIPE_TICK_INTERVAL):
                if shutdown_event.is_set():
                    break
                time.sleep(1)

        log.info("[SNIPE] Engine stopped")

    def tick(self) -> None:
        """Single tick — feed candles, then tick all 4 asset slots in parallel."""
        tick_start = time.time()

        # Phase 1: Fetch prices + feed candles (sequential, fast ~1s for 4 Binance calls)
        self._live_prices: dict[str, float] = {}
        for asset in ASSETS:
            price = self._fetch_live_price(asset)
            if price:
                self._live_prices[asset] = price
                self._candle_store.feed_tick(asset, price)

        # Phase 2: Tick all 4 asset slots in PARALLEL (the heavy part — CLOB calls)
        futures = {}
        for asset, slot in self._slots.items():
            futures[self._tick_pool.submit(self._tick_slot, slot)] = asset

        for future in as_completed(futures, timeout=15):
            try:
                future.result()
            except Exception as e:
                asset = futures[future]
                log.warning("[SNIPE] %s tick error: %s", asset.upper(), str(e)[:150])

        # Phase 3: Collect scores and evaluate correlation
        tick_scores: dict[str, tuple[str, float]] = {}
        for asset, slot in self._slots.items():
            if slot.last_score > 0 and slot.last_direction:
                tick_scores[asset] = (slot.last_direction, slot.last_score)

        if tick_scores:
            self._correlation_result = evaluate_correlation(
                tick_scores, self._active_position_count(),
            )

        elapsed = time.time() - tick_start
        if elapsed > 5.0:
            log.warning("[SNIPE] Tick took %.1fs (target <5s)", elapsed)

    def _tick_slot(self, slot: AssetSlot) -> None:
        """Tick a single asset's state machine."""
        now = time.time()

        if slot.state == SnipeState.IDLE:
            self._slot_on_idle(slot)
        elif slot.state == SnipeState.TRACKING:
            self._slot_on_tracking(slot)
        elif slot.state == SnipeState.ARMED:
            self._slot_on_armed(slot)
        elif slot.state == SnipeState.EXECUTING:
            self._slot_on_executing(slot)
        elif slot.state == SnipeState.COOLDOWN:
            if now > slot.cooldown_until:
                slot.state = SnipeState.IDLE
                log.info("[SNIPE] %s: Cooldown done -> IDLE", slot.asset.upper())

    def _slot_on_idle(self, slot: AssetSlot) -> None:
        """Wait for windows to enter snipe zone."""
        now = time.time()
        for w in self.window_tracker.all_active_windows():
            if w.traded or w.asset != slot.asset:
                continue
            remaining = w.end_ts - now
            if 30 < remaining <= 240:
                slot.state = SnipeState.TRACKING
                # Reset per-slot trackers
                if slot.asset in self._delta_signals:
                    self._delta_signals[slot.asset].reset()
                slot.scorer.reset_spread_history()
                slot.liquidity_ignited = False
                slot.ignition_bypassed = False
                slot.liquidity_monitor_start = time.time()
                slot.initial_spread = 0.98
                slot.last_score = 0.0
                slot.last_direction = ""
                threshold = self._effective_threshold()
                log.info(
                    "[SNIPE] %s: IDLE -> TRACKING (T-%.0fs, thresh=%.3f%%)",
                    slot.asset.upper(), remaining, threshold * 100,
                )
                return

    def _slot_on_tracking(self, slot: AssetSlot) -> None:
        """Evaluate windows with 10-component scoring + MTF gate."""
        now = time.time()
        asset = slot.asset

        # Use pre-fetched price from main tick loop (no duplicate Binance call)
        live_price = self._live_prices.get(asset)

        candidates = []
        for w in self.window_tracker.all_active_windows():
            if w.traded or w.asset != asset:
                continue
            remaining = w.end_ts - now
            if remaining <= 0 or remaining > 190:
                continue
            price = live_price if live_price else self._cache.get_price(w.asset)
            if not price or w.open_price <= 0:
                continue
            delta = (price - w.open_price) / w.open_price
            candidates.append((w, price, delta, remaining))

        if not candidates:
            slot.state = SnipeState.IDLE
            slot.last_score = 0.0
            slot.last_direction = ""
            return

        candidates.sort(key=lambda x: abs(x[2]), reverse=True)

        # Log top candidate
        remaining_top = candidates[0][3]
        if remaining_top <= 245:
            log.info(
                "[SNIPE] %s T-%.0fs | $%.2f | delta=%+.4f%%",
                asset.upper(), remaining_top, candidates[0][1],
                candidates[0][2] * 100,
            )

        # Liquidity Seeker gate — try to detect 5m CLOB ignition,
        # but don't block scoring. v8 routes to 15m/1h via MTF gate.
        if not slot.liquidity_ignited and not slot.ignition_bypassed:
            best_w, best_p, best_d, best_r = candidates[0]
            if abs(best_d) >= DELTA_CONFIRM_THRESHOLD:
                direction = "up" if best_d > 0 else "down"
                liq_token = best_w.up_token_id if direction == "up" else best_w.down_token_id

                liq_book = clob_book.get_orderbook(liq_token)
                if self._check_liquidity_ignition(liq_book, slot):
                    slot.liquidity_ignited = True
                    slot.ignition_failures[liq_token] = 0

            if not slot.liquidity_ignited:
                elapsed = time.time() - slot.liquidity_monitor_start
                # 30s grace period — then bypass to scoring (MTF will route to 15m/1h)
                if elapsed > 30:
                    if candidates:
                        best_w = candidates[0][0]
                        best_d = candidates[0][2]
                        dir_str = "up" if best_d > 0 else "down"
                        fail_token = best_w.up_token_id if dir_str == "up" else best_w.down_token_id
                        slot.ignition_failures[fail_token] = slot.ignition_failures.get(fail_token, 0) + 1
                    slot.ignition_bypassed = True  # Don't re-check, proceed to scoring
                    log.info(
                        "[IGNITION] %s: No 5m ignition after %.0fs — bypassing to scorer (MTF route)",
                        asset.upper(), elapsed,
                    )
                    # Fall through to scoring below
                else:
                    return  # Keep monitoring ignition (first 30s)

        # Gather Binance L2 data
        ob_signal = self._orderbook.get_signal(asset) if self._orderbook.is_connected else None
        ob_reading = self._orderbook.get_latest_reading(asset) if self._orderbook.is_connected else None

        # Gather SMC structure
        structure_5m = self._candle_store.get_structure(asset, "5m")
        structure_15m = self._candle_store.get_structure(asset, "15m")

        from bot.snipe.pyramid_executor import WAVES

        for window, price, delta, remaining in candidates:
            # Timing guard — allow more time when ignition bypassed (scoring via MTF)
            max_remaining = 240 if (slot.liquidity_ignited or slot.ignition_bypassed) else 180
            if remaining > max_remaining or remaining < 5:
                continue

            # Track delta direction
            sig_tracker = self._get_signal(window.asset)
            sig_tracker.evaluate(price, window.open_price, remaining)

            abs_delta = abs(delta)
            if abs_delta < DELTA_CONFIRM_THRESHOLD:
                continue

            direction = "up" if delta > 0 else "down"

            # Count sustained ticks
            sustained = 0
            for d in reversed(sig_tracker._recent_dirs):
                if d == direction:
                    sustained += 1
                else:
                    break

            token_id = window.up_token_id if direction == "up" else window.down_token_id
            opp_token_id = window.down_token_id if direction == "up" else window.up_token_id

            # Compute max price cap from pyramid wave schedule
            max_cap = WAVES[0][2]
            for _, _, cap, fire_below in WAVES:
                if remaining <= fire_below:
                    max_cap = cap

            # When ignition bypassed (5m book dead), skip CLOB API calls
            # — dead books return spread=0.98 every time, just wastes 3-4s
            if slot.ignition_bypassed:
                implied = 0.50  # Default for dead books
                target_book = None
                opp_book = None
            else:
                implied = self._fetch_implied_price(window.market_id, token_id)
                if not implied:
                    continue
                if implied < MIN_IMPLIED_PRICE:
                    continue
                if implied > max_cap:
                    continue
                target_book = clob_book.get_orderbook(token_id)
                opp_book = clob_book.get_orderbook(opp_token_id)

            # Score all 10 components (per-slot scorer for thread safety)
            score_result = slot.scorer.score(
                direction=direction,
                delta_pct=abs_delta * 100,
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

            # Update slot tracking
            slot.last_score = score_result.total_score
            slot.last_direction = direction

            # Timing Assistant (lock for thread safety)
            with self._timing_lock:
                self._timing_assistant.evaluate({
                    "score_result": score_result,
                    "clob_book": target_book,
                    "remaining_s": remaining,
                    "direction": direction,
                    "regime": "neutral",
                    "implied_price": implied,
                })

            if not score_result.should_trade:
                continue

            # Apply correlation bonus
            bonus = 0.0
            if self._correlation_result and self._correlation_result.score_bonus > 0:
                bonus = self._correlation_result.score_bonus
                score_result = score_result  # Score already computed, bonus is informational

            # ── MTF Gate: check 15m/1h structure confirmation ──
            mtf_result = check_mtf(
                asset, direction, self._candle_store, self._exec_preference,
            )

            # Determine execution market and token IDs
            exec_market_id = window.market_id
            exec_token_id = token_id
            exec_end_ts = window.end_ts
            exec_timeframe = "5m"

            if mtf_result.confirmed and mtf_result.exec_market:
                # Execute on higher timeframe market (real liquidity)
                emkt = mtf_result.exec_market
                exec_market_id = emkt.market_id
                exec_token_id = emkt.up_token_id if direction == "up" else emkt.down_token_id
                exec_end_ts = emkt.end_ts
                exec_timeframe = emkt.timeframe
                log.info(
                    "[SNIPE] %s: MTF confirmed → executing on %s market %s",
                    asset.upper(), exec_timeframe, exec_market_id[:16],
                )
            elif not mtf_result.confirmed:
                if slot.ignition_bypassed:
                    # 5m book dead AND MTF rejected — no viable execution venue
                    log.info(
                        "[SNIPE] %s: MTF rejected (%s) + 5m dead — no execution venue, skipping",
                        asset.upper(), mtf_result.strength,
                    )
                    continue
                # MTF gate rejected — fall back to 5m execution (existing behavior)
                log.info(
                    "[SNIPE] %s: MTF not confirmed (%s) — falling back to 5m execution",
                    asset.upper(), mtf_result.strength,
                )

            # Check max concurrent positions
            if self._active_position_count() >= MAX_CONCURRENT_POSITIONS:
                log.info(
                    "[SNIPE] %s: Max positions (%d/%d), skipping",
                    asset.upper(), self._active_position_count(),
                    MAX_CONCURRENT_POSITIONS,
                )
                return

            # Fill simulation
            shares_est = int(slot.executor._budget / max_cap)
            fill_est = estimate_fill(exec_token_id, max_cap, shares_est)
            log.info("[SNIPE] %s: FILL SIM: %s", asset.upper(), fill_est.detail)

            self._stats["signals"] += 1
            log.info(
                "[SNIPE] SIGNAL: %s %s | score=%.0f/100 (+%.0f corr) | "
                "delta=%+.4f%% | sustained=%d | T-%.0fs | exec=%s",
                asset.upper(), direction.upper(), score_result.total_score,
                bonus, delta * 100, sustained, remaining, exec_timeframe,
            )

            # Execute
            slot.current_window_id = exec_market_id
            slot.exec_market_id = exec_market_id
            slot.exec_end_ts = exec_end_ts
            slot.exec_timeframe = exec_timeframe

            # Apply MTF size multiplier and correlation sizing
            size_mult = mtf_result.size_multiplier if mtf_result.confirmed else 1.0
            if self._correlation_result:
                size_mult *= self._correlation_result.size_multiplier
            slot.executor._budget = ASSET_CONFIG.get(asset, {}).get("budget", 25.0) * size_mult

            slot.executor.start_position(
                exec_market_id, direction, window.open_price, asset,
                score=score_result.total_score,
                score_breakdown={k: v["weighted"] for k, v in score_result.components.items()},
            )

            # Fetch implied price for execution market token
            exec_implied = implied  # default to 5m implied
            if exec_market_id != window.market_id:
                exec_implied = self._fetch_implied_price(exec_market_id, exec_token_id)
                if not exec_implied:
                    exec_implied = implied  # Fallback

            result = slot.executor.execute_wave(
                1, exec_token_id, exec_implied,
                score=score_result.total_score,
                book_data=target_book,
                liquidity_confirmed=slot.liquidity_ignited,
            )
            if result:
                log.info(
                    "[SNIPE] %s: FILLED | T-%.0fs | %s | $%.2f | %.0f shares @ $%.3f | "
                    "exec=%s | score=%.0f",
                    asset.upper(), remaining, direction.upper(),
                    result.size_usd, result.shares, result.price,
                    exec_timeframe, score_result.total_score,
                )
                slot.state = SnipeState.EXECUTING
                slot.executing_since = time.time()
                self.window_tracker.mark_traded(window.market_id)
                log.info("[SNIPE] %s: TRACKING -> EXECUTING (filled on %s)", asset.upper(), exec_timeframe)
                return
            elif slot.executor.has_pending_order:
                slot.state = SnipeState.ARMED
                self.window_tracker.mark_traded(window.market_id)
                log.info("[SNIPE] %s: TRACKING -> ARMED (resting)", asset.upper())
                return
            else:
                if slot.liquidity_ignited:
                    log.info("[IGNITION] %s: FOK failed despite confirmed liquidity", asset.upper())
                else:
                    log.warning("[SNIPE] %s: Order failed — trying next", asset.upper())
                slot.executor.close_position()
                continue

    def _slot_on_armed(self, slot: AssetSlot) -> None:
        """Monitor resting GTC order for fills."""
        window = self.window_tracker.get_window(slot.current_window_id)
        # Use exec_end_ts for resolution timing (may be 15m/1h market)
        end_ts = slot.exec_end_ts if slot.exec_end_ts > 0 else (window.end_ts if window else 0)

        if not window and not slot.exec_end_ts:
            slot.executor.cancel_pending_order()
            self._finish_trade(slot)
            return

        now = time.time()
        remaining = end_ts - now

        if remaining <= 0:
            slot.executor.cancel_pending_order()
            self._finish_trade(slot)
            return

        # Cancel at T-5s
        if remaining <= 5:
            slot.executor.finalize_partial_fill()
            slot.executor.cancel_pending_order()
            if slot.executor.has_active_position and slot.executor.waves_fired > 0:
                slot.state = SnipeState.EXECUTING
                slot.executing_since = time.time()
                log.info("[SNIPE] %s: T-5s — have fills, holding through resolution", slot.asset.upper())
            else:
                slot.executor.close_position()
                slot.state = SnipeState.IDLE
                log.info("[SNIPE] %s: T-5s — no fills, cancelled", slot.asset.upper())
            return

        # Poll for fill
        if slot.executor.has_pending_order:
            fill = slot.executor.poll_pending_order()
            if fill:
                log.info(
                    "[SNIPE] %s: GTC FILLED | T-%.0fs | %s | $%.2f",
                    slot.asset.upper(), remaining, fill.direction.upper(), fill.size_usd,
                )
                slot.state = SnipeState.EXECUTING
                slot.executing_since = time.time()
                log.info("[SNIPE] %s: ARMED -> EXECUTING (GTC filled)", slot.asset.upper())
                return
        elif not slot.executor.has_active_position:
            slot.state = SnipeState.IDLE
            return

        # Reversal detection
        live_price = self._fetch_live_price(slot.asset)
        if live_price and slot.executor.has_active_position and window and window.open_price > 0:
            direction = slot.executor.active_direction
            delta = (live_price - window.open_price) / window.open_price
            current_dir = "up" if delta > 0 else "down"

            sig = self._get_signal(slot.asset)
            sig._recent_dirs.append(current_dir)

            if current_dir != direction and abs(delta) > self._effective_threshold():
                reversal_count = 0
                for d in reversed(sig._recent_dirs):
                    if d == current_dir:
                        reversal_count += 1
                    else:
                        break
                if reversal_count >= 3:
                    log.warning(
                        "[SNIPE] %s: REVERSAL %s->%s (delta=%+.3f%%) | Cancelling",
                        slot.asset.upper(), direction.upper(), current_dir.upper(),
                        delta * 100,
                    )
                    slot.executor.finalize_partial_fill()
                    slot.executor.cancel_pending_order()
                    if slot.executor.waves_fired > 0:
                        slot.state = SnipeState.EXECUTING
                        slot.executing_since = time.time()
                    else:
                        slot.executor.close_position()
                        slot.state = SnipeState.IDLE
                    return

    def _slot_on_executing(self, slot: AssetSlot) -> None:
        """Wait for resolution — use exec_end_ts for timing."""
        now = time.time()
        elapsed = now - slot.executing_since if slot.executing_since > 0 else 0

        # Use execution market end time (may be 15m/1h)
        end_ts = slot.exec_end_ts
        if end_ts > 0 and now < end_ts + 30:
            return  # Wait for market to close + 30s settle

        # Also check via window tracker
        window = self.window_tracker.get_window(slot.current_window_id)
        if window:
            remaining = window.end_ts - now
            if remaining > -30:
                return

        if elapsed < 30:
            return

        # Paper mode: resolve using asset price
        if self._dry_run and slot.executor.has_active_position:
            live_price = self._fetch_live_price(slot.asset)
            if live_price and slot.executor._active_position:
                open_price = slot.executor._active_position.open_price
                delta = (live_price - open_price) / open_price if open_price > 0 else 0
                resolved_dir = "up" if delta > 0 else "down"
                result = slot.executor.close_position(resolved_dir)
                if result:
                    self._record_outcome(result, slot)
                slot.cooldown_until = now + 30
                slot.state = SnipeState.COOLDOWN
                slot.exec_end_ts = 0
                slot.exec_timeframe = ""
                log.info(
                    "[SNIPE] %s: PAPER RESOLVED: %s (delta=%+.3f%%)",
                    slot.asset.upper(), resolved_dir.upper(), delta * 100,
                )
                return

        # Live mode: Check CLOB for resolution
        resolved_dir = self._fetch_resolution(slot.current_window_id)
        if resolved_dir:
            result = slot.executor.close_position(resolved_dir)
            if result:
                self._record_outcome(result, slot)
            slot.cooldown_until = now + 30
            slot.state = SnipeState.COOLDOWN
            slot.exec_end_ts = 0
            slot.exec_timeframe = ""
            log.info("[SNIPE] %s: EXECUTING -> COOLDOWN (%s)", slot.asset.upper(), resolved_dir.upper())
            return

        # Timeout: 20 min for 15m markets, 70 min for 1h, 10 min for 5m
        timeout_map = {"15m": 1200, "1h": 4200, "5m": 600}
        timeout = timeout_map.get(slot.exec_timeframe, 600)
        if elapsed >= timeout:
            log.warning("[SNIPE] %s: Resolution timeout (%.0fs)", slot.asset.upper(), elapsed)
            slot.executor.close_position()
            slot.cooldown_until = now + 30
            slot.state = SnipeState.COOLDOWN
            slot.exec_end_ts = 0
            slot.exec_timeframe = ""
            return

    def _check_liquidity_ignition(self, book: dict | None, slot: AssetSlot) -> bool:
        """Check if CLOB book shows real mid-price liquidity."""
        if not book:
            return False

        spread = book.get("spread", 1.0)
        best_ask = book.get("best_ask", 0)
        buy_pressure = book.get("buy_pressure", 0)
        sell_pressure = book.get("sell_pressure", 0)

        compression = 1.0 - (spread / slot.initial_spread) if slot.initial_spread > 0 else 0
        spread_ok = spread < LIQ_SPREAD_MAX or compression >= LIQ_SPREAD_COMPRESSION
        if not spread_ok:
            return False

        if best_ask <= 0 or best_ask >= LIQ_ASK_MAX:
            return False
        depth = int(sell_pressure / best_ask) if best_ask > 0 else 0
        if depth < LIQ_DEPTH_MIN:
            return False

        if buy_pressure <= 0:
            return False

        log.info(
            "[IGNITION] %s: Liquidity detected! spread=$%.3f (%.0f%% compressed) | "
            "ask=$%.3f depth=%d | buy_pressure=%.1f",
            slot.asset.upper(), spread, compression * 100, best_ask, depth, buy_pressure,
        )
        return True

    def _finish_trade(self, slot: AssetSlot) -> None:
        """Clean up trade and go to cooldown."""
        market_id = slot.current_window_id
        resolved_dir = self._fetch_resolution(market_id) or "" if market_id else ""
        result = slot.executor.close_position(resolved_dir)
        if result:
            self._record_outcome(result, slot)
        slot.cooldown_until = time.time() + 30
        slot.state = SnipeState.COOLDOWN
        slot.exec_end_ts = 0
        slot.exec_timeframe = ""

    def _fetch_resolution(self, market_id: str) -> str | None:
        """Check CLOB API for actual market resolution."""
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

    def _record_outcome(self, result: dict, slot: AssetSlot) -> None:
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

        # Timing Assistant
        try:
            last_rec = self._timing_assistant.get_last_recommendation()
            t_score = last_rec.timing_score if last_rec else 0
            t_size = last_rec.recommended_size_pct if last_rec else 1.0
            self._timing_assistant.record_outcome(
                agent="garves_snipe",
                direction=result.get("direction", ""),
                won=result.get("won", False),
                timing_score=t_score,
                size_pct=t_size,
                pnl_usd=result.get("pnl_usd", 0.0),
            )
        except Exception:
            pass

        # Budget escalation
        if self._consecutive_wins >= 3:
            for s in self._slots.values():
                s.executor._budget = self._escalated_budget
        else:
            for s in self._slots.values():
                ac = ASSET_CONFIG.get(s.asset, {"budget": 25.0})
                s.executor._budget = ac["budget"]

        # Event bus
        try:
            from shared.events import publish
            publish(
                agent="garves",
                event_type="snipe_trade_resolved",
                data=result,
                summary=(
                    f"Snipe {result['direction'].upper()} {slot.asset.upper()} "
                    f"${result['total_size_usd']:.2f} -> "
                    f"PnL ${result['pnl_usd']:+.2f} (exec={slot.exec_timeframe or '5m'})"
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
                won = result.get("won")
                emoji = "W" if won else "L" if won is False else "?"
                asset_name = result.get("asset", slot.asset).upper()
                msg = (
                    f"GARVES SNIPE [{emoji}]\n\n"
                    f"{result['direction'].upper()} {asset_name} {slot.exec_timeframe or '5m'}\n"
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

    def _fetch_live_price(self, asset: str) -> float | None:
        """Fetch live price for any asset — bypasses stale PriceCache."""
        symbol = BINANCE_SYMBOLS.get(asset)
        if not symbol:
            return self._cache.get_price(asset)
        try:
            resp = requests.get(
                f"https://api.binance.us/api/v3/ticker/price?symbol={symbol}",
                timeout=5,
            )
            if resp.status_code == 200:
                return float(resp.json()["price"])
        except Exception:
            pass
        return self._cache.get_price(asset)

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
        """Compute win rate from last 50 resolved trades (across all slots)."""
        # Use first slot's executor for history (all write to same file)
        first_slot = next(iter(self._slots.values()), None)
        if not first_slot:
            return None
        history = first_slot.executor.get_history(50)
        if not history:
            return None
        wins = sum(1 for t in history if t.get("won") is True)
        resolved = sum(1 for t in history if t.get("won") is not None)
        if resolved == 0:
            return None
        return round(wins / resolved * 100, 1)

    def get_status(self) -> dict:
        """Dashboard-friendly status — multi-asset with per-slot info."""
        threshold = self._effective_threshold()
        is_weekend = datetime.now(ET).weekday() >= 5

        # Per-asset slot status
        slots_status = {}
        for asset, slot in self._slots.items():
            slots_status[asset] = {
                "state": slot.state.value,
                "last_score": slot.last_score,
                "last_direction": slot.last_direction,
                "exec_timeframe": slot.exec_timeframe,
                "position": slot.executor.get_status(),
                "liquidity_ignited": slot.liquidity_ignited,
            }

        # Hot windows: all assets in TRACKING with scores
        hot_windows = []
        for asset, slot in self._slots.items():
            if slot.state == SnipeState.TRACKING and slot.last_score > 0:
                hot_windows.append({
                    "asset": asset,
                    "direction": slot.last_direction,
                    "score": slot.last_score,
                    "state": slot.state.value,
                })

        # Correlation
        corr = None
        if self._correlation_result:
            cr = self._correlation_result
            corr = {
                "dominant_direction": cr.dominant_direction,
                "aligned_count": cr.aligned_count,
                "total_scored": cr.total_scored,
                "score_bonus": cr.score_bonus,
                "size_multiplier": cr.size_multiplier,
            }

        # Use first slot for shared history/perf
        first_slot = next(iter(self._slots.values()), None)
        history = first_slot.executor.get_history(10) if first_slot else []
        performance = first_slot.executor.get_performance_stats() if first_slot else {}
        avg_latency = first_slot.executor.get_avg_latency_ms() if first_slot else None

        return {
            "enabled": self.enabled,
            "version": "v8-multi-asset",
            "dry_run": self._dry_run,
            "exec_preference": self._exec_preference,
            "delta_threshold_pct": round(threshold * 100, 3),
            "threshold_mode": "weekend" if is_weekend else "weekday",
            "tick_interval_s": SNIPE_TICK_INTERVAL,
            "max_positions": MAX_CONCURRENT_POSITIONS,
            "active_positions": self._active_position_count(),
            "consecutive_wins": self._consecutive_wins,
            "stats": self._stats.copy(),
            "slots": slots_status,
            "hot_windows": hot_windows,
            "correlation": corr,
            "window": self.window_tracker.get_status(),
            "history": history,
            "orderbook": self._orderbook.get_status() if hasattr(self, "_orderbook") else None,
            "scorer": self._scorer.get_status(),  # Legacy global scorer status
            "candles": self._candle_store.get_status(),
            "candle_warmup": self._candle_store.get_warmup_status(),
            "success_rate_50": self._compute_success_rate_50(),
            "avg_latency_ms": avg_latency,
            "performance": performance,
            "timing_assistant": self._timing_assistant.get_status(),
            "timestamp": time.time(),
        }

    # Legacy compat properties
    @property
    def pyramid(self):
        """Legacy compat — return first slot's executor."""
        return next(iter(self._slots.values())).executor if self._slots else None
