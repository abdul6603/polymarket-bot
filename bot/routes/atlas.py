"""Atlas (research/intelligence) routes: /api/atlas/*"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

from flask import Blueprint, jsonify, request

from bot.routes._utils import read_fresh

from bot.shared import (
    get_atlas,
    ATLAS_ROOT,
    COMPETITOR_INTEL_FILE,
    DATA_DIR,
)

# Brain write helpers (shared with brain routes)
_BRAIN_DIR = Path(__file__).parent.parent / "data" / "brains"


def _add_brain_note(agent: str, topic: str, content: str, note_type: str = "note", tags: list | None = None) -> str:
    """Add a note to an agent's brain and return the note ID."""
    _BRAIN_DIR.mkdir(parents=True, exist_ok=True)
    brain_file = _BRAIN_DIR / f"{agent}.json"
    if brain_file.exists():
        try:
            data = json.loads(brain_file.read_text())
        except Exception:
            data = {"agent": agent, "notes": []}
    else:
        data = {"agent": agent, "notes": []}

    note_id = f"note_{uuid.uuid4().hex[:8]}"
    et = ZoneInfo("America/New_York")
    note = {
        "id": note_id,
        "topic": topic[:200],
        "content": content[:5000],
        "type": note_type,
        "tags": (tags or [])[:10],
        "created_at": datetime.now(et).isoformat(),
        "source": "atlas-pipeline",
    }
    data["notes"].append(note)
    data["notes"] = data["notes"][-500:]
    brain_file.write_text(json.dumps(data, indent=2, default=str))
    return note_id

atlas_bp = Blueprint("atlas", __name__)


@atlas_bp.route("/api/atlas")
def api_atlas():
    """Atlas overview data."""
    atlas = get_atlas()
    if not atlas:
        return jsonify({"status": "offline", "error": "Atlas not available"})
    try:
        return jsonify(atlas.api_overview())
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)[:200]})


@atlas_bp.route("/api/atlas/report", methods=["POST"])
def api_atlas_report():
    """Generate a full Atlas report."""
    atlas = get_atlas()
    if not atlas:
        return jsonify({"error": "Atlas not available"}), 503
    try:
        report = atlas.api_full_report()
        return jsonify(report)
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500, 500


@atlas_bp.route("/api/atlas/garves")
def api_atlas_garves():
    """Atlas deep analysis of Garves."""
    atlas = get_atlas()
    if not atlas:
        return jsonify({"error": "Atlas not available"}), 503
    try:
        return jsonify(atlas.api_garves_deep())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500, 500


@atlas_bp.route("/api/atlas/soren")
def api_atlas_soren():
    """Atlas deep analysis of Soren."""
    atlas = get_atlas()
    if not atlas:
        return jsonify({"error": "Atlas not available"}), 503
    try:
        return jsonify(atlas.api_soren_deep())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500, 500


@atlas_bp.route("/api/atlas/live-research")
def api_atlas_live_research():
    """What Atlas is currently researching -- recent URLs, sources, insights."""
    atlas = get_atlas()
    if not atlas:
        return jsonify({"error": "Atlas not available", "articles": []}), 503
    try:
        stats = atlas.researcher.get_research_stats()
        articles = []
        for entry in reversed(stats.get("recent", [])):
            articles.append({
                "agent": entry.get("agent", "?"),
                "query": entry.get("query", ""),
                "source": entry.get("source", ""),
                "url": entry.get("url", ""),
                "insight": entry.get("insight", "")[:200],
                "quality": entry.get("quality_score", 0),
                "timestamp": entry.get("timestamp", ""),
            })
        return jsonify({
            "total_researched": stats.get("total_researches", 0),
            "seen_urls": stats.get("seen_urls", 0),
            "articles": articles,
        })
    except Exception as e:
        return jsonify({"error": str(e)[:200], "articles": []}), 500


@atlas_bp.route("/api/atlas/experiments")
def api_atlas_experiments():
    """Atlas experiment data."""
    atlas = get_atlas()
    if not atlas:
        return jsonify({"error": "Atlas not available"}), 503
    try:
        return jsonify(atlas.api_experiments())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500, 500


@atlas_bp.route("/api/atlas/knowledge")
def api_atlas_knowledge():
    """Atlas knowledge base."""
    atlas = get_atlas()
    if not atlas:
        return jsonify({"error": "Atlas not available"}), 503
    try:
        return jsonify(atlas.api_knowledge())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500, 500


@atlas_bp.route("/api/atlas/shelby")
def api_atlas_shelby():
    """Atlas deep analysis of Shelby."""
    atlas = get_atlas()
    if not atlas:
        return jsonify({"error": "Atlas not available"}), 503
    try:
        return jsonify(atlas.api_shelby_deep())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500, 500


@atlas_bp.route("/api/atlas/lisa")
def api_atlas_mercury():
    """Atlas deep analysis of Lisa."""
    atlas = get_atlas()
    if not atlas:
        return jsonify({"error": "Atlas not available"}), 503
    try:
        return jsonify(atlas.api_mercury_deep())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500, 500


@atlas_bp.route("/api/atlas/thor")
def api_atlas_thor():
    """Atlas deep analysis of Thor."""
    atlas = get_atlas()
    if not atlas:
        return jsonify({"error": "Atlas not available"}), 503
    try:
        return jsonify(atlas.api_thor_deep())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500, 500


@atlas_bp.route("/api/atlas/robotox")
def api_atlas_robotox():
    """Atlas deep analysis of Robotox."""
    atlas = get_atlas()
    if not atlas:
        return jsonify({"error": "Atlas not available"}), 503
    try:
        return jsonify(atlas.api_robotox_deep())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500, 500


@atlas_bp.route("/api/atlas/hawk")
def api_atlas_hawk():
    """Atlas analysis of Hawk — scanning, edge, positions."""
    try:
        status = read_fresh(DATA_DIR / "hawk_status.json", "~/polymarket-bot/data/hawk_status.json")
        opps = read_fresh(DATA_DIR / "hawk_opportunities.json", "~/polymarket-bot/data/hawk_opportunities.json")
        scan = status.get("scan", {})
        overview = {
            "running": status.get("running", False),
            "cycle": status.get("cycle", 0),
            "total_trades": status.get("total_trades", 0),
            "win_rate": f"{status.get('win_rate', 0):.1f}%",
            "pnl": f"${status.get('pnl', 0):.2f}",
            "open_positions": status.get("open_positions", 0),
            "total_exposure": f"${status.get('total_exposure', 0):.2f}",
            "bankroll": f"${status.get('effective_bankroll', 0):.2f}",
            "markets_eligible": scan.get("total_eligible", 0),
            "markets_contested": scan.get("contested", 0),
            "sports_analyzed": scan.get("sports_analyzed", 0),
            "weather_analyzed": scan.get("weather_analyzed", 0),
            "last_update": status.get("last_update", "—"),
        }
        top_opps = []
        if isinstance(opps, dict):
            for o in sorted(opps.get("opportunities", []), key=lambda x: abs(x.get("edge", 0)), reverse=True)[:5]:
                top_opps.append({"question": o.get("question", "")[:80], "edge": round(o.get("edge", 0) * 100, 1), "side": o.get("side", "")})
        breakdowns = {}
        if top_opps:
            breakdowns["top_opportunities"] = {f"{i+1}. {o['question']}": f"{o['edge']}% edge ({o['side']})" for i, o in enumerate(top_opps)}
        recs = []
        if status.get("win_rate", 0) < 40 and status.get("total_trades", 0) > 5:
            recs.append({"priority": "high", "recommendation": f"Win rate is {status.get('win_rate', 0):.1f}% — review confidence thresholds"})
        return jsonify({"overview": overview, "breakdowns": breakdowns, "recommendations": recs})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@atlas_bp.route("/api/atlas/quant")
