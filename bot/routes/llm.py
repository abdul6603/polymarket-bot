"""Intelligence (LLM + Memory) routes: /api/llm/*"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from flask import Blueprint, jsonify

log = logging.getLogger(__name__)
llm_bp = Blueprint("llm", __name__)

SHARED_DIR = Path.home() / "shared"
COSTS_FILE = SHARED_DIR / "llm_costs.jsonl"
CONFIG_FILE = SHARED_DIR / "llm_config.json"
MEMORY_DIR = SHARED_DIR / "memory"


@llm_bp.route("/api/llm/status")
def llm_status():
    """LLM server health + model info."""
    server_online = False
    model_info = {}

    try:
        sys.path.insert(0, str(SHARED_DIR))
        from llm_client import _load_config, _is_local_server_up
        cfg = _load_config()
        server_online = _is_local_server_up(cfg)
        model_info = {
            "local_large": cfg.get("models", {}).get("local_large", ""),
            "local_small": cfg.get("models", {}).get("local_small", ""),
            "base_url": cfg.get("local_server", {}).get("base_url", ""),
        }
    except Exception as e:
        log.debug("LLM status check failed: %s", str(e)[:100])

    return jsonify({
        "server_online": server_online,
        "models": model_info,
    })


@llm_bp.route("/api/llm/costs")
def llm_costs():
    """Cost tracking data — local vs cloud calls, daily savings."""
    try:
        sys.path.insert(0, str(SHARED_DIR))
        from llm_client import get_cost_summary
        summary_24h = get_cost_summary(hours=24)
        summary_7d = get_cost_summary(hours=168)
    except Exception as e:
        log.debug("LLM cost summary failed: %s", str(e)[:100])
        summary_24h = {"total_calls": 0, "total_cost": 0}
        summary_7d = {"total_calls": 0, "total_cost": 0}

    # Calculate savings estimate
    local_calls_24h = summary_24h.get("by_provider", {}).get("local", {}).get("calls", 0)
    # Rough estimate: each local call saves ~$0.001 (gpt-4o-mini equivalent)
    estimated_savings_24h = round(local_calls_24h * 0.001, 4)

    return jsonify({
        "last_24h": summary_24h,
        "last_7d": summary_7d,
        "estimated_savings_24h": estimated_savings_24h,
    })


@llm_bp.route("/api/llm/memory/<agent>")
def llm_memory(agent):
    """Per-agent memory stats."""
    try:
        sys.path.insert(0, str(SHARED_DIR))
        from agent_memory import AgentMemory
        mem = AgentMemory(agent)
        stats = mem.get_stats()
        patterns = mem.get_active_patterns(min_confidence=0.4)
        recent = mem.get_recent_decisions(limit=10)
        mem.close()
        return jsonify({
            "stats": stats,
            "top_patterns": patterns[:10],
            "recent_decisions": recent,
        })
    except Exception as e:
        return jsonify({"error": str(e)[:200], "stats": {}, "top_patterns": [], "recent_decisions": []})


@llm_bp.route("/api/llm/memory-all")
def llm_memory_all():
    """Memory stats for all agents."""
    all_stats = {}
    agents = [
        "shelby", "atlas", "lisa", "hawk", "soren",
        "garves", "quant", "viper", "robotox", "thor",
    ]
    try:
        sys.path.insert(0, str(SHARED_DIR))
        from agent_memory import AgentMemory
        for agent in agents:
            db_path = MEMORY_DIR / f"{agent}.db"
            if db_path.exists():
                try:
                    mem = AgentMemory(agent)
                    all_stats[agent] = mem.get_stats()
                    mem.close()
                except Exception:
                    all_stats[agent] = {"error": True}
    except Exception as e:
        log.debug("Memory all failed: %s", str(e)[:100])

    # Calculate totals
    total_decisions = sum(s.get("total_decisions", 0) for s in all_stats.values() if isinstance(s, dict))
    total_patterns = sum(s.get("active_patterns", 0) for s in all_stats.values() if isinstance(s, dict))
    total_knowledge = sum(s.get("total_knowledge", 0) for s in all_stats.values() if isinstance(s, dict))

    return jsonify({
        "agents": all_stats,
        "totals": {
            "decisions": total_decisions,
            "patterns": total_patterns,
            "knowledge": total_knowledge,
            "agents_with_memory": len([s for s in all_stats.values() if isinstance(s, dict) and s.get("total_decisions", 0) > 0]),
        },
    })


@llm_bp.route("/api/llm/routing")
def llm_routing():
    """Current routing configuration."""
    try:
        if CONFIG_FILE.exists():
            config = json.loads(CONFIG_FILE.read_text())
        else:
            config = {}
    except Exception:
        config = {}

    return jsonify(config)


@llm_bp.route("/api/llm/recent-calls")
def llm_recent_calls():
    """Last 50 LLM calls for the activity feed."""
    calls = []
    if COSTS_FILE.exists():
        try:
            lines = COSTS_FILE.read_text().strip().split("\n")
            for line in reversed(lines[-50:]):
                if line.strip():
                    calls.append(json.loads(line))
        except Exception:
            pass
    return jsonify({"calls": calls[:50]})
