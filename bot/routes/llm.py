"""Intelligence (LLM + Memory) routes: /api/llm/*"""
from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

from flask import Blueprint, jsonify, request

log = logging.getLogger(__name__)
llm_bp = Blueprint("llm", __name__)

SHARED_DIR = Path.home() / "shared"
COSTS_FILE = SHARED_DIR / "llm_costs.jsonl"
CONFIG_FILE = SHARED_DIR / "llm_config.json"
MEMORY_DIR = SHARED_DIR / "memory"


# Pro M3 LAN IP — LLM server runs on Pro only
PRO_LLM_URL = "http://10.0.0.127:11434/v1"


@llm_bp.route("/api/llm/status")
def llm_status():
    """LLM server health + model info. Checks localhost first, then Pro's LAN IP."""
    server_online = False
    model_info = {}

    try:
        sys.path.insert(0, str(SHARED_DIR))
        from llm_client import _load_config, _is_local_server_up
        cfg = _load_config()
        server_online = _is_local_server_up(cfg)

        # If localhost check failed, try Pro's LAN IP (we might be on Air)
        if not server_online:
            import urllib.request
            try:
                req = urllib.request.Request(f"{PRO_LLM_URL}/models", method="GET")
                with urllib.request.urlopen(req, timeout=3):
                    server_online = True
            except Exception:
                pass

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
        "razor", "odin",
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


@llm_bp.route("/api/llm/brain-activity")
def llm_brain_activity():
    """Per-agent brain activity — recent LLM calls per agent (last 5 min)."""
    import time
    activity = {}
    cutoff = time.time() - 300  # last 5 min
    agents_list = [
        "shelby", "atlas", "lisa", "hawk", "soren",
        "garves", "quant", "viper", "robotox", "thor",
    ]
    for a in agents_list:
        activity[a] = {"calls": 0, "last_call": None, "active": False}

    if COSTS_FILE.exists():
        try:
            lines = COSTS_FILE.read_text().strip().split("\n")
            for line in reversed(lines[-200:]):
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                    ts = entry.get("timestamp", 0)
                    agent = entry.get("agent", "unknown")
                    if isinstance(ts, str):
                        from datetime import datetime
                        ts = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                    if ts > cutoff and agent in activity:
                        activity[agent]["calls"] += 1
                        if not activity[agent]["last_call"]:
                            activity[agent]["last_call"] = entry.get("timestamp")
                        activity[agent]["active"] = True
                except (json.JSONDecodeError, Exception):
                    continue
        except Exception:
            pass

    return jsonify({"activity": activity})


@llm_bp.route("/api/llm/pattern-feed")
def llm_pattern_feed():
    """Recent learned patterns across all agents — for the learnings feed."""
    all_patterns = []
    agents_list = [
        "shelby", "atlas", "lisa", "hawk", "soren",
        "garves", "quant", "viper", "robotox", "thor",
    ]
    try:
        sys.path.insert(0, str(SHARED_DIR))
        from agent_memory import AgentMemory
        for agent in agents_list:
            db_path = MEMORY_DIR / f"{agent}.db"
            if not db_path.exists():
                continue
            try:
                mem = AgentMemory(agent)
                patterns = mem.get_active_patterns(min_confidence=0.3)
                for p in patterns[:5]:
                    p["agent"] = agent
                    all_patterns.append(p)
                mem.close()
            except Exception:
                continue
    except Exception:
        pass

    # Sort by most recent
    all_patterns.sort(key=lambda p: p.get("updated_at", ""), reverse=True)
    return jsonify({"patterns": all_patterns[:30]})