def api_atlas_quant():
    """Atlas analysis of Quant — backtesting, recommendations."""
    try:
        status = read_fresh(DATA_DIR / "quant_status.json", "~/polymarket-bot/data/quant_status.json")
        recs = read_fresh(DATA_DIR / "quant_recommendations.json", "~/polymarket-bot/data/quant_recommendations.json")
        analytics = read_fresh(DATA_DIR / "quant_analytics.json", "~/polymarket-bot/data/quant_analytics.json")
        overview = {
            "status": status.get("status", "unknown"),
            "last_run": status.get("last_run", "—"),
            "total_backtests": status.get("total_backtests", 0),
            "recommendations_count": len(recs) if isinstance(recs, list) else len(recs.get("recommendations", [])) if isinstance(recs, dict) else 0,
        }
        if isinstance(analytics, dict):
            overview["win_rate"] = analytics.get("win_rate", "—")
            overview["avg_edge"] = analytics.get("avg_edge", "—")
        return jsonify({"overview": overview, "recommendations": []})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@atlas_bp.route("/api/atlas/viper")
def api_atlas_viper():
    """Atlas analysis of Viper — revenue, cost audits."""
    try:
        status = read_fresh(DATA_DIR / "viper_status.json", "~/polymarket-bot/data/viper_status.json")
        costs = read_fresh(DATA_DIR / "viper_costs.json", "~/polymarket-bot/data/viper_costs.json")
        intel = read_fresh(DATA_DIR / "viper_intel.json", "~/polymarket-bot/data/viper_intel.json")
        overview = {
            "status": status.get("status", "unknown"),
            "last_scan": status.get("last_scan", "—"),
            "opportunities_found": status.get("opportunities", 0),
            "total_cost_tracked": costs.get("total", "—") if isinstance(costs, dict) else "—",
        }
        if isinstance(intel, dict):
            overview["intel_items"] = len(intel.get("items", intel.get("insights", [])))
        return jsonify({"overview": overview, "recommendations": []})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@atlas_bp.route("/api/atlas/odin")
def api_atlas_odin():
    """Atlas analysis of Odin — futures trading, regime, skills."""
    try:
        odin_root = Path.home() / "odin" / "data"
        status = read_fresh(odin_root / "odin_status.json", "~/odin/data/odin_status.json")
        config = status.get("config", {})
        regime = status.get("regime", {})
        overview = {
            "exchange": config.get("exchange", "Hyperliquid"),
            "mode": status.get("mode", "paper").upper(),
            "skills_loaded": status.get("skill_count", 0),
            "hl_pairs": config.get("hl_pairs", 0),
            "priority_coins": config.get("priority_coins", "—"),
            "cycle": status.get("cycle_count", 0),
            "last_scan": status.get("timestamp_et", "—"),
            "balance": status.get("balance", 0),
            "total_pnl": status.get("total_pnl", 0),
            "open_positions": status.get("open_positions", 0),
            "regime": regime.get("regime", "—"),
            "regime_bias": regime.get("direction_bias", "—"),
            "top_long": regime.get("top_long", "—"),
            "top_short": regime.get("top_short", "—"),
        }
        positions = status.get("paper_positions", [])
        opps = status.get("opportunities", [])
        breakdowns = {}
        if positions:
            breakdowns["positions"] = {p.get("symbol", "?"): f"{p.get('side', '?')} @ {p.get('entry_price', '?')}" for p in positions[:5]}
        if opps:
            breakdowns["opportunities"] = {o.get("symbol", "?"): f"{o.get('direction', '?')} (score {o.get('score', '?')}) — {', '.join(o.get('reasons', []))[:80]}" for o in opps[:5]}
        recs = []
        if status.get("mode", "paper") == "paper":
            recs.append({"priority": "info", "recommendation": "Odin is in PAPER mode — no real trades"})
        return jsonify({"overview": overview, "breakdowns": breakdowns, "recommendations": recs})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@atlas_bp.route("/api/atlas/improvements", methods=["POST"])
def api_atlas_improvements():
    """Generate improvement suggestions for all agents."""
    atlas = get_atlas()
    if not atlas:
        return jsonify({"error": "Atlas not available"}), 503
    try:
        return jsonify(atlas.api_improvements())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500, 500


@atlas_bp.route("/api/atlas/improvements/acknowledge", methods=["POST"])
def api_atlas_acknowledge():
    """Acknowledge current improvements so Atlas stops repeating them."""
    atlas = get_atlas()
    if not atlas:
        return jsonify({"error": "Atlas not available"}), 503
    try:
        # Get current improvements and acknowledge all of them
        improvements = atlas.api_improvements()
        all_suggestions = []
        for key in ["garves", "soren", "shelby", "mercury", "new_skills", "new_agents", "system_wide"]:
            items = improvements.get(key, [])
            if isinstance(items, list):
                all_suggestions.extend(items)
        count = atlas.improvements.acknowledge(all_suggestions)
        return jsonify({"acknowledged": count, "total_dismissed": len(atlas.improvements._acknowledged)})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500, 500


@atlas_bp.route("/api/atlas/costs")
def api_atlas_costs():
    """API cost tracker data (Tavily + OpenAI)."""
    cost_file = ATLAS_ROOT / "data" / "cost_tracker.json"
    try:
        tracker = read_fresh(cost_file, "~/atlas/data/cost_tracker.json")
        if not tracker:
            return jsonify({"today_tavily": 0, "today_openai": 0,
                            "month_tavily": 0, "month_openai": 0,
                            "projected_tavily": 0})

        daily = tracker.get("daily", {})
        today = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
        today_data = daily.get(today, {})

        # Monthly totals
        month_prefix = today[:7]  # "YYYY-MM"
        month_tavily = 0
        month_openai = 0
        days_in_month = 0
        for day_key, day_data in daily.items():
            if day_key.startswith(month_prefix):
                month_tavily += day_data.get("tavily_calls", 0)
                month_openai += day_data.get("openai_calls", 0)
                days_in_month += 1

        # Project monthly usage (calls + dollars)
        # Budgets: Tavily $90/mo (12k credits), OpenAI $50/mo
        TAVILY_BUDGET = 90.0
        TAVILY_MONTHLY_CREDITS = 12000
        OPENAI_BUDGET = 50.0
        # GPT-4o-mini pricing: $0.15/1M input + $0.60/1M output
        # Atlas uses ~300 output tokens + ~500 input tokens per call
        OPENAI_COST_PER_CALL = (500 * 0.15 + 300 * 0.60) / 1_000_000  # ~$0.000255

        month_openai_tokens = 0
        for day_key, day_data in daily.items():
            if day_key.startswith(month_prefix):
                month_openai_tokens += day_data.get("openai_tokens_est", 0)

        if days_in_month > 0:
            avg_daily_tavily = month_tavily / days_in_month
            avg_daily_openai = month_openai / days_in_month
            projected_tavily = int(avg_daily_tavily * 30)
            projected_openai = int(avg_daily_openai * 30)
        else:
            projected_tavily = 0
            projected_openai = 0

        # Dollar projections
        tavily_cost_projected = round(TAVILY_BUDGET * (projected_tavily / TAVILY_MONTHLY_CREDITS), 2) if TAVILY_MONTHLY_CREDITS else 0
        # OpenAI: use actual token estimates if available, else per-call estimate
        if month_openai_tokens > 0 and days_in_month > 0:
            avg_daily_tokens = month_openai_tokens / days_in_month
            projected_tokens = avg_daily_tokens * 30
            openai_cost_projected = round(projected_tokens * 0.60 / 1_000_000, 2)  # mostly output tokens
        else:
            openai_cost_projected = round(projected_openai * OPENAI_COST_PER_CALL, 2)

        return jsonify({
            "today_tavily": today_data.get("tavily_calls", 0),
            "today_openai": today_data.get("openai_calls", 0),
            "month_tavily": month_tavily,
            "month_openai": month_openai,
            "projected_tavily": projected_tavily,
            "projected_openai": projected_openai,
            "tavily_budget": TAVILY_BUDGET,
            "openai_budget": OPENAI_BUDGET,
            "tavily_cost_projected": tavily_cost_projected,
            "openai_cost_projected": openai_cost_projected,
            "total_cost_projected": round(tavily_cost_projected + openai_cost_projected, 2),
            "total_budget": TAVILY_BUDGET + OPENAI_BUDGET,
            "daily": daily,
        })
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@atlas_bp.route("/api/atlas/kb-search")
def api_atlas_kb_search():
    """Semantic search across the knowledge base.

    Query params: q=<search query>, top_k=<max results, default 10>
    Returns matched observations/learnings ranked by semantic similarity.
    """
    try:
        q = request.args.get("q", "").strip()
        if not q:
            return jsonify({"error": "Query parameter 'q' is required"}), 400
        top_k = min(int(request.args.get("top_k", 10)), 50)

        from atlas.brain import KnowledgeBase
        kb = KnowledgeBase()
        results = kb.semantic_search(q, top_k=top_k)

        # Check if embeddings are available
        embedding_available = False
        try:
            from shared.embedding_client import is_available
            embedding_available = is_available()
        except Exception:
            pass

        return jsonify({
            "query": q,
            "results": results,
            "count": len(results),
            "method": "semantic" if embedding_available else "keyword",
        })
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@atlas_bp.route("/api/atlas/kb-health")
def api_atlas_kb_health():
    """Knowledge base health metrics — confidence, age, contradictions."""
    try:
        from atlas.brain import KnowledgeBase
        kb = KnowledgeBase()
        return jsonify(kb.get_kb_health())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@atlas_bp.route("/api/atlas/kb-consolidate", methods=["POST"])
