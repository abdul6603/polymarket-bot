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


@atlas_bp.route("/api/atlas/thor")
def api_atlas_thor():
    """Atlas deep analysis of Thor."""
    atlas = get_atlas()
    if not atlas:
        return jsonify({"error": "Atlas not available"}), 503
    try:
        return jsonify(atlas.api_thor_deep())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@atlas_bp.route("/api/atlas/robotox")
def api_atlas_robotox():
    """Atlas deep analysis of Robotox."""
    atlas = get_atlas()
    if not atlas:
        return jsonify({"error": "Atlas not available"}), 503
    try:
        return jsonify(atlas.api_robotox_deep())
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
        return jsonify({"error": str(e)[:200]})


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
        return jsonify({"error": str(e)[:200]}), 500


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
        return jsonify({"error": str(e)[:200]}), 500


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

        # Gather research insights about agent orchestration
        research_log = []
        rl_path = ATLAS_ROOT / "data" / "research_log.json"
        if rl_path.exists():
            try:
                with open(rl_path) as f:
                    rl = json.load(f)
                research_log = [r for r in (rl if isinstance(rl, list) else [])
                                if any(kw in str(r).lower() for kw in
                                       ["agent", "orchestr", "multi-agent", "ai system", "infra"])][-10:]
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
            "total_agents": 7,
            "agents": ["Garves (Trading)", "Soren (Content)", "Shelby (Commander)",
                       "Atlas (Research)", "Lisa (Social)", "Thor (Engineering)", "Robotox (Monitoring)"],
            "features": features,
            "architecture": "Hierarchical: Owner > Claude > Shelby > Agents, Atlas cross-cuts",
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
        gaps.append("No ML-based anomaly detection (rule-based patterns only)")
        gaps.append("No A/B testing framework for agent configurations")
        gaps.append("No automated rollback on failed deployments")

        # Pull gap insights from Atlas learnings
        try:
            kb_file = ATLAS_ROOT / "data" / "knowledge_base.json"
            if kb_file.exists():
                kb = json.loads(kb_file.read_text())
                for learning in (kb.get("learnings", []))[-30:]:
                    insight = learning.get("insight", "")
                    conf = learning.get("confidence", 0)
                    if conf >= 0.75 and any(kw in insight.lower() for kw in
                                            ["needs", "bottleneck", "missing", "should", "improve", "low", "below"]):
                        gap_text = insight[:120]
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
        return jsonify({"error": str(e)[:200]}), 500


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
        current_agents = ["garves", "soren", "shelby", "atlas", "lisa", "thor", "robotox"]
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
        return jsonify({"error": str(e)[:200]}), 500
