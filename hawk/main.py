"""Hawk Main Bot Loop — scan markets, analyze with GPT-4o, execute trades."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from hawk.config import HawkConfig
from hawk.scanner import scan_all_markets
from hawk.analyst import batch_analyze
from hawk.edge import calculate_edge, rank_opportunities
from hawk.executor import HawkExecutor
from hawk.tracker import HawkTracker
from hawk.risk import HawkRiskManager

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
STATUS_FILE = DATA_DIR / "hawk_status.json"
OPPS_FILE = DATA_DIR / "hawk_opportunities.json"

ET = timezone(timedelta(hours=-5))

# Brain notes file
BRAIN_FILE = DATA_DIR / "brains" / "hawk.json"


def _load_brain_notes() -> list[dict]:
    """Load brain notes for Hawk."""
    if BRAIN_FILE.exists():
        try:
            data = json.loads(BRAIN_FILE.read_text())
            return data.get("notes", [])
        except Exception:
            pass
    return []


def _save_status(tracker: HawkTracker, running: bool = True, cycle: int = 0) -> None:
    """Save current status to data/hawk_status.json for dashboard."""
    DATA_DIR.mkdir(exist_ok=True)
    summary = tracker.summary()
    summary["running"] = running
    summary["cycle"] = cycle
    summary["last_update"] = datetime.now(ET).isoformat()
    try:
        STATUS_FILE.write_text(json.dumps(summary, indent=2))
    except Exception:
        log.exception("Failed to save Hawk status")


def _save_opportunities(opps: list[dict]) -> None:
    """Save latest opportunities for dashboard."""
    try:
        OPPS_FILE.write_text(json.dumps({"opportunities": opps, "updated": time.time()}, indent=2))
    except Exception:
        log.exception("Failed to save Hawk opportunities")


class HawkBot:
    """The Poker Shark — scans all Polymarket markets and trades mispriced contracts."""

    def __init__(self):
        self.cfg = HawkConfig()
        self.tracker = HawkTracker()
        self.risk = HawkRiskManager(self.cfg, self.tracker)
        self.executor: HawkExecutor | None = None
        self.cycle = 0

    def _init_executor(self) -> None:
        """Initialize CLOB client and executor."""
        client = None
        if not self.cfg.dry_run:
            try:
                from bot.auth import build_client
                from bot.config import Config
                garves_cfg = Config()
                client = build_client(garves_cfg)
            except Exception:
                log.warning("Could not initialize CLOB client, running in dry-run mode")
        self.executor = HawkExecutor(self.cfg, client, self.tracker)

    async def run(self) -> None:
        """Main loop — runs every cfg.cycle_minutes."""
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)-7s [HAWK] %(message)s",
            datefmt="%H:%M:%S",
        )
        log.info("Hawk starting — The Poker Shark")
        log.info("Config: bankroll=$%.0f, max_bet=$%.0f, max_concurrent=%d, min_edge=%.0f%%",
                 self.cfg.bankroll_usd, self.cfg.max_bet_usd, self.cfg.max_concurrent, self.cfg.min_edge * 100)
        log.info("Mode: %s", "DRY RUN" if self.cfg.dry_run else "LIVE TRADING")

        self._init_executor()
        _save_status(self.tracker, running=True, cycle=0)

        while True:
            self.cycle += 1
            log.info("=== Hawk Cycle %d ===", self.cycle)

            try:
                # Read brain notes
                notes = _load_brain_notes()
                if notes:
                    latest = notes[-1]
                    log.info("Brain note: [%s] %s", latest.get("topic", "?"), latest.get("content", "")[:100])

                # Daily reset
                self.risk.daily_reset()

                # Check shutdown
                if self.risk.is_shutdown():
                    log.warning("Daily loss cap hit — skipping cycle")
                    _save_status(self.tracker, running=True, cycle=self.cycle)
                    await asyncio.sleep(self.cfg.cycle_minutes * 60)
                    continue

                # 1. Scan all markets
                log.info("Scanning Polymarket markets...")
                markets = scan_all_markets(self.cfg)
                log.info("Found %d eligible markets", len(markets))

                if not markets:
                    _save_status(self.tracker, running=True, cycle=self.cycle)
                    await asyncio.sleep(self.cfg.cycle_minutes * 60)
                    continue

                # 2. Sort by volume and take top 20 for GPT analysis
                markets.sort(key=lambda m: m.volume, reverse=True)
                top_markets = markets[:20]

                # 3. Analyze with GPT-4o
                log.info("Analyzing top %d markets with GPT-4o...", len(top_markets))
                estimates = batch_analyze(self.cfg, top_markets, max_concurrent=5)

                # 4. Calculate edges
                opportunities = []
                estimate_map = {e.market_id: e for e in estimates}
                for market in top_markets:
                    est = estimate_map.get(market.condition_id)
                    if est:
                        opp = calculate_edge(market, est, self.cfg)
                        if opp:
                            opportunities.append(opp)

                # 5. Rank by expected value
                ranked = rank_opportunities(opportunities)
                log.info("Found %d opportunities with edge >= %.0f%%", len(ranked), self.cfg.min_edge * 100)

                # Save opportunities for dashboard
                opp_data = []
                for o in ranked:
                    opp_data.append({
                        "question": o.market.question[:200],
                        "category": o.market.category,
                        "market_price": _get_yes_price(o.market),
                        "estimated_prob": o.estimate.estimated_prob,
                        "edge": o.edge,
                        "direction": o.direction,
                        "position_size": o.position_size_usd,
                        "expected_value": o.expected_value,
                        "reasoning": o.estimate.reasoning[:200],
                    })
                _save_opportunities(opp_data)

                # 6. Execute trades
                if self.executor:
                    for opp in ranked:
                        allowed, reason = self.risk.check_trade(opp)
                        if not allowed:
                            log.info("Risk blocked: %s", reason)
                            continue
                        order_id = self.executor.place_order(opp)
                        if order_id:
                            log.info("Trade placed: %s | %s | edge=%.1f%%",
                                     opp.direction.upper(), opp.market.question[:50], opp.edge * 100)

                    # Check fills
                    self.executor.check_fills()

                # Save status
                _save_status(self.tracker, running=True, cycle=self.cycle)

            except Exception:
                log.exception("Hawk cycle %d failed", self.cycle)
                _save_status(self.tracker, running=True, cycle=self.cycle)

            log.info("Hawk cycle %d complete. Sleeping %d minutes...", self.cycle, self.cfg.cycle_minutes)
            await asyncio.sleep(self.cfg.cycle_minutes * 60)


def _get_yes_price(market) -> float:
    """Get YES price from market tokens."""
    for t in market.tokens:
        outcome = (t.get("outcome") or "").lower()
        if outcome in ("yes", "up"):
            try:
                return float(t.get("price", 0.5))
            except (ValueError, TypeError):
                return 0.5
    return 0.5