@llm_bp.route("/api/llm/cost-savings")
def llm_cost_savings():
    """Running total of money saved by using local MLX vs cloud."""
    total_local_calls = 0
    total_cloud_calls = 0
    total_cloud_cost = 0.0
    estimated_savings = 0.0

    if COSTS_FILE.exists():
        try:
            lines = COSTS_FILE.read_text().strip().split("\n")
            for line in lines:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                    provider = entry.get("provider", "")
                    cost = entry.get("cost", 0)
                    if "local" in provider.lower():
                        total_local_calls += 1
                        # Estimated cloud cost if this had been a cloud call
                        estimated_savings += 0.002  # ~$0.002 per call saved
                    else:
                        total_cloud_calls += 1
                        total_cloud_cost += cost if isinstance(cost, (int, float)) else 0
                except (json.JSONDecodeError, Exception):
                    continue
        except Exception:
            pass

    return jsonify({
        "local_calls": total_local_calls,
        "cloud_calls": total_cloud_calls,
        "cloud_cost": round(total_cloud_cost, 4),
        "estimated_savings": round(estimated_savings, 4),
        "total_calls": total_local_calls + total_cloud_calls,
    })


# ═══════════════════════════════════════════
#  Smart Action Endpoints
# ═══════════════════════════════════════════


@llm_bp.route("/api/llm/actions/prune-patterns", methods=["POST"])
def llm_prune_patterns():
    """Prune low-confidence patterns (< 0.3) across all agents."""
    pruned = {}
    agents_list = [
        "shelby", "atlas", "lisa", "hawk", "soren",
        "garves", "quant", "viper", "robotox", "thor",
    ]
    try:
        sys.path.insert(0, str(SHARED_DIR))
        from agent_memory import AgentMemory
        for agent in agents_list:
            db_path = MEMORY_DIR / f"{agent}.db"
            if not db_path.exists():
                continue
            try:
                mem = AgentMemory(agent)
                # Get low-confidence patterns
                conn = mem.conn
                cursor = conn.execute(
                    "UPDATE patterns SET active = 0 WHERE confidence < 0.3 AND active = 1"
                )
                count = cursor.rowcount
                conn.commit()
                if count > 0:
                    pruned[agent] = count
                mem.close()
            except Exception:
                continue
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

    total = sum(pruned.values())
    return jsonify({"pruned": pruned, "total_pruned": total})


@llm_bp.route("/api/llm/actions/health-check", methods=["POST"])
def llm_health_check():
    """Test LLM connectivity — local + cloud fallback."""
    results = {}

    # Test local server (try localhost, then Pro's LAN IP)
    try:
        sys.path.insert(0, str(SHARED_DIR))
        from llm_client import _load_config, _is_local_server_up, llm_call
        cfg = _load_config()
        results["local_server"] = _is_local_server_up(cfg)
        if not results["local_server"]:
            import urllib.request
            try:
                req = urllib.request.Request(f"{PRO_LLM_URL}/models", method="GET")
                with urllib.request.urlopen(req, timeout=3):
                    results["local_server"] = True
                    results["local_note"] = "via Pro LAN"
            except Exception:
                pass
    except Exception as e:
        results["local_server"] = False
        results["local_error"] = str(e)[:100]

    # Test a quick cloud call
    try:
        start = time.time()
        resp = llm_call(
            system="You are a test.",
            user="Respond with only: OK",
            agent="dashboard",
            task_type="fast",
            max_tokens=5,
        )
        latency = round((time.time() - start) * 1000)
        results["cloud_fallback"] = bool(resp and len(resp) > 0)
        results["cloud_response"] = resp[:50] if resp else ""
        results["cloud_latency_ms"] = latency
    except Exception as e:
        results["cloud_fallback"] = False
        results["cloud_error"] = str(e)[:100]

    # Check memory DBs
    db_count = 0
    total_size = 0
    for f in MEMORY_DIR.glob("*.db"):
        db_count += 1
        total_size += f.stat().st_size
    results["memory_dbs"] = db_count
    results["memory_size_mb"] = round(total_size / 1024 / 1024, 2)

    # Check cost log
    if COSTS_FILE.exists():
        results["cost_log_size_kb"] = round(COSTS_FILE.stat().st_size / 1024, 1)
        try:
            lines = COSTS_FILE.read_text().strip().split("\n")
            results["total_logged_calls"] = len(lines)
        except Exception:
            results["total_logged_calls"] = 0
    else:
        results["cost_log_size_kb"] = 0
        results["total_logged_calls"] = 0

    return jsonify(results)


