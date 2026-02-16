"""Brain Management Routes — Store/delete knowledge in any agent's brain from the dashboard."""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

from flask import Blueprint, jsonify, request

brain_bp = Blueprint("brain", __name__)

ET = timezone(timedelta(hours=-5))

# Universal brain storage directory
BRAIN_DIR = Path(__file__).parent.parent / "data" / "brains"
BRAIN_DIR.mkdir(parents=True, exist_ok=True)

VALID_AGENTS = ["claude", "garves", "soren", "shelby", "atlas", "lisa", "robotox", "thor"]


def _brain_file(agent: str) -> Path:
    return BRAIN_DIR / f"{agent}.json"


def _load_brain(agent: str) -> dict:
    f = _brain_file(agent)
    if f.exists():
        try:
            return json.loads(f.read_text())
        except (json.JSONDecodeError, Exception):
            pass
    return {"agent": agent, "notes": []}


def _save_brain(agent: str, data: dict) -> None:
    _brain_file(agent).write_text(json.dumps(data, indent=2, default=str))


@brain_bp.route("/api/brain/<agent>")
def api_brain_list(agent: str):
    """List all brain entries for an agent."""
    if agent not in VALID_AGENTS:
        return jsonify({"error": f"Unknown agent: {agent}"}), 400
    data = _load_brain(agent)
    return jsonify({
        "agent": agent,
        "notes": data.get("notes", []),
        "count": len(data.get("notes", [])),
    })


@brain_bp.route("/api/brain/<agent>", methods=["POST"])
def api_brain_add(agent: str):
    """Add a note to an agent's brain."""
    if agent not in VALID_AGENTS:
        return jsonify({"error": f"Unknown agent: {agent}"}), 400

    body = request.get_json(silent=True) or {}
    topic = (body.get("topic") or "").strip()
    content = (body.get("content") or "").strip()
    tags = body.get("tags", [])

    if not topic or not content:
        return jsonify({"error": "Both topic and content are required"}), 400

    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]

    note = {
        "id": f"note_{uuid.uuid4().hex[:8]}",
        "topic": topic[:200],
        "content": content[:5000],
        "tags": tags[:10],
        "created_at": datetime.now(ET).isoformat(),
        "source": "dashboard",
    }

    data = _load_brain(agent)
    data["notes"].append(note)
    # Keep max 500 notes per agent
    data["notes"] = data["notes"][-500:]
    _save_brain(agent, data)

    return jsonify({"success": True, "note": note})


@brain_bp.route("/api/brain/<agent>/<note_id>", methods=["DELETE"])
def api_brain_delete(agent: str, note_id: str):
    """Delete a note from an agent's brain."""
    if agent not in VALID_AGENTS:
        return jsonify({"error": f"Unknown agent: {agent}"}), 400

    data = _load_brain(agent)
    before = len(data["notes"])
    data["notes"] = [n for n in data["notes"] if n.get("id") != note_id]

    if len(data["notes"]) == before:
        return jsonify({"error": "Note not found"}), 404

    _save_brain(agent, data)
    return jsonify({"success": True, "deleted": note_id})


@brain_bp.route("/api/brain/all")
def api_brain_all():
    """Get brain note counts for all agents."""
    counts = {}
    for agent in VALID_AGENTS:
        data = _load_brain(agent)
        counts[agent] = len(data.get("notes", []))
    return jsonify({"agents": counts})


# ── Command Registry ──
COMMANDS_FILE = Path.home() / "thor" / "data" / "brotherhood_commands.json"

# Agent name mapping (JSON uses title case, dashboard uses lowercase)
_AGENT_MAP = {
    "claude": "Claude", "garves": "Garves", "soren": "Soren",
    "shelby": "Shelby", "atlas": "Atlas", "lisa": "Lisa",
    "robotox": "Robotox", "thor": "Thor", "dashboard": "Dashboard",
}


def _load_commands() -> list[dict]:
    if COMMANDS_FILE.exists():
        try:
            return json.loads(COMMANDS_FILE.read_text())
        except Exception:
            pass
    return []


@brain_bp.route("/api/commands")
def api_commands_all():
    """All agent commands/tools/endpoints."""
    data = _load_commands()
    return jsonify({"agents": data, "total": sum(len(a.get("commands", [])) for a in data)})