def api_atlas_kb_consolidate():
    """Trigger knowledge base consolidation — merge similar learnings."""
    try:
        from atlas.brain import KnowledgeBase
        kb = KnowledgeBase()
        return jsonify(kb.consolidate())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@atlas_bp.route("/api/atlas/kb-contradictions")
def api_atlas_kb_contradictions():
    """Get potential contradictions in the knowledge base."""
    try:
        from atlas.brain import KnowledgeBase
        kb = KnowledgeBase()
        return jsonify({"contradictions": kb.detect_contradictions()})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@atlas_bp.route("/api/atlas/kb-weighted")
def api_atlas_kb_weighted():
    """Get weighted learnings sorted by confidence * recency."""
    try:
        from flask import request
        agent = request.args.get("agent", None)
        from atlas.brain import KnowledgeBase
        kb = KnowledgeBase()
        return jsonify({"learnings": kb.get_weighted_learnings(agent=agent)})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@atlas_bp.route("/api/atlas/summarize", methods=["POST"])
def api_atlas_summarize():
    """Compress old observations into learnings."""
    atlas = get_atlas()
    if not atlas:
        return jsonify({"error": "Atlas not available"}), 503
    try:
        return jsonify(atlas.api_summarize_kb())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500, 500


_atlas_bg_pro_cache = {"data": None, "ts": 0}

