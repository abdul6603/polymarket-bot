"""Infrastructure routes: heartbeats, service registry, broadcasts, system health, event bus."""
from __future__ import annotations

import sys
from pathlib import Path

from flask import Blueprint, jsonify, request

# Agent hub path added via bot.shared.ensure_path at import time

infra_bp = Blueprint("infra", __name__)


@infra_bp.route("/api/heartbeats")
def api_heartbeats():
    """Get all agent heartbeats with health status."""
    try:
        from hub import AgentHub
        heartbeats = AgentHub.get_heartbeats()
        return jsonify({"heartbeats": heartbeats})
    except Exception as e:
        return jsonify({"heartbeats": {}, "error": str(e)[:200]})


@infra_bp.route("/api/registry")
def api_registry():
    """Get the service registry — which agents are registered."""
    try:
        from hub import AgentHub
        registry = AgentHub.get_registry()
        return jsonify({"registry": registry})
    except Exception as e:
        return jsonify({"registry": {}, "error": str(e)[:200]})


@infra_bp.route("/api/system-health")
def api_system_health():
    """Get overall system health summary."""
    try:
        from hub import AgentHub
        health = AgentHub.system_health()
        return jsonify(health)
    except Exception as e:
        return jsonify({"overall": "unknown", "error": str(e)[:200]})


# NOTE: /api/broadcasts is registered in overview.py (richer version with ack status)
# Removed duplicate route that was here to prevent Flask route collision.


@infra_bp.route("/api/agent-messages/<agent_name>")
def api_agent_messages(agent_name: str):
    """Get messages for a specific agent."""
    try:
        from hub import AgentHub
        hub = AgentHub(agent_name)
        messages = hub.get_messages(unread_only=False)
        return jsonify({"agent": agent_name, "messages": messages[-20:]})
    except Exception as e:
        return jsonify({"agent": agent_name, "messages": [], "error": str(e)[:200]})


