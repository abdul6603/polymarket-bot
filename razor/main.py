"""RazorBot — The Mathematician. Async orchestrator for completeness arbitrage.

"The numbers never lie. When A + B < 1, the proof writes itself."
"""
from __future__ import annotations

import asyncio
import logging
import signal
import time

from razor.config import RazorConfig
from razor.scanner import scan_all_markets, RazorMarket
from razor.feed import RazorFeed
from razor.engine import RazorEngine
from razor.executor import RazorExecutor
from razor.tracker import RazorTracker
from bot.auth import build_client
from bot.config import Config

log = logging.getLogger(__name__)

BANNER = """
=== RAZOR — The Mathematician ===
"The numbers never lie. When A + B < 1, the proof writes itself."
Bankroll: ${bankroll:,.0f} | Max/trade: ${max_trade} | Scan: {scan_s}s | Markets: ALL
Mode: {mode}
"""


class RazorBot:
    """Async orchestrator — discovery, arb scanning, exit management, status."""

    def __init__(self):
        self.cfg = RazorConfig()
        self.tracker = RazorTracker()
        self.feed: RazorFeed | None = None
        self.engine: RazorEngine | None = None
        self.executor: RazorExecutor | None = None
        self._markets: list[RazorMarket] = []
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._execute_lock = asyncio.Lock()  # Prevent race between arb_loop and clob_scan_loop

    async def run(self) -> None:
        """Main entry point — init, discover, loop."""
        self._setup_logging()

        print(BANNER.format(
            bankroll=self.cfg.bankroll_usd,
            max_trade=int(self.cfg.max_per_trade),
            scan_s=self.cfg.scan_interval_s,
            mode="DRY RUN" if self.cfg.dry_run else "LIVE",
        ))

        if not self.cfg.enabled:
            log.warning("Razor is disabled (RAZOR_ENABLED=false). Exiting.")
            return

        # Init CLOB client (reuse Garves creds via bot.config)
        bot_cfg = Config()
        client = build_client(bot_cfg)
        if not client and not self.cfg.dry_run:
            log.error("CLOB connection failed — cannot run in live mode")
            return
        if not client:
            log.warning("CLOB connection failed — running in dry-run mode only")

        self.executor = RazorExecutor(self.cfg, client)
        self.engine = RazorEngine(self.cfg, self.executor, self.tracker)
        self.feed = RazorFeed(self.cfg)

        # Initial market discovery
        log.info("Initial Gamma scan...")
        self._markets = scan_all_markets(self.cfg)
        log.info("Discovered %d binary markets", len(self._markets))

        if not self._markets:
            log.warning("No markets found — will retry on next discovery cycle")

        # Subscribe WS feed to all tokens
        await self.feed.start()
        token_ids = []
        for m in self._markets:
            token_ids.extend([m.token_a_id, m.token_b_id])
        if token_ids:
            await self.feed.subscribe(token_ids)
            log.info("Subscribed to %d tokens via WebSocket", len(token_ids))

        # Launch concurrent loops
        self._running = True
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self._shutdown()))

        self._tasks = [
            asyncio.create_task(self._discovery_loop()),
            asyncio.create_task(self._arb_loop()),
            asyncio.create_task(self._clob_scan_loop()),
            asyncio.create_task(self._status_loop()),
        ]

        log.info("Razor is running — 4 async loops active")

        try:
            await asyncio.gather(*self._tasks)
        except asyncio.CancelledError:
            pass

        log.info("Razor stopped.")

    async def _discovery_loop(self) -> None:
        """Re-discover markets every gamma_refresh_s (5 min)."""
        while self._running:
            try:
                await asyncio.sleep(self.cfg.gamma_refresh_s)
                if not self._running:
                    break

                log.info("Re-scanning Gamma API for new markets...")
                new_markets = await asyncio.get_event_loop().run_in_executor(
                    None, scan_all_markets, self.cfg,
                )

                # Find new markets not in current set
                current_ids = {m.condition_id for m in self._markets}
                added = [m for m in new_markets if m.condition_id not in current_ids]

                self._markets = new_markets

                if added and self.feed:
                    new_tokens = []
                    for m in added:
                        new_tokens.extend([m.token_a_id, m.token_b_id])
                    await self.feed.subscribe(new_tokens)
                    log.info("Discovery: %d total markets (+%d new, %d new tokens)",
                             len(self._markets), len(added), len(new_tokens))
                else:
                    log.info("Discovery: %d total markets (no new)", len(self._markets))

            except asyncio.CancelledError:
                return
            except Exception:
                log.exception("Discovery loop error")
                await asyncio.sleep(30)

    async def _arb_loop(self) -> None:
        """Main arb loop — scan prices + execute + manage exits every scan_interval_s."""
        # Wait a few seconds for WS to warm up
        await asyncio.sleep(3)

        while self._running:
            try:
                t0 = time.time()

                if self.engine and self.feed and self._markets:
                    # 1. Scan for opportunities (pure math, microsecond)
                    opps = self.engine.scan_opportunities(self._markets, self.feed)

                    # 2. Execute top opportunities (CLOB verification + order placement)
                    if opps:
                        async with self._execute_lock:
                            for opp in opps[:3]:
                                self.engine.execute_arb(opp)

                    # 3. Manage exits on all open positions
                    self.engine.manage_exits(self.feed)

                    # 4. Check settlements periodically (every ~60 cycles)
                    if int(t0) % 60 == 0:
                        await asyncio.get_event_loop().run_in_executor(
                            None, self.engine.check_settlements,
                        )

                elapsed = time.time() - t0
                sleep_time = max(0, self.cfg.scan_interval_s - elapsed)
                await asyncio.sleep(sleep_time)

            except asyncio.CancelledError:
                return
            except Exception:
                log.exception("Arb loop error")
                await asyncio.sleep(5)

    async def _clob_scan_loop(self) -> None:
        """CLOB batch scanner — rotates through ALL markets via REST orderbook.

        Catches arbs even when WS doesn't deliver ask data for a token.
        Checks 30 markets every 5s → full rotation of 4000 markets in ~11 min.
        """
        await asyncio.sleep(10)  # Let WS warm up first

        while self._running:
            try:
                if self.engine and self._markets:
                    opps = await asyncio.get_event_loop().run_in_executor(
                        None, self.engine.clob_batch_scan, self._markets,
                    )
                    if opps:
                        async with self._execute_lock:
                            for opp in opps[:3]:
                                self.engine.execute_arb(opp)

                await asyncio.sleep(5)

            except asyncio.CancelledError:
                return
            except Exception:
                log.exception("CLOB scan loop error")
                await asyncio.sleep(10)

    async def _status_loop(self) -> None:
        """Write status JSON + log stats every 30 seconds."""
        while self._running:
            try:
                await asyncio.sleep(30)
                if not self._running:
                    break

                stats = self.tracker.stats()
                feed_info = {
                    "ws_connected": self.feed.connected if self.feed else False,
                    "ws_tokens": self.feed.token_count if self.feed else 0,
                    "total_markets": len(self._markets),
                    "scan_interval_s": self.cfg.scan_interval_s,
                    "dry_run": self.cfg.dry_run,
                    "bankroll_usd": self.cfg.bankroll_usd,
                    "max_per_trade": self.cfg.max_per_trade,
                    "last_opps": len(self.engine.last_opportunities) if self.engine else 0,
                }
                self.tracker.save_status(extra=feed_info)

                log.info(
                    "STATUS: open=%d | exposure=$%.2f | PnL=$%.2f | "
                    "arbs=%d | markets=%d | ws=%s | opps=%d",
                    stats["open_count"], stats["exposure"], stats["total_pnl"],
                    stats["total_arbs"], len(self._markets),
                    "OK" if feed_info["ws_connected"] else "DOWN",
                    feed_info["last_opps"],
                )

            except asyncio.CancelledError:
                return
            except Exception:
                log.exception("Status loop error")
                await asyncio.sleep(30)

    async def _shutdown(self) -> None:
        """Graceful shutdown — close positions if live, stop feed."""
        log.info("Shutting down Razor...")
        self._running = False

        # If live and open positions, try to sell all
        if not self.cfg.dry_run and self.executor and self.tracker.open_positions:
            log.warning("Selling %d open positions on shutdown...", self.tracker.open_count)
            for pos in self.tracker.open_positions:
                self.executor.sell_both_sides(pos.token_a_id, pos.token_b_id, pos.shares)
                self.tracker.close_position(pos, 0.0, "shutdown")

        if self.feed:
            await self.feed.stop()

        for task in self._tasks:
            if not task.done():
                task.cancel()

        # Final status save
        self.tracker.save_status()

    def _setup_logging(self) -> None:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        # Quiet noisy libs
        logging.getLogger("websockets").setLevel(logging.WARNING)
        logging.getLogger("urllib3").setLevel(logging.WARNING)
