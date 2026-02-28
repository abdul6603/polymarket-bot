"""OdinBot — LLM-powered crypto futures trader (V8 — scalp + swing).

Architecture:
  1. CoinGlass scan  (every 3 min)  — regime detection, opportunity scoring
  2. Trading cycle   (adaptive)     — 30s when scalps open, 5min otherwise
  3. Macro polling   (every 10 min) — SPY/VIX/USDT.D/BTC.D context
  4. Monitor loop    (every 60s)    — paper positions + status JSON

Flow: CoinGlass → Regime → Data Filter → LLM Brain → Safety Rails → Execute
  5. Brotherhood   (every 60s)    — poll event bus for brother intelligence
  6. Reflection    (every 5 trades) — extract lessons from outcomes
  7. Dual mode: SCALP (2-20min, quick TP) + SWING (hours/days, bigger R:R)
"""
from __future__ import annotations

import asyncio
import json
import logging
import signal
import sys
import time
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

import pandas as pd

from odin.config import OdinConfig
from odin.exchange.hyperliquid_client import HyperliquidClient
from odin.exchange.ws_manager import OdinWSManager, WSEvent
from odin.execution.exit_manager import ExitManager
from odin.execution.order_manager import OrderManager
from odin.macro.coinglass import CoinGlassClient, MarketSnapshot
from odin.macro.regime import Direction as RegimeDirection, FundingArbInfo, RegimeBrain, RegimeState
from odin.macro.tracker import MacroDominanceTracker, MacroSignal
from odin.risk.circuit_breaker import CircuitBreaker
from odin.risk.portfolio_guard import PortfolioGuard, coin_tier, notional_cap_for_tier
from odin.risk.position_sizer import PositionSizer
from odin.strategy.multi_tf import MultiTimeframeAnalyzer
from odin.strategy.smc_engine import Direction as SMCDirection
from odin.strategy.signals import TradeSignal
from odin.intelligence.conviction import OdinConvictionEngine
from odin.intelligence.journal import OdinJournal
from odin.intelligence.brotherhood import BrotherhoodBridge
from odin.intelligence.llm_brain import OdinBrain
from odin.strategy.scalp_brain import ScalpBrain
from odin.intelligence.reflection import ReflectionEngine
from odin.intelligence.discord_pipeline import DiscordPipeline
from odin.skills import SkillRegistry
from odin.skills.ob_memory import OBMemory
from odin.skills.eye_vision import EyeVision
from odin.skills.self_evolve import SelfEvolve
from odin.skills.liquidity_raid import LiquidityRaidPredictor
from odin.skills.cross_chain_arb import CrossChainArbScout
from odin.skills.sentiment_fusion import SentimentFusion
from odin.skills.stop_hunt_sim import StopHuntSimulator
from odin.skills.auto_reporter import AutoReporter
from odin.skills.omnicoin import OmniCoinAnalyzer
from odin.analytics.trade_analyzer import TradeAnalyzer
from odin.analytics.edge_tracker import EdgeTracker
from odin.analytics.conviction_calibrator import ConvictionCalibrator
from odin.execution.slippage_tracker import SlippageTracker
from odin.risk.liquidity_guard import LiquidityGuard
from odin.market_selection import MarketSelector
from odin.debug.health_monitor import HealthMonitor

log = logging.getLogger("odin")

ET = ZoneInfo("America/New_York")

# Fallback majors (used if CoinGlass has no opportunities)
HL_MAJORS = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "SOLUSDT"]

# Top-20 coins allowed for scalp trades (no shitcoins)
SCALP_ALLOWED = {
    "BTC", "ETH", "XRP", "SOL", "BNB", "ADA", "DOGE", "AVAX", "DOT", "LINK",
    "MATIC", "UNI", "LTC", "BCH", "NEAR", "APT", "OP", "ARB", "FIL", "SUI",
}

# CoinGlass detail slots (3 API calls each) — rotate the rest
CG_DETAIL_SLOTS = 4
# How many symbols to analyze per trading cycle (rotate through universe)
SYMBOLS_PER_CYCLE = 8