@infra_bp.route("/api/hub-config")
def api_hub_config():
    """Get the hub configuration."""
    try:
        from hub import AgentHub
        config = AgentHub.get_config()
        return jsonify(config)
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@infra_bp.route("/api/health")
def api_health():
    """Live health: process detection + log recency + status files."""
    import json
    import subprocess
    import time
    from pathlib import Path

    AGENTS = {
        "garves":  {"plist": "com.garves.agent",  "log": "/tmp/garves.log",  "status": Path.home() / "polymarket-bot" / "data" / "garves_status.json"},
        "hawk":    {"plist": "com.hawk.agent",     "log": "/tmp/hawk.log",    "status": Path.home() / "polymarket-bot" / "data" / "hawk_status.json"},
        "shelby":  {"plist": "com.shelby.assistant",   "log": "/tmp/shelby.log",  "status": Path.home() / "shelby" / "data" / "health.json"},
        "atlas":   {"plist": "com.atlas.agent",   "log": "/tmp/atlas.log",   "status": Path.home() / "atlas" / "data" / "background_status.json"},
        "soren":   {"plist": "com.soren.agent",    "log": "/tmp/soren.log",   "status": None},
        "quant":   {"plist": "com.quant.agent",    "log": "/tmp/quant.log",   "status": Path.home() / "polymarket-bot" / "data" / "quant_status.json"},
        "robotox": {"plist": "com.robotox.agent",  "log": "/tmp/robotox.log", "status": Path.home() / "sentinel" / "data" / "health.json"},
        "thor":    {"plist": "com.thor.agent",     "log": "/tmp/thor.log",    "status": Path.home() / "thor" / "data" / "status.json"},
        "viper":   {"plist": "com.viper.agent",    "log": "/tmp/viper.log",   "status": Path.home() / "polymarket-bot" / "data" / "viper_status.json"},
        "lisa":    {"plist": "com.lisa.agent",      "log": "/tmp/lisa.log",    "status": Path.home() / "mercury" / "data" / "health.json"},
        "odin":    {"plist": "com.odin.agent",      "log": "/tmp/odin.log",    "status": Path.home() / "odin" / "data" / "status.json"},
        "oracle":  {"plist": "com.oracle.agent",    "log": "/tmp/oracle.log",  "status": Path.home() / "polymarket-bot" / "data" / "oracle_status.json"},
    }

    try:
        result = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=5)
        launchctl_output = result.stdout
    except Exception:
        launchctl_output = ""

    now = time.time()
    agents = {}

    for name, info in AGENTS.items():
        pid = None
        exit_code = None
        process_running = False

        for line in launchctl_output.splitlines():
            if info["plist"] in line:
                parts = line.split()
                if len(parts) >= 3:
                    pid_str = parts[0]
                    exit_str = parts[1]
                    pid = int(pid_str) if pid_str != "-" else None
                    exit_code = int(exit_str) if exit_str != "-" else None
                    process_running = pid is not None and pid > 0
                break

        log_path = Path(info["log"])
        log_age_min = None
        last_log = None
        if log_path.exists():
            mtime = log_path.stat().st_mtime
            log_age_min = round((now - mtime) / 60, 1)
            last_log = time.strftime("%H:%M:%S", time.localtime(mtime))

        status_data = None
        if info["status"] and info["status"].exists():
            try:
                with open(info["status"]) as f:
                    status_data = json.load(f)
            except Exception:
                pass

        if process_running and log_age_min is not None and log_age_min < 10:
            status = "healthy"
        elif process_running and (log_age_min is None or log_age_min < 60):
            status = "healthy"
        elif process_running:
            status = "degraded"
        elif not process_running and pid is None:
            status = "offline"
        else:
            status = "error"

        agents[name] = {
            "status": status,
            "pid": pid,
            "process_running": process_running,
            "log_age_min": log_age_min,
            "last_log": last_log,
            "exit_code": exit_code,
        }
        if status_data and isinstance(status_data, dict):
            for key in ("win_rate", "pnl", "cycle", "state", "running", "last_cycle"):
                if key in status_data:
                    agents[name][key] = status_data[key]

    healthy = sum(1 for a in agents.values() if a["status"] == "healthy")
    degraded = sum(1 for a in agents.values() if a["status"] == "degraded")
    offline = sum(1 for a in agents.values() if a["status"] == "offline")
    errored = sum(1 for a in agents.values() if a["status"] == "error")

    if errored > 0:
        overall = "unhealthy"
    elif offline > 2:
        overall = "degraded"
    elif degraded > 0:
        overall = "degraded"
    else:
        overall = "healthy"

    return jsonify({
        "overall": overall,
        "healthy": healthy,
        "degraded": degraded,
        "offline": offline,
        "error": errored,
        "total": len(agents),
        "agents": agents,
    })


@infra_bp.route("/api/health/<agent_name>")
def api_health_agent(agent_name: str):
    """Individual agent health status."""
    from pathlib import Path
    import json

    name_map = {"robotox": "sentinel"}
    lookup = name_map.get(agent_name, agent_name)

    path_map = {
        "garves": Path.home() / "polymarket-bot" / "data" / "health.json",
        "shelby": Path.home() / "shelby" / "data" / "health.json",
        "atlas": Path.home() / "atlas" / "data" / "health.json",
        "lisa": Path.home() / "mercury" / "data" / "health.json",
        "sentinel": Path.home() / "sentinel" / "data" / "health.json",
        "thor": Path.home() / "thor" / "data" / "health.json",
        "soren": Path.home() / "soren-content" / "data" / "health.json",
    }

    path = path_map.get(lookup)
    if not path:
        return jsonify({"error": f"Unknown agent: {agent_name}"}), 404

    if path.exists():
        try:
            with open(path) as f:
                return jsonify(json.load(f))
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)[:200]})

    return jsonify({"status": "unknown", "message": "No health file found"})


VALID_LOG_AGENTS = {
    "garves", "shelby", "atlas", "lisa", "robotox", "thor", "hawk", "viper",
    "quant", "soren", "mercury", "sentinel", "dashboard",
}


