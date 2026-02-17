"""Infrastructure routes: heartbeats, service registry, broadcasts, system health, event bus."""
from __future__ import annotations

import sys
from pathlib import Path

from flask import Blueprint, jsonify, request

# Add agent hub to path
sys.path.insert(0, str(Path.home() / ".agent-hub"))

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
        return jsonify({"error": str(e)[:200]})


@infra_bp.route("/api/health")
def api_health():
    """Aggregated health status for all agents."""
    from pathlib import Path
    import json

    health_paths = {
        "garves": Path.home() / "polymarket-bot" / "data" / "health.json",
        "shelby": Path.home() / "shelby" / "data" / "health.json",
        "atlas": Path.home() / "atlas" / "data" / "health.json",
        "lisa": Path.home() / "mercury" / "data" / "health.json",
        "robotox": Path.home() / "sentinel" / "data" / "health.json",
        "thor": Path.home() / "thor" / "data" / "health.json",
        "soren": Path.home() / "soren-content" / "data" / "health.json",
    }

    agents = {}
    for agent, path in health_paths.items():
        if path.exists():
            try:
                with open(path) as f:
                    agents[agent] = json.load(f)
            except Exception:
                agents[agent] = {"status": "error", "message": "Failed to read health file"}
        else:
            agents[agent] = {"status": "unknown", "message": "No health file"}

    healthy = sum(1 for a in agents.values() if a.get("status") == "healthy")
    degraded = sum(1 for a in agents.values() if a.get("status") == "degraded")
    errored = sum(1 for a in agents.values() if a.get("status") in ("error", "unknown"))

    overall = "healthy" if errored == 0 and degraded == 0 else "degraded" if errored == 0 else "unhealthy"

    return jsonify({
        "overall": overall,
        "healthy": healthy,
        "degraded": degraded,
        "error": errored,
        "agents": agents,
    })


@infra_bp.route("/api/health/<agent_name>")
def api_health_agent(agent_name: str):
    """Individual agent health status."""
    from pathlib import Path
    import json

    name_map = {"lisa": "mercury", "robotox": "sentinel"}
    lookup = name_map.get(agent_name, agent_name)

    path_map = {
        "garves": Path.home() / "polymarket-bot" / "data" / "health.json",
        "shelby": Path.home() / "shelby" / "data" / "health.json",
        "atlas": Path.home() / "atlas" / "data" / "health.json",
        "mercury": Path.home() / "mercury" / "data" / "health.json",
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


# ── Shared Event Bus ──

@infra_bp.route("/api/events")
def api_events():
    """Query the shared event bus with optional filters."""
    try:
        sys.path.insert(0, str(Path.home()))
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
        sys.path.insert(0, str(Path.home()))
        from shared.events import get_stats
        return jsonify(get_stats())
    except Exception as e:
        return jsonify({"total": 0, "error": str(e)[:200]})
