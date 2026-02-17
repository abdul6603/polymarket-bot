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
from hawk.edge import calculate_edge, calculate_confidence_tier, rank_opportunities
from hawk.executor import HawkExecutor
from hawk.tracker import HawkTracker
from hawk.risk import HawkRiskManager
from hawk.resolver import resolve_paper_trades
from hawk.briefing import generate_briefing

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
STATUS_FILE = DATA_DIR / "hawk_status.json"
OPPS_FILE = DATA_DIR / "hawk_opportunities.json"
SUGGESTIONS_FILE = DATA_DIR / "hawk_suggestions.json"
MODE_FILE = DATA_DIR / "hawk_mode.json"

from zoneinfo import ZoneInfo
ET = ZoneInfo("America/New_York")

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


def _save_suggestions(suggestions: list[dict]) -> None:
    """Save trade suggestions for dashboard review."""
    try:
        DATA_DIR.mkdir(exist_ok=True)
        SUGGESTIONS_FILE.write_text(json.dumps({
            "suggestions": suggestions,
            "updated": time.time(),
        }, indent=2))
    except Exception:
        log.exception("Failed to save Hawk suggestions")


def _load_viper_context() -> dict:
    """Load Viper market context intel for suggestion enrichment."""
    ctx_file = DATA_DIR / "viper_market_context.json"
    if ctx_file.exists():
        try:
            return json.loads(ctx_file.read_text())
        except Exception:
            pass
    return {}


class HawkBot:
    """The Poker Shark — scans all Polymarket markets and trades mispriced contracts."""

    def __init__(self):
        self.cfg = HawkConfig()
        self.tracker = HawkTracker()
        self.risk = HawkRiskManager(self.cfg, self.tracker)
        self.executor: HawkExecutor | None = None
        self.cycle = 0

    def _check_mode_toggle(self) -> None:
        """Check if mode was toggled via dashboard and update cfg accordingly."""
        if not MODE_FILE.exists():
            return
        try:
            mode_data = json.loads(MODE_FILE.read_text())
            new_dry_run = mode_data.get("dry_run", self.cfg.dry_run)
            if new_dry_run != self.cfg.dry_run:
                old_mode = "DRY RUN" if self.cfg.dry_run else "LIVE"
                new_mode = "DRY RUN" if new_dry_run else "LIVE"
                object.__setattr__(self.cfg, "dry_run", new_dry_run)
                log.info("Mode toggled: %s -> %s", old_mode, new_mode)
                # Reinitialize executor if switching to live
                if not new_dry_run:
                    self._init_executor()
        except Exception:
            log.exception("Failed to read mode toggle file")

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
                # Check mode toggle from dashboard
                self._check_mode_toggle()

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

                # 2. Smart market selection
                # Filter for "contested" markets where real debate exists:
                # - YES price between 15-85% (not penny bets or near-certainties)
                # - Prefer near-term events (days/weeks, not years)
                contested = []
                for m in markets:
                    yes_price = 0.5
                    for t in m.tokens:
                        if (t.get("outcome") or "").lower() in ("yes", "up"):
                            try:
                                yes_price = float(t.get("price", 0.5))
                            except (ValueError, TypeError):
                                pass
                            break
                    # Only contested markets — real uncertainty
                    if 0.12 <= yes_price <= 0.88:
                        contested.append(m)

                log.info("Contested markets (12-88%% price): %d / %d total", len(contested), len(markets))

                # Sort contested by volume, skip top 5 (still very efficient)
                contested.sort(key=lambda m: m.volume, reverse=True)
                target_markets = contested[5:45] if len(contested) > 45 else contested

                # 3. Analyze with GPT-4o (max 30 to balance cost vs coverage)
                target_markets = target_markets[:30]
                log.info("Analyzing %d contested mid-tier markets with GPT-4o...", len(target_markets))
                estimates = batch_analyze(self.cfg, target_markets, max_concurrent=5)

                # 4. Calculate edges
                opportunities = []
                estimate_map = {e.market_id: e for e in estimates}
                for market in target_markets:
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
                        "condition_id": o.market.condition_id,
                        "market_price": _get_yes_price(o.market),
                        "estimated_prob": o.estimate.estimated_prob,
                        "edge": o.edge,
                        "direction": o.direction,
                        "position_size": o.position_size_usd,
                        "expected_value": o.expected_value,
                        "reasoning": o.estimate.reasoning[:200],
                    })
                _save_opportunities(opp_data)

                # Generate briefing for Viper — targeted intel queries
                try:
                    generate_briefing(opp_data, self.cycle)
                except Exception:
                    log.exception("Failed to generate Hawk briefing")

                # 6. Build suggestions for dashboard review
                viper_ctx = _load_viper_context()
                suggestions = []
                for opp in ranked:
                    allowed, reason = self.risk.check_trade(opp)
                    if not allowed:
                        log.info("Risk blocked: %s", reason)
                        continue
                    cid = opp.market.condition_id
                    has_viper = len(viper_ctx.get(cid, [])) > 0
                    tier_info = calculate_confidence_tier(opp, has_viper_intel=has_viper)
                    suggestions.append({
                        "condition_id": cid,
                        "token_id": opp.token_id,
                        "question": opp.market.question[:200],
                        "category": opp.market.category,
                        "direction": opp.direction,
                        "position_size": round(opp.position_size_usd, 2),
                        "edge": round(opp.edge, 4),
                        "expected_value": round(opp.expected_value, 4),
                        "market_price": _get_yes_price(opp.market),
                        "estimated_prob": opp.estimate.estimated_prob,
                        "confidence": opp.estimate.confidence,
                        "reasoning": opp.estimate.reasoning[:300],
                        "score": tier_info["score"],
                        "tier": tier_info["tier"],
                        "viper_intel_count": len(viper_ctx.get(cid, [])),
                        "end_date": opp.market.end_date,
                        "volume": opp.market.volume,
                        "event_title": opp.market.event_title,
                    })

                _save_suggestions(suggestions)
                log.info("Saved %d trade suggestions (HIGH: %d, MEDIUM: %d, SPEC: %d)",
                         len(suggestions),
                         sum(1 for s in suggestions if s["tier"] == "HIGH"),
                         sum(1 for s in suggestions if s["tier"] == "MEDIUM"),
                         sum(1 for s in suggestions if s["tier"] == "SPECULATIVE"))

                # Check fills (live mode only)
                if self.executor and not self.cfg.dry_run:
                    self.executor.check_fills()

                # Resolve paper trades — check if any markets have settled
                if self.cfg.dry_run:
                    res = resolve_paper_trades()
                    if res["resolved"] > 0:
                        log.info(
                            "Resolved %d trades: %d W / %d L | P&L: $%.2f",
                            res["resolved"], res["wins"], res["losses"],
                            res.get("total_pnl", 0.0),
                        )
                        # Record PnL with risk manager so daily loss cap works
                        self.risk.record_pnl(res.get("total_pnl", 0.0))
                        # Reload tracker positions from disk
                        self.tracker._positions = []
                        self.tracker._load_positions()

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
