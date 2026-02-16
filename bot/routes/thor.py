"""Thor (coding lieutenant) routes: /api/thor/*"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from flask import Blueprint, jsonify, request

log = logging.getLogger(__name__)
thor_bp = Blueprint("thor", __name__)

THOR_DATA = Path.home() / "thor" / "data"
EXCEL_PATH = Path.home() / "Desktop" / "brotherhood_progress.xlsx"


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


ATLAS_DATA = Path.home() / "atlas" / "data"
SHELBY_DATA = Path.home() / "shelby" / "data"
SENTINEL_DATA = Path.home() / "sentinel" / "data"
GARVES_DATA = Path.home() / "polymarket-bot" / "data"
SOREN_DATA = Path.home() / "soren-content" / "data"
MERCURY_DATA = Path.home() / "mercury" / "data"

# Agent colors for smart action UI
AGENT_COLORS = {
    "garves": "#FFD700", "soren": "#9370DB", "shelby": "#4169E1",
    "atlas": "#32CD32", "lisa": "#FF69B4", "robotox": "#FF4500",
    "thor": "#00CED1", "dashboard": "#708090", "system": "#e0e0e0",
}

# Project file mappings
AGENT_FILE_MAP = {
    "garves": {"root": "polymarket-bot", "key_files": ["bot/signals.py", "bot/indicators.py", "bot/main.py"]},
    "soren": {"root": "soren-content", "key_files": ["generate.py", "writer.py", "scheduler.py"]},
    "shelby": {"root": "shelby", "key_files": ["shelby.py", "app.py", "core/tools.py"]},
    "atlas": {"root": "atlas", "key_files": ["brain.py", "researcher.py", "background.py"]},
    "lisa": {"root": "mercury", "key_files": ["mercury.py", "core/brain.py", "core/publisher.py"]},
    "robotox": {"root": "sentinel", "key_files": ["sentinel.py"]},
    "thor": {"root": "thor", "key_files": ["agent.py", "core/brain.py", "core/coder.py"]},
    "dashboard": {"root": "polymarket-bot", "key_files": ["bot/live_dashboard.py", "bot/static/dashboard.js", "bot/templates/dashboard.html"]},
}


def _load_completed_hashes() -> set:
    """Load hashes of already-accepted/completed actions to avoid re-suggesting."""
    try:
        history_file = Path(__file__).parent.parent / "data" / "brains" / "action_history.json"
        if history_file.exists():
            data = json.loads(history_file.read_text())
            return {a.get("title_hash", "") for a in data.get("actions", [])
                    if a.get("status") in ("accepted", "completed")}
    except Exception:
        pass
    return set()


def _load_action_learnings() -> list[dict]:
    """Load learnings from past action outcomes."""
    try:
        history_file = Path(__file__).parent.parent / "data" / "brains" / "action_history.json"
        if history_file.exists():
            data = json.loads(history_file.read_text())
            return data.get("learnings", [])
    except Exception:
        pass
    return []


def _generate_smart_actions(agent_filter: str = "") -> list[dict]:
    """Generate dynamic quick actions from live agent data.

    Args:
        agent_filter: If set, only return actions for this specific agent.
    """
    actions = []
    completed_hashes = _load_completed_hashes()

    # 1. Atlas improvements → actionable tasks
    try:
        imp_file = ATLAS_DATA / "improvements.json"
        if imp_file.exists():
            improvements = json.loads(imp_file.read_text())
            if isinstance(improvements, dict):
                for agent_name, items in improvements.items():
                    if not isinstance(items, list):
                        continue
                    for item in items[:2]:  # top 2 per agent
                        if not isinstance(item, dict):
                            continue
                        title = item.get("title", item.get("suggestion", ""))[:80]
                        if not title:
                            continue
                        desc = item.get("description", item.get("detail", title))
                        files = AGENT_FILE_MAP.get(agent_name, {}).get("key_files", [])
                        actions.append({
                            "id": f"atlas_{agent_name}_{hash(title) % 10000}",
                            "title": title,
                            "description": str(desc)[:500],
                            "agent": agent_name,
                            "source": "atlas",
                            "priority": item.get("priority", "normal"),
                            "target_files": files,
                            "color": AGENT_COLORS.get(agent_name, "#888"),
                        })
    except Exception:
        pass

    # 2. Robotox bug scan → fix actions
    try:
        scan_file = SENTINEL_DATA / "scan_results.json"
        if scan_file.exists():
            scan_data = json.loads(scan_file.read_text())
            bugs = scan_data if isinstance(scan_data, list) else scan_data.get("bugs", [])
            for bug in bugs[:3]:
                if not isinstance(bug, dict):
                    continue
                agent = bug.get("agent", "system")
                title = f"Fix: {bug.get('title', bug.get('description', 'Bug'))[:60]}"
                files = bug.get("files", AGENT_FILE_MAP.get(agent, {}).get("key_files", []))
                actions.append({
                    "id": f"bug_{hash(title) % 10000}",
                    "title": title,
                    "description": bug.get("description", title)[:500],
                    "agent": agent,
                    "source": "robotox",
                    "priority": "high",
                    "target_files": files if isinstance(files, list) else [],
                    "color": AGENT_COLORS.get(agent, "#FF4500"),
                })
    except Exception:
        pass

    # 3. Garves performance → optimization actions
    try:
        trades_file = GARVES_DATA / "trades.json"
        if trades_file.exists():
            trades = json.loads(trades_file.read_text())
            if isinstance(trades, list) and len(trades) >= 5:
                recent = trades[-20:]
                wins = sum(1 for t in recent if t.get("result") == "win")
                wr = wins / len(recent) * 100 if recent else 0
                if wr < 55:
                    actions.append({
                        "id": "garves_wr_low",
                        "title": f"Garves Win Rate Low ({wr:.0f}%) — Optimize Signals",
                        "description": f"Win rate dropped to {wr:.0f}% over last {len(recent)} trades. "
                                       "Review indicator weights, thresholds, and regime detection. "
                                       "Check if market conditions changed.",
                        "agent": "garves",
                        "source": "live_data",
                        "priority": "high",
                        "target_files": ["bot/signals.py", "bot/indicators.py", "bot/regime.py"],
                        "color": AGENT_COLORS["garves"],
                    })
    except Exception:
        pass

    # 4. Soren queue health → pipeline actions
    try:
        queue_file = Path.home() / "soren-content" / "data" / "content_queue.json"
        if queue_file.exists():
            queue = json.loads(queue_file.read_text())
            items = queue if isinstance(queue, list) else queue.get("items", [])
            pending = [i for i in items if i.get("status") == "pending"]
            failed = [i for i in items if i.get("status") == "failed"]
            if len(failed) > 2:
                actions.append({
                    "id": "soren_failed_posts",
                    "title": f"Soren: {len(failed)} Failed Posts — Fix Pipeline",
                    "description": f"{len(failed)} content items failed. Check generation errors, "
                                   "API connectivity, and retry failed items.",
                    "agent": "soren",
                    "source": "live_data",
                    "priority": "high",
                    "target_files": ["soren-content/generate.py", "mercury/core/publisher.py"],
                    "color": AGENT_COLORS["soren"],
                })
            if len(pending) < 3:
                actions.append({
                    "id": "soren_low_queue",
                    "title": f"Soren Queue Low ({len(pending)} pending) — Generate Content",
                    "description": "Content queue running low. Generate a fresh batch of content "
                                   "across all pillars to keep posting schedule on track.",
                    "agent": "soren",
                    "source": "live_data",
                    "priority": "normal",
                    "target_files": ["soren-content/generate.py", "soren-content/writer.py"],
                    "color": AGENT_COLORS["soren"],
                })
    except Exception:
        pass

    # 5. Atlas KB health → maintenance actions
    try:
        kb_file = ATLAS_DATA / "knowledge_base.json"
        if kb_file.exists():
            kb = json.loads(kb_file.read_text())
            obs_count = len(kb.get("observations", []))
            learnings = kb.get("learnings", [])
            unapplied = len([l for l in learnings if not l.get("applied")])
            if obs_count > 400:
                actions.append({
                    "id": "atlas_compress_kb",
                    "title": f"Atlas KB Bloated ({obs_count} obs) — Compress",
                    "description": f"Knowledge base has {obs_count} observations (max 500). "
                                   "Run auto-summarization to compress old observations into learnings.",
                    "agent": "atlas",
                    "source": "live_data",
                    "priority": "normal",
                    "target_files": ["atlas/brain.py"],
                    "color": AGENT_COLORS["atlas"],
                })
            if unapplied > 10:
                actions.append({
                    "id": "atlas_apply_learnings",
                    "title": f"Atlas: {unapplied} Unapplied Learnings — Review",
                    "description": f"{unapplied} validated learnings haven't been applied yet. "
                                   "Review and implement the most impactful ones.",
                    "agent": "atlas",
                    "source": "live_data",
                    "priority": "normal",
                    "target_files": ["atlas/brain.py", "atlas/improvements.py"],
                    "color": AGENT_COLORS["atlas"],
                })
    except Exception:
        pass

    # 6. Shelby assessments → low-scoring agent actions
    try:
        assess_file = SHELBY_DATA / "agent_assessments.json"
        if assess_file.exists():
            assessments = json.loads(assess_file.read_text())
            if isinstance(assessments, dict):
                for agent_name, data in assessments.items():
                    if not isinstance(data, dict):
                        continue
                    score = data.get("score", data.get("rating", 100))
                    if isinstance(score, (int, float)) and score < 60:
                        actions.append({
                            "id": f"low_score_{agent_name}",
                            "title": f"{agent_name.title()} Score Low ({score}) — Investigate",
                            "description": f"Shelby rates {agent_name.title()} at {score}/100. "
                                           f"Reason: {data.get('reason', data.get('notes', 'underperforming'))}. "
                                           "Investigate and fix root cause.",
                            "agent": agent_name,
                            "source": "shelby",
                            "priority": "high",
                            "target_files": AGENT_FILE_MAP.get(agent_name, {}).get("key_files", []),
                            "color": AGENT_COLORS.get(agent_name, "#888"),
                        })
    except Exception:
        pass

    # 7. Dependency checker → security actions
    try:
        dep_file = SENTINEL_DATA / "dep_report.json"
        if dep_file.exists():
            dep_data = json.loads(dep_file.read_text())
            cves = dep_data.get("vulnerabilities", dep_data.get("cves", []))
            if cves and len(cves) > 0:
                actions.append({
                    "id": "dep_cves",
                    "title": f"{len(cves)} Security Vulnerabilities Found — Patch",
                    "description": "Robotox found vulnerable dependencies. "
                                   "Update affected packages and verify no breaking changes.",
                    "agent": "system",
                    "source": "robotox",
                    "priority": "critical",
                    "target_files": [],
                    "color": "#FF0000",
                })
    except Exception:
        pass

    # 8. Thor failed tasks → retry actions
    try:
        tasks_dir = THOR_DATA / "tasks"
        if tasks_dir.exists():
            failed_tasks = []
            for f in tasks_dir.glob("task_*.json"):
                try:
                    td = json.loads(f.read_text())
                    if td.get("status") == "failed":
                        failed_tasks.append(td)
                except Exception:
                    pass
            if failed_tasks:
                latest = sorted(failed_tasks, key=lambda x: x.get("created_at", 0), reverse=True)[0]
                actions.append({
                    "id": f"retry_{latest.get('id', 'unknown')[:8]}",
                    "title": f"Retry Failed: {latest.get('title', 'Task')[:50]}",
                    "description": f"Task failed: {latest.get('error', 'unknown error')[:200]}. "
                                   "Retry with improved context.",
                    "agent": latest.get("agent", "system"),
                    "source": "thor",
                    "priority": "normal",
                    "target_files": latest.get("target_files", []),
                    "color": AGENT_COLORS["thor"],
                })
    except Exception:
        pass

    # Filter out already-completed/accepted actions
    filtered = []
    for a in actions:
        title_hash = a.get("title", "").strip().lower()[:80]
        if title_hash not in completed_hashes:
            filtered.append(a)
    actions = filtered

    # Filter by agent if requested
    if agent_filter:
        actions = [a for a in actions if a.get("agent") == agent_filter]

    # Sort by priority (critical > high > normal > low)
    priority_order = {"critical": 0, "high": 1, "normal": 2, "low": 3}
    actions.sort(key=lambda a: priority_order.get(a.get("priority", "normal"), 2))

    # Deduplicate by id
    seen = set()
    unique = []
    for a in actions:
        if a["id"] not in seen:
            seen.add(a["id"])
            unique.append(a)

    return unique[:12]


@thor_bp.route("/api/thor/smart-actions")
def api_thor_smart_actions():
    """Generate dynamic quick actions from live agent data."""
    try:
        agent = request.args.get("agent", "")
        actions = _generate_smart_actions(agent_filter=agent)
        learnings = _load_action_learnings()[-5:]
        return jsonify({
            "actions": actions,
            "count": len(actions),
            "generated_at": time.time(),
            "recent_learnings": learnings,
        })
    except Exception as e:
        return jsonify({"actions": [], "error": str(e)})


@thor_bp.route("/api/thor/quick-action", methods=["POST"])
def api_thor_quick_action():
    """Submit a smart action or custom action as a Thor task."""
    try:
        data = request.get_json() or {}

        # Accept either a pre-built action object or action fields directly
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
            assigned_by=f"dashboard-smart-action:{data.get('source', 'manual')}",
        )
        task_id = queue.submit(task)
        return jsonify({"task_id": task_id, "status": "submitted", "title": title})
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


@thor_bp.route("/api/thor/update-sheet", methods=["POST"])
def api_thor_update_sheet():
    """Scan recent Thor results and backfill any missing entries into the Excel progress sheet."""
    try:
        import openpyxl
        import shutil
        import subprocess

        # Use data dir copy to avoid macOS TCC Desktop restrictions
        data_copy = THOR_DATA / "brotherhood_progress.xlsx"

        if not data_copy.exists():
            return jsonify({"error": "Excel sheet not found — run: cp ~/Desktop/brotherhood_progress.xlsx ~/thor/data/"}), 404

        wb = openpyxl.load_workbook(str(data_copy))
        ws = wb.active

        # Collect existing feature names to avoid duplicates
        existing = set()
        for r in range(2, ws.max_row + 1):
            feat = ws.cell(r, 4).value or ""
            existing.add(feat.strip())

        # Scan Thor completed results
        results_dir = THOR_DATA / "results"
        tasks_dir = THOR_DATA / "tasks"
        added = 0

        for rfile in sorted(results_dir.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True):
            try:
                result = json.loads(rfile.read_text())
                task_id = result.get("task_id", "")
                task_files = list(tasks_dir.glob(f"{task_id}*.json"))
                if not task_files:
                    continue
                task = json.loads(task_files[0].read_text())
                if task.get("status") != "completed":
                    continue

                title = task.get("title", "")[:100]
                if title.strip() in existing:
                    continue

                agent = (task.get("agent") or "Thor").title()
                desc = (result.get("summary") or task.get("description", ""))[:300]
                files_written = result.get("files_written", {})
                files_str = ", ".join(list(files_written.keys())[:5]) if isinstance(files_written, dict) else str(files_written)[:200]
                created = task.get("created_at", time.time())
                from datetime import datetime
                date_str = datetime.fromtimestamp(created).strftime("%Y-%m-%d")

                ws.append([date_str, agent, "Improvement", title, desc[:300], files_str[:200], "Complete"])
                existing.add(title.strip())
                added += 1
            except Exception:
                continue

        wb.save(str(data_copy))

        # Copy back to Desktop via subprocess (bypasses TCC for child process)
        try:
            subprocess.run(["cp", str(data_copy), str(EXCEL_PATH)], timeout=5)
        except Exception:
            pass  # Data dir copy is still updated

        return jsonify({"status": "ok", "added": added, "total_rows": ws.max_row})
    except Exception as e:
        log.exception("Failed to update sheet")
        return jsonify({"error": str(e)}), 500


@thor_bp.route("/api/thor/update-dashboard", methods=["POST"])
def api_thor_update_dashboard():
    """Submit a task to Thor to update the dashboard with latest agent changes."""
    try:
        from thor.core.task_queue import CodingTask, TaskQueue
        from thor.config import ThorConfig
        cfg = ThorConfig()
        queue = TaskQueue(cfg.tasks_dir, cfg.results_dir)

        task = CodingTask(
            title="Update Command Center Dashboard — Sync Latest Changes",
            description=(
                "Review all recent changes across agents and update the Command Center dashboard "
                "(~/polymarket-bot/bot/templates/dashboard.html, bot/static/dashboard.js, bot/routes/) "
                "to reflect new features, endpoints, status displays, or UI sections. "
                "Check each agent's latest capabilities and ensure the dashboard accurately shows them. "
                "Only add/update what's actually missing — don't rewrite existing working sections."
            ),
            target_files=[
                str(Path.home() / "polymarket-bot/bot/templates/dashboard.html"),
                str(Path.home() / "polymarket-bot/bot/static/dashboard.js"),
            ],
            context_files=[
                str(Path.home() / "polymarket-bot/bot/routes/__init__.py"),
            ],
            agent="dashboard",
            priority="high",
            assigned_by="jordan_dashboard_button",
        )
        task_id = queue.submit(task)
        return jsonify({"task_id": task_id, "status": "submitted", "message": "Thor will update the dashboard"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