def _fetch_pro_atlas_bg():
    """Fetch Atlas background status from Pro via SSH (30s cache)."""
    import time, subprocess
    now = time.time()
    if _atlas_bg_pro_cache["data"] and (now - _atlas_bg_pro_cache["ts"]) < 30:
        return _atlas_bg_pro_cache["data"]
    try:
        result = subprocess.run(
            ["ssh", "pro", "cat", "~/atlas/data/background_status.json"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout)
            _atlas_bg_pro_cache["data"] = data
            _atlas_bg_pro_cache["ts"] = now
            return data
    except Exception:
        pass
    return None


@atlas_bp.route("/api/atlas/background/status")
def api_atlas_bg_status():
    """Get Atlas background loop status -- state, cycles, last cycle, errors, research stats."""
    atlas = get_atlas()
    if not atlas:
        return jsonify({"running": False, "state": "offline", "error": "Atlas not available"})
    try:
        bg = atlas.background
        status = bg.get_status()
        research = status.get("research_stats", {})
        data_feed = status.get("data_feed", {})

        # If local Atlas has no completed cycles, prefer Pro's real status
        local_cycles = status.get("cycles", 0)
        pro_status = None
        if local_cycles == 0:
            pro_status = _fetch_pro_atlas_bg()

        state_labels = {
            "idle": "Idle",
            "running": "Waiting for next cycle",
            "researching": "Researching agents",
            "feeding_agents": "Feeding data to agents",
            "teaching_lisa": "Teaching Lisa",
            "analyzing": "Analyzing agents",
            "spying": "Competitor intelligence",
            "v2_anomaly_detection": "Anomaly detection",
            "v2_experiment_runner": "Running experiments",
            "v2_onchain": "On-chain analysis",
            "generating_improvements": "Generating improvements",
            "summarizing_kb": "Summarizing knowledge base",
            "learning": "Learning from patterns",
            "v2_report_delivery": "Delivering reports",
            "stopped": "Stopped",
        }

        # Merge Pro data if available and more complete
        cycles = local_cycles
        last_cycle = status.get("last_cycle")
        last_findings = status.get("last_findings", 0)
        started_at = status.get("started_at")
        cumulative = status.get("cumulative_researches", 0)
        agent_feed_log = status.get("agent_feed_log", {})
        state = status.get("state", "idle")

        if pro_status and pro_status.get("cycles", 0) > cycles:
            cycles = pro_status["cycles"]
            last_cycle = pro_status.get("last_cycle", last_cycle)
            last_findings = pro_status.get("last_findings", last_findings)
            started_at = pro_status.get("started_at", started_at)
            cumulative = pro_status.get("cumulative_researches", cumulative)
            agent_feed_log = pro_status.get("agent_feed_log", agent_feed_log)
            state = pro_status.get("state", state)

        return jsonify({
            "running": status.get("running", False) or (pro_status is not None and pro_status.get("state") not in ("stopped", "idle")),
            "state": state,
            "state_label": state_labels.get(state, state.replace("_", " ").title()),
            "cycles": cycles,
            "started_at": started_at,
            "last_cycle": last_cycle,
            "last_findings": last_findings,
            "last_error": status.get("last_error") or (pro_status or {}).get("last_error"),
            "total_researches": cumulative or research.get("total_researches", 0),
            "unique_urls": research.get("seen_urls", 0) if isinstance(research.get("seen_urls"), int) else len(research.get("seen_urls", [])),
            "data_feed_active": data_feed.get("active", False),
            "data_feed_sources": data_feed.get("sources_count", 0),
            "current_target": status.get("current_target") or (pro_status or {}).get("current_target"),
            "recent_learn_count": status.get("recent_learn_count", 0),
            "cycle_minutes": 45,
            "agent_feed_log": agent_feed_log,
        })
    except Exception as e:
        return jsonify({"running": False, "state": "error", "error": str(e)[:200]})


@atlas_bp.route("/api/atlas/background/start", methods=["POST"])
def api_atlas_bg_start():
    """Start Atlas background research loop."""
    atlas = get_atlas()
    if not atlas:
        return jsonify({"error": "Atlas not available"}), 503
    try:
        return jsonify(atlas.api_start_background())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500, 500


@atlas_bp.route("/api/atlas/background/stop", methods=["POST"])
def api_atlas_bg_stop():
    """Stop Atlas background research loop."""
    atlas = get_atlas()
    if not atlas:
        return jsonify({"error": "Atlas not available"}), 503
    try:
        return jsonify(atlas.api_stop_background())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500, 500


@atlas_bp.route("/api/atlas/competitors")
def api_atlas_competitors():
    """Competitor intelligence data."""
    try:
        data = read_fresh(COMPETITOR_INTEL_FILE, "~/atlas/data/competitor_intel.json")
        if not data:
            return jsonify({"trading": [], "content": [], "ai_agents": [], "scanned_at": None})
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@atlas_bp.route("/api/atlas/learning/<agent>")
def api_atlas_learning(agent):
    """Learning status for an agent from Atlas knowledge base."""
    agent = agent.lower()
    kb_file = ATLAS_ROOT / "data" / "knowledge_base.json"
    result = {"observations": 0, "hypotheses": 0, "improvements_applied": 0, "learning_score": "Novice"}

    kb = read_fresh(kb_file, "~/atlas/data/knowledge_base.json")
    if kb:
        try:
            all_obs = kb.get("observations", [])
            agent_obs = [o for o in all_obs if o.get("agent", "").lower() == agent or agent in str(o.get("tags", "")).lower()]
            result["observations"] = len(agent_obs)

            all_hyp = kb.get("hypotheses", [])
            agent_hyp = [h for h in all_hyp if h.get("agent", "").lower() == agent or agent in str(h).lower()]
            result["hypotheses"] = len(agent_hyp)

            all_imp = kb.get("improvements", [])
            agent_imp = [im for im in all_imp if im.get("agent", "").lower() == agent or agent in str(im).lower()]
            result["improvements_applied"] = len(agent_imp)

            total = result["observations"] + result["hypotheses"] + result["improvements_applied"]
            if total >= 50:
                result["learning_score"] = "Expert"
            elif total >= 20:
                result["learning_score"] = "Advanced"
            elif total >= 5:
                result["learning_score"] = "Intermediate"
            else:
                result["learning_score"] = "Novice"
        except Exception:
            pass

    return jsonify(result)


@atlas_bp.route("/api/atlas/trade-analysis")
def api_atlas_trade_analysis():
    """Trade journal analysis from Atlas."""
    try:
        from atlas.trade_analyzer import TradeJournalAnalyzer
        analyzer = TradeJournalAnalyzer()
        latest = analyzer.get_latest()
        if latest:
            return jsonify(latest)
        return jsonify({"message": "No trade analysis yet", "analyzed_at": None})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@atlas_bp.route("/api/atlas/infra-eval")
def api_atlas_infra_eval():
    """Evaluate system infrastructure — agents, paths, health."""
    atlas = get_atlas()
    if not atlas:
        return jsonify({"error": "Atlas not available"}), 503
    try:
        status = atlas.system_status()
        kb_stats = atlas.kb.stats()
        # Collect per-agent quick status
        agents_health = {}
        for name, opt in [("garves", atlas.garves), ("soren", atlas.soren),
                          ("shelby", atlas.shelby), ("lisa", atlas.mercury),
                          ("thor", atlas.thor), ("robotox", atlas.robotox)]:
            try:
                agents_health[name] = opt.quick_status()
            except Exception as e:
                agents_health[name] = {"status": "error", "error": str(e)[:100]}

        # Infrastructure summary
        infra = {
            "timestamp": status.get("timestamp"),
            "knowledge_base": kb_stats,
            "agents_health": agents_health,
            "hierarchy": status.get("hierarchy", {}),
            "recommendations": [],
        }
        # Auto-generate infra recommendations
        total_agents = len(agents_health)
        errors = sum(1 for v in agents_health.values()
                     if isinstance(v, dict) and v.get("status") == "error")
        if errors:
            infra["recommendations"].append({
                "priority": "high",
                "recommendation": f"{errors}/{total_agents} agents returned errors on quick_status. Check their data files.",
            })
        obs_count = kb_stats.get("observations", kb_stats.get("total_observations", 0))
        if obs_count > 300:
            infra["recommendations"].append({
                "priority": "medium",
                "recommendation": f"Knowledge base has {obs_count} observations. Consider running Compress KB.",
            })
        if not infra["recommendations"]:
            infra["recommendations"].append({
                "priority": "info",
                "recommendation": "Infrastructure is healthy. All agent data files accessible.",
            })
        return jsonify(infra)
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500, 500


@atlas_bp.route("/api/atlas/thoughts")
def api_atlas_thoughts():
    """Atlas thoughts — recent observations, learnings, and hypotheses."""
    atlas = get_atlas()
    if not atlas:
        return jsonify({"error": "Atlas not available"}), 503
    try:
        observations = atlas.kb.get_observations(limit=15)
        learnings = atlas.kb.get_learnings()
        experiments = atlas.hypothesis.get_experiments()
        # Research articles
        research_log = []
        rl_path = ATLAS_ROOT / "data" / "research_log.json"
        if rl_path.exists():
            try:
                with open(rl_path) as f:
                    rl = json.load(f)
                research_log = rl[-10:] if isinstance(rl, list) else []
            except Exception:
                pass

        return jsonify({
            "observations": observations[-15:] if isinstance(observations, list) else [],
            "learnings": learnings[-10:] if isinstance(learnings, list) else [],
            "experiments": experiments[-5:] if isinstance(experiments, list) else [],
            "recent_research": research_log,
        })
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500, 500


@atlas_bp.route("/api/atlas/hub-eval")
def api_atlas_hub_eval():
    """Evaluate our agent hub vs industry best practices using Atlas research."""
    atlas = get_atlas()
    if not atlas:
        return jsonify({"error": "Atlas not available"}), 503
    try:
        # Gather competitor intel
        comp = {}
        comp_path = COMPETITOR_INTEL_FILE
        if comp_path and comp_path.exists():
            try:
                with open(comp_path) as f:
                    comp = json.load(f)
            except Exception:
                pass

        # Gather recent high-quality research insights (quality >= 8)
        research_log = []
        rl_path = ATLAS_ROOT / "data" / "research_log.json"
        if rl_path.exists():
            try:
                with open(rl_path) as f:
                    rl = json.load(f)
                # Get recent high-quality insights, grouped by agent (max 2 per agent)
                agent_counts = {}
                for r in reversed(rl if isinstance(rl, list) else []):
                    q = r.get("quality_score", 0)
                    a = r.get("agent", "general")
                    if q >= 8 and agent_counts.get(a, 0) < 2:
                        research_log.append(r)
                        agent_counts[a] = agent_counts.get(a, 0) + 1
                    if len(research_log) >= 10:
                        break
                research_log.reverse()
            except Exception:
                pass

        # Our system capabilities — dynamically detected
        features = [
            "Cross-agent intelligence sharing via Atlas KB",
            "Autonomous health monitoring and auto-fix (Robotox)",
            "Proactive scheduling with 4 daily routines (Shelby)",
            "Background research loop (45-min cycles, Tavily API)",
            "Agent economics tracking",
            "Task queue system (Thor)",
            "A/B content testing (Soren)",
            "Competitor intelligence gathering",
            "Telegram notifications",
            "Centralized dashboard with per-agent tabs",
        ]

        # Detect infrastructure that has been built
        has_event_bus = (Path.home() / "shared" / "events.py").exists()
        has_broadcast = (Path.home() / "shelby" / "core" / "broadcast.py").exists()
        has_log_watcher = (Path.home() / "sentinel" / "core" / "log_watcher.py").exists()
        has_dep_checker = (Path.home() / "sentinel" / "core" / "dep_checker.py").exists()
        has_brain_system = (Path.home() / "polymarket-bot" / "bot" / "brain_reader.py").exists()

        if has_event_bus:
            features.append("Shared event bus — structured inter-agent coordination (blackboard architecture)")
        if has_broadcast:
            features.append("Brotherhood broadcast system for announcements")
        if has_log_watcher:
            features.append("Smart log watcher with 11 error patterns and auto-fix")
        if has_dep_checker:
            features.append("Dependency vulnerability scanning")
        if has_brain_system:
            features.append("Agent brain notes system for persistent knowledge")

        our_system = {
            "total_agents": 11,
            "agents": [
                {"name": "Garves", "role": "Crypto Up/Down trader (Polymarket)", "color": "#00d4ff"},
                {"name": "Soren", "role": "Dark motivation content creator (@soren.era)", "color": "#cc66ff"},
                {"name": "Shelby", "role": "Commander — scheduler, task mgmt, Telegram", "color": "#ffaa00"},
                {"name": "Atlas", "role": "24/7 research engine, feeds all agents", "color": "#22aa44"},
                {"name": "Lisa", "role": "Social media manager (X, TikTok, Instagram)", "color": "#ff8800"},
                {"name": "Thor", "role": "Autonomous code generation agent", "color": "#ff6600"},
                {"name": "Robotox", "role": "System health monitor, auto-restart", "color": "#00ff44"},
                {"name": "Hawk", "role": "Non-crypto Polymarket scanner (sports, politics)", "color": "#FFD700"},
                {"name": "Viper", "role": "Revenue hunter — gigs, cost audits", "color": "#00ff88"},
                {"name": "Quant", "role": "Backtesting lab — parameter optimization", "color": "#00BFFF"},
                {"name": "Odin", "role": "BTC/ETH futures swing trader (Hyperliquid) — SMC + Macro", "color": "#E8DCC8"},
            ],
            "features": features,
            "architecture": "Jordan (Owner) > Claude (Godfather) > Thor + Shelby > All Agents. Atlas cross-cuts.",
        }

        # Dynamic strengths — based on what exists
        strengths = [
            "Unified command center dashboard",
            "Cross-agent knowledge sharing (Atlas feeds all)",
            "Autonomous monitoring and self-healing (Robotox)",
            "Hierarchical command structure with clear roles",
            "Background continuous research and learning",
        ]
        if has_event_bus:
            strengths.append("Shared event bus for real-time inter-agent coordination")
        if has_log_watcher:
            strengths.append("Intelligent log watching with autonomous fix escalation")

        # Dynamic gaps — only list things that are ACTUALLY missing
        gaps = []
        if not has_event_bus:
            gaps.append("No inter-agent direct messaging (agents go through Atlas/Shelby)")
            gaps.append("Limited real-time collaboration between agents")

        # Pull gap insights from Atlas learnings (clean, complete sentences)
        try:
            kb_file = ATLAS_ROOT / "data" / "knowledge_base.json"
            if kb_file.exists():
                kb = json.loads(kb_file.read_text())
                for learning in (kb.get("learnings", []))[-30:]:
                    insight = learning.get("insight", "")
                    conf = learning.get("confidence", 0)
                    agent = learning.get("agent", "")
                    if conf >= 0.75 and any(kw in insight.lower() for kw in
                                            ["needs", "bottleneck", "missing", "should", "improve", "low", "below"]):
                        # Truncate at sentence boundary, not mid-word
                        gap_text = insight[:300]
                        last_period = gap_text.rfind(".")
                        if last_period > 50:
                            gap_text = gap_text[:last_period + 1]
                        # Prefix with agent name for context
                        if agent and agent != "atlas":
                            agent_label = agent.title()
                            gap_text = f"[{agent_label}] {gap_text}"
                        if gap_text not in gaps:
                            gaps.append(gap_text)
        except Exception:
            pass

        # Dynamic recommendations — from Atlas improvements + learnings
        recommendations = []
        if not has_event_bus:
            recommendations.append({"priority": "high", "recommendation": "Add inter-agent event bus for real-time coordination"})
        recommendations.append({"priority": "medium", "recommendation": "Implement ML anomaly detection in Robotox log watcher"})
        recommendations.append({"priority": "medium", "recommendation": "Add agent config versioning and rollback system"})

        # Pull recommendations from Atlas improvements.json
        try:
            imp_file = ATLAS_ROOT / "data" / "improvements.json"
            if imp_file.exists():
                improvements = json.loads(imp_file.read_text())
                rec_titles = {r["recommendation"][:60] for r in recommendations}
                for agent_name, items in improvements.items():
                    if not isinstance(items, list):
                        continue
                    for item in items[:2]:
                        if not isinstance(item, dict):
                            continue
                        title = item.get("title", item.get("suggestion", ""))
                        if title and title[:60] not in rec_titles:
                            recommendations.append({
                                "priority": item.get("priority", "normal"),
                                "recommendation": f"[{agent_name.title()}] {title[:100]}",
                            })
                            rec_titles.add(title[:60])
        except Exception:
            pass

        # Build evaluation
        eval_result = {
            "our_system": our_system,
            "competitor_insights": comp.get("ai_agents", [])[:5],
            "research_insights": research_log,
            "strengths": strengths,
            "gaps": gaps[:8],
            "recommendations": recommendations[:8],
        }
        return jsonify(eval_result)
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500, 500


@atlas_bp.route("/api/atlas/deep-research", methods=["POST"])
def api_atlas_deep_research():
    """Targeted deep research on a specific agent or topic."""
    atlas = get_atlas()
    if not atlas:
        return jsonify({"error": "Atlas not available"}), 503
    try:
        body = request.get_json(silent=True) or {}
        agent = body.get("agent", "")
        query = body.get("query", "").strip()
        if not query:
            return jsonify({"error": "Query is required"}), 400

        # Use Atlas researcher to do targeted research
        results = []
        try:
            research_result = atlas.researcher.research(
                agent=agent or "general",
                query=query,
                max_results=5,
            )
            if isinstance(research_result, list):
                results = research_result
            elif isinstance(research_result, dict):
                results = research_result.get("results", [research_result])
        except Exception as e:
            results = [{"error": str(e)[:200]}]

        return jsonify({
            "agent": agent,
            "query": query,
            "results": results[:5],
            "count": len(results),
        })
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@atlas_bp.route("/api/atlas/competitor-summary")
def api_atlas_competitor_summary():
    """Auto-digest competitor intel into 3 actionable bullets."""
    if not COMPETITOR_INTEL_FILE.exists():
        return jsonify({"bullets": [], "has_data": False})
    try:
        with open(COMPETITOR_INTEL_FILE) as f:
            data = json.load(f)

        bullets = []
        # Extract key insights from each category
        for category in ["trading", "content", "ai_agents"]:
            items = data.get(category, [])
            if not items:
                continue
            # Get the most recent/relevant item
            for item in items[:2]:
                name = item.get("name", item.get("title", "Unknown"))
                insight = item.get("insight", item.get("description", item.get("summary", "")))
                if insight:
                    bullets.append({
                        "category": category,
                        "text": f"[{category.replace('_', ' ').title()}] {name}: {insight[:150]}",
                    })

        return jsonify({
            "bullets": bullets[:5],
            "has_data": bool(bullets),
            "total_entries": sum(len(data.get(c, [])) for c in ["trading", "content", "ai_agents"]),
        })
    except Exception as e:
        return jsonify({"error": str(e)[:200], "bullets": [], "has_data": False}), 500


# ── Content Intelligence Pipeline ──

CONTENT_FEED_LOG = ATLAS_ROOT / "data" / "content_feed_log.json"


def _load_feed_log() -> dict:
    if CONTENT_FEED_LOG.exists():
        try:
            with open(CONTENT_FEED_LOG) as f:
                return json.load(f)
        except Exception:
            pass
    return {"feeds": [], "stats": {"total_feeds": 0, "soren_feeds": 0, "lisa_feeds": 0}}


def _save_feed_log(log: dict) -> None:
    CONTENT_FEED_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(CONTENT_FEED_LOG, "w") as f:
        json.dump(log, f, indent=2)


@atlas_bp.route("/api/atlas/content-intel")
def api_atlas_content_intel():
    """Content intelligence for Soren/Lisa — niche trends, viral hooks, strategy."""
    et = ZoneInfo("America/New_York")
    # Gather content competitor intel
    content_findings = []
    if COMPETITOR_INTEL_FILE.exists():
        try:
            with open(COMPETITOR_INTEL_FILE) as f:
                comp = json.load(f)
            content_findings = comp.get("content", [])
        except Exception:
            pass

    # Gather Soren-specific competitor data
    soren_competitors = []
    soren_comp_file = ATLAS_ROOT / "data" / "soren_competitors.json"
    if soren_comp_file.exists():
        try:
            with open(soren_comp_file) as f:
                sc = json.load(f)
            soren_competitors = sc.get("competitors", [])
        except Exception:
            pass

    # Get Atlas KB learnings related to content
    content_learnings = []
    kb_file = ATLAS_ROOT / "data" / "knowledge_base.json"
    if kb_file.exists():
        try:
            with open(kb_file) as f:
                kb = json.load(f)
            for learning in kb.get("learnings", []):
                tags = str(learning.get("tags", "")).lower()
                agent = learning.get("agent", "").lower()
                insight = learning.get("insight", learning.get("learning", ""))
                if agent in ("soren", "lisa", "mercury") or any(
                    kw in tags for kw in ("content", "tiktok", "instagram", "viral", "engagement", "posting")
                ):
                    content_learnings.append({
                        "insight": insight[:200],
                        "confidence": learning.get("confidence", 0),
                        "agent": agent,
                    })
        except Exception:
            pass

    # Get Atlas improvements for Soren/Lisa
    soren_improvements = []
    lisa_improvements = []
    imp_file = ATLAS_ROOT / "data" / "improvements.json"
    if imp_file.exists():
        try:
            with open(imp_file) as f:
                improvements = json.load(f)
            soren_improvements = improvements.get("soren", [])[:5]
            lisa_improvements = improvements.get("lisa", improvements.get("mercury", []))[:5]
        except Exception:
            pass

    # Feed log status
    feed_log = _load_feed_log()
    recent_feeds = feed_log.get("feeds", [])[-5:]

    # X competitor intel from Lisa's scanner
    x_competitor_data = {}
    x_intel_file = Path.home() / "mercury" / "data" / "x_competitor_intel.json"
    if x_intel_file.exists():
        try:
            with open(x_intel_file) as f:
                x_competitor_data = json.load(f)
        except Exception:
            pass

    # X competitor playbook from Lisa's brain
    x_playbook = {}
    x_playbook_file = Path.home() / "mercury" / "data" / "x_competitor_playbook.json"
    if x_playbook_file.exists():
        try:
            with open(x_playbook_file) as f:
                x_playbook = json.load(f)
        except Exception:
            pass

    return jsonify({
        "content_findings": content_findings[:10],
        "soren_competitors": soren_competitors[:10],
        "content_learnings": sorted(content_learnings, key=lambda x: x.get("confidence", 0), reverse=True)[:10],
        "soren_improvements": soren_improvements,
        "lisa_improvements": lisa_improvements,
        "recent_feeds": recent_feeds,
        "feed_stats": feed_log.get("stats", {}),
        "last_scan": None,
        "x_competitor_hooks": x_competitor_data.get("viral_hooks", [])[:10],
        "x_competitor_accounts": x_competitor_data.get("account_data", [])[:10],
        "x_playbook_takeaways": x_playbook.get("takeaways", [])[-10:],
    })


@atlas_bp.route("/api/atlas/feed-content", methods=["POST"])
def api_atlas_feed_content():
    """Push content intelligence to Soren or Lisa's brain — one-click action."""
    try:
        body = request.get_json(silent=True) or {}
        target = body.get("target", "soren")  # soren or lisa
        feed_type = body.get("type", "niche_trends")  # niche_trends, viral_hooks, strategy, improvements
        et = ZoneInfo("America/New_York")

        # Build the intel package based on type
        intel_content = ""
        intel_topic = ""

        if feed_type == "niche_trends":
            intel_topic = "Niche Trend Intelligence from Atlas"
            # Pull content competitor findings
            findings = []
            if COMPETITOR_INTEL_FILE.exists():
                try:
                    with open(COMPETITOR_INTEL_FILE) as f:
                        comp = json.load(f)
                    findings = comp.get("content", [])[:8]
                except Exception:
                    pass
            soren_comp_file = ATLAS_ROOT / "data" / "soren_competitors.json"
            if soren_comp_file.exists():
                try:
                    with open(soren_comp_file) as f:
                        sc = json.load(f)
                    findings.extend(sc.get("competitors", [])[:5])
                except Exception:
                    pass
            if findings:
                intel_content = "TRENDING IN YOUR NICHE:\n" + "\n".join(
                    f"- {f.get('title', '')}: {f.get('snippet', f.get('description', ''))[:120]}"
                    for f in findings[:10]
                )
            else:
                intel_content = "No niche trend data available yet. Atlas needs to run a scan cycle first."

        elif feed_type == "viral_hooks":
            intel_topic = "Viral Hook Patterns from Atlas Research"
            kb_file = ATLAS_ROOT / "data" / "knowledge_base.json"
            hooks = []
            if kb_file.exists():
                try:
                    with open(kb_file) as f:
                        kb = json.load(f)
                    for l in kb.get("learnings", []):
                        text = l.get("insight", l.get("learning", "")).lower()
                        if any(kw in text for kw in ("hook", "viral", "engage", "retention", "caption", "thumbnail")):
                            hooks.append(l.get("insight", l.get("learning", ""))[:150])
                except Exception:
                    pass
            # Also pull from research log
            rl_path = ATLAS_ROOT / "data" / "research_log.json"
            if rl_path.exists():
                try:
                    with open(rl_path) as f:
                        rl = json.load(f)
                    for r in (rl if isinstance(rl, list) else []):
                        text = str(r).lower()
                        if any(kw in text for kw in ("hook", "viral", "engage", "retention")):
                            hooks.append(r.get("insight", r.get("summary", ""))[:150])
                except Exception:
                    pass
            if hooks:
                intel_content = "VIRAL HOOK PATTERNS:\n" + "\n".join(f"- {h}" for h in hooks[:10])
            else:
                intel_content = "No viral hook data yet. Run Atlas deep research on 'viral hooks dark motivation content' to gather data."

        elif feed_type == "strategy":
            intel_topic = "Content Strategy Intel from Atlas"
            # Pull improvements for the target agent
            imp_file = ATLAS_ROOT / "data" / "improvements.json"
            items = []
            if imp_file.exists():
                try:
                    with open(imp_file) as f:
                        improvements = json.load(f)
                    key = target if target != "lisa" else "mercury"
                    items = improvements.get(key, improvements.get(target, []))[:8]
                except Exception:
                    pass
            if items:
                intel_content = "STRATEGY RECOMMENDATIONS:\n" + "\n".join(
                    f"- [{i.get('priority', 'medium').upper()}] {i.get('title', i.get('suggestion', i.get('description', '')))[:120]}"
                    for i in items
                )
            else:
                intel_content = "No strategy improvements available. Run an Atlas improvement scan first."

        elif feed_type == "revenue":
            intel_topic = "Revenue & Monetization Intel from Atlas"
            # Pull from KB and research related to monetization
            revenue_intel = []
            kb_file = ATLAS_ROOT / "data" / "knowledge_base.json"
            if kb_file.exists():
                try:
                    with open(kb_file) as f:
                        kb = json.load(f)
                    for l in kb.get("learnings", []):
                        text = l.get("insight", l.get("learning", "")).lower()
                        if any(kw in text for kw in ("revenue", "monetiz", "income", "sponsor", "affiliate", "merch", "money")):
                            revenue_intel.append(l.get("insight", l.get("learning", ""))[:150])
                except Exception:
                    pass
            if revenue_intel:
                intel_content = "REVENUE OPPORTUNITIES:\n" + "\n".join(f"- {r}" for r in revenue_intel[:8])
            else:
                intel_content = "No revenue intelligence yet. Run Atlas deep research on 'dark motivation content monetization strategies' to gather data."

        if not intel_content:
            return jsonify({"error": "No intelligence available for this feed type"}), 400

        # Push to agent brain
        note_id = _add_brain_note(
            agent=target,
            topic=intel_topic,
            content=intel_content[:2000],
            note_type="note",
            tags=["atlas", "content-intel", feed_type],
        )

        # Log the feed
        feed_log = _load_feed_log()
        feed_log["feeds"].append({
            "target": target,
            "type": feed_type,
            "topic": intel_topic,
            "timestamp": datetime.now(et).isoformat(),
            "note_id": note_id,
            "content_preview": intel_content[:100],
        })
        feed_log["feeds"] = feed_log["feeds"][-50:]  # Keep last 50
        stats = feed_log.get("stats", {"total_feeds": 0, "soren_feeds": 0, "lisa_feeds": 0})
        stats["total_feeds"] = stats.get("total_feeds", 0) + 1
        if target == "soren":
            stats["soren_feeds"] = stats.get("soren_feeds", 0) + 1
        elif target == "lisa":
            stats["lisa_feeds"] = stats.get("lisa_feeds", 0) + 1
        feed_log["stats"] = stats
        _save_feed_log(feed_log)

        return jsonify({
            "success": True,
            "target": target,
            "type": feed_type,
            "topic": intel_topic,
            "note_id": note_id,
            "message": f"Intel pushed to {target.title()}'s brain",
        })
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@atlas_bp.route("/api/atlas/niche-scan", methods=["POST"])
def api_atlas_niche_scan():
    """Trigger a focused niche scan for Soren's dark motivation content space."""
    atlas = get_atlas()
    if not atlas:
        return jsonify({"error": "Atlas not available"}), 503
    try:
        # Use competitor spy to scan Soren's niche
        result = atlas.spy.scan_soren_competitors()
        return jsonify({
            "success": True,
            "total": result.get("total", 0),
            "new_count": result.get("new_count", 0),
            "takeaways": result.get("takeaways", []),
            "scanned_at": result.get("scanned_at"),
        })
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@atlas_bp.route("/api/atlas/content-pipeline")
def api_atlas_content_pipeline():
    """Status of the content intelligence pipeline — what's been fed, when, to whom."""
    feed_log = _load_feed_log()
    feeds = feed_log.get("feeds", [])
    stats = feed_log.get("stats", {})

    # Last feed per agent
    last_soren = None
    last_lisa = None
    for f in reversed(feeds):
        if f.get("target") == "soren" and not last_soren:
            last_soren = f
        elif f.get("target") == "lisa" and not last_lisa:
            last_lisa = f
        if last_soren and last_lisa:
            break

    return jsonify({
        "stats": stats,
        "recent_feeds": feeds[-8:],
        "last_soren_feed": last_soren,
        "last_lisa_feed": last_lisa,
        "total_feeds": len(feeds),
    })


@atlas_bp.route("/api/atlas/suggest-agent")
def api_atlas_suggest_agent():
    """Atlas suggests whether a new agent is needed and describes its role."""
    atlas = get_atlas()
    if not atlas:
        return jsonify({"error": "Atlas not available"}), 503
    try:
        # Check improvement suggestions for new_agents
        improvements = {}
        imp_path = ATLAS_ROOT / "data" / "improvements.json"
        if imp_path.exists():
            try:
                with open(imp_path) as f:
                    improvements = json.load(f)
            except Exception:
                pass

        suggested_agents = improvements.get("new_agents", [])

        # Check research insights for agent-related findings
        research_insights = []
        rl_path = ATLAS_ROOT / "data" / "research_log.json"
        if rl_path.exists():
            try:
                with open(rl_path) as f:
                    rl = json.load(f)
                research_insights = [r for r in (rl if isinstance(rl, list) else [])
                                     if any(kw in str(r).lower() for kw in
                                            ["new agent", "missing agent", "additional agent", "agent gap"])][-5:]
            except Exception:
                pass

        # Current system gaps that might warrant a new agent
        current_agents = ["garves", "soren", "shelby", "atlas", "lisa", "thor", "robotox", "hawk", "viper", "quant", "odin"]
        gap_areas = [
            {"area": "Analytics/BI", "description": "Dedicated data visualization and business intelligence agent",
             "need": "medium", "reason": "Currently Atlas handles both research and analytics — a dedicated BI agent could provide richer dashboards and trend analysis"},
            {"area": "DevOps/CI-CD", "description": "Automated deployment, testing pipeline, and infrastructure management",
             "need": "low", "reason": "Thor handles coding but there is no dedicated CI/CD pipeline agent — launchctl management is manual"},
            {"area": "Finance/Accounting", "description": "Track API costs, revenue, P&L across all agents",
             "need": "medium", "reason": "Shelby tracks economics but a dedicated finance agent could do deeper cost optimization"},
        ]

        return jsonify({
            "current_agents": len(current_agents),
            "agent_list": current_agents,
            "suggested_by_atlas": suggested_agents,
            "research_based_suggestions": research_insights,
            "gap_analysis": gap_areas,
            "verdict": "System is well-covered with 7 agents. Consider a BI/Analytics agent if dashboard complexity grows."
                       if not suggested_agents
                       else f"Atlas has identified {len(suggested_agents)} potential new agent(s).",
        })
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500, 500


# ═══════════════════════════════════════════════════════
# V8: Priority Queue + Dashboard Summary
# ═══════════════════════════════════════════════════════

import time as _time

_AGENTS_ALL = ["garves", "hawk", "odin", "soren", "lisa", "shelby", "thor", "robotox", "viper", "quant", "oracle"]


def _read_json(path: Path) -> dict | list | None:
    """Read a JSON file, return None on failure."""
    try:
        if path.exists():
            return json.loads(path.read_text())
    except Exception:
        pass
    return None


def _safe_ts(val, default: float = 0.0) -> float:
    """Convert timestamp value (float, int, or ISO string) to epoch float."""
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str) and val:
        try:
            return datetime.fromisoformat(val.replace("Z", "+00:00")).timestamp()
        except Exception:
            pass
    return default


