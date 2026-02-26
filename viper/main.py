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
from viper.cost_audit import audit_all, find_waste, get_llm_cost_recommendations
from viper.scorer import score_intel
from viper.shelby_push import push_to_shelby

log = logging.getLogger(__name__)

# Agent Brain — learning memory
_viper_brain = None
try:
    import sys as _sys
    _sys.path.insert(0, str(Path.home() / "shared"))
    _sys.path.insert(0, str(Path.home()))
    from agent_brain import AgentBrain
    _viper_brain = AgentBrain("viper", system_prompt="You are Viper, a market intelligence scanner.", task_type="fast")
except Exception:
    pass

DATA_DIR = Path(__file__).parent.parent / "data"
STATUS_FILE = DATA_DIR / "viper_status.json"
OPPS_FILE = DATA_DIR / "viper_opportunities.json"
COSTS_FILE = DATA_DIR / "viper_costs.json"
PUSHED_FILE = DATA_DIR / "viper_pushed.json"


def _load_pushed_ids() -> set:
    """Load set of already-pushed intel IDs to avoid re-pushing to Shelby."""
    if PUSHED_FILE.exists():
        try:
            return set(json.loads(PUSHED_FILE.read_text()))
        except Exception:
            pass
    return set()


def _save_pushed_ids(ids: set) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    try:
        PUSHED_FILE.write_text(json.dumps(list(ids)[-500:]))  # cap at 500
    except Exception:
        log.exception("Failed to save pushed IDs")

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

    # 3b. Push high-scoring items to Shelby (score >= 75, deduped)
    pushed_ids = _load_pushed_ids()
    push_count = 0
    for sd in scored:
        if sd["score"] >= 75 and sd["id"] not in pushed_ids:
            try:
                item_obj = IntelItem(**{k: v for k, v in sd.items() if k != "score"})
                if push_to_shelby(cfg, item_obj, sd["score"]):
                    pushed_ids.add(sd["id"])
                    push_count += 1
            except Exception:
                log.exception("Failed to push item %s to Shelby", sd.get("id", "?"))
            if push_count >= 5:  # cap at 5 pushes per cycle
                break
    if push_count > 0:
        _save_pushed_ids(pushed_ids)
        log.info("Pushed %d items to Shelby (score >= 75)", push_count)
    result["shelby_pushes"] = push_count
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
        self._total_pushes = len(_load_pushed_ids())

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
                self._total_pushes += result.get("shelby_pushes", 0)

                # Brain: record scan cycle + outcome
                if _viper_brain:
                    try:
                        _intel = result.get('intel_count', 0)
                        _matched = result.get('matched', 0)
                        _did = _viper_brain.remember_decision(
                            context=f"Cycle {self.cycle}: scanned sources",
                            decision=f"Found {_intel} intel items, {_matched} matched to markets",
                            confidence=0.5,
                            tags=["scan_cycle"],
                        )
                        # Record outcome — matches are the success metric
                        _score = min(1.0, _matched / 5.0) if _matched > 0 else -0.2
                        _viper_brain.remember_outcome(
                            _did, f"Intel={_intel}, matched={_matched}, tavily={'on' if result.get('tavily_ran') else 'off'}",
                            score=_score,
                        )
                        if _matched >= 3:
                            _viper_brain.learn_pattern(
                                "good_scan", f"Cycle with {_matched} market matches (tavily={'on' if result.get('tavily_ran') else 'off'})",
                                evidence_count=1, confidence=0.6,
                            )
                    except Exception:
                        pass

                tavily_note = " (Tavily: ON)" if result.get("tavily_ran") else " (Tavily: skipped)"
                briefing_note = f" | Briefing: {result.get('briefed_markets', 0)} markets" if result.get("briefing_active") else ""
                log.info("Cycle %d: %d items scanned, %d new, %d market matches%s%s",
                         self.cycle, result["intel_count"], result.get("new_items", 0),
                         result["matched"], tavily_note, briefing_note)
                log.info("Sources: %s", result.get("sources", {}))

                # LLM Cost Governor (every cycle — lightweight)
                gov_applied = []
                try:
                    from viper.llm_governor import run_governor
                    gov_state = run_governor()
                    gov_applied = gov_state.get("overrides_applied", [])
                    if gov_applied:
                        log.info("Governor: throttled %s", gov_applied)
                    sys_spend = gov_state.get("system_budget", {})
                    log.info("Governor: $%.4f/day (%.1f%% of $%.2f)",
                             sys_spend.get("spent_today", 0),
                             sys_spend.get("pct", 0),
                             sys_spend.get("daily_limit", 12))
                except Exception:
                    log.exception("LLM Governor failed")

                # Cost audit (every 12 cycles = every hour)
                if self.cycle % 12 == 0:
                    log.info("Running hourly cost audit...")
                    cost_data = audit_all()
                    # Add waste analysis
                    try:
                        cost_data["waste"] = find_waste()
                    except Exception:
                        log.exception("find_waste() failed")
                        cost_data["waste"] = []
                    # Add LLM cost recommendations
                    try:
                        cost_data["recommendations"] = get_llm_cost_recommendations()
                    except Exception:
                        log.exception("get_llm_cost_recommendations() failed")
                        cost_data["recommendations"] = ""
                    # Add LLM call pattern analysis
                    try:
                        from viper.cost_audit import analyze_llm_call_patterns
                        cost_data["llm_patterns"] = analyze_llm_call_patterns()
                    except Exception:
                        log.exception("analyze_llm_call_patterns() failed")
                        cost_data["llm_patterns"] = []
                    _save_costs(cost_data)
                    log.info("Total estimated monthly API cost: $%.2f", cost_data.get("total_monthly", 0))

                    # Brotherhood P&L (hourly)
                    try:
                        from viper.pnl import compute_pnl
                        pnl_data = compute_pnl()
                        log.info("Brotherhood P&L: net_daily=$%.2f", pnl_data.get("net_daily", 0))
                    except Exception:
                        log.exception("Brotherhood P&L computation failed")

                # Anomaly detection (every cycle — lightweight reads)
                anomalies = []
                try:
                    from viper.anomaly import detect_anomalies
                    anomalies = detect_anomalies()
                    if anomalies:
                        log.warning("Anomalies detected: %d alerts", len(anomalies))
                except Exception:
                    log.exception("Anomaly detection failed")

                # Agent digests (every 4th cycle = ~20 min)
                digests_generated = False
                if self.cycle % 4 == 0:
                    try:
                        from viper.digest import generate_digests
                        digests = generate_digests()
                        digests_generated = True
                        log.info("Agent digests generated: %s", list(digests.keys()))
                    except Exception:
                        log.exception("Digest generation failed")

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
                    "anomalies": anomalies,
                    "digests_generated": digests_generated,
                    "pushes": result.get("shelby_pushes", 0),
                    "pushed_to_shelby": (self._total_pushes if hasattr(self, '_total_pushes') else 0),
                    "governor_throttled": gov_applied,
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