@llm_bp.route("/api/llm/actions/export-learnings", methods=["POST"])
def llm_export_learnings():
    """Export all patterns and high-value decisions to a JSON summary."""
    export = {"exported_at": time.strftime("%Y-%m-%d %H:%M:%S"), "agents": {}}
    agents_list = [
        "shelby", "atlas", "lisa", "hawk", "soren",
        "garves", "quant", "viper", "robotox", "thor",
    ]
    try:
        sys.path.insert(0, str(SHARED_DIR))
        from agent_memory import AgentMemory
        for agent in agents_list:
            db_path = MEMORY_DIR / f"{agent}.db"
            if not db_path.exists():
                continue
            try:
                mem = AgentMemory(agent)
                patterns = mem.get_active_patterns(min_confidence=0.4)
                decisions = mem.get_recent_decisions(limit=20)
                stats = mem.get_stats()
                export["agents"][agent] = {
                    "stats": stats,
                    "patterns": patterns,
                    "recent_decisions": decisions,
                }
                mem.close()
            except Exception:
                continue
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

    # Save to file
    export_path = Path.home() / "shared" / "learnings_export.json"
    export_path.write_text(json.dumps(export, indent=2, default=str))

    return jsonify({
        "success": True,
        "path": str(export_path),
        "agents_exported": len(export["agents"]),
        "total_patterns": sum(
            len(a.get("patterns", [])) for a in export["agents"].values()
        ),
    })


@llm_bp.route("/api/llm/actions/reset-agent-memory", methods=["POST"])
def llm_reset_agent_memory():
    """Reset memory for a specific agent (requires agent param)."""
    agent = request.json.get("agent") if request.is_json else request.args.get("agent")
    if not agent:
        return jsonify({"error": "agent parameter required"}), 400

    allowed = [
        "shelby", "atlas", "lisa", "hawk", "soren",
        "garves", "quant", "viper", "robotox", "thor",
    ]
    if agent not in allowed:
        return jsonify({"error": f"Unknown agent: {agent}"}), 400

    db_path = MEMORY_DIR / f"{agent}.db"
    if not db_path.exists():
        return jsonify({"error": f"No memory DB for {agent}"}), 404

    try:
        # Backup first
        backup_path = MEMORY_DIR / f"{agent}.db.bak"
        import shutil
        shutil.copy2(db_path, backup_path)

        sys.path.insert(0, str(SHARED_DIR))
        from agent_memory import AgentMemory
        mem = AgentMemory(agent)
        mem.conn.execute("DELETE FROM decisions")
        mem.conn.execute("DELETE FROM patterns")
        mem.conn.execute("DELETE FROM knowledge")
        mem.conn.commit()
        mem.close()
        return jsonify({
            "success": True,
            "agent": agent,
            "backup": str(backup_path),
        })
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@llm_bp.route("/api/llm/actions/compact-costs", methods=["POST"])
def llm_compact_costs():
    """Compact cost log — keep last 7 days, archive the rest."""
    if not COSTS_FILE.exists():
        return jsonify({"message": "No cost log to compact"})

    try:
        lines = COSTS_FILE.read_text().strip().split("\n")
        original_count = len(lines)
        cutoff = time.time() - (7 * 86400)

        kept = []
        archived = []
        for line in lines:
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                ts = entry.get("timestamp", "")
                if isinstance(ts, str) and ts:
                    from datetime import datetime
                    ts_epoch = datetime.fromisoformat(
                        ts.replace("Z", "+00:00")
                    ).timestamp()
                else:
                    ts_epoch = ts if isinstance(ts, (int, float)) else 0
                if ts_epoch > cutoff:
                    kept.append(line)
                else:
                    archived.append(line)
            except Exception:
                kept.append(line)  # Keep unparseable lines

        if archived:
            archive_path = SHARED_DIR / "llm_costs_archive.jsonl"
            with open(archive_path, "a") as f:
                f.write("\n".join(archived) + "\n")
            COSTS_FILE.write_text("\n".join(kept) + "\n")

        return jsonify({
            "original_lines": original_count,
            "kept": len(kept),
            "archived": len(archived),
        })
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500