@atlas_bp.route("/api/atlas/priority-queue")
def api_atlas_priority_queue():
    """Smart priority queue — ranked actions based on real system state.

    Reads KB health, agent feeds, research log, costs to generate
    a ranked list of what Atlas should do next.
    """
    actions: list[dict] = []
    now = _time.time()

    # ── 1. KB Health Actions ──
    kb_file = ATLAS_ROOT / "data" / "knowledge_base.json"
    kb_data = _read_json(kb_file)
    if kb_data:
        observations = kb_data.get("observations", [])
        learnings = kb_data.get("learnings", [])
        old_obs = [o for o in observations
                   if (now - _safe_ts(o.get("timestamp"), now)) > 7 * 86400]
        low_conf = [l for l in learnings if l.get("confidence", 1.0) < 0.4]

        if len(old_obs) > 10:
            actions.append({
                "id": "compress_kb",
                "title": f"Compress KB ({len(old_obs)} stale observations)",
                "description": f"{len(old_obs)} observations older than 7 days. Compress into learnings to keep brain sharp.",
                "priority": 70 + min(len(old_obs), 30),
                "category": "kb",
                "action_endpoint": "/api/atlas/summarize",
                "action_method": "POST",
                "impact": f"Reduce {len(observations)} obs to ~{len(observations) - len(old_obs) + len(old_obs) // 3}",
            })

        if low_conf:
            actions.append({
                "id": "prune_low_conf",
                "title": f"Review {len(low_conf)} low-confidence learnings",
                "description": "Learnings with <40% confidence may be wrong. Consolidate or remove.",
                "priority": 50 + len(low_conf) * 2,
                "category": "kb",
                "action_endpoint": "/api/atlas/kb-consolidate",
                "action_method": "POST",
                "impact": f"Clean {len(low_conf)} weak entries",
            })

        if len(observations) > 0 and len(learnings) < len(observations) // 2:
            actions.append({
                "id": "learn_from_obs",
                "title": "Extract learnings from observations",
                "description": f"Only {len(learnings)} learnings from {len(observations)} observations. Atlas should synthesize more.",
                "priority": 60,
                "category": "kb",
                "action_endpoint": "/api/atlas/summarize",
                "action_method": "POST",
                "impact": f"Grow learnings from {len(learnings)} to ~{len(learnings) + len(observations) // 4}",
            })

    # ── 2. Agent Feed Actions ──
    bg_file = ATLAS_ROOT / "data" / "background_status.json"
    bg_data = _read_json(bg_file)
    if bg_data:
        feed_log = bg_data.get("agent_feed_log", {})
        for agent in _AGENTS_ALL:
            last_feed = feed_log.get(agent, {})
            last_ts = _safe_ts(last_feed.get("last_fed", 0)) if isinstance(last_feed, dict) else 0.0
            hours_stale = (now - last_ts) / 3600 if last_ts > 0 else 999

            if hours_stale > 6:
                prio = min(95, 55 + int(hours_stale))
                actions.append({
                    "id": f"feed_{agent}",
                    "title": f"Feed {agent.title()} (stale {int(hours_stale)}h)",
                    "description": f"Last intel feed was {int(hours_stale)}h ago. {agent.title()} may be operating on outdated data.",
                    "priority": prio,
                    "category": "feeding",
                    "action_endpoint": f"/api/atlas/{agent}",
                    "action_method": "GET",
                    "impact": f"Fresh intel for {agent.title()}",
                })

    # ── 3. Research Actions ──
    rl_file = ATLAS_ROOT / "data" / "research_log.json"
    rl_data = _read_json(rl_file)
    if rl_data and isinstance(rl_data, list):
        recent = [r for r in rl_data if (now - _safe_ts(r.get("timestamp"), 0)) < 86400]
        if len(recent) < 5:
            actions.append({
                "id": "boost_research",
                "title": "Research output low today",
                "description": f"Only {len(recent)} research entries in 24h. Atlas should run a focused cycle.",
                "priority": 65,
                "category": "research",
                "action_endpoint": "/api/atlas/background/start",
                "action_method": "POST",
                "impact": "Trigger fresh research cycle",
            })

        # Quality check
        scored = [r for r in recent if r.get("quality_score")]
        if scored:
            avg_q = sum(r["quality_score"] for r in scored) / len(scored)
            if avg_q < 6.0:
                actions.append({
                    "id": "improve_quality",
                    "title": f"Research quality dropping ({avg_q:.1f}/10)",
                    "description": "Recent research quality below 6/10. Consider changing topics or sources.",
                    "priority": 60,
                    "category": "research",
                    "action_endpoint": "/api/atlas/deep-research",
                    "action_method": "POST",
                    "impact": "Higher quality intel",
                })

    # ── 4. Cost Budget Actions ──
    cost_file = ATLAS_ROOT / "data" / "cost_tracker.json"
    cost_data = _read_json(cost_file)
    if cost_data:
        daily = cost_data.get("daily", {})
        et = ZoneInfo("America/New_York")
        today = datetime.now(et).strftime("%Y-%m-%d")
        today_data = daily.get(today, {})
        tavily_today = today_data.get("tavily_calls", 0)
        if tavily_today > 300:
            actions.append({
                "id": "budget_warning",
                "title": f"High API usage today ({tavily_today} Tavily calls)",
                "description": "Burning through Tavily credits fast. Consider pausing non-critical research.",
                "priority": 75,
                "category": "system",
                "action_endpoint": "/api/atlas/background/stop",
                "action_method": "POST",
                "impact": "Save API budget",
            })

    # ── 5. Improvement Scan ──
    imp_file = ATLAS_ROOT / "data" / "improvements.json"
    imp_data = _read_json(imp_file)
    total_suggestions = 0
    if imp_data:
        for key, val in imp_data.items():
            if isinstance(val, list):
                total_suggestions += len(val)
    if total_suggestions == 0:
        actions.append({
            "id": "run_improvement_scan",
            "title": "Generate improvement suggestions",
            "description": "No current improvement suggestions. Atlas should scan all agents for optimization opportunities.",
            "priority": 45,
            "category": "system",
            "action_endpoint": "/api/atlas/improvements",
            "action_method": "POST",
            "impact": "Find new optimizations",
        })

    # Sort by priority (highest first)
    actions.sort(key=lambda a: a["priority"], reverse=True)

    return jsonify({
        "actions": actions[:8],
        "total_actions": len(actions),
        "generated_at": now,
    })


