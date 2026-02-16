"""Atlas (research/intelligence) routes: /api/atlas/*"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

from flask import Blueprint, jsonify, request

from bot.shared import (
    get_atlas,
    ATLAS_ROOT,
    COMPETITOR_INTEL_FILE,
)

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
        return jsonify({"error": str(e)[:200]}), 500


@atlas_bp.route("/api/atlas/garves")
def api_atlas_garves():
    """Atlas deep analysis of Garves."""
    atlas = get_atlas()
    if not atlas:
        return jsonify({"error": "Atlas not available"}), 503
    try:
        return jsonify(atlas.api_garves_deep())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@atlas_bp.route("/api/atlas/soren")
def api_atlas_soren():
    """Atlas deep analysis of Soren."""
    atlas = get_atlas()
    if not atlas:
        return jsonify({"error": "Atlas not available"}), 503
    try:
        return jsonify(atlas.api_soren_deep())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@atlas_bp.route("/api/atlas/live-research")
def api_atlas_live_research():
    """What Atlas is currently researching -- recent URLs, sources, insights."""
    atlas = get_atlas()
    if not atlas:
        return jsonify({"error": "Atlas not available", "articles": []})
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
        return jsonify({"error": str(e)[:200], "articles": []})


@atlas_bp.route("/api/atlas/experiments")
def api_atlas_experiments():
    """Atlas experiment data."""
    atlas = get_atlas()
    if not atlas:
        return jsonify({"error": "Atlas not available"}), 503
    try:
        return jsonify(atlas.api_experiments())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@atlas_bp.route("/api/atlas/knowledge")
def api_atlas_knowledge():
    """Atlas knowledge base."""
    atlas = get_atlas()
    if not atlas:
        return jsonify({"error": "Atlas not available"}), 503
    try:
        return jsonify(atlas.api_knowledge())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@atlas_bp.route("/api/atlas/shelby")
def api_atlas_shelby():
    """Atlas deep analysis of Shelby."""
    atlas = get_atlas()
    if not atlas:
        return jsonify({"error": "Atlas not available"}), 503
    try:
        return jsonify(atlas.api_shelby_deep())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@atlas_bp.route("/api/atlas/mercury")
def api_atlas_mercury():
    """Atlas deep analysis of Mercury."""
    atlas = get_atlas()
    if not atlas:
        return jsonify({"error": "Atlas not available"}), 503
    try:
        return jsonify(atlas.api_mercury_deep())
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
        return jsonify({"error": str(e)[:200]}), 500


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
        return jsonify({"error": str(e)[:200]}), 500


@atlas_bp.route("/api/atlas/costs")
def api_atlas_costs():
    """API cost tracker data (Tavily + OpenAI)."""
    cost_file = ATLAS_ROOT / "data" / "cost_tracker.json"
    if not cost_file.exists():
        return jsonify({"today_tavily": 0, "today_openai": 0,
                        "month_tavily": 0, "month_openai": 0,
                        "projected_tavily": 0})
    try:
        with open(cost_file) as f:
            tracker = json.load(f)

        daily = tracker.get("daily", {})
        today = datetime.now(timezone(timedelta(hours=-5))).strftime("%Y-%m-%d")
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
        return jsonify({"error": str(e)[:200]})


@atlas_bp.route("/api/atlas/summarize", methods=["POST"])
def api_atlas_summarize():
    """Compress old observations into learnings."""
    atlas = get_atlas()
    if not atlas:
        return jsonify({"error": "Atlas not available"}), 503
    try:
        return jsonify(atlas.api_summarize_kb())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


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
        # Build a clean response
        state = status.get("state", "idle")
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
        return jsonify({
            "running": status.get("running", False),
            "state": state,
            "state_label": state_labels.get(state, state.replace("_", " ").title()),
            "cycles": status.get("cycles", 0),
            "started_at": status.get("started_at", None),
            "last_cycle": status.get("last_cycle", None),
            "last_findings": status.get("last_findings", 0),
            "last_error": status.get("last_error", None),
            "total_researches": research.get("total_researches", 0),
            "unique_urls": research.get("seen_urls", 0) if isinstance(research.get("seen_urls"), int) else len(research.get("seen_urls", [])),
            "data_feed_active": data_feed.get("active", False),
            "data_feed_sources": data_feed.get("sources_count", 0),
            "current_target": status.get("current_target", None),
            "recent_learn_count": status.get("recent_learn_count", 0),
            "cycle_minutes": 45,
            "agent_feed_log": status.get("agent_feed_log", {}),
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
        return jsonify({"error": str(e)[:200]}), 500


@atlas_bp.route("/api/atlas/background/stop", methods=["POST"])
def api_atlas_bg_stop():
    """Stop Atlas background research loop."""
    atlas = get_atlas()
    if not atlas:
        return jsonify({"error": "Atlas not available"}), 503
    try:
        return jsonify(atlas.api_stop_background())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@atlas_bp.route("/api/atlas/competitors")
def api_atlas_competitors():
    """Competitor intelligence data."""
    if not COMPETITOR_INTEL_FILE.exists():
        return jsonify({"trading": [], "content": [], "ai_agents": [], "scanned_at": None})
    try:
        with open(COMPETITOR_INTEL_FILE) as f:
            data = json.load(f)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)[:200]})


@atlas_bp.route("/api/atlas/learning/<agent>")
def api_atlas_learning(agent):
    """Learning status for an agent from Atlas knowledge base."""
    agent = agent.lower()
    kb_file = ATLAS_ROOT / "data" / "knowledge_base.json"
    result = {"observations": 0, "hypotheses": 0, "improvements_applied": 0, "learning_score": "Novice"}

    if kb_file.exists():
        try:
            with open(kb_file) as f:
                kb = json.load(f)
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