@infra_bp.route("/api/agent-logs/<agent_name>")
def api_agent_logs(agent_name: str):
    """Get structured logs for an agent from the hub log files."""
    import json
    # Prevent path traversal — only allow known agent names
    if agent_name not in VALID_LOG_AGENTS:
        return jsonify({"agent": agent_name, "logs": [], "error": "Unknown agent"}), 404
    log_file = Path.home() / ".agent-hub" / "logs" / f"{agent_name}.jsonl"
    if not log_file.exists():
        return jsonify({"agent": agent_name, "logs": [], "message": "No logs yet"})

    try:
        lines = []
        with open(log_file, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            read_size = min(size, 16384)
            f.seek(max(0, size - read_size))
            data = f.read().decode("utf-8", errors="replace")

        for line in data.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                lines.append(json.loads(line))
            except json.JSONDecodeError:
                continue

        return jsonify({"agent": agent_name, "logs": lines[-50:]})
    except Exception as e:
        return jsonify({"agent": agent_name, "logs": [], "error": str(e)[:200]})




@infra_bp.route("/api/system-summary")
def api_system_summary():
    """Today's activity summary across all agents."""
    import json
    import time
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    from pathlib import Path

    ET = ZoneInfo("America/New_York")
    today = datetime.now(ET).strftime("%Y-%m-%d")
    now = time.time()
    day_start = datetime.now(ET).replace(hour=0, minute=0, second=0).timestamp()

    summary = {}

    # Garves trades today (check main file + today's archive)
    garves_today = {"trades": 0, "wins": 0, "pnl": 0.0, "real_trades": 0, "paper_trades": 0}
    garves_files = [
        Path.home() / "polymarket-bot" / "data" / "trades.jsonl",
        Path.home() / "polymarket-bot" / "data" / "archives" / f"trades_{today}.jsonl",
    ]
    for trades_file in garves_files:
        if trades_file.exists():
            try:
                with open(trades_file) as f:
                    for line in f:
                        if not line.strip():
                            continue
                        t = json.loads(line)
                        ts = t.get("timestamp", 0)
                        if ts > day_start:
                            garves_today["trades"] += 1
                            is_dry = t.get("dry_run", True)
                            if is_dry:
                                garves_today["paper_trades"] += 1
                            else:
                                garves_today["real_trades"] += 1
                            pnl = t.get("pnl", 0)
                            if pnl and pnl > 0:
                                garves_today["wins"] += 1
                            garves_today["pnl"] += pnl or 0
            except Exception:
                pass
    summary["garves"] = garves_today

    # Hawk trades today
    hawk_file = Path.home() / "polymarket-bot" / "data" / "hawk_trades.jsonl"
    hawk_today = {"trades": 0, "wins": 0, "pnl": 0.0, "real_trades": 0, "paper_trades": 0}
    if hawk_file.exists():
        try:
            with open(hawk_file) as f:
                for line in f:
                    if not line.strip():
                        continue
                    t = json.loads(line)
                    ts = t.get("timestamp", 0)
                    if ts > day_start:
                        hawk_today["trades"] += 1
                        oid = t.get("order_id", "")
                        is_dry = t.get("dry_run", "dry" in oid.lower())
                        if is_dry:
                            hawk_today["paper_trades"] += 1
                        else:
                            hawk_today["real_trades"] += 1
                        pnl = t.get("pnl", 0)
                        if pnl and pnl > 0:
                            hawk_today["wins"] += 1
                        hawk_today["pnl"] += pnl or 0
        except Exception:
            pass
    summary["hawk"] = hawk_today

    # Soren content today
    queue_file = Path.home() / "soren-content" / "data" / "content_queue.json"
    soren_today = {"generated": 0, "approved": 0, "posted": 0}
    if queue_file.exists():
        try:
            with open(queue_file) as f:
                items = json.load(f)
            for item in items:
                created = item.get("created_at", "")
                if today in created:
                    soren_today["generated"] += 1
                if item.get("status") == "approved":
                    soren_today["approved"] += 1
                if item.get("status") == "posted":
                    soren_today["posted"] += 1
        except Exception:
            pass
    summary["soren"] = soren_today

    # LLM costs today
    costs_file = Path.home() / "shared" / "llm_costs.jsonl"
    llm_today = {"calls": 0, "cost_usd": 0.0, "local_calls": 0, "cloud_calls": 0}
    if costs_file.exists():
        try:
            with open(costs_file) as f:
                for line in f:
                    if not line.strip():
                        continue
                    entry = json.loads(line)
                    ts = entry.get("timestamp", 0)
                    if ts > day_start:
                        llm_today["calls"] += 1
                        llm_today["cost_usd"] += entry.get("cost_usd", 0)
                        if entry.get("model", "").startswith("mlx"):
                            llm_today["local_calls"] += 1
                        else:
                            llm_today["cloud_calls"] += 1
        except Exception:
            pass
    llm_today["cost_usd"] = round(llm_today["cost_usd"], 4)
    summary["llm"] = llm_today

    # Event bus today
    events_file = Path.home() / "shared" / "events.jsonl"
    events_today = {"total": 0, "by_agent": {}}
    if events_file.exists():
        try:
            with open(events_file) as f:
                for line in f:
                    if not line.strip():
                        continue
                    ev = json.loads(line)
                    ts = ev.get("timestamp", 0)
                    if ts > day_start:
                        events_today["total"] += 1
                        agent = ev.get("agent", "unknown")
                        events_today["by_agent"][agent] = events_today["by_agent"].get(agent, 0) + 1
        except Exception:
            pass
    summary["events"] = events_today

    # Atlas cycles today
    atlas_status = Path.home() / "atlas" / "data" / "background_status.json"
    atlas_today = {"cycles": 0, "patterns": 0}
    if atlas_status.exists():
        try:
            with open(atlas_status) as f:
                data = json.load(f)
            atlas_today["cycles"] = data.get("cycles_completed", 0)
            atlas_today["patterns"] = data.get("patterns_mined", 0)
        except Exception:
            pass
    summary["atlas"] = atlas_today

    # Portfolio totals
    garves_bal = 88.81  # Default
    hawk_bal = 173.85
    try:
        gs = Path.home() / "polymarket-bot" / "data" / "garves_status.json"
        if gs.exists():
            with open(gs) as f:
                gd = json.load(f)
            garves_bal = gd.get("portfolio_value", garves_bal)
    except Exception:
        pass
    try:
        hs = Path.home() / "polymarket-bot" / "data" / "hawk_status.json"
        if hs.exists():
            with open(hs) as f:
                hd = json.load(f)
            hawk_bal = hd.get("effective_bankroll", hawk_bal)
    except Exception:
        pass

    summary["portfolio"] = {
        "garves_usd": round(garves_bal, 2),
        "hawk_usd": round(hawk_bal, 2),
        "total_usd": round(garves_bal + hawk_bal, 2),
    }

    return jsonify(summary)


# ── Shared Event Bus ──

@infra_bp.route("/api/events")
def api_events():
    """Query the shared event bus with optional filters."""
    try:
        # Path already added via bot.shared.ensure_path
        from shared.events import get_events

        since_id = request.args.get("since_id")
        agent = request.args.get("agent")
        event_type = request.args.get("type")
        severity = request.args.get("severity")
        limit = int(request.args.get("limit", 50))

        events = get_events(
            since_id=since_id,
            agent=agent,
            event_type=event_type,
            severity=severity,
            limit=min(limit, 200),
        )
        return jsonify({"events": events})
    except Exception as e:
        return jsonify({"events": [], "error": str(e)[:200]})


@infra_bp.route("/api/events/stats")
def api_events_stats():
    """Get event bus statistics."""
    try:
        # Path already added via bot.shared.ensure_path
        from shared.events import get_stats
        return jsonify(get_stats())
    except Exception as e:
        return jsonify({"total": 0, "error": str(e)[:200]})
