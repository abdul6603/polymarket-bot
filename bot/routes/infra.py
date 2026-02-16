"""Infrastructure routes: heartbeats, service registry, broadcasts, system health."""
from __future__ import annotations

import sys
from pathlib import Path

from flask import Blueprint, jsonify

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


@infra_bp.route("/api/broadcasts")
def api_broadcasts():
    """Get recent broadcasts with acknowledgment status."""
    try:
        sys.path.insert(0, str(Path.home() / "shelby"))
        from core.broadcast import get_recent_broadcasts
        broadcasts = get_recent_broadcasts(limit=15)
        return jsonify({"broadcasts": broadcasts})
    except Exception as e:
        return jsonify({"broadcasts": [], "error": str(e)[:200]})


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


@infra_bp.route("/api/agent-logs/<agent_name>")
def api_agent_logs(agent_name: str):
    """Get structured logs for an agent from the hub log files."""
    import json
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