@brain_bp.route("/api/commands/<agent>")
def api_commands_agent(agent: str):
    """Commands for a specific agent."""
    target = _AGENT_MAP.get(agent, agent.title())
    data = _load_commands()
    for a in data:
        if a.get("agent_name", "").lower() == agent or a.get("agent_name") == target:
            return jsonify(a)
    return jsonify({"agent_name": agent, "commands": [], "error": "Agent not found"})


# ══════════════════════════════════════════════
# ACTION MEMORY — Learning Loop for Smart Actions
# ══════════════════════════════════════════════
ACTION_HISTORY_FILE = BRAIN_DIR / "action_history.json"


def _load_action_history() -> dict:
    if ACTION_HISTORY_FILE.exists():
        try:
            return json.loads(ACTION_HISTORY_FILE.read_text())
        except Exception:
            pass
    return {"actions": [], "learnings": []}


def _save_action_history(data: dict) -> None:
    ACTION_HISTORY_FILE.write_text(json.dumps(data, indent=2, default=str))


def _hash_action(title: str) -> str:
    """Simple hash for deduplication."""
    return title.strip().lower()[:80]


@brain_bp.route("/api/actions/history")
def api_action_history():
    """Get action history with outcomes."""
    data = _load_action_history()
    agent = request.args.get("agent")
    actions = data.get("actions", [])
    if agent:
        actions = [a for a in actions if a.get("agent") == agent]
    return jsonify({
        "actions": actions[-50:],
        "learnings": data.get("learnings", [])[-20:],
        "total_actions": len(data.get("actions", [])),
        "total_learnings": len(data.get("learnings", [])),
    })


@brain_bp.route("/api/actions/accept", methods=["POST"])
def api_action_accept():
    """Record that an action was accepted/submitted to Thor."""
    body = request.get_json(silent=True) or {}
    action_id = body.get("action_id", "")
    title = body.get("title", "")
    agent = body.get("agent", "")
    source = body.get("source", "")
    description = body.get("description", "")

    if not title:
        return jsonify({"error": "title required"}), 400

    data = _load_action_history()
    entry = {
        "id": f"act_{uuid.uuid4().hex[:8]}",
        "action_id": action_id,
        "title": title[:200],
        "title_hash": _hash_action(title),
        "agent": agent,
        "source": source,
        "description": description[:500],
        "status": "accepted",
        "accepted_at": datetime.now(ET).isoformat(),
        "completed_at": None,
        "outcome": None,
        "outcome_score": None,
    }
    data["actions"].append(entry)
    data["actions"] = data["actions"][-500:]
    _save_action_history(data)
    return jsonify({"success": True, "entry": entry})


@brain_bp.route("/api/actions/complete", methods=["POST"])
def api_action_complete():
    """Record the outcome of a completed action."""
    body = request.get_json(silent=True) or {}
    action_entry_id = body.get("id", "")
    outcome = body.get("outcome", "")  # "success", "partial", "failed"
    outcome_notes = body.get("notes", "")
    score = body.get("score", 0)  # -1 to 1 (bad to good)

    data = _load_action_history()
    found = False
    for a in data["actions"]:
        if a.get("id") == action_entry_id:
            a["status"] = "completed"
            a["completed_at"] = datetime.now(ET).isoformat()
            a["outcome"] = outcome
            a["outcome_notes"] = outcome_notes[:500]
            a["outcome_score"] = score
            found = True

            # Auto-generate learning from outcome
            if outcome in ("success", "failed"):
                learning = {
                    "id": f"learn_{uuid.uuid4().hex[:8]}",
                    "from_action": a["title"],
                    "agent": a["agent"],
                    "source": a["source"],
                    "outcome": outcome,
                    "score": score,
                    "insight": f"Action '{a['title'][:60]}' for {a['agent']} "
                               f"{'worked well' if outcome == 'success' else 'did not help'}. "
                               f"{outcome_notes[:200]}",
                    "learned_at": datetime.now(ET).isoformat(),
                }
                data["learnings"].append(learning)
                data["learnings"] = data["learnings"][-200:]
            break

    if not found:
        return jsonify({"error": "Action not found"}), 404

    _save_action_history(data)
    return jsonify({"success": True})


@brain_bp.route("/api/actions/completed-hashes")
def api_action_completed_hashes():
    """Return hashes of all completed/accepted actions for dedup."""
    data = _load_action_history()
    hashes = set()
    for a in data.get("actions", []):
        if a.get("status") in ("accepted", "completed"):
            h = a.get("title_hash", "")
            if h:
                hashes.add(h)
    return jsonify({"hashes": list(hashes)})
