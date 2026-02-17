"""Viper Main Bot Loop — scan web, audit costs, score opportunities, push to Shelby."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from viper.config import ViperConfig
from viper.scanner import scan_all
from viper.cost_audit import audit_all
from viper.monetize import get_soren_metrics
from viper.scorer import score_opportunity
from viper.shelby_push import push_to_shelby

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
STATUS_FILE = DATA_DIR / "viper_status.json"
OPPS_FILE = DATA_DIR / "viper_opportunities.json"
COSTS_FILE = DATA_DIR / "viper_costs.json"

ET = timezone(timedelta(hours=-5))

BRAIN_FILE = DATA_DIR / "brains" / "viper.json"


def _load_brain_notes() -> list[dict]:
    """Load brain notes for Viper."""
    if BRAIN_FILE.exists():
        try:
            data = json.loads(BRAIN_FILE.read_text())
            return data.get("notes", [])
        except Exception:
            pass
    return []


def _save_status(summary: dict) -> None:
    """Save current status for dashboard."""
    DATA_DIR.mkdir(exist_ok=True)
    summary["last_update"] = datetime.now(ET).isoformat()
    try:
        STATUS_FILE.write_text(json.dumps(summary, indent=2))
    except Exception:
        log.exception("Failed to save Viper status")


def _save_opportunities(opps: list[dict]) -> None:
    """Save opportunities for dashboard."""
    try:
        OPPS_FILE.write_text(json.dumps({"opportunities": opps, "updated": time.time()}, indent=2))
    except Exception:
        log.exception("Failed to save Viper opportunities")


def _save_costs(costs: dict) -> None:
    """Save cost audit for dashboard."""
    try:
        COSTS_FILE.write_text(json.dumps(costs, indent=2))
    except Exception:
        log.exception("Failed to save Viper costs")


class ViperBot:
    """The Silent Assassin — finds revenue opportunities and pushes them to Shelby."""

    def __init__(self):
        self.cfg = ViperConfig()
        self.cycle = 0
        self.total_found = 0
        self.total_pushed = 0

    async def run(self) -> None:
        """Main loop — runs every cfg.cycle_minutes."""
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)-7s [VIPER] %(message)s",
            datefmt="%H:%M:%S",
        )
        log.info("Viper starting — The Silent Assassin")
        log.info("Config: cycle=%dmin, min_score=%d, dry_run=%s",
                 self.cfg.cycle_minutes, self.cfg.min_opportunity_score, self.cfg.dry_run)

        _save_status({"running": True, "total_found": 0, "pushed_to_shelby": 0})

        while True:
            self.cycle += 1
            log.info("=== Viper Cycle %d ===", self.cycle)

            try:
                # Read brain notes
                notes = _load_brain_notes()
                if notes:
                    latest = notes[-1]
                    log.info("Brain note: [%s] %s", latest.get("topic", "?"), latest.get("content", "")[:100])

                # 1. Scan all sources
                log.info("Scanning web for opportunities...")
                opportunities = scan_all()
                self.total_found += len(opportunities)

                # 2. Score all opportunities
                scored = []
                for opp in opportunities:
                    score = score_opportunity(opp)
                    scored.append((opp, score))
                scored.sort(key=lambda x: x[1], reverse=True)

                # Save for dashboard
                opp_data = []
                for opp, score in scored:
                    opp_data.append({
                        "id": opp.id,
                        "source": opp.source,
                        "title": opp.title[:200],
                        "description": opp.description[:300],
                        "estimated_value_usd": opp.estimated_value_usd,
                        "effort_hours": opp.effort_hours,
                        "urgency": opp.urgency,
                        "confidence": opp.confidence,
                        "url": opp.url,
                        "category": opp.category,
                        "score": score,
                        "status": "pushed" if score >= self.cfg.min_opportunity_score else "low_score",
                    })
                _save_opportunities(opp_data)

                # 3. Push high-score to Shelby
                pushed_count = 0
                for opp, score in scored:
                    if score >= self.cfg.min_opportunity_score:
                        if self.cfg.dry_run:
                            log.info("[DRY RUN] Would push to Shelby: %s (score=%d)", opp.title[:50], score)
                            pushed_count += 1
                        else:
                            if push_to_shelby(self.cfg, opp, score):
                                pushed_count += 1
                self.total_pushed += pushed_count

                # 4. Cost audit
                log.info("Running API cost audit...")
                cost_data = audit_all()
                _save_costs(cost_data)
                log.info("Total estimated monthly API cost: $%.2f", cost_data.get("total_monthly", 0))

                # 5. Check Soren metrics
                log.info("Checking Soren monetization metrics...")
                soren_metrics = get_soren_metrics(self.cfg)
                log.info("Soren: %d followers, %.1f%% engagement, CPM=$%.2f",
                         soren_metrics.get("followers", 0),
                         soren_metrics.get("engagement_rate", 0) * 100,
                         soren_metrics.get("estimated_cpm", 0))

                # 6. Calculate summary
                revenue_potential = sum(opp.estimated_value_usd for opp, s in scored if s >= self.cfg.min_opportunity_score)
                cost_savings = sum(c["cost_usd"] for c in cost_data.get("costs", []) if c.get("waste"))

                summary = {
                    "running": True,
                    "cycle": self.cycle,
                    "total_found": self.total_found,
                    "pushed_to_shelby": self.total_pushed,
                    "revenue_potential": round(revenue_potential, 0),
                    "cost_savings": round(cost_savings, 0),
                    "this_cycle_found": len(opportunities),
                    "this_cycle_pushed": pushed_count,
                }
                _save_status(summary)

                log.info("Cycle %d: found=%d, scored=%d high, pushed=%d",
                         self.cycle, len(opportunities),
                         sum(1 for _, s in scored if s >= self.cfg.min_opportunity_score),
                         pushed_count)

            except Exception:
                log.exception("Viper cycle %d failed", self.cycle)
                _save_status({"running": True, "cycle": self.cycle, "total_found": self.total_found,
                              "pushed_to_shelby": self.total_pushed, "error": True})

            log.info("Viper cycle %d complete. Sleeping %d minutes...", self.cycle, self.cfg.cycle_minutes)
            await asyncio.sleep(self.cfg.cycle_minutes * 60)