@atlas_bp.route("/api/atlas/dashboard-summary")
def api_atlas_dashboard_summary():
    """Unified summary for Atlas dashboard — KB health, feeds, research ROI."""
    now = _time.time()

    # ── KB Health ──
    kb_file = ATLAS_ROOT / "data" / "knowledge_base.json"
    kb_data = _read_json(kb_file)
    obs_count = 0
    learn_count = 0
    avg_confidence = 0.0
    stale_count = 0
    kb_score = 0

    if kb_data:
        observations = kb_data.get("observations", [])
        learnings = kb_data.get("learnings", [])
        obs_count = len(observations)
        learn_count = len(learnings)
        if learnings:
            confs = [l.get("confidence", 0.5) for l in learnings]
            avg_confidence = sum(confs) / len(confs)
        stale_count = sum(1 for o in observations
                         if (now - _safe_ts(o.get("timestamp"), now)) > 7 * 86400)
        # Health score: 0-100
        freshness = max(0, 100 - stale_count * 2)
        confidence_score = int(avg_confidence * 100)
        ratio_score = min(100, int(learn_count / max(obs_count, 1) * 200))
        kb_score = (freshness + confidence_score + ratio_score) // 3

    # ── Agent Feed Status ──
    bg_file = ATLAS_ROOT / "data" / "background_status.json"
    bg_data = _read_json(bg_file)
    feeds: list[dict] = []
    fed_count = 0
    starving_count = 0

    if bg_data:
        feed_log = bg_data.get("agent_feed_log", {})
        for agent in _AGENTS_ALL:
            last_feed = feed_log.get(agent, {})
            last_ts = _safe_ts(last_feed.get("last_fed", 0)) if isinstance(last_feed, dict) else 0.0
            hours_ago = (now - last_ts) / 3600 if last_ts > 0 else 999
            status = "fresh" if hours_ago < 3 else "stale" if hours_ago < 12 else "starving"
            if status == "fresh":
                fed_count += 1
            elif status == "starving":
                starving_count += 1
            feeds.append({
                "agent": agent,
                "hours_ago": round(hours_ago, 1) if hours_ago < 500 else None,
                "status": status,
            })
        feeds.sort(key=lambda f: -(f["hours_ago"] or 999))

    # ── Research ROI ──
    rl_file = ATLAS_ROOT / "data" / "research_log.json"
    rl_data = _read_json(rl_file) or []
    cost_file = ATLAS_ROOT / "data" / "cost_tracker.json"
    cost_data = _read_json(cost_file) or {}

    total_researches = len(rl_data) if isinstance(rl_data, list) else 0
    recent_24h = [r for r in (rl_data if isinstance(rl_data, list) else [])
                  if (now - _safe_ts(r.get("timestamp"), 0)) < 86400]
    scored = [r for r in recent_24h if r.get("quality_score")]
    avg_quality = round(sum(r["quality_score"] for r in scored) / len(scored), 1) if scored else 0
    high_quality = sum(1 for r in scored if r["quality_score"] >= 7)
    hit_rate = round(high_quality / len(scored) * 100, 1) if scored else 0

    # Cost
    daily = cost_data.get("daily", {})
    et = ZoneInfo("America/New_York")
    today_key = datetime.now(et).strftime("%Y-%m-%d")
    today_data = daily.get(today_key, {})
    month_prefix = today_key[:7]
    month_tavily = sum(d.get("tavily_calls", 0) for k, d in daily.items() if k.startswith(month_prefix))
    month_openai = sum(d.get("openai_calls", 0) for k, d in daily.items() if k.startswith(month_prefix))

    return jsonify({
        "kb": {
            "score": kb_score,
            "observations": obs_count,
            "learnings": learn_count,
            "avg_confidence": round(avg_confidence, 2),
            "stale": stale_count,
        },
        "feeds": {
            "agents": feeds,
            "fed": fed_count,
            "starving": starving_count,
            "total": len(_AGENTS_ALL),
        },
        "research": {
            "total": total_researches,
            "today": len(recent_24h),
            "avg_quality": avg_quality,
            "hit_rate": hit_rate,
            "today_tavily": today_data.get("tavily_calls", 0),
            "month_tavily": month_tavily,
            "month_openai": month_openai,
            "tavily_budget": 12000,
        },
    })
