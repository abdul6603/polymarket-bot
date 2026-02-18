from __future__ import annotations

import asyncio
import logging
import signal
import sys
import time
from pathlib import Path

from bot.binance_feed import BinanceFeed
from bot.config import Config
from bot.conviction import ConvictionEngine
from bot.derivatives_feed import DerivativesFeed
from bot.http_session import get_session
from bot.auth import build_client
from bot.execution import Executor
from bot.market_discovery import fetch_markets, rank_markets
from bot.price_cache import PriceCache
from bot.regime import RegimeAdjustment, detect_regime
from bot.risk import PositionTracker, check_risk
from bot.signals import SignalEngine
from bot.bankroll import BankrollManager
from bot.straddle import StraddleEngine
from bot.tracker import PerformanceTracker
from bot.ws_feed import MarketFeed
from bot.v2_tools import is_emergency_stopped, accept_commands, process_command
from bot.daily_cycle import should_reset, archive_and_reset
from bot.orderbook_check import check_orderbook_depth
from bot.macro import get_context as get_macro_context

log = logging.getLogger("bot")

# Telegram trade alerts — loaded from .env (never hardcode secrets)
import os
_TG_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
_TG_CHAT = os.environ.get("TG_CHAT_ID", "")


def _send_telegram(text: str) -> bool:
    """Send a Telegram message to Jordan via Shelby's bot."""
    if not _TG_TOKEN or not _TG_CHAT:
        return False
    try:
        import requests
        resp = requests.post(
            f"https://api.telegram.org/bot{_TG_TOKEN}/sendMessage",
            json={"chat_id": _TG_CHAT, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
        if resp.status_code == 200:
            return True
        # Retry without parse_mode in case of markdown errors
        retry = requests.post(
            f"https://api.telegram.org/bot{_TG_TOKEN}/sendMessage",
            json={"chat_id": _TG_CHAT, "text": text},
            timeout=10,
        )
        return retry.status_code == 200
    except Exception as e:
        log.warning("Telegram alert failed: %s", str(e)[:100])
        return False


def _fetch_implied_price_rest(cfg: Config, market_id: str, up_token_id: str) -> float | None:
    """REST fallback: fetch current token price from CLOB when WS has no data."""
    try:
        resp = get_session().get(
            f"{cfg.clob_host}/markets/{market_id}",
            timeout=5,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        tokens = data.get("tokens", [])
        for t in tokens:
            if t.get("token_id") == up_token_id:
                price = t.get("price")
                if price is not None:
                    return float(price)
        return None
    except Exception:
        return None


class TradingBot:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._setup_logging()

        self.tracker = PositionTracker()
        self.price_cache = PriceCache()
        self.price_cache.preload_from_disk()
        self.signal_engine = SignalEngine(cfg, self.price_cache)
        self.binance_feed = BinanceFeed(cfg, self.price_cache)
        self.feed = MarketFeed(cfg)

        # Build CLOB client (None if no credentials)
        if cfg.private_key and not cfg.dry_run:
            self.client = build_client(cfg)
        else:
            self.client = None
            if cfg.dry_run:
                log.info("Running in DRY RUN mode — no CLOB client needed")
            else:
                log.warning("No private key configured")

        # Sync tracker with real Polymarket positions
        self.tracker.sync_from_chain(self.client)

        self.derivatives_feed = DerivativesFeed(cfg)
        self.executor = Executor(cfg, self.client, self.tracker)
        self.conviction_engine = ConvictionEngine()
        self.straddle_engine = StraddleEngine(cfg, self.executor, self.tracker, self.price_cache)
        self.perf_tracker = PerformanceTracker(cfg, position_tracker=self.tracker)
        self.bankroll_manager = BankrollManager()
        self._shutdown_event = asyncio.Event()
        self._subscribed_tokens: set[str] = set()
        # Track when tokens were first subscribed (for warmup)
        self._subscribe_time: dict[str, float] = {}

        # Agent Hub heartbeat (optional integration — add ~/.agent-hub to path for import)
        self._hub = None
        try:
            import sys as _sys
            _sys.path.insert(0, str(Path.home() / ".agent-hub"))
            from hub import AgentHub
            self._hub = AgentHub("garves")
            self._hub.register(capabilities=[
                "trading", "signals", "conviction_engine",
                "straddle", "multi_asset", "multi_timeframe",
            ])
        except Exception:
            pass
        # Per-market cooldown: market_id -> last trade timestamp
        self._market_cooldown: dict[str, float] = {}
        self.COOLDOWN_SECONDS = 90  # 1.5 min cooldown after trading a market
        # Per-market stacking cap: market_id -> trade count (prevents concentrated risk)
        # Initialized from pending trades so restarts don't reset the count
        self._market_trade_count: dict[str, int] = {}
        for _tid, _prec in self.perf_tracker._pending.items():
            mid = _prec.market_id
            self._market_trade_count[mid] = self._market_trade_count.get(mid, 0) + 1
        if self._market_trade_count:
            log.info("Loaded market trade counts from %d pending trades: %s",
                     sum(self._market_trade_count.values()),
                     {k[:10]: v for k, v in self._market_trade_count.items()})
        self.MAX_TRADES_PER_MARKET = 1  # Max 1 trade per market — no stacking (was 3, caused $73 concentrated loss)
        # Smart stacking: escalating conviction for each additional bet in same window
        self.STACK_EDGE_ESCALATION = 0.02       # +2% edge per stacked bet
        self.STACK_CONFIDENCE_ESCALATION = 0.05  # +5% confidence per stacked bet

    def _setup_logging(self) -> None:
        logging.basicConfig(
            level=getattr(logging, self.cfg.log_level.upper(), logging.INFO),
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )

    async def run(self) -> None:
        """Main async loop: discover → evaluate all markets → trade best signals."""
        log.info("=" * 60)
        log.info("Garves V2 — Multi-Timeframe Trading Bot")
        log.info("Signal -> Probability -> Edge -> Action -> Confidence -> P&L")
        log.info("Assets: BTC, ETH, SOL, XRP | Timeframes: 5m, 15m, 1h, 4h, weekly")
        log.info("Ensemble: 11 indicators + Temporal Arb + ATR Filter + Fee Awareness + ConvictionEngine")
        log.info("Risk: max %d concurrent, $%.2f cap, 5min cooldown",
                 self.cfg.max_concurrent_positions, self.cfg.max_position_usd)
        log.info("Dry run: %s | Tick: %ds", self.cfg.dry_run, self.cfg.tick_interval_s)
        bankroll_status = self.bankroll_manager.get_status()
        log.info("Bankroll: $%.2f (PnL: $%+.2f, mult: %.2fx)",
                 bankroll_status["bankroll_usd"], bankroll_status["pnl_usd"],
                 bankroll_status["multiplier"])
        log.info("V2: emergency_stop, trade_journal, shelby_commands, trade_alerts")
        log.info("=" * 60)

        # Start Binance real-time price feed + derivatives feed + Polymarket WebSocket feed
        await self.binance_feed.start()
        await self.derivatives_feed.start()
        await self.feed.start()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._handle_shutdown)

        try:
            while not self._shutdown_event.is_set():
                await self._tick()
                try:
                    await asyncio.wait_for(
                        self._shutdown_event.wait(),
                        timeout=self.cfg.tick_interval_s,
                    )
                except asyncio.TimeoutError:
                    pass
        finally:
            await self._cleanup()

    def _handle_shutdown(self) -> None:
        log.info("Shutdown signal received")
        self._shutdown_event.set()

    def _check_mode_toggle(self) -> None:
        """Check if mode was toggled via dashboard and update cfg accordingly."""
        mode_file = Path(__file__).parent.parent / "data" / "garves_mode.json"
        if not mode_file.exists():
            return
        try:
            import json as _json
            mode_data = _json.loads(mode_file.read_text())
            new_dry_run = mode_data.get("dry_run", self.cfg.dry_run)
            if new_dry_run != self.cfg.dry_run:
                old_mode = "DRY RUN" if self.cfg.dry_run else "LIVE"
                new_mode = "DRY RUN" if new_dry_run else "LIVE"
                object.__setattr__(self.cfg, "dry_run", new_dry_run)
                log.info("Mode toggled: %s -> %s", old_mode, new_mode)
                # Reinitialize CLOB client if switching to live
                if not new_dry_run and self.cfg.private_key:
                    self.client = build_client(self.cfg)
                    self.executor = Executor(self.cfg, self.client, self.tracker)
                elif new_dry_run:
                    self.client = None
                    self.executor = Executor(self.cfg, None, self.tracker)
        except Exception:
            log.exception("Failed to read Garves mode toggle file")

    async def _tick(self) -> None:
        """Single tick: evaluate ALL discovered markets, trade any with edge."""
        log.info("--- Tick ---")

        # Check mode toggle from dashboard
        self._check_mode_toggle()

        # Daily cycle: archive yesterday's trades and start fresh at midnight ET
        if should_reset():
            try:
                report = archive_and_reset()
                day = report.get("date", "?")
                s = report.get("summary", {})
                log.info(
                    "=== DAILY RESET === %s: %dW-%dL (%.1f%%) PnL=$%.2f | Archived & cleared",
                    day, s.get("wins", 0), s.get("losses", 0),
                    s.get("win_rate", 0), s.get("pnl", 0),
                )
                # Reload tracker with empty state
                self.perf_tracker = PerformanceTracker(self.cfg, position_tracker=self.tracker)
                # Reset per-market trade counts and cooldowns for new day
                self._market_trade_count = {}
                self._market_cooldown = {}
            except Exception as e:
                log.error("Daily reset failed: %s", str(e)[:200])

        # V2: Check emergency stop
        stop_info = is_emergency_stopped()
        if stop_info:
            log.warning("[V2] EMERGENCY STOP active: %s — skipping tick. Standing by.",
                        stop_info.get("reason", "unknown"))
            return

        # V2: Process Shelby commands
        commands = accept_commands()
        for cmd in commands:
            resp = process_command(cmd, bot=self)
            log.info("[V2] Shelby command '%s' -> %s", cmd.get("action"), resp.get("action", "done"))

        # Read brain notes from dashboard
        from bot.brain_reader import read_brain_notes
        brain_notes = read_brain_notes("garves")
        if brain_notes:
            for note in brain_notes:
                log.info("[BRAIN:%s] %s: %s", note.get("type", "note").upper(), note.get("topic", "?"), note.get("content", "")[:120])

        # Atlas intelligence feed — learnings from research cycles
        from bot.atlas_feed import get_actionable_insights
        atlas_insights = get_actionable_insights("garves")
        if atlas_insights:
            log.info("[ATLAS] %d actionable insights for Garves:", len(atlas_insights))
            for insight in atlas_insights[:3]:
                log.info("[ATLAS] → %s", insight[:150])

        # 0. Detect market regime (Fear & Greed based)
        regime = detect_regime()
        self.executor.regime = regime
        log.info("[REGIME] %s (FnG=%d) — size=%.1fx edge=%.2fx",
                 regime.label.upper(), regime.fng_value,
                 regime.size_multiplier, regime.edge_multiplier)

        # 1. Discover all markets across assets and timeframes
        all_markets = fetch_markets(self.cfg)
        ranked = rank_markets(all_markets)

        if not ranked:
            log.info("No tradeable markets found, waiting...")
            return

        log.info("Evaluating %d markets for signals...", len(ranked))

        # Collect all tokens we need WS data for
        all_tokens = set()
        for dm in ranked:
            tokens = dm.raw.get("tokens", [])
            for t in tokens:
                tid = t.get("token_id", "")
                if tid:
                    all_tokens.add(tid)

        # Subscribe to any new tokens and track subscription time
        new_tokens = all_tokens - self._subscribed_tokens
        now = time.time()
        if new_tokens:
            await self.feed.subscribe(list(all_tokens))
            for tid in new_tokens:
                self._subscribe_time[tid] = now
            self._subscribed_tokens = all_tokens

        # Expire stale conviction signals at the start of each tick
        self.conviction_engine.expire_stale_signals()

        # Get derivatives intelligence (funding rates + liquidations)
        deriv_status = self.derivatives_feed.get_status()
        deriv_data = {
            "funding_rates": deriv_status.get("funding_rates", {}),
            "liquidations": deriv_status.get("liquidations", {}),
        } if deriv_status.get("connected") else None

        if deriv_data:
            fr_count = len(deriv_data["funding_rates"])
            liq_total = sum(
                v.get("event_count", 0)
                for v in deriv_data["liquidations"].values()
            )
            log.info("[DERIVATIVES] Funding rates: %d assets | Liquidation events: %d (5m window)",
                     fr_count, liq_total)

        # ── External Data Intelligence (Phase 1 — Multi-API) ──
        macro_ctx = None
        try:
            macro_ctx = get_macro_context()
            if macro_ctx and macro_ctx.is_event_day:
                log.info("[MACRO] EVENT DAY: %s — edge_multiplier=%.1fx (require stronger signals)",
                         macro_ctx.event_type.upper(), macro_ctx.edge_multiplier)
                # Apply macro edge multiplier on top of regime
                if regime:
                    regime = RegimeAdjustment(
                        label=regime.label,
                        fng_value=regime.fng_value,
                        size_multiplier=regime.size_multiplier,
                        edge_multiplier=regime.edge_multiplier * macro_ctx.edge_multiplier,
                        confidence_floor=regime.confidence_floor,
                        consensus_offset=regime.consensus_offset,
                    )
        except Exception:
            log.debug("Macro context fetch failed")

        # Gather external data per asset (cached, won't re-fetch each tick)
        external_data_cache: dict[str, dict] = {}
        defi = None
        mempool_data = None
        try:
            from bot.defi_data import get_data as get_defi
            defi = get_defi()
        except Exception:
            log.debug("DeFi data fetch failed")
        try:
            from bot.mempool import get_data as get_mempool
            mempool_data = get_mempool()
        except Exception:
            log.debug("Mempool data fetch failed")

        for asset_name in ("bitcoin", "ethereum", "solana", "xrp"):
            ext: dict = {}
            try:
                from bot.coinglass import get_data as get_coinglass
                ext["coinglass"] = get_coinglass(asset_name)
            except Exception:
                ext["coinglass"] = None
            ext["macro"] = macro_ctx
            ext["defi"] = defi
            ext["mempool"] = mempool_data
            try:
                from bot.whale_tracker import get_flow as get_whale_flow
                ext["whale"] = get_whale_flow(asset_name)
            except Exception:
                ext["whale"] = None
            external_data_cache[asset_name] = ext

        # Log external data status
        if external_data_cache:
            sources = []
            sample = next(iter(external_data_cache.values()), {})
            if sample.get("defi"): sources.append("DeFi")
            if sample.get("mempool"): sources.append("Mempool")
            if sample.get("macro"): sources.append("Macro")
            if sample.get("coinglass"): sources.append("Coinglass")
            if sources:
                log.info("[EXT DATA] Active sources: %s", ", ".join(sources))

        # Save external data state for dashboard
        self._save_external_data_state(external_data_cache, macro_ctx)

        # 2. Evaluate each market for signals, trade the best ones
        trades_this_tick = 0
        for dm in ranked:
            market = dm.raw
            market_id = dm.market_id
            timeframe = dm.timeframe.name
            asset = dm.asset

            # Per-market cooldown — don't re-enter same market too quickly
            last_trade = self._market_cooldown.get(market_id, 0)
            if now - last_trade < self.COOLDOWN_SECONDS:
                continue

            # Per-market stacking cap — prevent concentrated risk on one market
            if self._market_trade_count.get(market_id, 0) >= self.MAX_TRADES_PER_MARKET:
                continue

            # Extract token IDs
            tokens = market.get("tokens", [])
            if len(tokens) < 2:
                continue

            up_token = down_token = ""
            for t in tokens:
                outcome = (t.get("outcome") or "").lower()
                tid = t.get("token_id", "")
                if outcome in ("up", "yes"):
                    up_token = tid
                elif outcome in ("down", "no"):
                    down_token = tid

            if not up_token or not down_token:
                continue

            # Get implied price from WS cache
            prices = self.feed.latest_price
            implied_up = prices.get(up_token)

            # REST fallback: if WS has no price data, fetch via REST API
            if implied_up is None:
                sub_time = self._subscribe_time.get(up_token, now)
                if now - sub_time > 5:  # after 5s warmup
                    implied_up = _fetch_implied_price_rest(self.cfg, market_id, up_token)
                    if implied_up is not None:
                        log.debug("REST fallback: implied_up=%.3f for %s", implied_up, market_id[:12])

            orderbooks = self.feed.latest_orderbook
            ob = orderbooks.get(up_token)

            # Get Binance spot depth for this asset
            spot_depth = self.binance_feed.get_depth(asset)

            # Generate signal for this specific market
            sig = self.signal_engine.generate_signal(
                up_token, down_token,
                asset=asset,
                timeframe=timeframe,
                implied_up_price=implied_up,
                orderbook=ob,
                regime=regime,
                derivatives_data=deriv_data,
                spot_depth=spot_depth,
                external_data=external_data_cache.get(asset.lower()),
            )
            if not sig:
                continue

            # ── Smart Window Stacking: escalating conviction for additional bets ──
            stack_count = self._market_trade_count.get(market_id, 0)
            if stack_count > 0:
                escalated_edge = 0.08 + stack_count * self.STACK_EDGE_ESCALATION
                escalated_conf = 0.55 + stack_count * self.STACK_CONFIDENCE_ESCALATION
                if sig.edge < escalated_edge or sig.confidence < escalated_conf:
                    log.info(
                        "[%s/%s] Smart stack filter: bet #%d needs edge>=%.1f%% conf>=%.0f%% "
                        "(have edge=%.1f%% conf=%.0f%%) — BLOCKED",
                        asset.upper(), timeframe, stack_count + 1,
                        escalated_edge * 100, escalated_conf * 100,
                        sig.edge * 100, sig.confidence * 100,
                    )
                    continue
                log.info(
                    "[%s/%s] Smart stack filter: bet #%d passed (edge=%.1f%%>=%.1f%% conf=%.0f%%>=%.0f%%)",
                    asset.upper(), timeframe, stack_count + 1,
                    sig.edge * 100, escalated_edge * 100,
                    sig.confidence * 100, escalated_conf * 100,
                )

            rr_str = f"R:R={sig.reward_risk_ratio:.2f}" if sig.reward_risk_ratio else "R:R=N/A"
            log.info(
                "[%s/%s] SIGNAL: %s (prob=%.3f, edge=%.1f%%, conf=%.2f, %s) | Implied: %s | %s",
                asset.upper(), timeframe, sig.direction.upper(),
                sig.probability, sig.edge * 100, sig.confidence, rr_str,
                f"${implied_up:.3f}" if implied_up else "N/A",
                dm.question[:50],
            )

            # ── ConvictionEngine: register signal + score conviction ──
            votes = sig.indicator_votes or {}
            # Filter out disabled indicators (weight=0) for accurate conviction scoring
            from bot.weight_learner import get_dynamic_weights
            from bot.signals import WEIGHTS
            dw = get_dynamic_weights(WEIGHTS)
            active_votes = {k: v for k, v in votes.items() if dw.get(k, 1.0) > 0}
            up_count = sum(1 for d in active_votes.values() if d == "up")
            down_count = sum(1 for d in active_votes.values() if d == "down")
            snapshot = ConvictionEngine.build_snapshot(
                signal=sig,
                indicator_votes=active_votes,
                up_count=up_count,
                down_count=down_count,
                total_indicators=len(active_votes),
            )
            self.conviction_engine.register_signal(snapshot)
            self.conviction_engine.register_timeframe_signal(asset, timeframe, sig.direction)

            conviction = self.conviction_engine.score(
                signal=sig,
                asset_snapshot=snapshot,
                regime=regime,
                atr_value=sig.atr_value,
            )

            if conviction.position_size_usd <= 0:
                log.info("  -> ConvictionEngine: score=%.0f [%s] -> $0 (NO TRADE)",
                         conviction.total_score, conviction.tier_label)
                continue

            # Risk check (pass actual conviction size, not default $10)
            allowed, reason = check_risk(self.cfg, sig, self.tracker, market_id,
                                         trade_size_usd=conviction.position_size_usd)
            if not allowed:
                log.info("  -> Blocked: %s", reason)
                continue

            # Orderbook depth check — verify liquidity before placing order
            ob_ok, ob_reason, ob_analysis = check_orderbook_depth(
                clob_host=self.cfg.clob_host,
                token_id=sig.token_id,
                order_size_usd=conviction.position_size_usd,
                target_price=sig.probability,
            )
            if not ob_ok:
                log.info("  -> Orderbook blocked: %s", ob_reason)
                continue
            if ob_analysis:
                log.info(
                    "  -> Orderbook: liq=$%.0f (bid=$%.0f ask=$%.0f) spread=$%.3f slip=%.1f%%",
                    ob_analysis.total_liquidity_usd, ob_analysis.bid_liquidity_usd,
                    ob_analysis.ask_liquidity_usd, ob_analysis.spread,
                    ob_analysis.estimated_slippage_pct * 100,
                )

            # Execute with conviction-based sizing
            order_id = self.executor.place_order(
                sig, market_id, conviction_size=conviction.position_size_usd
            )
            if order_id:
                trades_this_tick += 1
                self._market_cooldown[market_id] = now
                self._market_trade_count[market_id] = self._market_trade_count.get(market_id, 0) + 1
                log.info(
                    "  -> Order placed: %s | Conviction: %.0f/100 [%s] $%.2f%s",
                    order_id, conviction.total_score, conviction.tier_label,
                    conviction.position_size_usd,
                    " ALL-ALIGNED" if conviction.all_assets_aligned else "",
                )

                # Telegram alert for live trades
                if not self.cfg.dry_run:
                    try:
                        rr_tg = f"R:R: {sig.reward_risk_ratio:.2f}" if sig.reward_risk_ratio else "R:R: N/A"
                        ob_tg = ""
                        if ob_analysis:
                            ob_tg = f"\nBook: ${ob_analysis.total_liquidity_usd:.0f} liq | ${ob_analysis.spread:.3f} spread | {ob_analysis.estimated_slippage_pct*100:.1f}% slip"
                        msg = (
                            f"*GARVES TRADE*\n\n"
                            f"*{sig.direction.upper()}* on {asset.upper()}/{timeframe}\n"
                            f"Market: _{dm.question[:80]}_\n"
                            f"Size: *${conviction.position_size_usd:.2f}*\n"
                            f"Price: ${sig.probability:.3f} | Edge: {sig.edge*100:.1f}% | {rr_tg}\n"
                            f"Conviction: {conviction.total_score:.0f}/100 [{conviction.tier_label}]{ob_tg}\n"
                            f"Order: `{order_id}`"
                        )
                        _send_telegram(msg)
                    except Exception:
                        pass

                # Track for performance measurement
                self.perf_tracker.record_signal(
                    signal=sig,
                    market_id=market_id,
                    question=dm.question,
                    implied_up_price=implied_up if implied_up else 0.5,
                    binance_price=self.price_cache.get_price(asset) or 0.0,
                    market_end_time=time.time() + dm.remaining_s,
                    indicator_votes=sig.indicator_votes,
                    regime_label=regime.label,
                    regime_fng=regime.fng_value,
                    ob_liquidity_usd=ob_analysis.total_liquidity_usd if ob_analysis else 0.0,
                    ob_spread=ob_analysis.spread if ob_analysis else 0.0,
                    ob_slippage_pct=ob_analysis.estimated_slippage_pct if ob_analysis else 0.0,
                )

        # ── Straddle Engine: if no directional trades and regime is fear ──
        if trades_this_tick == 0 and regime.label in ("extreme_fear", "fear"):
            feed_prices = self.feed.latest_price
            straddle_opps = self.straddle_engine.scan_for_straddles(
                ranked, regime, feed_prices)
            if straddle_opps:
                best = straddle_opps[0]
                result = self.straddle_engine.execute_straddle(best)
                if result:
                    trades_this_tick += 1
                    log.info("[STRADDLE] Executed: %s + %s", result[0], result[1])

        if trades_this_tick > 0:
            log.info("Placed %d order(s) this tick", trades_this_tick)
        else:
            log.info("No trades this tick (positions: %d, exposure: $%.2f)",
                     self.tracker.count, self.tracker.total_exposure)

        # Save candle data to disk for backtesting
        self.price_cache.save_candles()

        # Save derivatives + depth state for dashboard
        self._save_derivatives_state(deriv_data)

        # Stop-loss: check if any positions need early exit
        stopped = self.executor.check_stop_losses()
        if stopped:
            log.info("Stop-loss exited %d position(s) this tick", stopped)
            sl = getattr(self.executor, '_last_stop_loss', None)
            if sl:
                _send_telegram(
                    f"*STOP-LOSS* {sl['direction'].upper()}\n"
                    f"Entry: ${sl['entry_price']:.3f} -> Bid: ${sl['bid']:.3f}\n"
                    f"Recovered: *${sl['recovery']:.2f}* of ${sl['size_usd']:.2f}\n"
                    f"Saved vs full loss: *${sl['loss_saved']:.2f}*"
                )
                self.executor._last_stop_loss = None

        # Check existing fills (+ expire dry-run positions)
        self.executor.check_fills()

        # Check market resolutions for performance tracking
        self.perf_tracker.check_resolutions()
        if self.perf_tracker.pending_count > 0:
            log.info("Performance tracker: %d trades pending resolution", self.perf_tracker.pending_count)

        # Send heartbeat with live trading metrics
        if self._hub:
            try:
                stats = self.perf_tracker.quick_stats() if hasattr(self.perf_tracker, 'quick_stats') else {}
                self._hub.heartbeat(status="trading", metrics={
                    "trades_this_tick": trades_this_tick,
                    "open_positions": self.tracker.count,
                    "exposure_usd": round(self.tracker.total_exposure, 2),
                    "pending_trades": self.perf_tracker.pending_count,
                    "regime": regime.label if regime else "unknown",
                    "dry_run": self.cfg.dry_run,
                })
            except Exception:
                pass

    def _save_derivatives_state(self, deriv_data: dict | None) -> None:
        """Persist derivatives + depth state to disk for dashboard access."""
        import json as _json
        data_dir = Path(__file__).parent.parent / "data"
        try:
            # Derivatives state (funding rates + liquidations)
            state = deriv_data or {"funding_rates": {}, "liquidations": {}}
            state["connected"] = self.derivatives_feed.get_status().get("connected", False)
            state["timestamp"] = time.time()
            state_file = data_dir / "derivatives_state.json"
            with open(state_file, "w") as f:
                _json.dump(state, f)

            # Spot depth summary
            depth = self.binance_feed.get_depth_summary()
            if depth:
                depth_file = data_dir / "spot_depth.json"
                with open(depth_file, "w") as f:
                    _json.dump(depth, f)
        except Exception:
            pass

    def _save_external_data_state(self, ext_cache: dict, macro_ctx) -> None:
        """Persist external data state to disk for dashboard access."""
        import json as _json
        from dataclasses import asdict
        data_dir = Path(__file__).parent.parent / "data"
        try:
            state = {"timestamp": time.time(), "assets": {}}
            for asset_name, ext in ext_cache.items():
                asset_state = {}
                if ext.get("coinglass"):
                    cg = ext["coinglass"]
                    asset_state["coinglass"] = {
                        "oi_usd": cg.oi_usd,
                        "oi_change_1h_pct": cg.oi_change_1h_pct,
                        "long_short_ratio": cg.long_short_ratio,
                        "avg_funding_rate": cg.avg_funding_rate,
                        "etf_net_flow_usd": cg.etf_net_flow_usd,
                        "etf_available": cg.etf_available,
                    }
                if ext.get("whale"):
                    w = ext["whale"]
                    asset_state["whale"] = {
                        "deposits_usd": w.deposits_usd,
                        "withdrawals_usd": w.withdrawals_usd,
                        "net_flow_usd": w.net_flow_usd,
                        "tx_count": w.tx_count,
                    }
                state["assets"][asset_name] = asset_state

            if ext_cache and next(iter(ext_cache.values()), {}).get("defi"):
                defi = next(iter(ext_cache.values()))["defi"]
                state["defi"] = {
                    "stablecoin_mcap_usd": defi.stablecoin_mcap_usd,
                    "stablecoin_change_7d_pct": defi.stablecoin_change_7d_pct,
                    "tvl_usd": defi.tvl_usd,
                    "tvl_change_24h_pct": defi.tvl_change_24h_pct,
                }
            if ext_cache and next(iter(ext_cache.values()), {}).get("mempool"):
                mp = next(iter(ext_cache.values()))["mempool"]
                state["mempool"] = {
                    "fastest_fee": mp.fastest_fee,
                    "fee_ratio": mp.fee_ratio_vs_baseline,
                    "tx_count": mp.tx_count,
                    "congestion_level": mp.congestion_level,
                }
            if macro_ctx:
                state["macro"] = {
                    "is_event_day": macro_ctx.is_event_day,
                    "event_type": macro_ctx.event_type,
                    "edge_multiplier": macro_ctx.edge_multiplier,
                    "dxy_value": macro_ctx.dxy_value,
                    "dxy_trend": macro_ctx.dxy_trend,
                    "vix_value": macro_ctx.vix_value,
                }

            state_file = data_dir / "external_data_state.json"
            with open(state_file, "w") as f:
                _json.dump(state, f)
        except Exception:
            pass

    async def _cleanup(self) -> None:
        log.info("Shutting down...")
        self.executor.cancel_all_open()
        await self.binance_feed.stop()
        await self.derivatives_feed.stop()
        await self.feed.stop()
        log.info("Shutdown complete")


def main() -> None:
    cfg = Config()
    bot = TradingBot(cfg)
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
