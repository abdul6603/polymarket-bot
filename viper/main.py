"""Viper Main Loop — 24/7 market intelligence engine.

Scans real-time data sources every 5 minutes:
  1. Tavily — breaking news across all categories
  2. Polymarket — volume spikes, trending markets
  3. Reddit — prediction market communities

Feeds intelligence directly to Hawk for enhanced probability analysis.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

from viper.config import ViperConfig
from viper.scanner import scan_all
from viper.intel import append_intel, load_intel, save_intel, IntelItem
from viper.market_matcher import update_market_context
from viper.cost_audit import audit_all
from viper.scorer import score_intel

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
STATUS_FILE = DATA_DIR / "viper_status.json"
OPPS_FILE = DATA_DIR / "viper_opportunities.json"
COSTS_FILE = DATA_DIR / "viper_costs.json"

ET = timezone(timedelta(hours=-5))

BRAIN_FILE = DATA_DIR / "brains" / "viper.json"


def _load_brain_notes() -> list[dict]:
    if BRAIN_FILE.exists():
        try:
            data = json.loads(BRAIN_FILE.read_text())
            return data.get("notes", [])
        except Exception:
            pass
    return []


def _save_status(summary: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    summary["last_update"] = datetime.now(ET).isoformat()
    try:
        STATUS_FILE.write_text(json.dumps(summary, indent=2))
    except Exception:
        log.exception("Failed to save Viper status")


def _save_opportunities(items: list[dict]) -> None:
    try:
        OPPS_FILE.write_text(json.dumps({"opportunities": items, "updated": time.time()}, indent=2))
    except Exception:
        log.exception("Failed to save Viper opportunities")


def _save_costs(costs: dict) -> None:
    try:
        COSTS_FILE.write_text(json.dumps(costs, indent=2))
    except Exception:
        log.exception("Failed to save Viper costs")


def run_single_scan(cfg: ViperConfig) -> dict:
    """Run a single intelligence scan cycle. Used by both the main loop and the API trigger."""
    result = {"intel_count": 0, "matched": 0, "sources": {}}

    # 1. Scan all sources
    intel_items = scan_all(cfg.tavily_api_key, cfg.clob_host)
    result["intel_count"] = len(intel_items)

    # Count by source
    for item in intel_items:
        src = item.source.split("/")[0] if "/" in item.source else item.source
        result["sources"][src] = result["sources"].get(src, 0) + 1

    # 2. Append to intel feed (deduplicates)
    new_count = append_intel(intel_items)
    result["new_items"] = new_count

    # 3. Score and save as opportunities
    scored = []
    for item in intel_items:
        score = score_intel(item)
        d = asdict(item)
        d["score"] = score
        scored.append(d)
    scored.sort(key=lambda x: x["score"], reverse=True)
    _save_opportunities(scored[:100])

    # 4. Match intel to markets → build context for Hawk
    matched = update_market_context()
    result["matched"] = matched

    return result


class ViperBot:
    """The Silent Assassin — 24/7 market intelligence engine feeding Hawk."""

    def __init__(self):
        self.cfg = ViperConfig()
        self.cycle = 0
        self.total_intel = 0
        self.total_matched = 0

    async def run(self) -> None:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)-7s [VIPER] %(message)s",
            datefmt="%H:%M:%S",
        )
        log.info("Viper starting — The Silent Assassin (24/7 Intelligence Engine)")
        log.info("Config: cycle=%dmin, tavily=%s, clob=%s",
                 self.cfg.cycle_minutes,
                 "YES" if self.cfg.tavily_api_key else "NO",
                 self.cfg.clob_host[:30])

        _save_status({"running": True, "total_intel": 0, "total_matched": 0, "mode": "intelligence"})

        while True:
            self.cycle += 1
            log.info("=== Viper Cycle %d ===", self.cycle)

            try:
                # Read brain notes
                notes = _load_brain_notes()
                if notes:
                    latest = notes[-1]
                    log.info("Brain note: [%s] %s", latest.get("topic", "?"), latest.get("content", "")[:100])

                # Run the scan
                result = run_single_scan(self.cfg)
                self.total_intel += result.get("new_items", 0)
                self.total_matched += result.get("matched", 0)

                log.info("Cycle %d: %d items scanned, %d new, %d market matches",
                         self.cycle, result["intel_count"], result.get("new_items", 0), result["matched"])
                log.info("Sources: %s", result.get("sources", {}))

                # Cost audit (every 12 cycles = every hour)
                if self.cycle % 12 == 0:
                    log.info("Running hourly cost audit...")
                    cost_data = audit_all()
                    _save_costs(cost_data)
                    log.info("Total estimated monthly API cost: $%.2f", cost_data.get("total_monthly", 0))

                # Save status
                _save_status({
                    "running": True,
                    "mode": "intelligence",
                    "cycle": self.cycle,
                    "total_intel": self.total_intel,
                    "total_matched": self.total_matched,
                    "last_scan_items": result["intel_count"],
                    "last_scan_new": result.get("new_items", 0),
                    "last_scan_matched": result["matched"],
                    "sources": result.get("sources", {}),
                })

            except Exception:
                log.exception("Viper cycle %d failed", self.cycle)
                _save_status({
                    "running": True,
                    "mode": "intelligence",
                    "cycle": self.cycle,
                    "total_intel": self.total_intel,
                    "total_matched": self.total_matched,
                    "error": True,
                })

            log.info("Viper cycle %d complete. Next scan in %d minutes...", self.cycle, self.cfg.cycle_minutes)
            await asyncio.sleep(self.cfg.cycle_minutes * 60)
