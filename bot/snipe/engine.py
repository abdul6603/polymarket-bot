"""Snipe Engine v10 — BTC Flow Scanner + 15m/1h Execution.

State machine:
  IDLE -> TRACKING -> ARMED -> EXECUTING -> COOLDOWN -> IDLE

Runs in its own background thread (independent of Garves's main event loop).
Ticks every 2s. BTC only — single state machine.

5m windows provide high-resolution flow detection. When signal fires
(flow strong + score >=75 + implied <=0.52), execution routes to the
active 15m or 1h market where real liquidity exists.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
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
from bot.snipe.candle_store import CandleStore, PERIODS as CANDLE_PERIODS
from bot.snipe.signal_scorer import SignalScorer
from bot.snipe import clob_book
from bot.snipe.fill_simulator import estimate_fill
from bot.snipe.timing_learner import TimingLearner
from bot.snipe.timing_assistant import TimingAssistant
from bot.snipe.flow_detector import FlowDetector, FlowResult
from bot.snipe.market_bridge import find_execution_market
from bot.snipe.resolution_scalper import ResolutionScalper

log = logging.getLogger("garves.snipe")

ET = ZoneInfo("America/New_York")

# Tick interval in seconds — 2s gives ~22 ticks per 180s snipe zone
SNIPE_TICK_INTERVAL = 2

# Weekend pre-futures: low vol, lower threshold to catch small moves
WEEKEND_PREFUTURES_THRESHOLD = 0.0007  # 0.070%
FUTURES_THRESHOLD = 0.0008             # 0.080%

# Minimum CLOB implied price to enter
MIN_IMPLIED_PRICE = 0.40
MAX_IMPLIED_PRICE = 0.55  # Never buy above $0.55 — break-even WR = 55%

# Delta confirmation threshold
DELTA_CONFIRM_THRESHOLD = 0.0005  # 0.05%

# Liquidity Seeker thresholds
LIQ_SPREAD_MAX = 0.40
LIQ_ASK_MAX = 0.65
LIQ_DEPTH_MIN = 20
LIQ_MONITOR_S = 90
LIQ_SPREAD_COMPRESSION = 0.60

# BTC-only config — v10 flow scanner + MTF execution
ASSETS = ("bitcoin",)
SNIPE_ASSET_BLACKLIST = set()  # BTC is the only asset
BINANCE_SYMBOLS = {
    "bitcoin": "BTCUSDT",
    "ethereum": "ETHUSDT",
    "solana": "SOLUSDT",
    "xrp": "XRPUSDT",
}

# All assets that need live prices (flow scanner + resolution scalper)
PRICE_ASSETS = ("bitcoin", "ethereum", "solana", "xrp")
ASSET_CONFIG = {
    "bitcoin": {"base_threshold": 60},
}

# Default execution timeframe — 5m scanner signals execute on 15m/1h markets
DEFAULT_EXEC_TF = "15m"  # "15m" or "1h" — where real liquidity lives

# Max implied price for entry — tighter gate for flow sniper
MAX_IMPLIED_FLOW = 0.52  # Never buy above $0.52 — good risk/reward

# CLOB data-quality thresholds (good data = lower bar, dead data = higher bar)
CLOB_QUALITY_GOOD_SPREAD_COMPRESSION = 0.50
CLOB_QUALITY_GOOD_DEPTH_MIN = 20
CLOB_QUALITY_GOOD_BUY_PRESSURE_MIN = 0.5
CLOB_QUALITY_THRESHOLD_BONUS = -3         # lower by 3 when CLOB good (good liq → easier trigger)
CLOB_QUALITY_THRESHOLD_PENALTY_DEAD = 8   # raise by 8 when CLOB dead (BTC:70, ETH/SOL:66)
CLOB_QUALITY_THRESHOLD_PENALTY_STALE = 5  # raise by 5 when CLOB stale (BTC:67, ETH/SOL:63)
THRESHOLD_OVERRIDE_FILE = Path(__file__).parent.parent.parent / "data" / "snipe_threshold_override.json"
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
    """BTC-only flow anticipation snipe engine with two-stage firing."""

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

        # BTC-only slot with executor + scorer
        self._snipe_budget = cfg.order_size_usd  # from .env ORDER_SIZE_USD
        self._slots: dict[str, AssetSlot] = {}
        for asset in ASSETS:
            ac = ASSET_CONFIG.get(asset, {"base_threshold": 75})
            slot = AssetSlot(asset=asset)
            slot.executor = PyramidExecutor(
                cfg, clob_client, dry_run=dry_run,
                budget_per_window=self._snipe_budget,
            )
            slot.scorer = SignalScorer(threshold=ac["base_threshold"])
            self._slots[asset] = slot

        self._orderbook = OrderBookSignal()
        self._orderbook.start()

        # Candle structure
        self._candle_store = CandleStore()
        self._scorer = SignalScorer(threshold=75)  # Legacy compat

        # Data-quality-aware threshold state
        self._last_threshold_log: dict[str, float] = {}
        self._last_threshold_value: dict[str, int] = {}
        self._threshold_override: int | None = None
        self._threshold_override_expires: float = 0.0

        self._last_tick_elapsed = 0.0
        self._timing_lock = threading.Lock()

        # Flow detector — core of v9 strategy
        self._flow_detector = FlowDetector()

        self._base_budget = self._snipe_budget
        self._escalated_budget = self._snipe_budget * 1.5
        self._consecutive_wins = 0
        self._stats = {
            "signals": 0, "trades": 0, "wins": 0,
            "losses": 0, "pnl": 0.0, "total_invested": 0.0,
        }

        self._status_file = Path(__file__).parent.parent.parent / "data" / "snipe_status.json"
        self.enabled = getattr(cfg, "snipe_enabled", True)

        # Timing Assistant
        self._timing_learner = TimingLearner()
        self._timing_assistant = TimingAssistant(self._timing_learner)

        # Resolution Scalper — Engine #2 (last 15-90s of 5m windows)
        self._resolution_scalper = ResolutionScalper(
            cfg=cfg,
            price_cache=price_cache,
            window_tracker=self.window_tracker,
            orderbook_signal=self._orderbook,
            clob_client=clob_client,
            dry_run=dry_run,
            bankroll=cfg.bankroll_usd,
        )

        # Warm-up tracking
        self._engine_start_ts = time.time()
        self._last_warmup_log = 0.0
        self._warmup_notified = False

        # Execution routing tracking (for dashboard)
        self._last_exec_routing: dict = {}

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
            "[SNIPE] Engine v10 started | 5m scanner → %s execution | "
            "threshold=%.3f%% (%s) | "
            "max_positions=%d | tick=%ds | dry_run=%s | orderbook=%s",
            DEFAULT_EXEC_TF,
            threshold * 100,
            "weekend" if is_weekend else "weekday",
            MAX_CONCURRENT_POSITIONS, SNIPE_TICK_INTERVAL,
            self._dry_run,
            "connected" if self._orderbook.is_connected else "disconnected",
        )

        self._preload_candles()

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._thread_loop, shutdown_event)

    def _preload_candles(self) -> None:
        """Pre-load 50 historical candles per asset/timeframe from Binance REST.

        Eliminates the cold-start blind spot where volume_spike and BOS/CHoCH
        indicators have no data for 25-300 minutes after a restart.
        """
        tf_to_interval = {"5m": "5m", "15m": "15m", "1h": "1h"}
        for asset in ASSETS:
            symbol = BINANCE_SYMBOLS.get(asset)
            if not symbol:
                continue
            for tf, interval in tf_to_interval.items():
                try:
                    resp = requests.get(
                        "https://api.binance.us/api/v3/klines",
                        params={"symbol": symbol, "interval": interval, "limit": 51},
                        timeout=10,
                    )
                    if resp.status_code != 200:
                        log.warning("[PRELOAD] %s/%s: HTTP %d", asset.upper(), tf, resp.status_code)
                        continue
                    raw = resp.json()
                    # Exclude last candle (still open)
                    klines = [
                        {
                            "timestamp": k[0] / 1000.0,
                            "open": k[1],
                            "high": k[2],
                            "low": k[3],
                            "close": k[4],
                        }
                        for k in raw[:-1]
                    ]
                    count = self._candle_store.seed_from_klines(asset, tf, klines)
                    log.info("[PRELOAD] %s/%s: seeded %d candles", asset.upper(), tf, count)
                except Exception as e:
                    log.warning("[PRELOAD] %s/%s: %s", asset.upper(), tf, str(e)[:120])

    def _thread_loop(self, shutdown_event: asyncio.Event) -> None:
        """Blocking loop running in a background thread."""
        while not shutdown_event.is_set():
            try:
                self.tick()
                self._save_status()
                self._warmup_log()
            except Exception as e:
                log.warning("[SNIPE] Tick error: %s", str(e)[:200])

            for _ in range(SNIPE_TICK_INTERVAL):
                if shutdown_event.is_set():
                    break
                time.sleep(1)

        log.info("[SNIPE] Engine stopped")

    def tick(self) -> None:
        """Single tick — fetch BTC price, then tick the single BTC slot directly."""
        tick_start = time.time()

        # Phase 1: Fetch BTC price — WS cache first, REST fallback if stale (>12s)
        self._live_prices: dict[str, float] = {}
        self._price_sources: dict[str, str] = {}
        stale_threshold = 12.0
        for asset in PRICE_ASSETS:
            age = self._cache.get_price_age(asset)
            if age <= stale_threshold:
                price = self._cache.get_price(asset)
                self._price_sources[asset] = "ws"
            else:
                price = self._fetch_live_price(asset)
                self._price_sources[asset] = "rest"
                if age < float("inf"):
                    log.warning(
                        "[PRICE] %s: PriceCache stale (%.1fs old) — REST fallback",
                        asset.upper(), age,
                    )
            if price:
                self._live_prices[asset] = price
                self._candle_store.feed_tick(asset, price)

        # Phase 2: Tick BTC slot directly (single asset, no thread pool needed)
        for asset, slot in self._slots.items():
            try:
                self._tick_slot(slot)
            except Exception as e:
                log.warning("[SNIPE] %s tick error: %s", asset.upper(), str(e)[:150])

        # Phase 3: Resolution Scalper — scans windows with 15-90s remaining
        try:
            self._resolution_scalper.tick(self._live_prices)
        except Exception as e:
            log.warning("[RES-SCALP] tick error: %s", str(e)[:150])

        elapsed = time.time() - tick_start
        self._last_tick_elapsed = elapsed
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
        """Wait for windows to enter snipe zone — enter at T-300 (window start)."""
        if slot.asset in SNIPE_ASSET_BLACKLIST:
            return
        now = time.time()
        for w in self.window_tracker.all_active_windows():
            if w.traded or w.asset != slot.asset:
                continue
            remaining = w.end_ts - now
            if 30 < remaining <= 300:
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
                # Reset flow detector for new window
                self._flow_detector.reset()
                threshold = self._effective_threshold()
                log.info(
                    "[SNIPE] %s: IDLE -> TRACKING (T-%.0fs, thresh=%.3f%%)",
                    slot.asset.upper(), remaining, threshold * 100,
                )
                return

    def _slot_on_tracking(self, slot: AssetSlot) -> None:
        """Evaluate windows with flow detection + score confirmation."""
        asset = slot.asset
        if asset in SNIPE_ASSET_BLACKLIST:
            slot.state = SnipeState.IDLE
            return
        now = time.time()

        # Use pre-fetched price from main tick loop
        live_price = self._live_prices.get(asset)

        candidates = []
        for w in self.window_tracker.all_active_windows():
            if w.traded or w.asset != asset:
                continue
            remaining = w.end_ts - now
            if remaining <= 0 or remaining > 300:
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
        log.info(
            "[SNIPE] %s T-%.0fs | $%.2f | delta=%+.4f%%",
            asset.upper(), remaining_top, candidates[0][1],
            candidates[0][2] * 100,
        )

        # Flow detection — feed BOTH UP and DOWN CLOB books every tick
        best_w = candidates[0][0]
        up_book = clob_book.get_orderbook(best_w.up_token_id)
        down_book = clob_book.get_orderbook(best_w.down_token_id)
        flow = self._flow_detector.feed(up_book, down_book)

        if flow.is_strong:
            log.info(
                "[FLOW] BTC: %s flow detected | strength=%.2f | sustained=%d | %s",
                flow.direction.upper(), flow.strength, flow.sustained_ticks, flow.detail,
            )

        # Gather Binance L2 data
        ob_signal = self._orderbook.get_signal(asset) if self._orderbook.is_connected else None
        ob_reading = self._orderbook.get_latest_reading(asset) if self._orderbook.is_connected else None

        # Gather SMC structure (5m only — no 15m needed for flow sniper)
        structure_5m = self._candle_store.get_structure(asset, "5m")

        from bot.snipe.pyramid_executor import WAVES

        for window, price, delta, remaining in candidates:
            # Can fire during entire window including early flow
            if remaining > 300 or remaining < 5:
                continue

            # Track delta direction
            sig_tracker = self._get_signal(window.asset)
            sig_tracker.evaluate(price, window.open_price, remaining)

            abs_delta = abs(delta)

            # Use flow direction as primary, delta as fallback
            if flow.direction != "none":
                direction = flow.direction
            elif abs_delta >= DELTA_CONFIRM_THRESHOLD:
                direction = "up" if delta > 0 else "down"
            else:
                continue

            # Count sustained delta ticks
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

            # Fetch implied price
            implied = self._fetch_implied_price(window.market_id, token_id)
            if not implied:
                continue
            if implied < MIN_IMPLIED_PRICE:
                continue

            target_book = clob_book.get_orderbook(token_id)
            opp_book = clob_book.get_orderbook(opp_token_id)

            # Data-quality-aware threshold
            dynamic_thresh = self._compute_dynamic_threshold(asset, target_book, slot)
            if asset == "bitcoin":
                dynamic_thresh = max(dynamic_thresh, 65)
            slot.scorer.threshold = dynamic_thresh

            # Score all 9 components with flow data
            score_result = slot.scorer.score(
                direction=direction,
                delta_pct=abs_delta * 100,
                sustained_ticks=sustained,
                ob_imbalance=ob_reading.imbalance if ob_reading else None,
                ob_strength=ob_signal.strength if ob_signal else None,
                clob_book=target_book,
                clob_book_opposite=opp_book,
                structure_5m=structure_5m,
                structure_15m=None,  # Not used in v9
                remaining_s=remaining,
                implied_price=implied,
                flow_strength=flow.strength,
                flow_sustained_ticks=flow.sustained_ticks,
            )

            # Update slot tracking
            slot.last_score = score_result.total_score
            slot.last_direction = direction

            # Timing Assistant
            with self._timing_lock:
                self._timing_assistant.evaluate({
                    "score_result": score_result,
                    "clob_book": target_book,
                    "remaining_s": remaining,
                    "direction": direction,
                    "regime": "neutral",
                    "implied_price": implied,
                })

            # ── Two-stage gate: flow + score + price ──
            if not flow.is_strong:
                continue  # Flow not detected — don't fire
            if not score_result.should_trade:
                continue  # Score below threshold — skip
            if implied > MAX_IMPLIED_FLOW:
                log.info("[SNIPE] %s: implied $%.3f > $%.2f — bad risk/reward, skipping",
                         asset.upper(), implied, MAX_IMPLIED_FLOW)
                continue  # Price too high — bad risk/reward

            # ── Resolve execution market — 15m/1h where real liquidity exists ──
            exec_mkt = find_execution_market("bitcoin", DEFAULT_EXEC_TF)
            if exec_mkt:
                exec_market_id = exec_mkt.market_id
                exec_end_ts = exec_mkt.end_ts
                exec_timeframe = exec_mkt.timeframe
                exec_token_id = exec_mkt.up_token_id if direction == "up" else exec_mkt.down_token_id
                # Re-check implied price on EXECUTION market (not scanner)
                exec_implied = self._fetch_implied_price(exec_market_id, exec_token_id)
                if exec_implied and exec_implied > MAX_IMPLIED_FLOW:
                    log.info("[SNIPE] BTC: exec market implied $%.3f > $%.2f — skipping",
                             exec_implied, MAX_IMPLIED_FLOW)
                    continue
                if exec_implied:
                    implied = exec_implied  # Use execution market's implied for fill pricing
                # Fetch execution market's book for fill simulation
                target_book = clob_book.get_orderbook(exec_token_id)
                log.info(
                    "[SNIPE] BTC: Signal on 5m → Executing on %s | market=%s... | "
                    "direction=%s | implied=$%.3f",
                    exec_timeframe, exec_market_id[:16], direction.upper(), implied,
                )
            else:
                # Fallback to 5m if no 15m/1h market found
                exec_market_id = window.market_id
                exec_token_id = token_id
                exec_end_ts = window.end_ts
                exec_timeframe = "5m"
                log.warning("[SNIPE] BTC: No %s market found — falling back to 5m", DEFAULT_EXEC_TF)

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
                "[SNIPE] SIGNAL: %s %s | score=%.0f/100 | flow=%.2f | "
                "delta=%+.4f%% | sustained=%d | T-%.0fs | exec=%s",
                asset.upper(), direction.upper(), score_result.total_score,
                flow.strength, delta * 100, sustained, remaining, exec_timeframe,
            )

            # Track execution routing for dashboard
            self._last_exec_routing = {
                "scanner_tf": "5m",
                "exec_tf": exec_timeframe,
                "exec_market": exec_market_id[:16],
                "direction": direction,
                "score": score_result.total_score,
                "flow_strength": flow.strength,
                "timestamp": time.time(),
            }

            # Execute
            slot.current_window_id = exec_market_id
            slot.exec_market_id = exec_market_id
            slot.exec_end_ts = exec_end_ts
            slot.exec_timeframe = exec_timeframe

            # Overnight sizing — thin liquidity, less conviction
            size_mult = 1.0
            now_et = datetime.now(ET)
            if 2 <= now_et.hour < 6:
                size_mult *= 0.50
                log.info("[SNIPE] %s: Overnight sizing (2-6AM) — 50%% budget", asset.upper())

            slot.executor._budget = self._snipe_budget * size_mult

            slot.executor.start_position(
                exec_market_id, direction, window.open_price, asset,
                score=score_result.total_score,
                score_breakdown={k: v["weighted"] for k, v in score_result.components.items()},
            )

            result = slot.executor.execute_wave(
                1, exec_token_id, implied,
                score=score_result.total_score,
                book_data=target_book,
                liquidity_confirmed=True,  # Flow detected = liquidity confirmed
            )
            if result:
                log.info(
                    "[SNIPE] %s: FILLED | T-%.0fs | %s | $%.2f | %.0f shares @ $%.3f | "
                    "flow=%.2f | score=%.0f | exec=%s",
                    asset.upper(), remaining, direction.upper(),
                    result.size_usd, result.shares, result.price,
                    flow.strength, score_result.total_score, exec_timeframe,
                )
                slot.state = SnipeState.EXECUTING
                slot.executing_since = time.time()
                self.window_tracker.mark_traded(window.market_id)
                self._resolution_scalper.mark_flow_claimed(window.market_id)
                log.info("[SNIPE] %s: TRACKING -> EXECUTING (flow snipe filled on %s)", asset.upper(), exec_timeframe)
                try:
                    from shared.events import publish, TRADE_EXECUTED
                    publish(
                        agent="garves",
                        event_type=TRADE_EXECUTED,
                        data={
                            "asset": asset.upper(),
                            "direction": direction.upper(),
                            "score": round(score_result.total_score, 1),
                            "size_usd": round(result.size_usd, 2),
                            "shares": round(result.shares, 1),
                            "price": round(result.price, 3),
                            "exec_tf": exec_timeframe,
                            "market_id": exec_market_id[:12],
                            "fill_type": "instant",
                            "flow_strength": round(flow.strength, 2),
                        },
                        summary=(
                            f"FLOW SNIPE {direction.upper()} BTC "
                            f"${result.size_usd:.2f} @ ${result.price:.3f} "
                            f"score={score_result.total_score:.0f} flow={flow.strength:.2f} "
                            f"exec={exec_timeframe}"
                        ),
                    )
                except Exception:
                    pass
                return
            elif slot.executor.has_pending_order:
                slot.state = SnipeState.ARMED
                self.window_tracker.mark_traded(window.market_id)
                self._resolution_scalper.mark_flow_claimed(window.market_id)
                log.info("[SNIPE] %s: TRACKING -> ARMED (resting on %s)", asset.upper(), exec_timeframe)
                return
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
                try:
                    from shared.events import publish, TRADE_EXECUTED
                    publish(
                        agent="garves",
                        event_type=TRADE_EXECUTED,
                        data={
                            "asset": slot.asset.upper(),
                            "direction": fill.direction.upper(),
                            "size_usd": round(fill.size_usd, 2),
                            "market_id": (slot.current_window_id or "")[:12],
                            "fill_type": "gtc",
                        },
                        summary=(
                            f"GTC FILLED {fill.direction.upper()} {slot.asset.upper()} "
                            f"${fill.size_usd:.2f}"
                        ),
                    )
                except Exception:
                    pass
                return
        elif not slot.executor.has_active_position:
            slot.state = SnipeState.IDLE
            return

        # Reversal detection
        live_price = self._live_prices.get(slot.asset) or self._cache.get_price(slot.asset)
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
            live_price = self._live_prices.get(slot.asset) or self._cache.get_price(slot.asset)
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

    # ── Data-Quality-Aware Threshold System ──

    def _assess_clob_quality(self, book: dict | None, slot: AssetSlot) -> str:
        """Assess CLOB data quality for threshold adjustment.

        Returns "good", "stale", or "dead".
        """
        if not book:
            return "dead"

        spread = book.get("spread", 0)
        best_ask = book.get("best_ask", 0)
        buy_pressure = book.get("buy_pressure", 0)
        sell_pressure = book.get("sell_pressure", 0)

        # All zeros = dead
        if spread == 0 and best_ask == 0 and buy_pressure == 0 and sell_pressure == 0:
            return "dead"

        compression = 1.0 - (spread / slot.initial_spread) if slot.initial_spread > 0 else 0
        depth = int(sell_pressure / best_ask) if best_ask > 0 else 0

        if (compression >= CLOB_QUALITY_GOOD_SPREAD_COMPRESSION
                and depth >= CLOB_QUALITY_GOOD_DEPTH_MIN
                and buy_pressure >= CLOB_QUALITY_GOOD_BUY_PRESSURE_MIN):
            return "good"

        return "stale"

    def _compute_dynamic_threshold(self, asset: str, book: dict | None,
                                   slot: AssetSlot) -> int:
        """Compute per-asset threshold based on CLOB data quality.

        Good CLOB data → lower threshold (more confident).
        Dead/stale CLOB → raise threshold (less confident).
        """
        # Check Robotox override first
        self._load_threshold_override()
        if (self._threshold_override is not None
                and time.time() < self._threshold_override_expires):
            threshold = self._threshold_override
            self._log_threshold_change(asset, threshold, "override", book, slot)
            return threshold

        # Clear expired override
        if self._threshold_override is not None and time.time() >= self._threshold_override_expires:
            self._threshold_override = None
            self._threshold_override_expires = 0.0

        base = ASSET_CONFIG.get(asset, {"base_threshold": 75})["base_threshold"]
        quality = self._assess_clob_quality(book, slot)

        if quality == "good":
            adjustment = CLOB_QUALITY_THRESHOLD_BONUS
        elif quality == "dead":
            adjustment = CLOB_QUALITY_THRESHOLD_PENALTY_DEAD
        else:  # stale
            adjustment = CLOB_QUALITY_THRESHOLD_PENALTY_STALE

        threshold = max(55, min(70, base + adjustment))
        self._log_threshold_change(asset, threshold, quality, book, slot)
        return threshold

    def _log_threshold_change(self, asset: str, threshold: int,
                              quality: str, book: dict | None,
                              slot: AssetSlot) -> None:
        """Log threshold changes (rate-limited to prevent spam)."""
        now = time.time()
        prev_value = self._last_threshold_value.get(asset)
        last_log = self._last_threshold_log.get(asset, 0)

        if prev_value == threshold and (now - last_log) < 60:
            return

        self._last_threshold_value[asset] = threshold
        self._last_threshold_log[asset] = now

        if book and quality != "dead":
            spread = book.get("spread", 0)
            buy_pressure = book.get("buy_pressure", 0)
            sell_pressure = book.get("sell_pressure", 0)
            best_ask = book.get("best_ask", 0)
            compression = 1.0 - (spread / slot.initial_spread) if slot.initial_spread > 0 else 0
            depth = int(sell_pressure / best_ask) if best_ask > 0 else 0
            log.info(
                "[THRESHOLD] %s: threshold=%d — %s CLOB (compression=%.0f%%, depth=%d, bp=%.1f)",
                asset.upper(), threshold, quality, compression * 100, depth, buy_pressure,
            )
        else:
            log.info("[THRESHOLD] %s: threshold=%d — %s CLOB", asset.upper(), threshold, quality)

    def _load_threshold_override(self) -> None:
        """Load Robotox threshold override from file if present."""
        if self._threshold_override is not None and time.time() < self._threshold_override_expires:
            return  # Already loaded and valid

        try:
            if THRESHOLD_OVERRIDE_FILE.exists():
                data = json.loads(THRESHOLD_OVERRIDE_FILE.read_text())
                expires = data.get("expires", 0)
                if time.time() < expires:
                    self._threshold_override = int(data.get("threshold", 55))
                    self._threshold_override_expires = expires
                    log.info(
                        "[THRESHOLD] Override active: threshold=%d (expires in %.0fm, reason=%s)",
                        self._threshold_override,
                        (expires - time.time()) / 60,
                        data.get("reason", "unknown"),
                    )
                else:
                    # Expired — clean up
                    self._threshold_override = None
                    self._threshold_override_expires = 0.0
                    try:
                        THRESHOLD_OVERRIDE_FILE.unlink()
                    except OSError:
                        pass
        except Exception:
            pass  # Parse errors — ignore, use normal threshold

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

        # Cross-write to central trades.jsonl for learning systems
        try:
            from bot.trade_logger import append_normalized_trade
            append_normalized_trade(
                asset=result.get("asset", slot.asset),
                direction=result.get("direction", ""),
                won=result.get("won", False),
                pnl=result.get("pnl_usd", 0),
                size_usd=result.get("total_size_usd", 0),
                entry_price=result.get("avg_entry", 0),
                timeframe="5m",
                engine="snipe",
                trade_id=f"snipe-{result.get('market_id', '')[:12]}_{int(result.get('timestamp', 0))}",
                dry_run=getattr(self, '_dry_run', True),
            )
        except Exception:
            pass

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
                s.executor._budget = self._snipe_budget

        # Event bus
        try:
            from shared.events import publish
            publish(
                agent="garves",
                event_type="snipe_trade_resolved",
                data=result,
                summary=(
                    f"Flow Snipe {result['direction'].upper()} BTC "
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
                won = result.get("won")
                asset_name = result.get("asset", slot.asset).upper()
                pnl = result['pnl_usd']
                _tf = slot.exec_timeframe or '5m'
                _dir = result['direction'].upper()
                _total_trades = self._stats['wins'] + self._stats['losses']
                _wr = (self._stats['wins'] / _total_trades * 100) if _total_trades > 0 else 0
                if won:
                    _icon = "\U0001f7e2"  # green
                    _result = "WIN"
                elif won is False:
                    _icon = "\U0001f534"  # red
                    _result = "LOSS"
                else:
                    _icon = "\u2753"
                    _result = "PENDING"
                msg = (
                    f"\U0001f3af *GARVES FLOW SNIPE* \u2014 {_icon} *{_result}*\n"
                    f"\n"
                    f"{_dir} {asset_name} / {_tf}\n"
                    f"\U0001f30a Waves: {result['waves']} | Invested: ${result['total_size_usd']:.2f}\n"
                    f"\U0001f4c9 Avg Entry: ${result['avg_entry']:.3f}\n"
                    f"\U0001f4b0 P&L: *${pnl:+.2f}*\n"
                    f"\n"
                    f"\U0001f4ca Season: {self._stats['wins']}W-{self._stats['losses']}L "
                    f"({_wr:.0f}%) | Net: ${self._stats['pnl']:+.2f}"
                )
                requests.post(
                    f"https://api.telegram.org/bot{tg_token}/sendMessage",
                    json={"chat_id": tg_chat, "text": msg, "parse_mode": "Markdown"},
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

    def _get_warmup_diagnostics(self) -> dict:
        """Compute warm-up diagnostics: max realistic score, base-value components."""
        elapsed_min = (time.time() - self._engine_start_ts) / 60.0
        warmup = self._candle_store.get_warmup_status()

        # Components stuck at base values
        base_components = []

        # BOS/structure — check if BTC has real structure
        has_5m_structure = any(
            self._candle_store.get_structure(a, "5m").get("trend") != "neutral"
            for a in ASSETS
        )
        has_15m_structure = False  # Not used in v9
        if not has_5m_structure:
            base_components.append(("bos_choch_5m", 1.5, 5))

        # Max realistic score = 100 - (max - base) for each stuck component
        points_lost = sum(mx - base for _, base, mx in base_components)
        max_realistic = round(100 - points_lost)

        # Per-asset thresholds from data-quality system
        per_asset_thresholds = {}
        for a, s in self._slots.items():
            per_asset_thresholds[a] = self._last_threshold_value.get(a, ASSET_CONFIG.get(a, {"base_threshold": 75})["base_threshold"])

        # Use max per-asset threshold for can_reach check
        max_thresh = max(per_asset_thresholds.values()) if per_asset_thresholds else 72

        override_active = (self._threshold_override is not None
                           and time.time() < self._threshold_override_expires)

        return {
            "elapsed_min": round(elapsed_min, 1),
            "target_min": 60,
            "progress_pct": warmup["progress_pct"],
            "max_realistic_score": max_realistic,
            "threshold": max_thresh,
            "threshold_mode": "data_quality",
            "per_asset_thresholds": per_asset_thresholds,
            "threshold_override_active": override_active,
            "can_reach_threshold": max_realistic >= max_thresh,
            "base_components": [
                {"name": n, "current": b, "max": m}
                for n, b, m in base_components
            ],
            "has_5m_structure": has_5m_structure,
            "has_15m_structure": has_15m_structure,
            "clob_bypassed": False,
            "ready": elapsed_min >= 60 and warmup["progress_pct"] >= 80,
        }

    def _warmup_log(self) -> None:
        """Log warm-up progress every 30s. Notify at 60 min."""
        now = time.time()
        if now - self._last_warmup_log < 30:
            return
        self._last_warmup_log = now

        diag = self._get_warmup_diagnostics()
        base_names = ", ".join(c["name"] for c in diag["base_components"])
        clob_note = " | CLOB bypassed" if diag["clob_bypassed"] else ""

        log.info(
            "[WARMUP] %.0f/%.0f min (%d%%) | Max realistic score: ~%d/100 (thresh=%d) | "
            "Base components: %s%s",
            diag["elapsed_min"], diag["target_min"], diag["progress_pct"],
            diag["max_realistic_score"], diag["threshold"],
            base_names or "none", clob_note,
        )

        # Auto-notify at 60 min
        if diag["elapsed_min"] >= 60 and not self._warmup_notified:
            self._warmup_notified = True
            log.info(
                "[WARMUP] === 60 MINUTES REACHED === "
                "CandleStore mature. Max realistic score: %d/100. "
                "Structure: 5m=%s 15m=%s. "
                "Suggest re-evaluating threshold (currently %d). "
                "If CLOB still dead, consider redistributing 20pts from clob_sp+clob_p.",
                diag["max_realistic_score"],
                "YES" if diag["has_5m_structure"] else "NO",
                "YES" if diag["has_15m_structure"] else "NO",
                diag["threshold"],
            )

    def get_status(self) -> dict:
        """Dashboard-friendly status — BTC-only flow sniper."""
        threshold = self._effective_threshold()
        is_weekend = datetime.now(ET).weekday() >= 5

        # BTC slot status
        slots_status = {}
        for asset, slot in self._slots.items():
            slots_status[asset] = {
                "state": slot.state.value,
                "last_score": slot.last_score,
                "last_direction": slot.last_direction,
                "exec_timeframe": slot.exec_timeframe,
                "position": slot.executor.get_status(),
                "threshold": self._last_threshold_value.get(asset, ASSET_CONFIG.get(asset, {"base_threshold": 75})["base_threshold"]),
            }

        # Hot windows
        hot_windows = []
        for asset, slot in self._slots.items():
            if slot.state == SnipeState.TRACKING and slot.last_score > 0:
                hot_windows.append({
                    "asset": asset,
                    "direction": slot.last_direction,
                    "score": slot.last_score,
                    "state": slot.state.value,
                })

        # Use BTC slot for history/perf
        first_slot = next(iter(self._slots.values()), None)
        history = first_slot.executor.get_history(10) if first_slot else []
        performance = first_slot.executor.get_performance_stats() if first_slot else {}
        avg_latency = first_slot.executor.get_avg_latency_ms() if first_slot else None

        return {
            "enabled": self.enabled,
            "version": "v10-flow-scanner-mtf-exec",
            "strategy": "flow_scanner_mtf_exec",
            "default_exec_tf": DEFAULT_EXEC_TF,
            "execution_routing": "5m → " + DEFAULT_EXEC_TF,
            "last_exec_routing": self._last_exec_routing,
            "dry_run": self._dry_run,
            "delta_threshold_pct": round(threshold * 100, 3),
            "threshold_mode": "weekend" if is_weekend else "weekday",
            "tick_interval_s": SNIPE_TICK_INTERVAL,
            "max_positions": MAX_CONCURRENT_POSITIONS,
            "active_positions": self._active_position_count(),
            "consecutive_wins": self._consecutive_wins,
            "stats": self._stats.copy(),
            "slots": slots_status,
            "hot_windows": hot_windows,
            "flow_detector": self._flow_detector.get_status(),
            "window": self.window_tracker.get_status(),
            "history": history,
            "orderbook": self._orderbook.get_status() if hasattr(self, "_orderbook") else None,
            "scorer": self._scorer.get_status(),
            "candles": self._candle_store.get_status(),
            "candle_warmup": self._candle_store.get_warmup_status(),
            "warmup_diagnostics": self._get_warmup_diagnostics(),
            "threshold_info": {
                "mode": "data_quality",
                "per_asset": {
                    a: self._last_threshold_value.get(a, ASSET_CONFIG.get(a, {"base_threshold": 75})["base_threshold"])
                    for a in ASSETS
                },
                "override_active": (self._threshold_override is not None
                                    and time.time() < self._threshold_override_expires),
                "override_value": self._threshold_override,
                "override_ttl_s": max(0, int(self._threshold_override_expires - time.time()))
                    if self._threshold_override is not None else 0,
            },
            "price_freshness": {
                asset: {
                    "age_s": round(self._cache.get_price_age(asset), 1),
                    "source": getattr(self, "_price_sources", {}).get(asset, "unknown"),
                    "stale": self._cache.get_price_age(asset) > 10.0,
                }
                for asset in PRICE_ASSETS
            },
            "success_rate_50": self._compute_success_rate_50(),
            "avg_latency_ms": avg_latency,
            "performance": performance,
            "timing_assistant": self._timing_assistant.get_status(),
            "resolution_scalper": self._resolution_scalper.get_status(),
            "timestamp": time.time(),
        }

    # Legacy compat properties
    @property
    def pyramid(self):
        """Legacy compat — return first slot's executor."""
        return next(iter(self._slots.values())).executor if self._slots else None
