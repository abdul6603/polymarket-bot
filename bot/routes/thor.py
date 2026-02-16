"""Thor (coding lieutenant) routes: /api/thor/*"""
from __future__ import annotations

import json
from pathlib import Path

from flask import Blueprint, jsonify, request

thor_bp = Blueprint("thor", __name__)

THOR_DATA = Path.home() / "thor" / "data"


@thor_bp.route("/api/thor")
def api_thor():
    """Thor's full status."""
    try:
        # Read status file (written by Thor's reporter)
        status_file = THOR_DATA / "status.json"
        if status_file.exists():
            status = json.loads(status_file.read_text())
        else:
            status = {"state": "offline", "agent": "thor"}

        # Queue stats
        tasks_dir = THOR_DATA / "tasks"
        results_dir = THOR_DATA / "results"
        pending = completed = failed = in_progress = 0
        if tasks_dir.exists():
            for f in tasks_dir.glob("task_*.json"):
                try:
                    data = json.loads(f.read_text())
                    s = data.get("status", "")
                    if s == "pending":
                        pending += 1
                    elif s == "completed":
                        completed += 1
                    elif s == "failed":
                        failed += 1
                    elif s == "in_progress":
                        in_progress += 1
                except Exception:
                    pass

        status["queue"] = {
            "pending": pending,
            "in_progress": in_progress,
            "completed": completed,
            "failed": failed,
        }

        # Knowledge stats
        kb_index = THOR_DATA / "knowledge" / "index.json"
        if kb_index.exists():
            try:
                index = json.loads(kb_index.read_text())
                status["knowledge_entries"] = len(index)
            except Exception:
                status["knowledge_entries"] = 0
        else:
            status["knowledge_entries"] = 0

        # Brain stats from activity log
        activity_file = THOR_DATA / "activity.json"
        if activity_file.exists():
            try:
                activities = json.loads(activity_file.read_text())
                total_tokens = sum(a.get("tokens", 0) for a in activities)
                status["total_tokens"] = total_tokens
                status["total_activities"] = len(activities)
            except Exception:
                pass

        return jsonify(status)
    except Exception as e:
        return jsonify({"state": "offline", "error": str(e)})


