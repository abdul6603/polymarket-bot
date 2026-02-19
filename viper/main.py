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

# Agent Brain — learning memory
_viper_brain = None
try:
    import sys as _sys
    _sys.path.insert(0, str(Path.home() / "shared"))
    from agent_brain import AgentBrain
    _viper_brain = AgentBrain("viper", system_prompt="You are Viper, a market intelligence scanner.", task_type="fast")
except Exception:
    pass

DATA_DIR = Path(__file__).parent.parent / "data"
STATUS_FILE = DATA_DIR / "viper_status.json"
OPPS_FILE = DATA_DIR / "viper_opportunities.json"
COSTS_FILE = DATA_DIR / "viper_costs.json"

from zoneinfo import ZoneInfo
ET = ZoneInfo("America/New_York")

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


def run_single_scan(cfg: ViperConfig, cycle: int = 0) -> dict:
    """Run a single intelligence scan cycle. Used by both the main loop and the API trigger.

    Tavily runs every cycle when triggered from API, but only every 3rd cycle
    in the main loop (to save Tavily credits). Reddit + Polymarket always run.
    cycle=0 means "run everything" (API trigger).
    """
    result = {"intel_count": 0, "matched": 0, "sources": {}, "briefing_active": False}

    # Check if Hawk briefing exists
    briefing_file = DATA_DIR / "hawk_briefing.json"
    if briefing_file.exists():
        try:
            import json as _json
            bf = _json.loads(briefing_file.read_text())
            import time as _time
            age = _time.time() - bf.get("generated_at", 0)
            result["briefing_active"] = age < 7200
            result["briefed_markets"] = bf.get("briefed_markets", 0)
        except Exception:
            pass

    # Determine if Tavily should run this cycle
    # cycle=0 → always run (API trigger), otherwise every 3rd cycle
    run_tavily = (cycle == 0) or (cycle % 3 == 1)

    # 1. Scan sources (Tavily conditionally, Reddit+Polymarket always)
    if run_tavily:
        intel_items = scan_all(cfg.tavily_api_key, cfg.clob_host)
    else:
        # Skip Tavily, only free sources
        from viper.scanner import scan_polymarket_activity, scan_reddit_predictions
        intel_items = []
        seen_ids: set[str] = set()
        for item in scan_polymarket_activity(cfg.clob_host):
            if item.id not in seen_ids:
                seen_ids.add(item.id)
                intel_items.append(item)
        for item in scan_reddit_predictions():
            if item.id not in seen_ids:
                seen_ids.add(item.id)
                intel_items.append(item)
        log.info("Tavily skipped this cycle (cycle %d, runs every 3rd)", cycle)

    result["intel_count"] = len(intel_items)
    result["tavily_ran"] = run_tavily

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

    # 5. Soren opportunity scout (every 6th cycle = ~30min, or cycle=0 for API trigger)
    run_soren = (cycle == 0) or (cycle % 6 == 1)
    if run_soren:
        try:
            from viper.soren_scout import scout_soren_opportunities
            soren_opps = scout_soren_opportunities(cfg.tavily_api_key)
            result["soren_opportunities"] = len(soren_opps)
            log.info("Soren scout: %d opportunities found", len(soren_opps))
        except Exception:
            log.exception("Soren scout failed")
            result["soren_opportunities"] = 0
    else:
        result["soren_opportunities"] = -1  # -1 = skipped this cycle

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
            cycle_start = time.time()
            log.info("=== Viper Cycle %d ===", self.cycle)

            try:
                # Read brain notes
                notes = _load_brain_notes()
                if notes:
                    latest = notes[-1]
                    log.info("Brain note: [%s] %s", latest.get("topic", "?"), latest.get("content", "")[:100])

                # Run the scan (pass cycle for Tavily throttling)
                result = run_single_scan(self.cfg, cycle=self.cycle)
                self.total_intel += result.get("new_items", 0)
                self.total_matched += result.get("matched", 0)

                # Brain: record scan cycle
                if _viper_brain:
                    try:
                        _viper_brain.remember_decision(
                            context=f"Cycle {self.cycle}: scanned sources",
                            decision=f"Found {result.get('intel_count', 0)} intel items, {result.get('matched', 0)} matched to markets",
                            confidence=0.5,
                            tags=["scan_cycle"],
                        )
                    except Exception:
                        pass

                tavily_note = " (Tavily: ON)" if result.get("tavily_ran") else " (Tavily: skipped)"
                briefing_note = f" | Briefing: {result.get('briefed_markets', 0)} markets" if result.get("briefing_active") else ""
                log.info("Cycle %d: %d items scanned, %d new, %d market matches%s%s",
                         self.cycle, result["intel_count"], result.get("new_items", 0),
                         result["matched"], tavily_note, briefing_note)
                log.info("Sources: %s", result.get("sources", {}))

                # Cost audit (every 12 cycles = every hour)
                if self.cycle % 12 == 0:
                    log.info("Running hourly cost audit...")
                    cost_data = audit_all()
                    _save_costs(cost_data)
                    log.info("Total estimated monthly API cost: $%.2f", cost_data.get("total_monthly", 0))

                # Save status
                soren_count = result.get("soren_opportunities", -1)
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
                    "briefing_active": result.get("briefing_active", False),
                    "briefed_markets": result.get("briefed_markets", 0),
                    "tavily_ran": result.get("tavily_ran", False),
                    "soren_opportunities": soren_count if soren_count >= 0 else None,
                })

                # Publish cycle_completed to the shared event bus
                scan_duration = round(time.time() - cycle_start, 1)
                try:
                    from shared.events import publish as bus_publish
                    bus_publish(
                        agent="viper",
                        event_type="cycle_completed",
                        data={
                            "cycle": self.cycle,
                            "opportunities_count": result.get("new_items", 0),
                            "scan_duration": scan_duration,
                            "total_scanned": result["intel_count"],
                            "matched": result["matched"],
                        },
                        summary=f"Viper cycle {self.cycle}: {result.get('new_items', 0)} new items in {scan_duration}s",
                    )
                except Exception:
                    pass  # Never let bus failure crash Viper

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