class OdinBot:
    """Odin — regime-adaptive crypto futures trader (scalp + swing)."""

    def __init__(self, cfg: Optional[OdinConfig] = None):
        self._cfg = cfg or OdinConfig()
        self._running = False

        # Exchange (Hyperliquid)
        self._client = HyperliquidClient(self._cfg)
        self._hl_tradeable: set[str] = set()  # bare symbols on HL

        # CoinGlass — the regime brain
        self._cg = CoinGlassClient(
            api_key=self._cfg.coinglass_api_key,
            top_n=self._cfg.top_coins_count,
        ) if self._cfg.coinglass_api_key else None
        self._regime_brain = RegimeBrain()
        self._last_regime: Optional[RegimeState] = None
        self._last_snapshot: Optional[MarketSnapshot] = None

        # Strategy (SMC for entry timing)
        self._analyzer = MultiTimeframeAnalyzer(
            htf_label=self._cfg.htf,
            mtf_label=self._cfg.mtf,
            ltf_label=self._cfg.ltf,
        )

        # Legacy macro (SPY/VIX — still useful context)
        self._macro = MacroDominanceTracker(
            data_dir=self._cfg.data_dir / "macro"
        )
        self._last_macro: Optional[MacroSignal] = None

        # Risk
        self._sizer = PositionSizer(
            risk_per_trade_usd=self._cfg.risk_per_trade_usd,
            risk_per_trade_pct=self._cfg.risk_per_trade_pct,
            max_leverage=self._cfg.max_leverage,
            default_leverage=self._cfg.default_leverage,
            max_exposure_pct=self._cfg.max_exposure_pct,
        )
        self._breaker = CircuitBreaker(
            starting_capital=self._cfg.starting_capital,
            max_consecutive_losses=self._cfg.max_consecutive_losses,
            max_daily_loss_pct=self._cfg.max_daily_loss_pct,
            max_weekly_loss_pct=self._cfg.max_weekly_loss_pct,
            max_monthly_dd_pct=self._cfg.max_monthly_dd_pct,
            max_total_dd_pct=self._cfg.max_total_dd_pct,
            state_file=self._cfg.data_dir / "circuit_breaker.json",
        )

        # Portfolio Guard (Phase 2)
        self._portfolio_guard = PortfolioGuard(cfg=self._cfg)

        # CoinGlass rotation state — rotate which coins get detailed data
        self._cg_detail_offset = 0
        self._analysis_offset = 0  # Rotate which symbols get analyzed per cycle

        # Exit management
        self._exit_mgr = ExitManager(
            trail_atr_mult=self._cfg.trail_atr_multiplier,
            trail_breakeven_r=self._cfg.trail_breakeven_r,
            trail_activate_r=self._cfg.trail_activate_r,
            partial_tp1_pct=self._cfg.partial_tp1_pct,
            partial_tp1_r=self._cfg.partial_tp1_r,
            partial_tp2_pct=self._cfg.partial_tp2_pct,
            partial_tp2_r=self._cfg.partial_tp2_r,
            partial_tp3_r=self._cfg.partial_tp3_r,
            max_stale_hours=self._cfg.max_stale_hours,
            stale_threshold_r=self._cfg.stale_threshold_r,
            regime_chop_mult=self._cfg.exit_regime_chop_mult,
            regime_trend_mult=self._cfg.exit_regime_trend_mult,
        )

        # Execution
        self._order_mgr = OrderManager(
            client=self._client if not self._cfg.dry_run else None,
            dry_run=self._cfg.dry_run,
            data_dir=self._cfg.data_dir,
            exit_manager=self._exit_mgr,
            paper_fee_rate=self._cfg.paper_fee_rate,
        )

        # WebSocket manager (Phase 3)
        self._ws: Optional[OdinWSManager] = None
        self._ws_prices: dict[str, float] = {}  # WS-cached prices (bare symbol → price)
        self._ws_last_exit_check: float = 0.0
        self._price_snapshots: list[dict[str, float]] = []  # WS price history for alt movers

        # Scalp sniper — real-time pump/dump detection
        self._sniper_prices: dict[str, list[tuple[float, float]]] = {}  # coin → [(ts, price), ...]
        self._sniper_cooldown: dict[str, float] = {}  # coin → last trigger time
        self._sniper_last_scan: float = 0.0

        # Intelligence
        self._journal = OdinJournal()
        self._brotherhood = BrotherhoodBridge()
        self._conviction = OdinConvictionEngine(self._journal, self._brotherhood)

        # LLM Brain (V7) — replaces rule-based SMC + conviction scoring
        self._brain = OdinBrain(self._cfg)

        # Scalp Brain (V8) — rule-based, no LLM, instant decisions
        self._scalp_brain = ScalpBrain(
            base_risk_usd=20.0,  # $20 base → $2-5K positions with 0.3-1.5% SL
            min_score=55,
        )
        self._reflection = ReflectionEngine(self._journal, self._brain)
        self._reflection.configure(
            reflect_every_n=self._cfg.reflection_every_n,
            max_lessons=self._cfg.max_active_lessons,
        )
        # Load existing lessons into brain
        self._brain.set_lessons(self._reflection.get_active_lessons())

        # Discord Intelligence Pipeline
        self._discord_pipeline = DiscordPipeline(
            brotherhood=self._brotherhood,
            order_mgr=self._order_mgr,
            sizer=self._sizer,
            breaker=self._breaker,
            cfg=self._cfg,
        )
        self._brotherhood.attach_discord_pipeline(self._discord_pipeline)

        # Skills (all 13)
        self._skills = SkillRegistry()
        self._ob_memory = OBMemory(self._cfg.data_dir / "ob_memory.db")
        self._skills.register("ob_memory", self._ob_memory)
        self._eye_vision = EyeVision(self._cfg.data_dir)
        self._skills.register("eye_vision", self._eye_vision)
        self._self_evolve = SelfEvolve(self._cfg.data_dir)
        self._skills.register("self_evolve", self._self_evolve)
        self._liquidity_raid = LiquidityRaidPredictor()
        self._skills.register("liquidity_raid", self._liquidity_raid)
        self._cross_chain_arb = CrossChainArbScout()
        self._skills.register("cross_chain_arb", self._cross_chain_arb)
        self._sentiment_fusion = SentimentFusion()
        self._skills.register("sentiment_fusion", self._sentiment_fusion)
        self._stop_hunt_sim = StopHuntSimulator()
        self._skills.register("stop_hunt_sim", self._stop_hunt_sim)
        self._auto_reporter = AutoReporter(self._cfg.data_dir)
        self._skills.register("auto_reporter", self._auto_reporter)
        self._omnicoin = OmniCoinAnalyzer(self._skills, self._cfg.data_dir)
        self._skills.register("omnicoin", self._omnicoin)
        # Register intelligence modules as skills too
        self._skills.register("conviction", self._conviction)
        self._skills.register("journal", self._journal)
        self._skills.register("brotherhood", self._brotherhood)
        self._skills.register("regime", self._regime_brain)

        # Discipline Layer
        self._trade_analyzer = TradeAnalyzer(self._cfg.data_dir)
        self._slippage_tracker = SlippageTracker(self._cfg.data_dir)
        self._market_selector = MarketSelector(min_score=self._cfg.min_market_score)
        self._conviction_calibrator = ConvictionCalibrator()
        self._liquidity_guard = LiquidityGuard()
        self._health_monitor = HealthMonitor(self._cfg.data_dir)
        self._edge_tracker = EdgeTracker(self._cfg.data_dir)
        self._last_weekly_review: float = 0.0

        # Status
        self._status_file = self._cfg.data_dir / "odin_status.json"
        self._cycle_count = 0
        self._cycle_balance = self._cfg.starting_capital
        self._last_signal: Optional[TradeSignal] = None
        self._start_time = 0.0
        self._cb_logged_until: float = 0.0

    # ── Main Entry ──

    async def run(self) -> None:
        """Start all concurrent loops."""
        self._setup_logging()
        self._running = True
        self._start_time = time.time()

        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._shutdown)

        mode = "PAPER" if self._cfg.dry_run else "LIVE"

        # Fetch Hyperliquid tradeable pairs
        self._hl_tradeable = self._client.get_tradeable_pairs()
        log.info("[INIT] Hyperliquid: %d tradeable pairs", len(self._hl_tradeable))

        log.info("=" * 60)
        log.info("  ODIN V8 — SCALP + SWING TRADER — %s MODE", mode)
        log.info("  Exchange: Hyperliquid (%d pairs) | CoinGlass: %s",
                 len(self._hl_tradeable), "ON" if self._cg else "OFF")
        log.info("  Capital: $%.0f | Risk: 1R cap ($%.0f)",
                 self._cfg.starting_capital, self._cfg.risk_per_trade_usd)
        log.info("  Max positions: %d (scalp=%d, swing=%d) | Cycle: %ds | Universe: %d coins",
                 self._cfg.max_open_positions,
                 self._cfg.scalp_max_positions, self._cfg.swing_max_positions,
                 self._cfg.cycle_seconds, self._cfg.max_priority_coins)
        log.info("  Portfolio Guard: heat=%g%% | same-dir=%d | tiers: $%g/$%g/$%g",
                 self._cfg.portfolio_max_heat_pct, self._cfg.max_same_direction,
                 self._cfg.notional_cap_major, self._cfg.notional_cap_mid,
                 self._cfg.notional_cap_alt)
        log.info("  WebSocket: %s | Scalp Sniper: ON (5s scan)",
                 "ENABLED" if self._cfg.ws_enabled else "OFF")
        log.info("  Skills: %s", ", ".join(self._skills.skill_names))
        log.info("=" * 60)

        # Start WebSocket manager (Phase 3)
        if self._cfg.ws_enabled:
            try:
                self._ws = OdinWSManager(self._cfg, loop=asyncio.get_event_loop())
                self._ws.start()
                log.info("[INIT] WebSocket started")
            except Exception as e:
                log.warning("[INIT] WebSocket failed — falling back to REST: %s", str(e)[:200])
                self._ws = None

        # Dynamic priority: top coins from CoinGlass filtered to HL-tradeable
        priority = self._get_cg_priority()

        # Initial CoinGlass scan
        if self._cg:
            try:
                self._last_snapshot = self._cg.scan_market(priority)
                self._last_regime = self._regime_brain.analyze(self._last_snapshot)
                self._enrich_regime_with_funding()
                log.info("[INIT] Regime: %s (score=%.0f) | %d opportunities",
                         self._last_regime.regime.value,
                         self._last_regime.global_score,
                         len(self._last_regime.opportunities))
            except Exception as e:
                log.warning("[INIT] CoinGlass scan failed: %s", str(e)[:200])

        # Initial macro fetch
        try:
            self._last_macro = self._macro.get_signal()
            log.info("[INIT] Macro: %s score=%d",
                     self._last_macro.regime.value, self._last_macro.score)
        except Exception as e:
            log.warning("[INIT] Macro fetch failed: %s", str(e)[:150])

        self._write_status()

        tasks = [
            asyncio.create_task(self._trading_loop(), name="trading"),
            asyncio.create_task(self._coinglass_loop(), name="coinglass"),
            asyncio.create_task(self._macro_loop(), name="macro"),
            asyncio.create_task(self._monitor_loop(), name="monitor"),
            asyncio.create_task(self._brotherhood_loop(), name="brotherhood"),
            asyncio.create_task(self._health_loop(), name="health"),
            asyncio.create_task(self._scalp_sniper_loop(), name="scalp_sniper"),
        ]
        if self._ws:
            tasks.append(asyncio.create_task(self._ws_event_loop(), name="ws_events"))
            tasks.append(asyncio.create_task(self._ws_health_loop(), name="ws_health"))

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            log.info("Odin tasks cancelled — shutting down")
        finally:
            self._running = False
            self._write_status()
            log.info("Odin stopped.")

    def _shutdown(self) -> None:
        self._running = False
        log.info("Shutdown signal received")
        if self._ws:
            self._ws.stop()
        for task in asyncio.all_tasks():
            if task.get_name() in (
                "trading", "coinglass", "macro", "monitor",
                "brotherhood", "ws_events", "ws_health", "health",
                "scalp_sniper",
            ):
                task.cancel()

    # ── Dynamic Priority ──

    def _get_cg_priority(self) -> list[str]:
        """Top coins for DETAILED CoinGlass data (3 calls each).

        Returns only CG_DETAIL_SLOTS coins. Rotates through top 20 each cycle
        so all coins get detailed data over 5 cycles (15 min).
        BTC + ETH always included. Remaining slots rotate.
        """
        max_coins = self._cfg.max_priority_coins

        if self._last_snapshot and self._last_snapshot.top_symbols:
            tradeable = [
                s for s in self._last_snapshot.top_symbols
                if s in self._hl_tradeable
            ][:max_coins]
        else:
            tradeable = [
                s for s in ["BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "AVAX", "LINK"]
                if s in self._hl_tradeable
            ]

        if not tradeable:
            return ["BTC", "ETH"]

        # Always include BTC + ETH for detailed data
        always = [s for s in ["BTC", "ETH"] if s in tradeable]
        rest = [s for s in tradeable if s not in always]

        # Rotate through the rest for remaining detail slots
        slots = max(0, CG_DETAIL_SLOTS - len(always))
        if rest and slots > 0:
            start = self._cg_detail_offset % max(1, len(rest))
            rotated = rest[start:start + slots]
            if len(rotated) < slots:
                rotated += rest[:slots - len(rotated)]
            self._cg_detail_offset += slots
            return always + rotated

        return always

    def _get_full_universe(self) -> list[str]:
        """Full list of tradeable coins (up to max_priority_coins) for symbol picking."""
        max_coins = self._cfg.max_priority_coins
        if self._last_snapshot and self._last_snapshot.top_symbols:
            return [
                s for s in self._last_snapshot.top_symbols
                if s in self._hl_tradeable
            ][:max_coins]
        return [
            s for s in ["BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "AVAX", "LINK"]
            if s in self._hl_tradeable
        ]

    # ── Funding Arb Enrichment ──

    def _enrich_regime_with_funding(self) -> None:
        """Fetch HL native funding rates and inject arb info into regime state."""
        if not self._last_regime:
            return
        symbols = [s.replace("USDT", "") for s in self._cfg.symbols]
        notional_est = self._cfg.starting_capital * self._cfg.default_leverage
        for sym in symbols:
            try:
                rates = self._client.get_funding_rate(sym)
                rate_8h = rates.get("rate_8h", 0.0)
                arb = self._regime_brain.funding_arb_opportunity(
                    sym, rate_8h, notional_est,
                    min_rate=self._cfg.funding_arb_min_rate,
                )
                self._last_regime.funding_arbs[sym] = arb
            except Exception as e:
                log.debug("[FUNDING] Error enriching %s: %s", sym, str(e)[:100])

    # ── CoinGlass Loop (Regime Brain) ──

    async def _coinglass_loop(self) -> None:
        """Poll CoinGlass every 3 min for regime updates."""
        while self._running:
            await asyncio.sleep(self._cfg.coinglass_poll_seconds)
            if not self._cg:
                continue
            try:
                priority = self._get_cg_priority()
                self._last_snapshot = self._cg.scan_market(priority)
                self._last_regime = self._regime_brain.analyze(self._last_snapshot)
                self._enrich_regime_with_funding()
                log.info(
                    "[CG] Regime: %s (%.0f) | bias=%s | opps=%d | calls=%d/30 | priority=%s",
                    self._last_regime.regime.value,
                    self._last_regime.global_score,
                    self._last_regime.direction_bias.value,
                    len(self._last_regime.opportunities),
                    self._last_snapshot.api_calls_used,
                    ",".join(priority[:4]) + (f"...+{len(priority)-4}" if len(priority) > 4 else ""),
                )
            except Exception as e:
                log.warning("[CG] Scan error: %s", str(e)[:200])

    # ── Trading Loop ──

    async def _trading_loop(self) -> None:
        """Main trading cycle — uses regime brain to pick symbols and direction."""
        while self._running:
            try:
                # Kill switch check — brotherhood-wide emergency stop
                from shared.killswitch import is_killed
                kill_info = is_killed()
                if kill_info:
                    log.warning("[CYCLE] Kill switch active: %s", kill_info.get("reason"))
                    await asyncio.sleep(60)
                    continue

                self._cycle_count += 1

                # Fetch balance once per cycle for safety guards + logging
                self._cycle_balance = self._get_balance()
                log.info("[CYCLE %d] Starting... | Balance: $%.2f",
                         self._cycle_count, self._cycle_balance)

                # Check circuit breaker
                cb_state = self._breaker.check()
                if not cb_state.trading_allowed:
                    # Only log once per pause period to avoid spam
                    pause_ts = getattr(cb_state, "pause_until", 0) or 0
                    if pause_ts != self._cb_logged_until:
                        log.info("[CYCLE] Circuit breaker: %s", cb_state.reason)
                        self._cb_logged_until = pause_ts
                    # Sleep 10 min when paused instead of normal cycle
                    await asyncio.sleep(max(self._cfg.cycle_seconds, 600))
                    continue

                # Check position limits
                open_count = self._order_mgr.get_open_positions_count()
                if open_count >= self._cfg.max_open_positions:
                    log.info("[CYCLE] Max positions (%d) reached", open_count)
                    await asyncio.sleep(cycle_time if 'cycle_time' in dir() else self._cfg.cycle_seconds)
                    continue

                # Get symbols to trade from regime brain
                symbols_to_trade = self._pick_symbols()

                # Pre-fetch MTF candles for the data filter
                _filter_dfs: dict[str, pd.DataFrame] = {}
                for sym, _ in symbols_to_trade:
                    mtf_raw = self._fetch_candles(sym, self._cfg.mtf, 20)
                    if mtf_raw:
                        _filter_dfs[sym] = self._candles_to_df(mtf_raw)

                # Data filter: only send interesting symbols to LLM
                filtered_symbols = self._brain.screen(
                    symbols_to_trade, _filter_dfs,
                    self._last_regime, self._last_macro,
                )

                for symbol in filtered_symbols:
                    if self._order_mgr.get_open_positions_count() >= self._cfg.max_open_positions:
                        break
                    if self._order_mgr.has_position_for_symbol(symbol):
                        continue
                    await self._analyze_and_trade(symbol)

                # Adaptive cycle time: faster when scalps might be needed
                has_scalp = any(
                    p.get("trade_type") == "scalp"
                    for p in (
                        self._order_mgr.get_paper_positions()
                        if self._cfg.dry_run
                        else self._order_mgr.get_live_positions()
                    )
                )
                cycle_time = (
                    self._cfg.scalp_cycle_seconds if has_scalp
                    else self._cfg.cycle_seconds
                )
                log.info("[CYCLE %d] Complete. Next in %ds. (mode=%s)",
                         self._cycle_count, cycle_time,
                         "scalp" if has_scalp else "swing")

                # Signal cycle status for dashboard badge
                try:
                    import json as _json
                    _sc_file = Path(__file__).parent.parent / "data" / "odin_signal_cycle.json"
                    _sc_file.write_text(_json.dumps({
                        "last_eval_at": time.time(),
                        "cycle": self._cycle_count,
                        "cycle_seconds": self._cfg.cycle_seconds,
                        "symbols_scanned": len(symbols_to_trade),
                        "open_positions": self._order_mgr.get_open_positions_count(),
                        "regime": self._last_regime.regime.value if self._last_regime else "unknown",
                        "mode": "scalp" if has_scalp else "swing",
                    }))
                except Exception:
                    pass

            except Exception as e:
                import traceback
                log.error("[CYCLE] Error: %s\n%s", str(e)[:300], traceback.format_exc())

            await asyncio.sleep(self._cfg.cycle_seconds)

    def _pick_symbols(self) -> list[tuple[str, str]]:
        """Pick symbols + direction from regime brain + alt movers.

        Returns [(symbol, direction)] in USDT format.
        Sources:
          1. CoinGlass regime opportunities (directional bias)
          2. Top alt movers from WS prices (scalp candidates — any direction)
        """
        picks: list[tuple[str, str]] = []
        picked_syms: set[str] = set()

        # ── Source 1: CoinGlass regime opportunities ──
        if self._last_regime and self._last_regime.opportunities:
            all_opps = [
                opp for opp in self._last_regime.opportunities
                if opp.symbol in self._hl_tradeable
            ]

            if self._cfg.restrict_to_config_symbols:
                allowed_bare = {s.replace("USDT", "") for s in self._cfg.symbols}
                all_opps = [opp for opp in all_opps if opp.symbol in allowed_bare]

            per_cycle = self._cfg.symbols_per_cycle
            if len(all_opps) > per_cycle:
                start = self._analysis_offset % max(1, len(all_opps))
                windowed = all_opps[start:start + per_cycle]
                if len(windowed) < per_cycle:
                    windowed += all_opps[:per_cycle - len(windowed)]
                self._analysis_offset += per_cycle
                all_opps = windowed

            scored_opps = []
            for opp in all_opps:
                try:
                    cg_metrics = {}
                    if self._last_snapshot:
                        cg_metrics = self._last_snapshot.coins.get(opp.symbol, {}) \
                            if hasattr(self._last_snapshot, "coins") else {}
                    journal_fitness = self._journal.get_journal_fitness(
                        f"{opp.symbol}USDT", opp.direction.value,
                        self._last_regime.regime.value if self._last_regime else "neutral",
                        datetime.now(ET).hour,
                    )
                    regime_data = self._last_regime.to_dict() if self._last_regime else {}
                    ms = self._market_selector.score(
                        opp.symbol, regime_data, cg_metrics, journal_fitness,
                    )
                    if ms.composite >= self._cfg.min_market_score:
                        scored_opps.append((opp, ms.composite))
                    else:
                        log.debug("[MARKET] %s: score=%d SKIP", opp.symbol, ms.composite)
                except Exception:
                    scored_opps.append((opp, 50))

            scored_opps.sort(key=lambda x: x[1], reverse=True)
            for opp, score in scored_opps:
                hl_sym = f"{opp.symbol}USDT"
                picks.append((hl_sym, opp.direction.value))
                picked_syms.add(hl_sym)

        # ── Source 2: Alt movers from WS prices (scalp fuel) ──
        # Scan for coins that just moved — perfect scalp candidates
        alt_movers = self._find_alt_movers()
        slots_left = self._cfg.symbols_per_cycle - len(picks)
        for sym, direction in alt_movers[:max(slots_left, 4)]:
            if sym not in picked_syms and not self._order_mgr.has_position_for_symbol(sym):
                picks.append((sym, direction))
                picked_syms.add(sym)

        # ── Fallback: majors with regime bias ──
        if not picks:
            bias = "NONE"
            if self._last_regime:
                bias = self._last_regime.direction_bias.value
            if bias == "NONE":
                if self._last_macro and self._last_macro.regime.value in ("bull", "strong_bull"):
                    bias = "LONG"
                elif self._last_macro and self._last_macro.regime.value in ("bear",):
                    bias = "SHORT"
                else:
                    # Even in neutral — scan top alts, LLM decides direction
                    universe = self._get_full_universe()
                    for sym_bare in universe[:4]:
                        hl_sym = f"{sym_bare}USDT"
                        if hl_sym not in picked_syms:
                            picks.append((hl_sym, "LONG"))  # LLM will override direction
                    if picks:
                        log.info("[PICK] Neutral regime — scanning %d alts for LLM", len(picks))
                        return picks
                    log.info("[PICK] No symbols to scan")
                    return []

            for sym in HL_MAJORS:
                if not any(p[0] == sym for p in picks):
                    picks.append((sym, bias))

        return picks

    def _find_alt_movers(self) -> list[tuple[str, str]]:
        """Find alts with recent price movement from WS price snapshots.

        Compares current WS price vs 5-min-ago snapshot. Zero API calls.
        Returns [(symbol, direction)] sorted by move magnitude.
        """
        movers: list[tuple[str, str, float]] = []

        # Need at least 3 snapshots (3+ min of data)
        if len(self._price_snapshots) < 3:
            log.info("[ALT-MOVERS] Warming up (%d/3 snapshots)", len(self._price_snapshots))
            return []

        # Compare current prices vs ~5 min ago
        old_snap = self._price_snapshots[-5] if len(self._price_snapshots) >= 5 else self._price_snapshots[0]
        universe = self._get_full_universe()

        for bare in universe:
            # Scalps only on top-20 coins — no shitcoins
            if bare not in SCALP_ALLOWED:
                continue

            current = self._ws_prices.get(bare, 0)
            old_price = old_snap.get(bare, 0)

            if current <= 0 or old_price <= 0:
                continue

            move_pct = (current - old_price) / old_price * 100
            abs_move = abs(move_pct)

            # Scalp-worthy: >= 0.5% move in 5 min
            if abs_move >= 0.5:
                direction = "LONG" if move_pct > 0 else "SHORT"
                movers.append((f"{bare}USDT", direction, abs_move))

        movers.sort(key=lambda x: x[2], reverse=True)
        top5 = ", ".join(f"{m[0].replace('USDT','')} {m[1][0]}{m[2]:.1f}%" for m in movers[:5])
        log.info("[ALT-MOVERS] %d movers (>0.5%%): %s", len(movers), top5 or "none")

        return [(sym, d) for sym, d, _ in movers]

    async def _analyze_and_trade(self, symbol: str) -> None:
        """LLM-powered analysis pipeline for one symbol. Opus decides direction."""
        # Check brotherhood pause
        should_pause, pause_reason = self._brotherhood.should_pause_trading()
        if should_pause:
            log.info("[%s] Brotherhood pause: %s", symbol, pause_reason)
            return

        # Fetch candles (3 timeframes)
        htf_candles = self._fetch_candles(symbol, self._cfg.htf, 200)
        mtf_candles = self._fetch_candles(symbol, self._cfg.mtf, 200)
        ltf_candles = self._fetch_candles(symbol, self._cfg.ltf, 200)

        if not mtf_candles or not ltf_candles:
            log.info("[%s] No candle data (mtf=%s ltf=%s)", symbol,
                     bool(mtf_candles), bool(ltf_candles))
            return

        htf_df = self._candles_to_df(htf_candles) if htf_candles else pd.DataFrame()
        mtf_df = self._candles_to_df(mtf_candles)
        ltf_df = self._candles_to_df(ltf_candles)

        # Scalp-friendly: new alts may lack daily candles — that's OK for scalps
        # Need enough MTF (4H) for structure + LTF (15m) for entry
        if len(mtf_df) < 20 or len(ltf_df) < 15:
            log.info("[%s] Not enough candles (htf=%d mtf=%d ltf=%d)",
                     symbol, len(htf_df), len(mtf_df), len(ltf_df))
            return

        # Current price
        current_price = self._client.get_price(symbol)
        if current_price <= 0:
            current_price = float(ltf_df["close"].iloc[-1])

        # Query OB memory for structure zones near current price
        structure_zones = self._ob_memory.get_active_zones(
            symbol, price_range=(
                current_price * 0.96,
                current_price * 1.04,
            ),
        )

        # Also refresh OB memory from current candles (keep the data fresh)
        try:
            mtf_signal = self._analyzer.analyze(htf_df, mtf_df, ltf_df, current_price)
            if hasattr(mtf_signal, "smc_structure"):
                smc_struct = mtf_signal.smc_structure
            else:
                from odin.strategy.smc_engine import SMCEngine
                _smc = SMCEngine()
                smc_struct = _smc.analyze(mtf_df)
            self._ob_memory.store_patterns(symbol, self._cfg.mtf, {
                "active_obs": [
                    {"price_level": o.price_level, "top": o.top, "bottom": o.bottom,
                     "strength": o.strength, "direction": o.direction,
                     "volume_zscore": o.volume_zscore, "mitigated": o.mitigated,
                     "details": o.details}
                    for o in smc_struct.active_obs
                ],
                "active_fvgs": [
                    {"price_level": f.price_level, "top": f.top, "bottom": f.bottom,
                     "strength": f.strength, "direction": f.direction,
                     "mitigated": f.mitigated}
                    for f in smc_struct.active_fvgs
                ],
            })
            # Refresh zones after storing
            structure_zones = self._ob_memory.get_active_zones(
                symbol, price_range=(current_price * 0.96, current_price * 1.04),
            )
        except Exception as e:
            log.debug("[%s] OB memory refresh error: %s", symbol, str(e)[:100])

        # ── LLM Brain: Claude Opus 4.6 full analysis ──
        balance = self._get_balance()
        open_count = self._order_mgr.get_open_positions_count()

        trade_signal = self._brain.analyze(
            symbol=symbol,
            htf_df=htf_df,
            mtf_df=mtf_df,
            ltf_df=ltf_df,
            current_price=current_price,
            regime=self._last_regime,
            macro=self._last_macro,
            zones=structure_zones,
            brotherhood=self._brotherhood,
            balance=balance,
            open_positions=open_count,
        )

        if trade_signal is None:
            return  # LLM said FLAT or parse failed

        self._last_signal = trade_signal
        direction = trade_signal.direction
        sl = trade_signal.stop_loss
        tp1 = trade_signal.take_profit_1
        tp2 = trade_signal.take_profit_2
        rr = trade_signal.risk_reward

        # Publish directional call to event bus (ALL signals, even if blocked later)
        self._brotherhood.publish_signal({
            "symbol": symbol,
            "direction": direction,
            "conviction_score": trade_signal.conviction_score,
            "tier": "LLM",
            "should_trade": True,
            "entry_price": current_price,
            "regime": self._last_regime.regime.value if self._last_regime else "neutral",
        })

        # Check tradeable (R:R, SL present)
        if not trade_signal.tradeable:
            log.info("[%s] Signal not tradeable: conv=%.0f rr=%.1f",
                     symbol, trade_signal.conviction_score, trade_signal.risk_reward)
            return

        # ── Safety Rails (KEPT UNCHANGED) ──

        # Portfolio Guard
        self._portfolio_guard.update_state(
            balance=balance,
            positions=self._order_mgr.get_paper_positions()
                if self._cfg.dry_run else [],
        )
        tier = coin_tier(symbol)
        tier_cap = notional_cap_for_tier(tier, self._cfg)
        # Use LLM-decided risk if available, otherwise config default
        trade_risk = trade_signal.llm_risk_usd if trade_signal.llm_risk_usd > 0 \
            else self._cfg.risk_per_trade_usd
        trade_type = getattr(trade_signal, "trade_type", "swing")
        guard_decision = self._portfolio_guard.check_trade(
            symbol=symbol,
            direction=direction,
            risk_usd=trade_risk,
            notional_usd=tier_cap,
            trade_type=trade_type,
        )
        if not guard_decision.allowed:
            log.info("[%s] Portfolio Guard BLOCKED: %s",
                     symbol, "; ".join(guard_decision.reasons))
            return

        # Circuit Breaker (per-symbol)
        if self._breaker.is_symbol_blocked(symbol, self._cfg.coin_blacklist_after_losses):
            log.info("[%s] Symbol blocked by circuit breaker (%d consecutive losses)",
                     symbol,
                     self._breaker.state.per_symbol_losses.get(
                         symbol.replace("USDT", "").upper(), 0))
            return

        # Discipline layer scalars
        vol_scalar = self._liquidity_guard.get_volatility_scalar(
            self._last_regime.regime.value if self._last_regime else "neutral",
            atr_percentile=50,
        )
        dd_scalar = self._liquidity_guard.get_drawdown_scalar(
            self._breaker.state.daily_pnl / max(balance, 1) * 100,
            self._breaker.state.weekly_pnl / max(balance, 1) * 100,
        )
        edge_scalar = self._edge_tracker.get_risk_scalar()

        # ── Position Sizing (LLM-driven risk) ──
        # Priority: PortfolioGuard cap > LLM risk > config default
        effective_risk = guard_decision.adjusted_risk_usd or trade_risk
        # Funding arb data for sizer bonus/penalty
        bare = symbol.replace("USDT", "")
        funding_arb = (
            self._last_regime.funding_arbs.get(bare)
            if self._last_regime and self._last_regime.funding_arbs
            else None
        )

        size = self._sizer.calculate(
            balance=balance,
            entry_price=current_price,
            stop_loss=sl,
            confidence=trade_signal.risk_multiplier,
            trade_type=trade_type,
            macro_multiplier=1.0,
            current_exposure=self._order_mgr.get_total_exposure(),
            conviction_score=trade_signal.conviction_score,
            structure_zones=structure_zones,
            direction=direction,
            notional_cap_override=guard_decision.notional_cap or tier_cap,
            risk_override=effective_risk,
            volatility_scalar=vol_scalar,
            drawdown_scalar=dd_scalar,
            edge_scalar=edge_scalar,
            funding_rate_8h=funding_arb.rate_8h if funding_arb else 0.0,
            funding_collect_side=funding_arb.collect_side if funding_arb else "NONE",
            funding_bonus_pct=self._cfg.funding_bonus_pct,
            funding_penalty_pct=self._cfg.funding_penalty_pct,
            funding_arb_min_rate=self._cfg.funding_arb_min_rate,
        )

        if size.notional_usd < 5:
            log.info("[%s] Position too small ($%.2f)", symbol, size.notional_usd)
            return

        # Update SL/TP from sizer's smart placement
        if size.sl_price > 0:
            sl = size.sl_price
            trade_signal.stop_loss = sl
            sl_dist = abs(current_price - sl)
            if direction == "LONG":
                tp1 = round(current_price + sl_dist * self._cfg.target_rr, 2)
                tp2 = round(current_price + sl_dist * (self._cfg.target_rr + 1), 2)
            else:
                tp1 = round(current_price - sl_dist * self._cfg.target_rr, 2)
                tp2 = round(current_price - sl_dist * (self._cfg.target_rr + 1), 2)
            trade_signal.take_profit_1 = tp1
            trade_signal.take_profit_2 = tp2
            rr = self._cfg.target_rr

        log.info(
            "[%s] TRADE: %s %s $%.2f | SL=$%.2f (%s, %.1f%%) TP=$%.2f (R:R %.1f) "
            "| risk=$%.0f notional=$%.0f lev=%dx conv=%.0f/100 | LLM Brain",
            symbol, direction, trade_type.upper(), current_price, sl, size.sl_source,
            size.sl_distance_pct, tp1, rr,
            size.risk_usd, size.notional_usd, size.leverage,
            trade_signal.conviction_score,
        )

        # ── Execution (KEPT UNCHANGED) ──
        pos_id = None
        use_limit = False
        if structure_zones and self._cfg.ws_enabled:
            best_zone = max(structure_zones, key=lambda z: z.strength)
            if best_zone.strength >= 60:
                zone_dist_pct = abs(current_price - best_zone.price_level) / current_price * 100
                if 0.05 < zone_dist_pct < self._cfg.zone_alert_radius_pct:
                    if self._order_mgr.get_pending_count_for_symbol(symbol) < self._cfg.max_pending_per_symbol:
                        use_limit = True
                        log.info("[%s] Using limit entry near %s zone (strength=%.0f, dist=%.2f%%)",
                                 symbol, best_zone.zone_type, best_zone.strength, zone_dist_pct)

        if use_limit and structure_zones:
            best_zone = max(structure_zones, key=lambda z: z.strength)
            if self._cfg.scaled_entry_tranches > 1:
                order_ids = self._order_mgr.execute_scaled_entry(
                    trade_signal, size,
                    zone_top=best_zone.top,
                    zone_bottom=best_zone.bottom,
                    tranches=self._cfg.scaled_entry_tranches,
                    ttl_seconds=self._cfg.limit_order_ttl_seconds,
                )
                pos_id = order_ids[0] if order_ids else None
            else:
                limit_price = best_zone.price_level
                pos_id = self._order_mgr.execute_limit_entry(
                    trade_signal, size, limit_price,
                    ttl_seconds=self._cfg.limit_order_ttl_seconds,
                )
        else:
            pos_id = self._order_mgr.execute_signal(trade_signal, size, balance=self._cycle_balance)

        if pos_id:
            log.info("[%s] Position opened: %s", symbol, pos_id)

            # Record in journal
            decision_id = self._journal.record_trade_open({
                "symbol": symbol, "direction": direction,
                "entry_price": current_price, "stop_loss": sl, "take_profit": tp1,
                "conviction_score": trade_signal.conviction_score,
                "conviction_breakdown": trade_signal.conviction_breakdown,
                "regime": self._last_regime.regime.value if self._last_regime else "neutral",
                "smc_patterns": trade_signal.smc_patterns,
            })
            self._order_mgr.set_position_meta(pos_id, "decision_id", decision_id)

            # Log trade context for reflection engine
            self._reflection.log_trade_context(
                decision_id=decision_id,
                signal=trade_signal,
                regime=self._last_regime,
                macro=self._last_macro,
                candle_summary=self._brain.last_candle_summary,
            )

            # Publish to brotherhood
            self._brotherhood.publish_trade_open({
                "symbol": symbol, "direction": direction,
                "entry_price": current_price,
                "conviction_score": trade_signal.conviction_score,
            })

    # ── Macro Loop (Legacy) ──

    async def _macro_loop(self) -> None:
        while self._running:
            await asyncio.sleep(self._cfg.macro_poll_seconds)
            try:
                self._last_macro = self._macro.get_signal()
                log.info("[MACRO] %s score=%d",
                         self._last_macro.regime.value, self._last_macro.score)
            except Exception as e:
                log.warning("[MACRO] Fetch error: %s", str(e)[:150])

    # ── Monitor Loop ──

    async def _monitor_loop(self) -> None:
        while self._running:
            await asyncio.sleep(self._cfg.status_write_seconds)
            try:
                # Kill switch warning for live mode (don't close positions — TP/SL handles it)
                if not self._cfg.dry_run:
                    from shared.killswitch import is_killed
                    kill_info = is_killed()
                    if kill_info:
                        log.warning("[MONITOR] Kill switch active in LIVE mode: %s "
                                    "(TP/SL triggers remain, no new trades)",
                                    kill_info.get("reason"))

                # REST-based position checks (fallback when WS is not handling it)
                ws_handling_exits = (
                    self._ws and self._ws.connected and self._ws.last_tick_age < 10
                )
                if self._cfg.dry_run and not ws_handling_exits:
                    prices = self._get_current_prices()
                    regime_label = (
                        self._last_regime.regime.value if self._last_regime else "neutral"
                    )
                    closed = self._order_mgr.check_paper_positions(
                        prices, regime_label,
                        funding_arbs=self._last_regime.funding_arbs if self._last_regime else {},
                        funding_extension_hours=self._cfg.funding_stale_extension_hours,
                    )
                    for result in closed:
                        self._handle_closed_position(result)

                    # Check pending order fills
                    self._order_mgr.check_pending_orders(prices)

                # Live position sync — detect TP/SL triggers on exchange
                if not self._cfg.dry_run:
                    live_closed = self._order_mgr.check_live_positions()
                    for result in live_closed:
                        self._handle_closed_position(result)

                # Sweep stale pending orders (both WS and REST paths)
                swept = self._order_mgr.sweep_stale_orders()
                if swept:
                    log.info("[MONITOR] Swept %d expired orders", swept)

                self._check_resets()
                self._write_status()
            except Exception as e:
                log.warning("[MONITOR] Error: %s", str(e)[:150])

    # ── Brotherhood Loop ──

    async def _brotherhood_loop(self) -> None:
        """Poll event bus every 60s for brother intelligence."""
        while self._running:
            await asyncio.sleep(60)
            try:
                summary = self._brotherhood.poll_events()
                if summary.get("events_processed", 0) > 0:
                    log.info("[BROTHERHOOD] Processed %d events",
                             summary["events_processed"])
                # Check for manual discord approvals
                self._discord_pipeline.check_approvals()
            except Exception as e:
                log.debug("[BROTHERHOOD] Error: %s", str(e)[:150])

    # ── WebSocket Event Loop (Phase 3) ──

    async def _ws_event_loop(self) -> None:
        """Process events from WebSocket queue — real-time prices and fills."""
        if not self._ws:
            return
        log.info("[WS-LOOP] Started — processing real-time events")
        while self._running:
            try:
                event: WSEvent = await asyncio.wait_for(
                    self._ws.queue.get(), timeout=5.0,
                )

                if event.channel == "allMids":
                    self._on_ws_prices(event.data)

                elif event.channel == "userFills":
                    self._order_mgr.on_fill(event.data)

                elif event.channel == "orderUpdates":
                    self._order_mgr.on_order_update(event.data)

            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.debug("[WS-LOOP] Error: %s", str(e)[:150])
                await asyncio.sleep(1)

    def _on_ws_prices(self, mids: dict[str, float]) -> None:
        """Handle real-time price update from WS allMids."""
        # Update cached prices (bare + USDT format)
        for coin, price in mids.items():
            self._ws_prices[coin] = price
            self._ws_prices[f"{coin}USDT"] = price

        # ── Scalp sniper: track prices for pump/dump detection ──
        now = time.time()
        self._sniper_track_prices(mids, now)

        # Snapshot prices every 60s for alt mover detection
        if not self._price_snapshots or now - self._price_snapshots[-1].get("_ts", 0) >= 60:
            snap = dict(self._ws_prices)
            snap["_ts"] = now
            self._price_snapshots.append(snap)
            # Keep last 10 snapshots (10 min of history)
            if len(self._price_snapshots) > 10:
                self._price_snapshots = self._price_snapshots[-10:]

        # Real-time exit checks (every WS tick instead of every 60s)
        if now - self._ws_last_exit_check < 2.0:
            return  # Throttle to max every 2 seconds
        self._ws_last_exit_check = now

        if self._cfg.dry_run:
            regime_label = (
                self._last_regime.regime.value if self._last_regime else "neutral"
            )
            try:
                closed = self._order_mgr.check_paper_positions(
                    self._ws_prices, regime_label,
                    funding_arbs=self._last_regime.funding_arbs if self._last_regime else {},
                    funding_extension_hours=self._cfg.funding_stale_extension_hours,
                )
                for result in closed:
                    self._handle_closed_position(result)

                # Also check pending limit order fills
                filled = self._order_mgr.check_pending_orders(self._ws_prices)
                for pos_id in filled:
                    log.info("[WS] Pending order filled → %s", pos_id)
            except Exception as e:
                log.debug("[WS-EXIT] Error: %s", str(e)[:100])
        else:
            # Live mode: check live positions every 10s (not every WS tick)
            if not hasattr(self, "_ws_last_live_check"):
                self._ws_last_live_check = 0.0
            if now - self._ws_last_live_check >= 10.0:
                self._ws_last_live_check = now
                try:
                    live_closed = self._order_mgr.check_live_positions()
                    for result in live_closed:
                        self._handle_closed_position(result)
                except Exception as e:
                    log.debug("[WS-LIVE] Error: %s", str(e)[:100])

        # Zone alerts: check if price is near high-strength OB zones
        self._check_zone_alerts(mids)

    # ── Scalp Sniper (real-time pump/dump detector) ──

    def _sniper_track_prices(self, mids: dict[str, float], now: float) -> None:
        """Track price ticks for every coin. Called on every WS update."""
        for coin, price in mids.items():
            if coin not in self._sniper_prices:
                self._sniper_prices[coin] = []
            self._sniper_prices[coin].append((now, price))
            # Keep last 5 min of ticks (trim old)
            cutoff = now - 300
            self._sniper_prices[coin] = [
                (t, p) for t, p in self._sniper_prices[coin] if t > cutoff
            ]

    async def _scalp_sniper_loop(self) -> None:
        """Every 5 seconds: scan all coins for pump/dump → instant scalp trigger.

        This is the core scalp engine. It doesn't wait for the 5-min trading cycle.
        Detects: >0.5% move in last 2-3 min across entire universe.
        Triggers: instant LLM analysis → scalp entry within seconds.
        """
        log.info("[SNIPER] Scalp sniper started — scanning every 5s")
        # Warm up: wait 60s for price history to build
        await asyncio.sleep(60)

        while self._running:
            try:
                await asyncio.sleep(5)
                now = time.time()

                # Skip if we're at max scalp positions
                scalp_count = sum(
                    1 for p in (
                        self._order_mgr.get_paper_positions()
                        if self._cfg.dry_run
                        else self._order_mgr.get_live_positions()
                    )
                    if p.get("trade_type") == "scalp"
                )
                if scalp_count >= self._cfg.scalp_max_positions:
                    continue

                # Skip if total positions maxed
                if self._order_mgr.get_open_positions_count() >= self._cfg.max_open_positions:
                    continue

                # Check circuit breaker
                cb = self._breaker.check()
                if not cb.trading_allowed:
                    continue

                # Scan all tracked coins for pump/dump
                triggers = self._sniper_scan(now)

                for sym, direction, move_pct, speed in triggers[:2]:  # Max 2 triggers per scan
                    if self._order_mgr.has_position_for_symbol(sym):
                        continue
                    if self._order_mgr.has_pending_for_symbol(sym):
                        continue

                    log.info(
                        "[SNIPER] %s %s %.1f%% in %.0fs — triggering scalp analysis",
                        sym, direction, move_pct, speed,
                    )

                    # Fire scalp analysis (non-blocking)
                    asyncio.create_task(
                        self._scalp_entry(sym, direction, move_pct),
                        name=f"scalp_{sym}",
                    )

            except asyncio.CancelledError:
                break
            except Exception as e:
                log.debug("[SNIPER] Error: %s", str(e)[:150])

    def _sniper_scan(self, now: float) -> list[tuple[str, str, float, float]]:
        """Scan all coins for recent pump/dump moves.

        Returns [(symbol, direction, move_pct, seconds)] sorted by move magnitude.
        """
        triggers: list[tuple[str, str, float, float]] = []

        for coin, ticks in self._sniper_prices.items():
            if len(ticks) < 5:
                continue

            sym = f"{coin}USDT"
            if sym not in {f"{s}USDT" for s in (self._hl_tradeable or set())}:
                continue

            # Cooldown: don't re-trigger same coin within 10 min
            if now - self._sniper_cooldown.get(sym, 0) < 600:
                continue

            current_price = ticks[-1][1]

            # Check move over different windows (30s, 60s, 120s, 180s)
            for window_sec in (60, 120, 180):
                cutoff = now - window_sec
                old_ticks = [p for t, p in ticks if t <= cutoff + 10 and t >= cutoff - 10]
                if not old_ticks:
                    continue

                old_price = old_ticks[0]
                if old_price <= 0:
                    continue

                move_pct = (current_price - old_price) / old_price * 100
                abs_move = abs(move_pct)

                # Thresholds scale with window:
                # 60s: 0.4%, 120s: 0.6%, 180s: 0.8%
                min_move = 0.3 + window_sec / 300
                if abs_move >= min_move:
                    direction = "LONG" if move_pct > 0 else "SHORT"
                    triggers.append((sym, direction, abs_move, window_sec))
                    self._sniper_cooldown[sym] = now
                    break  # One trigger per coin

        triggers.sort(key=lambda x: x[2], reverse=True)
        return triggers

    async def _scalp_entry(self, symbol: str, direction: str, move_pct: float) -> None:
        """Fast scalp entry — rule-based brain, no LLM, instant decision.

        ScalpBrain checks: EMA trend, volume, RSI, momentum.
        ~0.1s decision vs 15s LLM call. Built for speed.
        """
        try:
            # ── Hard blocks before any work ──
            bare = symbol.replace("USDT", "")
            if bare in self._cfg.permanent_blacklist:
                log.info("[SNIPER] %s: PERMANENTLY BLACKLISTED — skip", bare)
                return

            # Cooldown: no repeat scalps on same coin within N seconds
            import time as _time
            _last_scalp_key = f"_last_scalp_{bare}"
            _last_t = getattr(self, _last_scalp_key, 0)
            if _time.time() - _last_t < self._cfg.scalp_cooldown_seconds:
                log.info("[SNIPER] %s: cooldown (%ds left) — skip",
                         bare, int(self._cfg.scalp_cooldown_seconds - (_time.time() - _last_t)))
                return

            # Fetch only LTF candles (5m/15m — all we need for a scalp)
            ltf_candles = self._fetch_candles(symbol, self._cfg.ltf, 50)

            if not ltf_candles or len(ltf_candles) < 25:
                log.info("[SNIPER] %s: not enough candles (%d)",
                         symbol, len(ltf_candles) if ltf_candles else 0)
                return

            ltf_df = self._candles_to_df(ltf_candles)

            # Current price from WS (fastest)

            current_price = self._ws_prices.get(bare, self._ws_prices.get(symbol, 0))
            if current_price <= 0:
                current_price = self._client.get_price(symbol)
            if current_price <= 0:
                return

            # ── ScalpBrain: instant rule-based decision ──
            decision = self._scalp_brain.analyze(
                ltf_df=ltf_df,
                direction_hint=direction,
                current_price=current_price,
                move_pct=move_pct,
            )

            if not decision.trade:
                return  # ScalpBrain already logged the skip reason

            # ── Safety checks ──
            balance = self._get_balance()
            trade_type = "scalp"

            # Portfolio guard
            self._portfolio_guard.update_state(
                balance=balance,
                positions=self._order_mgr.get_paper_positions()
                    if self._cfg.dry_run else [],
            )
            tier = coin_tier(symbol)
            tier_cap = notional_cap_for_tier(tier, self._cfg)

            guard_decision = self._portfolio_guard.check_trade(
                symbol=symbol,
                direction=decision.direction,
                risk_usd=decision.risk_usd,
                notional_usd=tier_cap,
                trade_type=trade_type,
            )
            if not guard_decision.allowed:
                log.info("[SNIPER] %s: Guard blocked: %s",
                         symbol, "; ".join(guard_decision.reasons))
                return

            # Position sizing
            effective_risk = guard_decision.adjusted_risk_usd or decision.risk_usd
            size = self._sizer.calculate(
                balance=balance,
                entry_price=current_price,
                stop_loss=decision.stop_loss,
                confidence=decision.confidence,
                trade_type=trade_type,
                macro_multiplier=1.0,
                current_exposure=self._order_mgr.get_total_exposure(),
                conviction_score=decision.conviction_score,
                direction=decision.direction,
                notional_cap_override=guard_decision.notional_cap or tier_cap,
                risk_override=effective_risk,
            )

            if size.notional_usd < 10:
                log.info("[SNIPER] %s: position too small ($%.2f)", symbol, size.notional_usd)
                return

            # Set cooldown timestamp
            setattr(self, f"_last_scalp_{bare}", _time.time())

            # Build TradeSignal for order manager
            signal = TradeSignal(
                symbol=symbol,
                direction=decision.direction,
                confidence=decision.confidence,
                entry_price=current_price,
                stop_loss=decision.stop_loss,
                take_profit_1=decision.take_profit,
                take_profit_2=decision.take_profit,
                risk_reward=1.5,
                trade_type="scalp",
                conviction_score=decision.conviction_score,
                entry_reason=f"SCALP sniper: {move_pct:.1f}% move | " + " | ".join(decision.reasons[:3]),
            )

            log.info(
                "[SNIPER] %s SCALP: %s $%.2f | SL=$%.4f TP=$%.4f "
                "| risk=$%.0f notional=$%.0f lev=%dx score=%d | %.1f%% trigger",
                symbol, decision.direction, current_price,
                decision.stop_loss, decision.take_profit,
                size.risk_usd, size.notional_usd, size.leverage,
                decision.conviction_score, move_pct,
            )

            # Execute — market entry for speed
            pos_id = self._order_mgr.execute_signal(
                signal, size, balance=self._cycle_balance,
            )

            if pos_id:
                log.info("[SNIPER] %s position opened: %s", symbol, pos_id)
                self._brotherhood.publish_trade_open({
                    "symbol": symbol, "direction": decision.direction,
                    "entry_price": current_price,
                    "conviction_score": decision.conviction_score,
                    "trade_type": "scalp",
                })

        except Exception as e:
            import traceback
            log.error("[SNIPER] %s error: %s\n%s",
                      symbol, str(e)[:200], traceback.format_exc())

    def _check_zone_alerts(self, mids: dict[str, float]) -> None:
        """Check if any WS prices are near high-strength OB zones."""
        if not hasattr(self, "_zone_alert_cooldown"):
            self._zone_alert_cooldown: dict[str, float] = {}

        radius = self._cfg.zone_alert_radius_pct / 100.0
        now = time.time()

        for coin, price in mids.items():
            symbol = f"{coin}USDT"
            # Cooldown: only alert once per symbol per 5 minutes
            if now - self._zone_alert_cooldown.get(symbol, 0) < 300:
                continue

            # Skip if we already have a position or pending order
            if self._order_mgr.has_position_for_symbol(symbol):
                continue
            if self._order_mgr.has_pending_for_symbol(symbol):
                continue

            # Check OB memory for high-strength zones near current price
            try:
                zones = self._ob_memory.get_active_zones(
                    symbol,
                    price_range=(price * (1 - radius), price * (1 + radius)),
                )
                strong_zones = [z for z in zones if z.strength >= 70]
                if strong_zones:
                    self._zone_alert_cooldown[symbol] = now
                    zone = strong_zones[0]
                    log.info(
                        "[ZONE-ALERT] %s @ $%.2f near %s %s zone "
                        "(strength=%.0f, level=$%.2f, dist=%.2f%%) → auto-trigger",
                        symbol, price, zone.direction, zone.zone_type,
                        zone.strength, zone.price_level,
                        abs(price - zone.price_level) / price * 100,
                    )
                    # Auto-trigger scalp analysis for high-strength zone hits
                    if (
                        zone.strength >= 75
                        and self._order_mgr.get_open_positions_count()
                        < self._cfg.max_open_positions
                    ):
                        asyncio.create_task(
                            self._analyze_and_trade(symbol),
                            name=f"zone_scalp_{symbol}",
                        )
            except Exception:
                pass

    def _handle_closed_position(self, result) -> None:
        """Common handler for closed positions (DRY — used by both monitor and WS)."""
        self._breaker.record_trade(result.pnl_usd, symbol=result.symbol)
        if result.is_win:
            self._portfolio_guard.record_win(result.symbol)
        else:
            self._portfolio_guard.record_loss(result.symbol)
        log.info("[CLOSED] %s %s PnL=$%.2f (%s)",
                 result.symbol, result.side, result.pnl_usd, result.exit_reason)

        decision_id = self._order_mgr.get_position_meta(
            result.trade_id, "decision_id"
        )
        if decision_id:
            self._journal.record_trade_close(decision_id, {
                "pnl_usd": result.pnl_usd,
                "exit_reason": result.exit_reason,
                "hold_hours": result.hold_duration_hours,
                "exit_price": result.exit_price,
                "symbol": result.symbol,
                "direction": result.side,
            })
        self._brotherhood.publish_trade_close({
            "symbol": result.symbol,
            "direction": result.side,
            "pnl_usd": result.pnl_usd,
            "exit_reason": result.exit_reason,
            "is_win": result.is_win,
        })
        self._discord_pipeline.record_discord_outcome(
            result.trade_id, result.pnl_usd, result.is_win,
        )

        # Reflection Engine — log close and maybe extract lessons
        if decision_id:
            self._reflection.log_trade_close(
                decision_id=decision_id,
                symbol=result.symbol,
                direction=result.side,
                entry_price=result.entry_price,
                exit_price=result.exit_price,
                pnl_usd=result.pnl_usd,
                exit_reason=result.exit_reason,
            )
            self._reflection.maybe_reflect()

        trade_dict = {
            "trade_id": result.trade_id,
            "symbol": result.symbol,
            "direction": result.side,
            "entry_price": result.entry_price,
            "exit_price": result.exit_price,
            "pnl_usd": result.pnl_usd,
            "exit_reason": result.exit_reason,
            "hold_hours": result.hold_duration_hours,
            "conviction_score": getattr(result, "conviction_score", 0),
            "regime": self._last_regime.regime.value if self._last_regime else "",
        }
        self._auto_reporter.report_trade(trade_dict)

        # Post-trade analysis (discipline layer)
        try:
            analysis = self._trade_analyzer.analyze(result, trade_dict)
            if analysis:
                log.info(
                    "[ANALYSIS] %s %s: entry=%d/100 exit=%d/100 sl=%d/100 "
                    "timing=%d/100 | EV capture: %d%%",
                    result.symbol, result.side,
                    analysis.get("entry_quality", 0),
                    analysis.get("exit_quality", 0),
                    analysis.get("sl_quality", 0),
                    analysis.get("timing_quality", 0),
                    analysis.get("ev_capture_pct", 0),
                )
        except Exception as e:
            log.debug("[ANALYSIS] Error: %s", str(e)[:100])

        # Slippage tracking
        try:
            signal_ts = getattr(result, "signal_timestamp", 0) or 0
            fill_ts = getattr(result, "fill_timestamp", 0) or 0
            if result.entry_price > 0 and signal_ts > 0:
                self._slippage_tracker.record(
                    symbol=result.symbol,
                    signal_price=result.entry_price,
                    fill_price=result.entry_price,
                    signal_ts=signal_ts,
                    fill_ts=fill_ts or signal_ts,
                    order_type="market",
                )
        except Exception as e:
            log.debug("[SLIPPAGE] Record error: %s", str(e)[:100])

    async def _ws_health_loop(self) -> None:
        """Periodic WS health check and reconnect."""
        while self._running:
            await asyncio.sleep(30)
            if self._ws:
                try:
                    await self._ws.health_check()
                except Exception as e:
                    log.debug("[WS-HEALTH] Error: %s", str(e)[:100])

    # ── Health + Weekly Review ──

    async def _health_loop(self) -> None:
        """Run health diagnostics every 30 min + trigger weekly review."""
        while self._running:
            await asyncio.sleep(self._cfg.health_check_seconds)
            try:
                slippage_stats = {}
                for sym in ["BTCUSDT", "ETHUSDT"]:
                    stats = self._slippage_tracker.get_symbol_stats(sym)
                    if stats.get("sample_size", 0) > 0:
                        slippage_stats[sym] = stats

                recent = self._journal.get_recent_trades(30)
                report = self._health_monitor.run_diagnostic(recent, slippage_stats)

                if report.get("overall") == "RED":
                    log.critical(
                        "[HEALTH] RED — %d flags: %s",
                        report.get("red_flags", 0),
                        "; ".join(report.get("recommendations", [])),
                    )
            except Exception as e:
                log.debug("[HEALTH] Loop error: %s", str(e)[:150])

            # Weekly review check (configurable day + hour)
            try:
                now = datetime.now(ET)
                if (
                    now.weekday() == self._cfg.weekly_review_day
                    and now.hour == self._cfg.weekly_review_hour
                    and time.time() - self._last_weekly_review > 82800
                ):
                    await self._weekly_review()
                    self._last_weekly_review = time.time()
            except Exception as e:
                log.warning("[WEEKLY] Review error: %s", str(e)[:200])

    async def _weekly_review(self) -> None:
        """Monday 2 AM ET — conviction calibration, self-evolve, edge report."""
        log.info("[WEEKLY] Starting weekly review...")

        trades = self._journal.get_recent_trades(100)
        if len(trades) < 10:
            log.info("[WEEKLY] Skipped — only %d trades", len(trades))
            return

        # 1. Conviction calibration
        try:
            from odin.intelligence.conviction import COMPONENT_WEIGHTS
            new_weights = self._conviction_calibrator.suggest_weights(
                dict(COMPONENT_WEIGHTS), trades,
            )
            if new_weights:
                applied = self._conviction.apply_calibration(new_weights)
                if applied:
                    log.info("[WEEKLY] Conviction weights updated")
        except Exception as e:
            log.warning("[WEEKLY] Calibration error: %s", str(e)[:150])

        # 2. Self-evolve parameter mutation
        try:
            evo_result = self._self_evolve.evolve(trades)
            log.info("[WEEKLY] SelfEvolve: gen=%d WR=%.1f%%",
                     evo_result.get("generation", 0), evo_result.get("best_wr", 0))
        except Exception as e:
            log.warning("[WEEKLY] Evolve error: %s", str(e)[:150])

        # 3. Edge tracker report
        try:
            edge_report = self._edge_tracker.weekly_report()
            log.info("[WEEKLY] Edge: %s → %s (Sharpe=%.2f)",
                     edge_report.get("edge_status", "?"),
                     edge_report.get("recommendation", "?"),
                     edge_report.get("overall", {}).get("sharpe", 0))
        except Exception as e:
            log.warning("[WEEKLY] Edge report error: %s", str(e)[:150])

        # 4. Publish summary to event bus
        try:
            from shared.events import publish
            publish("odin", "weekly_review", {
                "trades_analyzed": len(trades),
                "timestamp": time.time(),
            })
        except Exception:
            pass

        log.info("[WEEKLY] Review complete — %d trades analyzed", len(trades))

    # ── Helpers ──

    def _fetch_candles(self, symbol: str, interval: str, limit: int) -> list:
        try:
            return self._client.get_klines(symbol=symbol, interval=interval, limit=limit)
        except Exception as e:
            log.debug("[FETCH] %s %s error: %s", symbol, interval, str(e)[:100])
            return []

    def _candles_to_df(self, candles: list) -> pd.DataFrame:
        if not candles:
            return pd.DataFrame()
        rows = [{"timestamp": c.timestamp, "open": c.open, "high": c.high,
                 "low": c.low, "close": c.close, "volume": c.volume} for c in candles]
        df = pd.DataFrame(rows)
        df.sort_values("timestamp", inplace=True)
        df.reset_index(drop=True, inplace=True)
        return df

    def _get_balance(self) -> float:
        if self._cfg.dry_run:
            return self._breaker.state.current_balance
        try:
            bal = self._client.get_balance()
            self._breaker.update_balance(bal.total_balance)
            return bal.available_balance
        except Exception:
            return self._breaker.state.current_balance

    def _get_current_prices(self) -> dict[str, float]:
        """Get prices — WS cache first, REST fallback."""
        # Prefer WS cached prices if fresh (< 10s old)
        if self._ws and self._ws.connected and self._ws.last_tick_age < 10:
            return dict(self._ws_prices)

        # REST fallback
        try:
            all_prices = self._client.get_all_prices()
            prices: dict[str, float] = {}
            for sym, price in all_prices.items():
                prices[f"{sym}USDT"] = price
                prices[sym] = price
            return prices
        except Exception:
            symbols = set()
            for p in self._order_mgr.get_paper_positions():
                symbols.add(p["symbol"])
            for s in HL_MAJORS:
                symbols.add(s)
            prices = {}
            for symbol in symbols:
                try:
                    prices[symbol] = self._client.get_price(symbol)
                except Exception:
                    pass
            return prices

    def _read_health_report(self) -> dict:
        """Read persisted health report (written by _health_loop)."""
        health_file = self._cfg.data_dir / "health_report.json"
        if health_file.exists():
            try:
                return json.loads(health_file.read_text())
            except Exception:
                pass
        return {"overall": "UNKNOWN"}

    def _check_resets(self) -> None:
        now = datetime.now(ET)
        if now.hour == 0 and now.minute < 2:
            self._breaker.reset_daily()
        if now.weekday() == 6 and now.hour == 0 and now.minute < 2:
            self._breaker.reset_weekly()
        if now.day == 1 and now.hour == 0 and now.minute < 2:
            self._breaker.reset_monthly()

    def _write_status(self) -> None:
        try:
            cb = self._breaker.state
            regime = self._last_regime
            sig = self._last_signal

            status = {
                "agent": "odin",
                "version": "8.0.0",
                "mode": "paper" if self._cfg.dry_run else "live",
                "running": self._running,
                "uptime_hours": round((time.time() - self._start_time) / 3600, 2)
                    if self._start_time else 0,
                "cycle_count": self._cycle_count,
                "timestamp": time.time(),
                "timestamp_et": datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S"),

                # Account
                "balance": round(cb.current_balance, 2),
                "peak_balance": round(cb.peak_balance, 2),
                "total_pnl": round(cb.total_pnl, 2),
                "daily_pnl": round(cb.daily_pnl, 2),
                "weekly_pnl": round(cb.weekly_pnl, 2),
                "drawdown_pct": cb.drawdown_pct,

                # Circuit breaker
                "trading_allowed": cb.trading_allowed,
                "cb_reason": cb.reason,
                "consecutive_losses": cb.consecutive_losses,

                # Positions
                "open_positions": self._order_mgr.get_open_positions_count(),
                "total_exposure": round(self._order_mgr.get_total_exposure(), 2),
                "paper_positions": self._order_mgr.get_paper_positions(self._ws_prices)
                    if self._cfg.dry_run else [],
                "live_positions": self._order_mgr.get_live_positions()
                    if not self._cfg.dry_run else [],

                # CoinGlass Regime
                "regime": regime.to_dict() if regime else None,
                "opportunities": [
                    {"symbol": o.symbol, "direction": o.direction.value,
                     "score": round(o.score), "reasons": o.reasons[:2]}
                    for o in (regime.opportunities[:10] if regime else [])
                ],

                # Last signal
                "last_signal": sig.to_dict() if sig else None,

                # Intelligence
                "conviction": self._conviction.get_status() if hasattr(self, "_conviction") else None,
                "brotherhood": self._brotherhood.get_status() if hasattr(self, "_brotherhood") else None,
                "journal_stats": self._journal.get_stats() if hasattr(self, "_journal") else None,

                # Skills
                "discord_pipeline": self._discord_pipeline.get_status() if hasattr(self, "_discord_pipeline") else None,
                "skills": self._skills.get_all_status() if hasattr(self, "_skills") else {},
                "skill_count": self._skills.skill_count if hasattr(self, "_skills") else 0,
                "ob_memory": self._ob_memory.get_stats() if hasattr(self, "_ob_memory") else {},
                "omnicoin_last": self._omnicoin.get_status() if hasattr(self, "_omnicoin") else {},

                # Portfolio Guard (Phase 2)
                "portfolio_guard": self._portfolio_guard.get_status(),

                # WebSocket + Pending Orders (Phase 3)
                "ws_status": self._ws.get_status() if self._ws else {"connected": False},
                "pending_orders": self._order_mgr.get_pending_orders(),
                "pending_order_count": len(self._order_mgr.get_pending_orders()),

                # Discipline Layer
                "health": self._read_health_report(),
                "edge": {
                    "status": self._edge_tracker.detect_decay()
                        if hasattr(self, "_edge_tracker") else "unknown",
                    "risk_scalar": self._edge_tracker.get_risk_scalar()
                        if hasattr(self, "_edge_tracker") else 1.0,
                    "sharpe": self._edge_tracker.rolling_sharpe()
                        if hasattr(self, "_edge_tracker") else 0,
                },
                "rolling_stats": self._trade_analyzer.get_rolling_stats()
                    if hasattr(self, "_trade_analyzer") else {},

                # Config
                "config": {
                    "exchange": "Hyperliquid",
                    "hl_pairs": len(self._hl_tradeable),
                    "risk_per_trade": self._cfg.risk_per_trade_usd,
                    "risk_cap": "1R",
                    "target_rr": self._cfg.target_rr,
                    "max_positions": self._cfg.max_open_positions,
                    "coinglass": bool(self._cg),
                    "cycle_seconds": self._cfg.cycle_seconds,
                    "priority_coins": self._cfg.max_priority_coins,
                    "detail_slots": CG_DETAIL_SLOTS,
                    "symbols_per_cycle": self._cfg.symbols_per_cycle,
                    "coin_universe": len(self._get_full_universe()),
                    "ws_enabled": self._cfg.ws_enabled,
                    "scaled_tranches": self._cfg.scaled_entry_tranches,
                    "limit_ttl_s": self._cfg.limit_order_ttl_seconds,
                },
            }

            self._status_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._status_file, "w") as f:
                json.dump(status, f, indent=2, default=str)

        except Exception as e:
            log.debug("[STATUS] Write error: %s", e)

    def _setup_logging(self) -> None:
        level = getattr(logging, self._cfg.log_level.upper(), logging.INFO)
        odin_log = logging.getLogger("odin")
        odin_log.setLevel(level)

        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(level)
        fmt = logging.Formatter(
            "%(asctime)s [%(name)s] %(levelname)s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        ch.setFormatter(fmt)
        odin_log.addHandler(ch)

        log_file = self._cfg.data_dir / "odin.log"
        fh = logging.FileHandler(log_file)
        fh.setLevel(level)
        fh.setFormatter(fmt)
        odin_log.addHandler(fh)

        for lib in ("urllib3", "requests", "yfinance", "peewee"):
            logging.getLogger(lib).setLevel(logging.WARNING)
