from __future__ import annotations

import asyncio
import logging
import signal
import sys
import time

from bot.binance_feed import BinanceFeed
from bot.config import Config
from bot.http_session import get_session
from bot.auth import build_client
from bot.execution import Executor
from bot.market_discovery import fetch_markets, rank_markets
from bot.price_cache import PriceCache
from bot.regime import RegimeAdjustment, detect_regime
from bot.risk import PositionTracker, check_risk
from bot.signals import SignalEngine
from bot.straddle import StraddleEngine
from bot.tracker import PerformanceTracker
from bot.ws_feed import MarketFeed
from bot.v2_tools import is_emergency_stopped, accept_commands, process_command, daily_trade_report

log = logging.getLogger("bot")


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

        self.executor = Executor(cfg, self.client, self.tracker)
        self.straddle_engine = StraddleEngine(cfg, self.executor, self.tracker, self.price_cache)
        self.perf_tracker = PerformanceTracker(cfg)
        self._shutdown_event = asyncio.Event()
        self._subscribed_tokens: set[str] = set()
        # Track when tokens were first subscribed (for warmup)
        self._subscribe_time: dict[str, float] = {}
        # Per-market cooldown: market_id -> last trade timestamp
        self._market_cooldown: dict[str, float] = {}
        self.COOLDOWN_SECONDS = 300  # 5 min cooldown after trading a market

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
        log.info("Assets: BTC, ETH, SOL | Timeframes: 5m, 15m, 1h, 4h")
        log.info("Ensemble: 11 indicators + Temporal Arb + ATR Filter + Fee Awareness")
        log.info("Risk: max %d concurrent, $%.2f cap, 5min cooldown",
                 self.cfg.max_concurrent_positions, self.cfg.max_position_usd)
        log.info("Dry run: %s | Tick: %ds", self.cfg.dry_run, self.cfg.tick_interval_s)
        log.info("V2: emergency_stop, trade_journal, shelby_commands, trade_alerts")
        log.info("=" * 60)

        # Start Binance real-time price feed + Polymarket WebSocket feed
        await self.binance_feed.start()
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

    async def _tick(self) -> None:
        """Single tick: evaluate ALL discovered markets, trade any with edge."""
        log.info("--- Tick ---")

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

            # Generate signal for this specific market
            sig = self.signal_engine.generate_signal(
                up_token, down_token,
                asset=asset,
                timeframe=timeframe,
                implied_up_price=implied_up,
                orderbook=ob,
                regime=regime,
            )
            if not sig:
                continue

            log.info(
                "[%s/%s] SIGNAL: %s (prob=%.3f, edge=%.1f%%, conf=%.2f) | Implied: %s | %s",
                asset.upper(), timeframe, sig.direction.upper(),
                sig.probability, sig.edge * 100, sig.confidence,
                f"${implied_up:.3f}" if implied_up else "N/A",
                dm.question[:50],
            )

            # Risk check
            allowed, reason = check_risk(self.cfg, sig, self.tracker, market_id)
            if not allowed:
                log.info("  -> Blocked: %s", reason)
                continue

            # Execute
            order_id = self.executor.place_order(sig, market_id)
            if order_id:
                trades_this_tick += 1
                self._market_cooldown[market_id] = now
                log.info("  -> Order placed: %s", order_id)

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

        # Check existing fills (+ expire dry-run positions)
        self.executor.check_fills()

        # Check market resolutions for performance tracking
        self.perf_tracker.check_resolutions()
        if self.perf_tracker.pending_count > 0:
            log.info("Performance tracker: %d trades pending resolution", self.perf_tracker.pending_count)

    async def _cleanup(self) -> None:
        log.info("Shutting down...")
        self.executor.cancel_all_open()
        await self.binance_feed.stop()
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
