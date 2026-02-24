"""OracleBot — main orchestrator for the weekly cycle.

Sunday 00:00 UTC cycle:
  1. Resolve last week's predictions
  2. Scan weekly markets (Above/Below, Price Range, Hit Price)
  3. Gather data context (prices, derivatives, macro, atlas, agents)
  4. Run ensemble (Claude + Gemini + Grok + local Qwen)
  5. Calculate edges and select trades
  6. Execute trades (or dry run)
  7. Generate report and update dashboard
  8. Send report to TG via Shelby
  9. Update Excel sheets
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from oracle.config import OracleConfig
from oracle.data_pipeline import MarketContext, gather_context
from oracle.edge_calculator import calculate_edges, select_trades
from oracle.ensemble import EnsembleResult, run_ensemble
from oracle.executor import execute_trades
from oracle.reporter import generate_report
from oracle.scanner import WeeklyMarket, scan_weekly_markets, filter_tradeable
from oracle.swarm import gather_agent_signals
from oracle.tracker import OracleTracker

# Cross-agent communication (optional — graceful if shared layer missing)
try:
    from shared.events import publish as bus_publish
except ImportError:
    def bus_publish(*a, **kw): pass

try:
    from shared.agent_brain import AgentBrain
except ImportError:
    AgentBrain = None

log = logging.getLogger(__name__)

DATA_DIR = Path.home() / "polymarket-bot" / "data"


class OracleBot:
    """The Weekly Crypto Oracle."""

    def __init__(self) -> None:
        self.cfg = OracleConfig()
        self.tracker = OracleTracker(self.cfg)
        self.brain = None
        if AgentBrain is not None:
            try:
                self.brain = AgentBrain("oracle", role="weekly crypto market analyst")
            except Exception:
                pass
        self._setup_logging()

    def _setup_logging(self) -> None:
        fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        logging.basicConfig(
            level=logging.INFO,
            format=fmt,
            datefmt="%H:%M:%S",
            handlers=[logging.StreamHandler(sys.stdout)],
        )

    async def run(self) -> None:
        """Main entry point — runs the weekly cycle then sleeps until next Sunday."""
        log.info("Oracle — The Weekly Crypto Oracle — started")
        log.info("Mode: %s | Bankroll: $%.0f | Max trades: %d",
                 "DRY RUN" if self.cfg.dry_run else "LIVE",
                 self.cfg.bankroll, self.cfg.max_trades_per_week)

        while True:
            try:
                # Check if it's time to run
                now = datetime.now(timezone.utc)
                if self._should_run(now):
                    await self._weekly_cycle()

                # Check for emergency mid-week trigger
                if self._check_emergency():
                    log.warning("Emergency volatility detected — running mid-week update")
                    await self._weekly_cycle(emergency=True)

                # Heartbeat so health monitors know Oracle is alive
                days_until_sunday = (6 - now.weekday()) % 7
                next_run = "TODAY" if days_until_sunday == 0 else f"in {days_until_sunday}d"
                log.info("[HEARTBEAT] alive | next cycle %s | %s",
                         next_run, now.strftime("%a %H:%M UTC"))

                # Sleep until next check (every 30 minutes)
                await asyncio.sleep(1800)

            except KeyboardInterrupt:
                break
            except Exception:
                log.exception("Cycle error")
                await asyncio.sleep(300)

        self.tracker.close()
        if self.brain:
            try:
                self.brain.close()
            except Exception:
                pass
        log.info("Oracle shut down")

    def _should_run(self, now: datetime) -> bool:
        """Check if it's Sunday 00:xx UTC."""
        if now.weekday() != 6:  # 6 = Sunday
            return False
        if now.hour != self.cfg.cycle_hour_utc:
            return False
        # Only run once per hour (check minute < 30 to avoid double-run)
        if now.minute > 30:
            return False

        # Check if we already ran this week
        status = self._read_status()
        last_run = status.get("last_run", "")
        if last_run and last_run[:10] == now.strftime("%Y-%m-%d"):
            return False

        return True

    def _check_emergency(self) -> bool:
        """Check if BTC moved enough for an emergency mid-week update."""
        status = self._read_status()
        btc_at_run = status.get("btc_price_at_run", 0)
        if btc_at_run <= 0:
            return False

        try:
            import requests
            resp = requests.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": "bitcoin", "vs_currencies": "usd"},
                timeout=5,
            )
            current = resp.json().get("bitcoin", {}).get("usd", 0)
            if current <= 0:
                return False

            change = abs(current - btc_at_run) / btc_at_run
            if change >= self.cfg.emergency_volatility_pct:
                # Only trigger once per emergency
                if not status.get("emergency_triggered"):
                    return True
        except Exception:
            pass
        return False

    async def _weekly_cycle(self, emergency: bool = False) -> None:
        """Execute the full weekly analysis and trading cycle."""
        cycle_type = "EMERGENCY MID-WEEK" if emergency else "WEEKLY"
        log.info("=" * 60)
        log.info("Starting %s cycle", cycle_type)
        log.info("=" * 60)

        now = datetime.now(timezone.utc)
        week_start = now.strftime("%Y-%m-%d")

        # Step 1: Resolve last week's predictions
        if not emergency:
            last_week = (now - timedelta(days=7)).strftime("%Y-%m-%d")
            resolution = self.tracker.resolve_predictions(last_week)
            if resolution.get("resolved", 0) > 0:
                log.info("Resolved %d predictions from last week, P&L: $%.2f",
                         resolution["resolved"], resolution.get("pnl", 0))
                bus_publish("oracle", "predictions_resolved", {
                    "resolved": resolution["resolved"],
                    "pnl": resolution.get("pnl", 0),
                    "week": last_week,
                })
                log.info("Published resolution to event bus")

        # Step 2: Scan weekly markets (Polymarket + Kalshi)
        log.info("Step 2: Scanning weekly markets...")
        poly_markets = scan_weekly_markets(self.cfg)

        # V9: Merge Kalshi crypto markets if enabled
        kalshi_markets = []
        cross_platform_pairs = None
        if self.cfg.kalshi_enabled:
            try:
                from oracle.scanner import scan_kalshi_crypto_markets
                kalshi_markets = scan_kalshi_crypto_markets(self.cfg)
                log.info("Kalshi scan: %d crypto markets", len(kalshi_markets))
            except Exception:
                log.warning("[KALSHI] Crypto scan failed (non-fatal)")

        all_markets = poly_markets + kalshi_markets

        # Find cross-platform arbitrage pairs
        if kalshi_markets:
            try:
                from oracle.edge_calculator import find_cross_platform_pairs
                cross_platform_pairs = find_cross_platform_pairs(poly_markets, kalshi_markets)
            except Exception:
                log.debug("[CROSS-PLATFORM] Pair detection failed (non-fatal)")

        tradeable = filter_tradeable(all_markets)
        log.info("Found %d total markets (%d Poly + %d Kalshi), %d tradeable",
                 len(all_markets), len(poly_markets), len(kalshi_markets), len(tradeable))

        if not tradeable:
            log.warning("No tradeable markets found, skipping cycle")
            self._write_status({"last_run": week_start, "error": "no tradeable markets"})
            return

        # Step 3: Gather data context
        log.info("Step 3: Gathering market data...")
        context = gather_context(self.cfg)

        # Step 3b: Add agent signals from swarm
        context.agent_signals = gather_agent_signals()

        # Step 4: Run ensemble
        log.info("Step 4: Running LLM ensemble (%d models)...", len(self.cfg.ensemble_weights))
        ensemble = run_ensemble(self.cfg, tradeable, context)

        if not ensemble.predictions:
            log.warning("Ensemble returned no predictions, skipping cycle")
            self._write_status({"last_run": week_start, "error": "no predictions"})
            return

        # Step 5: Calculate edges (with cross-platform boost if available)
        log.info("Step 5: Calculating edges...")
        signals = calculate_edges(
            self.cfg, tradeable, ensemble.predictions,
            cross_platform_pairs=cross_platform_pairs,
        )
        selected = select_trades(self.cfg, signals)

        # Step 6: Execute trades
        log.info("Step 6: Executing %d trades...", len(selected))
        results = execute_trades(self.cfg, selected)

        # Step 7: Record predictions
        log.info("Step 7: Recording predictions...")
        self.tracker.record_predictions(
            week_start, selected, results, ensemble.model_outputs,
        )

        # Step 8: Generate report
        accuracy = self.tracker.get_accuracy_stats()
        report = generate_report(
            self.cfg, context, ensemble, signals, selected, results, accuracy,
        )

        # Save report
        self.tracker.record_weekly_report(
            week_start=week_start,
            regime=ensemble.regime,
            confidence=ensemble.confidence,
            total_scanned=len(all_markets),
            tradeable=len(tradeable),
            trades_placed=len([r for r in results if r.success]),
            total_wagered=sum(t.size for t in selected),
            report_md=report,
            context_json=json.dumps(context.to_dict()),
        )

        # Step 9: Write status file for dashboard
        btc_price = context.prices.get("bitcoin", 0)
        self._write_status({
            "last_run": now.isoformat(),
            "week_start": week_start,
            "cycle_type": cycle_type,
            "regime": ensemble.regime,
            "confidence": ensemble.confidence,
            "markets_scanned": len(all_markets),
            "tradeable_markets": len(tradeable),
            "trades_placed": len([r for r in results if r.success]),
            "total_wagered": sum(t.size for t in selected),
            "total_expected_value": sum(t.expected_value for t in selected),
            "btc_price_at_run": btc_price,
            "emergency_triggered": emergency,
            "accuracy": accuracy,
            "dry_run": self.cfg.dry_run,
            "report": report,
            "predictions": [
                {
                    "question": s.market.question[:80],
                    "asset": s.market.asset,
                    "type": s.market.market_type,
                    "oracle_prob": round(s.oracle_prob, 3),
                    "market_prob": round(s.market_prob, 3),
                    "edge": round(s.edge, 3),
                    "side": s.side,
                    "conviction": s.conviction,
                    "size": s.size,
                }
                for s in signals[:20]
            ],
        })

        # Step 10: Update Excel
        self._update_excel(week_start, len(selected), sum(t.size for t in selected))

        # Log summary
        log.info("=" * 60)
        log.info("%s CYCLE COMPLETE", cycle_type)
        log.info("Markets: %d scanned, %d tradeable", len(all_markets), len(tradeable))
        log.info("Trades: %d placed, $%.0f wagered", len(selected), sum(t.size for t in selected))
        log.info("Regime: %s (%.0f%% confidence)", ensemble.regime, ensemble.confidence * 100)
        log.info("=" * 60)

        # Publish to event bus for cross-agent communication
        bus_publish("oracle", "weekly_predictions", {
            "trades": len(selected),
            "regime": ensemble.regime,
            "confidence": round(ensemble.confidence, 3),
            "wagered": round(sum(t.size for t in selected), 2),
            "week": week_start,
        })
        log.info("Published weekly predictions to event bus")

        # Record brain decision for learning
        if self.brain:
            try:
                self.brain.remember_decision(
                    context=f"Week {week_start}: {len(all_markets)} markets, "
                            f"regime={ensemble.regime}, BTC=${btc_price:,.0f}",
                    decision=f"Placed {len(selected)} trades, ${sum(t.size for t in selected):.0f} wagered",
                    reasoning=f"Ensemble confidence {ensemble.confidence:.0%}, "
                              f"{len(tradeable)} tradeable from {len(all_markets)} scanned",
                    confidence=ensemble.confidence,
                    tags=["weekly_cycle", ensemble.regime, cycle_type.lower()],
                )
                log.info("Recorded brain decision")
            except Exception:
                log.debug("Failed to record brain decision")

        # Print report to stdout (goes to log file)
        print("\n" + report + "\n")

    def _read_status(self) -> dict:
        path = self.cfg.status_path()
        try:
            if path.exists():
                return json.loads(path.read_text())
        except Exception:
            pass
        return {}

    def _write_status(self, data: dict) -> None:
        path = self.cfg.status_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, default=str))

    def _update_excel(self, week: str, trades: int, wagered: float) -> None:
        """Update progress Excel sheet."""
        try:
            sys.path.insert(0, str(Path.home() / "shared"))
            from progress import append_progress
            append_progress(
                agent="Oracle",
                change_type="cycle",
                feature="weekly_analysis",
                description=f"Week {week}: {trades} trades, ${wagered:.0f} wagered",
            )
        except Exception:
            log.debug("Failed to update Excel progress")
