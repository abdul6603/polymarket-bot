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
from bot.risk import PositionTracker, DrawdownBreaker, check_risk
from bot.signals import SignalEngine
from bot.bankroll import BankrollManager
from bot.straddle import StraddleEngine
from bot.tracker import PerformanceTracker
from bot.ws_feed import MarketFeed
from bot.v2_tools import is_emergency_stopped, accept_commands, process_command
from bot.daily_cycle import should_reset, archive_and_reset
from bot.orderbook_check import check_orderbook_depth
from bot.macro import get_context as get_macro_context
from bot.maker_engine import MakerEngine
from bot.snipe.engine import SnipeEngine

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
        self.drawdown_breaker = DrawdownBreaker()
        self.drawdown_breaker.update()  # scan trades on startup
        self.price_cache = PriceCache()
        self.price_cache.preload_from_disk()
        self.signal_engine = SignalEngine(cfg, self.price_cache)
        self.binance_feed = BinanceFeed(cfg, self.price_cache)
        self.feed = MarketFeed(cfg)

        # Build CLOB client (needed for snipe live mode even when taker is dry-run)
        if cfg.private_key:
            self.client = build_client(cfg)
        else:
            self.client = None
            log.warning("No private key configured")

        # Sync tracker with real Polymarket positions
        self.tracker.sync_from_chain(self.client)

        self.derivatives_feed = DerivativesFeed(cfg)
        self.executor = Executor(cfg, self.client, self.tracker)
        self.conviction_engine = ConvictionEngine()
        self.straddle_engine = StraddleEngine(cfg, self.executor, self.tracker, self.price_cache)
        self.maker_engine = MakerEngine(cfg, self.client, self.price_cache)
        self.snipe_engine = SnipeEngine(
            cfg=cfg,
            price_cache=self.price_cache,
            clob_client=self.client,
            dry_run=False,  # LIVE MODE: v7 validated (4W-0L, +$60)
            budget_per_window=cfg.snipe_budget_per_window,
            delta_threshold=cfg.snipe_delta_threshold / 100,
        )
        # Connect CLOB orderbook bridge — REST-based with 5s cache
        from bot.snipe import clob_book
        clob_book.init(cfg.clob_host)
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
        # Agent Brain — learning memory + LLM reasoning
        self._brain = None
        self._shared_llm_call = None
        try:
            import sys as _sys2
            _sys2.path.insert(0, str(Path.home() / "shared"))
            _sys2.path.insert(0, str(Path.home()))
            from agent_brain import AgentBrain
            from llm_client import llm_call as _garves_llm
            self._brain = AgentBrain("garves", system_prompt="You are Garves, a crypto prediction market trader on Polymarket. You analyze BTC/ETH/SOL up-or-down markets.", task_type="analysis")
            self._shared_llm_call = _garves_llm
        except Exception:
            pass
        self._trade_journal_counter = 0  # Counts resolved trades for LLM analysis trigger
        self._tick_counter = 0  # For periodic cache clearing (fee rates TTL)

        # Per-market cooldown: market_id -> last trade timestamp
        self._market_cooldown: dict[str, float] = {}
        self.COOLDOWN_SECONDS = 90  # 1.5 min cooldown after trading a market
        # Per-market stacking cap: market_id -> trade count (prevents concentrated risk)
        # Loaded from trades.jsonl so counts survive bot restarts mid-day
        self._market_trade_count: dict[str, int] = self._load_market_counts()
        if self._market_trade_count:
            log.info("Loaded market trade counts from trades file: %d markets, %d total trades",
                     len(self._market_trade_count),
                     sum(self._market_trade_count.values()))
        self.MAX_TRADES_PER_MARKET = 1  # Max 1 trade per market — no stacking (was 3, caused $73 concentrated loss)
        # Smart stacking: escalating conviction for each additional bet in same window
        self.STACK_EDGE_ESCALATION = 0.02       # +2% edge per stacked bet
        self.STACK_CONFIDENCE_ESCALATION = 0.05  # +5% confidence per stacked bet

        # Balance cache: written by the bot (which has VPN), read by dashboard
        self._balance_cache_file = Path(__file__).parent.parent / "data" / "polymarket_balance.json"
        self._last_balance_sync = 0.0

    def _load_market_counts(self) -> dict[str, int]:
        """Load market trade counts from today's trades file to survive restarts."""
        import json as _json
        trades_file = Path(__file__).parent.parent / "data" / "trades.jsonl"
        counts: dict[str, int] = {}
        if not trades_file.exists():
            return counts
        try:
            for line in trades_file.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                trade = _json.loads(line)
                mid = trade.get("market_id", "")
                if mid:
                    counts[mid] = counts.get(mid, 0) + 1
        except Exception as e:
            log.warning("Failed to load market counts from trades: %s", str(e)[:100])
        return counts

    def _sync_balance_cache(self) -> None:
        """Write real USDC balance to cache file every 2 min (for dashboard)."""
        import json as _json
        now = time.time()
        if now - self._last_balance_sync < 120:
            return
        self._last_balance_sync = now
        if self.client is None:
            return
        try:
            from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
            params = BalanceAllowanceParams(
                asset_type=AssetType.COLLATERAL,
                signature_type=2,
            )
            result = self.client.get_balance_allowance(params)
            cash = int(result.get("balance", "0")) / 1e6

            # Position value from tracker
            pos_val = sum(
                p.shares * p.current_price
                for p in self.tracker.positions.values()
                if p.shares > 0
            ) if hasattr(self.tracker, 'positions') else 0.0

            bankroll = float(self.cfg.bankroll_usd) if hasattr(self.cfg, 'bankroll_usd') else 250.0
            portfolio = cash + pos_val
            cache = {
                "portfolio": round(portfolio, 2),
                "cash": round(cash, 2),
                "positions_value": round(pos_val, 2),
                "pnl": round(portfolio - bankroll, 2),
                "bankroll": bankroll,
                "live": True,
                "error": None,
                "fetched_at": now,
                "source": "garves_bot",
            }
            self._balance_cache_file.write_text(_json.dumps(cache, indent=2))
            log.info("[BALANCE] Cash=$%.2f Positions=$%.2f Portfolio=$%.2f",
                     cash, pos_val, portfolio)
        except Exception as e:
            log.debug("Balance sync failed: %s", str(e)[:100])

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
        log.info("Assets: BTC, ETH, SOL, XRP | Timeframes: 5m(snipe), 15m, 1h, 4h, weekly")
        log.info("Ensemble: 11 indicators + Temporal Arb + ATR Filter + Fee Awareness + ConvictionEngine")
        log.info("Risk: max %d concurrent, $%.2f cap, 5min cooldown",
                 self.cfg.max_concurrent_positions, self.cfg.max_position_usd)
        log.info("Dry run: %s | Tick: %ds", self.cfg.dry_run, self.cfg.tick_interval_s)
        bankroll_status = self.bankroll_manager.get_status()
        log.info("Bankroll: $%.2f (PnL: $%+.2f, mult: %.2fx)",
                 bankroll_status["bankroll_usd"], bankroll_status["pnl_usd"],
                 bankroll_status["multiplier"])
        log.info("V2: emergency_stop, trade_journal, shelby_commands, trade_alerts")
        if self.snipe_engine.enabled:
            log.info("SnipeEngine: ENABLED (budget=$%.0f/window, delta=%.2f%%, 3-wave pyramid)",
                     self.cfg.snipe_budget_per_window, self.cfg.snipe_delta_threshold)
        else:
            log.info("SnipeEngine: disabled (set SNIPE_ENABLED=true to activate)")
        if self.maker_engine.enabled:
            log.info("MakerEngine: ENABLED (quote=$%.0f, max_inv=$%.0f, exposure=$%.0f, tick=%.0fs)",
                     self.maker_engine.quote_size_usd, self.maker_engine.max_inventory_usd,
                     self.maker_engine.max_total_exposure, self.maker_engine.tick_interval_s)
        else:
            log.info("MakerEngine: disabled (set MAKER_ENABLED=true to activate)")
        log.info("=" * 60)

        # Start Binance real-time price feed + derivatives feed + Polymarket WebSocket feed
        await self.binance_feed.start()
        await self.derivatives_feed.start()
        await self.feed.start()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._handle_shutdown)

        try:
            # Run taker + maker + snipe loops concurrently
            taker_task = asyncio.create_task(self._taker_loop())
            maker_task = asyncio.create_task(self._maker_loop())
            snipe_task = asyncio.create_task(self._snipe_loop())
            await asyncio.gather(taker_task, maker_task, snipe_task)
        finally:
            await self._cleanup()

    async def _taker_loop(self) -> None:
        """Taker strategy loop: evaluate markets every tick_interval_s."""
        while not self._shutdown_event.is_set():
            await self._tick()
            try:
                await asyncio.wait_for(
                    self._shutdown_event.wait(),
                    timeout=self.cfg.tick_interval_s,
                )
            except asyncio.TimeoutError:
                pass

    async def _maker_loop(self) -> None:
        """Maker strategy loop: refresh quotes every maker_tick_interval_s."""
        if not self.maker_engine.enabled:
            return

        log.info("[MAKER] Maker loop started (%.0fs interval)", self.maker_engine.tick_interval_s)

        # Cache of discovered markets for maker quoting
        _maker_markets: list[dict] = []
        _last_discovery = 0.0

        while not self._shutdown_event.is_set():
            try:
                now = time.time()

                # Re-discover markets every 60s (don't spam Gamma API)
                if now - _last_discovery > 60:
                    try:
                        all_markets = fetch_markets(self.cfg)
                        ranked = rank_markets(all_markets)
                        _maker_markets = []
                        for dm in ranked:
                            _maker_markets.append({
                                "market_id": dm.market_id,
                                "tokens": dm.raw.get("tokens", []),
                                "asset": dm.asset,
                                "timeframe": dm.timeframe.name,
                            })
                        _last_discovery = now
                    except Exception as e:
                        log.debug("[MAKER] Market discovery failed: %s", str(e)[:100])

                # Get current regime for spread computation
                regime_label = "neutral"
                try:
                    regime = detect_regime()
                    regime_label = regime.label
                except Exception:
                    pass

                self.maker_engine.tick(_maker_markets, regime_label)

            except Exception as e:
                log.warning("[MAKER] Tick error: %s", str(e)[:200])

            try:
                await asyncio.wait_for(
                    self._shutdown_event.wait(),
                    timeout=self.maker_engine.tick_interval_s,
                )
            except asyncio.TimeoutError:
                pass

    async def _snipe_loop(self) -> None:
        """Snipe engine loop for BTC 5m markets (5s tick)."""
        await self.snipe_engine.run_loop(self._shutdown_event)

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
        self._tick_counter += 1

        # Clear SDK fee rate cache every 60 ticks (~30 min) to avoid stale rates
        if self.client and self._tick_counter % 60 == 0:
            try:
                if hasattr(self.client, '_fee_rates'):
                    self.client._fee_rates.clear()
                    log.info("[SDK] Cleared fee rate cache (tick %d)", self._tick_counter)
            except Exception:
                pass

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

        # Sync balance cache for dashboard (every 2 min)
        self._sync_balance_cache()

        # 1. Discover all markets across assets and timeframes
        all_markets = fetch_markets(self.cfg)

        # Feed all 5m markets to snipe engine (BTC, ETH, SOL, XRP — isolated from taker)
        markets_5m = [dm for dm in all_markets if dm.timeframe.name == "5m"]
        if markets_5m:
            self.snipe_engine.window_tracker.update(markets_5m)

        # Filter 5m out of taker pipeline (snipe handles them separately)
        ranked = rank_markets([dm for dm in all_markets if dm.timeframe.name != "5m"])

        if not ranked:
            log.info("No tradeable markets found, waiting...")
            return

        log.info("Evaluating %d markets for signals...", len(ranked))

        # Collect all tokens we need WS data for (taker markets only — 5m uses REST)
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

            # ── Brain Pre-Decision: consult learned patterns + LLM reasoning ──
            try:
                if self._brain:
                    _brain_adj = 0.0
                    _brain_reasons = []
                    _situation = f"{asset.upper()}/{timeframe} regime={regime.label} dir={sig.direction}"

                    # 1. Check learned patterns for relevant win/loss signals
                    _patterns = self._brain.memory.get_active_patterns(min_confidence=0.5)
                    for _p in _patterns:
                        _desc = _p.get("description", "").lower()
                        _asset_match = asset.lower() in _desc
                        _tf_match = timeframe.lower() in _desc
                        _regime_match = regime.label.lower() in _desc
                        _dir_match = sig.direction.lower() in _desc
                        if _asset_match and (_tf_match or _regime_match or _dir_match):
                            _ev = _p.get("evidence_count", 1)
                            _conf = _p.get("confidence", 0.5)
                            if _ev >= 3:
                                _is_loss = any(w in _desc for w in ("loss", "lose", "bad", "avoid", "fail", "negative"))
                                _is_win = any(w in _desc for w in ("win", "profit", "good", "strong", "positive"))
                                if _is_loss:
                                    _penalty = min(0.05, _conf * 0.05)
                                    _brain_adj -= _penalty
                                    _brain_reasons.append(f"loss_pattern({_ev}ev,{_conf:.0%})")
                                elif _is_win:
                                    _boost = min(0.05, _conf * 0.04)
                                    _brain_adj += _boost
                                    _brain_reasons.append(f"win_pattern({_ev}ev,{_conf:.0%})")

                    # 2. Check similar past decisions for win/loss track record
                    _past = self._brain.memory.get_relevant_context(_situation, limit=10)
                    if _past:
                        _resolved = [d for d in _past if d.get("resolved")]
                        if len(_resolved) >= 3:
                            _wins = sum(1 for d in _resolved if d.get("outcome_score", 0) > 0)
                            _losses = sum(1 for d in _resolved if d.get("outcome_score", 0) < 0)
                            _total = _wins + _losses
                            if _total >= 3:
                                _wr = _wins / _total
                                if _wr < 0.35:
                                    _brain_adj -= 0.03
                                    _brain_reasons.append(f"history({_wins}W/{_losses}L={_wr:.0%})")
                                elif _wr > 0.65:
                                    _brain_adj += 0.02
                                    _brain_reasons.append(f"history({_wins}W/{_losses}L={_wr:.0%})")

                    # 3. LLM brain.think() for contextual reasoning (fast -> 3B for latency)
                    if self._shared_llm_call:
                        try:
                            _t0 = time.time()
                            _think = self._brain.think(
                                situation=f"{_situation} edge={sig.edge:.3f} confidence={sig.confidence:.2f}",
                                question="Should confidence be adjusted? Reply ONLY: +0.XX, -0.XX, or 0 (max +/-0.05). One number only.",
                                task_type="fast",
                                max_tokens=15,
                                temperature=0.1,
                            )
                            _think_elapsed = time.time() - _t0
                            if _think and _think.content and _think_elapsed < 3.0:
                                try:
                                    _llm_adj = float(_think.content.strip())
                                    _llm_adj = max(-0.05, min(0.05, _llm_adj))
                                    if _llm_adj != 0.0:
                                        _brain_adj += _llm_adj
                                        _brain_reasons.append(f"llm_think({_llm_adj:+.3f},{_think_elapsed:.1f}s)")
                                except (ValueError, TypeError):
                                    pass
                        except Exception:
                            pass  # LLM never blocks trading

                    # 4. Apply adjustment (capped at +/- 0.10 with LLM, was 0.05)
                    if _brain_adj != 0.0:
                        _brain_adj = max(-0.10, min(0.10, _brain_adj))
                        _old_conf = sig.confidence
                        sig.confidence = max(0.0, min(1.0, sig.confidence + _brain_adj))
                        log.info(
                            "  -> [BRAIN] Confidence adjusted %.2f -> %.2f (%+.3f) | Reasons: %s",
                            _old_conf, sig.confidence, _brain_adj, ", ".join(_brain_reasons),
                        )
            except Exception as _brain_err:
                log.debug("Brain pre-decision failed (non-fatal): %s", str(_brain_err)[:100])

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

            if conviction.ml_win_prob is not None:
                log.info("  -> [ML] Win prob: %.1f%% | ML pts: %.1f/4",
                         conviction.ml_win_prob * 100,
                         conviction.components.get("ml_win_probability", 0))

            if conviction.position_size_usd <= 0:
                log.info("  -> ConvictionEngine: score=%.0f [%s] -> $0 (NO TRADE)",
                         conviction.total_score, conviction.tier_label)
                continue

            # Cross-asset time-window exposure cap: block if same timeframe
            # has > total exposure across ALL assets combined
            CROSS_ASSET_TW_CAP = 15.0
            tw_exposure = sum(
                p.size_usd for p in self.tracker.open_positions
                if getattr(p, "timeframe", "") == timeframe
            )
            if tw_exposure + conviction.position_size_usd > CROSS_ASSET_TW_CAP:
                log.info(
                    "  -> Blocked: cross-asset time-window cap ($%.2f + $%.2f > $%.2f for %s)",
                    tw_exposure, conviction.position_size_usd, CROSS_ASSET_TW_CAP, timeframe,
                )
                continue

            # Risk check (pass actual conviction size, not default $10)
            allowed, reason = check_risk(self.cfg, sig, self.tracker, market_id,
                                         trade_size_usd=conviction.position_size_usd,
                                         drawdown_breaker=self.drawdown_breaker)
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

                # Publish trade_placed to shared event bus
                try:
                    from shared.events import publish as bus_publish
                    bus_publish(
                        agent="garves",
                        event_type="trade_placed",
                        data={
                            "asset": asset,
                            "direction": sig.direction,
                            "timeframe": timeframe,
                            "size_usd": round(conviction.position_size_usd, 2),
                            "edge": round(sig.edge, 4),
                            "conviction_score": round(conviction.total_score, 1),
                            "order_id": order_id,
                            "market_id": market_id,
                            "dry_run": self.cfg.dry_run,
                        },
                        summary=f"{sig.direction.upper()} {asset.upper()}/{timeframe} ${conviction.position_size_usd:.2f} (edge={sig.edge*100:.1f}%)",
                    )
                except Exception:
                    pass

                # Brain: record trade decision
                if self._brain:
                    try:
                        _ctx = f"{asset.upper()}/{timeframe} regime={regime.label} FnG={regime.fng_value} edge={sig.edge*100:.1f}% conf={sig.confidence:.2f}"
                        _dec = f"{sig.direction.upper()} size=${conviction.position_size_usd:.2f} conviction={conviction.total_score:.0f}"
                        _reason = f"Indicators: {str({k:v for k,v in (sig.indicator_votes or {}).items()})[:200]}"
                        _did = self._brain.remember_decision(_ctx, _dec, reasoning=_reason, confidence=sig.confidence, tags=[asset, timeframe, regime.label])
                        # Store decision_id on the trade for outcome tracking
                        self.perf_tracker.set_decision_id(f"{market_id[:12]}_{int(time.time())}", _did)
                    except Exception:
                        pass

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
                    size_usd=conviction.position_size_usd,
                    entry_price=round(sig.probability, 4),
                    ml_win_prob=conviction.ml_win_prob or 0.0,
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
        _prev_resolved = getattr(self.perf_tracker, '_total_resolved', 0)
        self.perf_tracker.check_resolutions()
        _new_resolved = getattr(self.perf_tracker, '_total_resolved', 0)
        _just_resolved = _new_resolved - _prev_resolved
        if _just_resolved > 0:
            self._trade_journal_counter += _just_resolved
            # Update drawdown breaker after new resolutions
            self.drawdown_breaker.update()

        if self.perf_tracker.pending_count > 0:
            log.info("Performance tracker: %d trades pending resolution", self.perf_tracker.pending_count)

        # ── Trade Journal Analysis: every 10 resolved trades, LLM analyzes patterns ──
        if self._trade_journal_counter >= 10 and self._brain and self._shared_llm_call:
            self._trade_journal_counter = 0
            try:
                _stats = self.perf_tracker.quick_stats() if hasattr(self.perf_tracker, 'quick_stats') else {}
                _wr = _stats.get("win_rate", 0)
                _pnl = _stats.get("total_pnl", 0)
                _total = _stats.get("total_trades", 0)
                _analysis = self._shared_llm_call(
                    system=(
                        "You are Garves's trade journal analyst. Analyze recent trading performance "
                        "and identify 1-2 actionable patterns. Be specific about what to keep doing "
                        "and what to change. Max 3 sentences."
                    ),
                    user=(
                        f"Last 10 trades resolved. Overall stats: {_total} trades, "
                        f"{_wr:.0%} win rate, ${_pnl:.2f} PnL. "
                        f"Regime: {regime.label if regime else 'unknown'}."
                    ),
                    agent="garves",
                    task_type="reasoning",
                    max_tokens=150,
                    temperature=0.3,
                )
                if _analysis:
                    log.info("[TRADE JOURNAL] LLM analysis: %s", _analysis.strip()[:300])
                    self._brain.learn_pattern(
                        "trade_journal",
                        _analysis.strip()[:200],
                        evidence_count=10, confidence=0.6,
                    )
            except Exception as _je:
                log.debug("Trade journal analysis failed: %s", str(_je)[:100])

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
        self.maker_engine.cancel_all()
        self.executor.cancel_all_open()
        await self.binance_feed.stop()
        await self.derivatives_feed.stop()
        await self.feed.stop()
        log.info("Shutdown complete")


def _kill_orphans() -> None:
    """Kill any other bot.main processes to prevent orphan buildup."""
    import subprocess
    my_pid = os.getpid()
    try:
        result = subprocess.run(
            ["pgrep", "-f", "bot.main"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            pid = int(line.strip())
            if pid != my_pid:
                try:
                    os.kill(pid, 9)
                except ProcessLookupError:
                    pass
    except Exception:
        pass


def main() -> None:
    _kill_orphans()
    cfg = Config()
    bot = TradingBot(cfg)
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