@thor_bp.route("/api/thor/queue")
def api_thor_queue():
    """Thor's task queue."""
    try:
        tasks_dir = THOR_DATA / "tasks"
        tasks = []
        if tasks_dir.exists():
            for f in sorted(tasks_dir.glob("task_*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
                try:
                    data = json.loads(f.read_text())
                    tasks.append(data)
                except Exception:
                    pass
        return jsonify({"tasks": tasks[:50]})
    except Exception as e:
        return jsonify({"error": str(e)})


@thor_bp.route("/api/thor/results")
def api_thor_results():
    """Thor's recent results."""
    try:
        results_dir = THOR_DATA / "results"
        results = []
        if results_dir.exists():
            for f in sorted(results_dir.glob("result_*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
                try:
                    data = json.loads(f.read_text())
                    # Don't send full file contents over API
                    if "files_written" in data:
                        data["files_written"] = list(data["files_written"].keys())
                    results.append(data)
                except Exception:
                    pass
        return jsonify({"results": results[:20]})
    except Exception as e:
        return jsonify({"error": str(e)})


@thor_bp.route("/api/thor/activity")
def api_thor_activity():
    """Thor's recent activity log."""
    try:
        activity_file = THOR_DATA / "activity.json"
        if activity_file.exists():
            activities = json.loads(activity_file.read_text())
            return jsonify({"activities": activities[-30:]})
        return jsonify({"activities": []})
    except Exception as e:
        return jsonify({"error": str(e)})


QUICK_ACTIONS = {
    "optimize_garves": {
        "title": "Optimize Garves Signal Accuracy",
        "description": "Review and optimize signal generation in bot/signals.py and bot/indicators.py. "
                       "Analyze indicator weights, accuracy thresholds, and consensus logic. "
                       "Suggest improvements to increase win rate.",
        "target_files": ["bot/signals.py", "bot/indicators.py"],
        "agent": "garves",
        "priority": "high",
    },
    "fix_soren_pipeline": {
        "title": "Fix Soren Posting Pipeline",
        "description": "Debug and fix issues in Lisa's posting scheduler and Soren's content pipeline. "
                       "Check for stale queue items, failed posts, and scheduling gaps.",
        "target_files": ["mercury/core/scheduler.py", "soren-content/generate.py"],
        "agent": "soren",
        "priority": "normal",
    },
    "clear_shelby_tasks": {
        "title": "Clear Shelby Stale Tasks",
        "description": "Audit shelby/shelby.py task management. Remove stale/completed tasks, "
                       "fix any stuck tasks, and optimize the task queue.",
        "target_files": ["shelby/shelby.py", "shelby/data/tasks.json"],
        "agent": "shelby",
        "priority": "normal",
    },
    "improve_atlas_dedup": {
        "title": "Improve Atlas Research Deduplication",
        "description": "Enhance deduplication in atlas/researcher.py to prevent redundant research. "
                       "Improve URL tracking, content hashing, and result quality filtering.",
        "target_files": ["atlas/researcher.py"],
        "agent": "atlas",
        "priority": "normal",
    },
    "dashboard_scan": {
        "title": "Dashboard Bug Scan",
        "description": "Scan bot/live_dashboard.py and bot/static/dashboard.js for bugs, "
                       "broken API calls, missing error handling, and performance issues.",
        "target_files": ["bot/live_dashboard.py", "bot/static/dashboard.js"],
        "agent": "dashboard",
        "priority": "normal",
    },
    "system_audit": {
        "title": "Run Full System Health Audit",
        "description": "Comprehensive audit of all agents: check imports, file integrity, "
                       "config validity, API connectivity, and inter-agent communication. "
                       "Report on each agent's health status.",
        "target_files": [
            "bot/main.py", "bot/signals.py", "shelby/shelby.py",
            "atlas/brain.py", "mercury/core/brain.py", "sentinel/sentinel.py",
        ],
        "agent": "system",
        "priority": "high",
    },
}


@thor_bp.route("/api/thor/quick-action", methods=["POST"])
def api_thor_quick_action():
    """Submit a pre-built quick action task to Thor's queue."""
    try:
        data = request.get_json() or {}
        action = data.get("action", "")
        if action not in QUICK_ACTIONS:
            return jsonify({"error": f"Unknown action: {action}", "available": list(QUICK_ACTIONS.keys())}), 400

        template = QUICK_ACTIONS[action]

        from thor.core.task_queue import CodingTask, TaskQueue
        from thor.config import ThorConfig
        cfg = ThorConfig()
        queue = TaskQueue(cfg.tasks_dir, cfg.results_dir)

        task = CodingTask(
            title=template["title"],
            description=template["description"],
            target_files=template.get("target_files", []),
            context_files=template.get("context_files", []),
            agent=template.get("agent", ""),
            priority=template.get("priority", "normal"),
            assigned_by="dashboard-quick-action",
        )
        task_id = queue.submit(task)
        return jsonify({"task_id": task_id, "status": "submitted", "action": action, "title": template["title"]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@thor_bp.route("/api/thor/submit", methods=["POST"])
def api_thor_submit():
    """Submit a coding task to Thor via API."""
    try:
        data = request.get_json() or {}
        title = data.get("title", "")
        description = data.get("description", "")
        if not title or not description:
            return jsonify({"error": "title and description required"}), 400

        from thor.core.task_queue import CodingTask, TaskQueue
        from thor.config import ThorConfig
        cfg = ThorConfig()
        queue = TaskQueue(cfg.tasks_dir, cfg.results_dir)

        task = CodingTask(
            title=title,
            description=description,
            target_files=data.get("target_files", []),
            context_files=data.get("context_files", []),
            agent=data.get("agent", ""),
            priority=data.get("priority", "normal"),
            test_command=data.get("test_command", ""),
            assigned_by=data.get("assigned_by", "dashboard"),
        )
        task_id = queue.submit(task)
        return jsonify({"task_id": task_id, "status": "submitted"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
