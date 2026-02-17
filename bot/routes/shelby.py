"""Shelby (commander) routes: /api/shelby/*"""
from __future__ import annotations

import csv
import io
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from flask import Blueprint, Response, jsonify, request

# Add shelby to path for core.tasks imports
_SHELBY_DIR = Path("/Users/abdallaalhamdan/shelby")
if str(_SHELBY_DIR) not in sys.path:
    sys.path.insert(0, str(_SHELBY_DIR))

from bot.shared import (
    _load_trades,
    ET,
    DATA_DIR,
    SOREN_QUEUE_FILE,
    SOREN_ROOT,
    ATLAS_ROOT,
    MERCURY_ROOT,
    MERCURY_POSTING_LOG,
    SHELBY_ROOT_DIR,
    SHELBY_TASKS_FILE,
    SHELBY_PROFILE_FILE,
    SHELBY_CONVERSATION_FILE,
    SHELBY_SCHEDULER_LOG,
    SHELBY_ASSESSMENTS_FILE,
    SHELBY_AGENT_REGISTRY_FILE,
    _DEFAULT_ASSESSMENTS,
)

shelby_bp = Blueprint("shelby", __name__)


@shelby_bp.route("/api/shelby")
def api_shelby():
    """Shelby tasks, user profile, and status data."""
    # Running status
    shelby_running = False
    try:
        result = subprocess.run(["pgrep", "-f", "app.py"], capture_output=True, text=True)
        shelby_running = bool(result.stdout.strip())
    except Exception:
        pass

    # Tasks — load via core.tasks for migration + priority sorting
    try:
        from core.tasks import list_tasks as _list_tasks, prioritize_all, _load_tasks, _migrate_task
        all_tasks = _load_tasks()
        for t in all_tasks:
            _migrate_task(t)
        # Sort by priority desc
        all_tasks.sort(key=lambda t: t.get("priority", 0), reverse=True)
        tasks = all_tasks
    except Exception:
        tasks = []
        if SHELBY_TASKS_FILE.exists():
            try:
                with open(SHELBY_TASKS_FILE) as f:
                    tasks = json.load(f)
            except Exception:
                pass

    tasks_pending = sum(1 for t in tasks if t.get("status") == "pending")
    tasks_in_progress = sum(1 for t in tasks if t.get("status") == "in_progress")
    tasks_done = sum(1 for t in tasks if t.get("status") in ("done", "completed"))
    tasks_high_priority = sum(1 for t in tasks if t.get("priority", 0) > 70 and t.get("status") != "done")

    # User profile / preferences
    profile = {}
    if SHELBY_PROFILE_FILE.exists():
        try:
            with open(SHELBY_PROFILE_FILE) as f:
                profile = json.load(f)
        except Exception:
            pass

    # Conversation stats
    conversations = []
    if SHELBY_CONVERSATION_FILE.exists():
        try:
            with open(SHELBY_CONVERSATION_FILE) as f:
                conversations = json.load(f)
        except Exception:
            pass

    user_msgs = sum(1 for c in conversations if c.get("role") == "user")
    assistant_msgs = sum(1 for c in conversations if c.get("role") == "assistant")

    return jsonify({
        "running": shelby_running,
        "tasks": tasks[:50],
        "tasks_total": len(tasks),
        "tasks_pending": tasks_pending,
        "tasks_in_progress": tasks_in_progress,
        "tasks_done": tasks_done,
        "tasks_high_priority": tasks_high_priority,
        "profile": profile,
        "profile_keys": len(profile),
        "conversation_total": len(conversations),
        "user_messages": user_msgs,
        "assistant_messages": assistant_msgs,
    })


@shelby_bp.route("/api/shelby/tasks", methods=["POST"])
def api_shelby_tasks_create():
    """Create a new task with full V2 schema."""
    data = request.get_json()
    if not data or not data.get("title"):
        return jsonify({"error": "title is required"}), 400
    try:
        from core.tasks import add_task
        task = add_task(
            title=data["title"],
            due=data.get("due"),
            agent=data.get("agent"),
            category=data.get("category"),
            difficulty=int(data.get("difficulty", 2)),
            benefit=int(data.get("benefit", 2)),
            tags=data.get("tags"),
            notes=data.get("notes"),
        )
        # Auto-dispatch if the agent+title matches a known action
        dispatched = False
        try:
            from bot.task_dispatcher import dispatch_task
            dispatched = dispatch_task(task)
        except Exception:
            pass
        return jsonify({"success": True, "task": task, "dispatched": dispatched})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@shelby_bp.route("/api/shelby/tasks/<int:task_id>", methods=["PUT"])
def api_shelby_tasks_update(task_id):
    """Update task fields."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400
    try:
        from core.tasks import update_task
        fields = {}
        for key in ("title", "due", "agent", "category", "difficulty", "benefit", "tags", "notes", "status"):
            if key in data:
                val = data[key]
                if key in ("difficulty", "benefit") and val is not None:
                    val = int(val)
                fields[key] = val
        task = update_task(task_id, **fields)
        if not task:
            return jsonify({"error": f"Task #{task_id} not found"}), 404
        return jsonify({"success": True, "task": task})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@shelby_bp.route("/api/shelby/tasks/<int:task_id>", methods=["DELETE"])
def api_shelby_tasks_delete(task_id):
    """Delete a task."""
    try:
        from core.tasks import delete_task
        ok = delete_task(task_id)
        if not ok:
            return jsonify({"error": f"Task #{task_id} not found"}), 404
        return jsonify({"success": True, "deleted": task_id})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@shelby_bp.route("/api/shelby/tasks/<int:task_id>/dispatch", methods=["POST"])
def api_shelby_tasks_dispatch(task_id):
    """Manually dispatch (or re-dispatch) a task to its assigned agent."""
    try:
        from core.tasks import get_task
        task = get_task(task_id)
        if not task:
            return jsonify({"error": f"Task #{task_id} not found"}), 404
        if not task.get("agent"):
            return jsonify({"error": "Task has no assigned agent"}), 400
        from bot.task_dispatcher import dispatch_task
        dispatched = dispatch_task(task)
        if not dispatched:
            return jsonify({"error": "No matching action for this task/agent combination"}), 400
        return jsonify({"success": True, "dispatched": True, "task_id": task_id})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@shelby_bp.route("/api/shelby/schedule/dispatch", methods=["POST"])
def api_shelby_schedule_dispatch():
    """Manually trigger scheduled dispatch for a routine."""
    data = request.get_json() or {}
    routine = data.get("routine", "")
    valid = ("dispatch_morning", "dispatch_midday", "dispatch_evening", "dispatch_eod")
    if routine not in valid:
        return jsonify({"error": f"Invalid routine. Use one of: {', '.join(valid)}"}), 400
    try:
        from core.scheduler import ProactiveScheduler
        sched = ProactiveScheduler()
        result = sched._run_scheduled_dispatch(routine)
        return jsonify({"success": True, "routine": routine, "result": result})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@shelby_bp.route("/api/shelby/tasks/prioritize", methods=["POST"])
def api_shelby_tasks_prioritize():
    """Recalculate all task priorities."""
    try:
        from core.tasks import prioritize_all
        tasks = prioritize_all()
        return jsonify({"success": True, "total": len(tasks)})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@shelby_bp.route("/api/shelby/brief")
def api_shelby_brief():
    """Daily brief: aggregate activity from all agents + pending approvals."""
    now = datetime.now(ET)
    today_str = now.strftime("%A, %B %d, %Y")
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

    # Garves activity today
    trades = _load_trades()
    today_trades = [t for t in trades if t.get("timestamp", 0) >= today_start]
    today_resolved = [t for t in today_trades if t.get("resolved") and t.get("outcome") not in ("unknown", None)]
    today_wins = sum(1 for t in today_resolved if t.get("won"))
    today_losses = len(today_resolved) - today_wins
    today_wr = (today_wins / len(today_resolved) * 100) if today_resolved else 0
    today_pending = sum(1 for t in today_trades if not t.get("resolved"))
    # PnL today
    today_pnl = 0.0
    for t in today_resolved:
        implied = t.get("implied_up_price", 0.5)
        d = t.get("direction", "up")
        ep = implied if d == "up" else (1 - implied)
        stake = float(os.getenv("ORDER_SIZE_USD", "5.0"))
        if t.get("won"):
            today_pnl += stake * (1 - ep) - stake * 0.02
        else:
            today_pnl += -stake * ep

    garves_running = False
    try:
        result = subprocess.run(["pgrep", "-f", "bot.main"], capture_output=True, text=True)
        garves_running = bool(result.stdout.strip())
    except Exception:
        pass

    # Soren activity
    queue = []
    if SOREN_QUEUE_FILE.exists():
        try:
            with open(SOREN_QUEUE_FILE) as f:
                queue = json.load(f)
        except Exception:
            pass
    soren_pending = [q for q in queue if q.get("status") == "pending"]
    soren_posted_today = [q for q in queue if q.get("status") == "posted" and q.get("posted_at", "")[:10] == now.strftime("%Y-%m-%d")]
    soren_awaiting = [q for q in soren_pending if q.get("scheduled_time", "") <= now.isoformat()]

    # Shelby tasks
    tasks = []
    if SHELBY_TASKS_FILE.exists():
        try:
            with open(SHELBY_TASKS_FILE) as f:
                tasks = json.load(f)
        except Exception:
            pass
    active_tasks = [t for t in tasks if t.get("status") == "pending"]

    # Mercury / Lisa review stats
    mercury_brief = {"total_posts": 0}
    if MERCURY_POSTING_LOG.exists():
        try:
            with open(MERCURY_POSTING_LOG) as f:
                mlog = json.load(f)
            today_iso = now.strftime("%Y-%m-%d")
            today_posts = [p for p in mlog if p.get("posted_at", "")[:10] == today_iso]
            reviewed = [p for p in mlog if p.get("review_score") is not None and p.get("review_score", -1) != -1]
            mercury_brief = {
                "total_posts": len(mlog),
                "posted_today": len(today_posts),
                "reviews_total": len(reviewed),
                "review_avg": round(sum(p["review_score"] for p in reviewed) / len(reviewed), 1) if reviewed else None,
                "review_pass_rate": round(sum(1 for p in reviewed if p["review_score"] >= 7) / len(reviewed) * 100, 1) if reviewed else None,
            }
        except Exception:
            pass

    return jsonify({
        "date": today_str,
        "greeting": f"Good {'morning' if now.hour < 12 else 'afternoon' if now.hour < 17 else 'evening'}, sir.",
        "garves": {
            "running": garves_running,
            "trades_today": len(today_trades),
            "wins_today": today_wins,
            "losses_today": today_losses,
            "win_rate_today": round(today_wr, 1),
            "pnl_today": round(today_pnl, 2),
            "pending": today_pending,
        },
        "soren": {
            "queue_pending": len(soren_pending),
            "posted_today": len(soren_posted_today),
            "awaiting_approval": len(soren_awaiting),
            "awaiting_items": [{"id": q["id"], "title": q.get("title",""), "pillar": q.get("pillar",""), "platform": q.get("platform","")} for q in soren_awaiting[:10]],
        },
        "shelby": {
            "active_tasks": len(active_tasks),
            "tasks": [{"title": t.get("title",""), "due": t.get("due",""), "status": t.get("status","")} for t in active_tasks[:10]],
        },
        "mercury": mercury_brief,
        "approvals_needed": len(soren_awaiting),
    })


@shelby_bp.route("/api/shelby/schedule")
def api_shelby_schedule():
    """Shelby proactive scheduler status."""
    log_data = {}
    if SHELBY_SCHEDULER_LOG.exists():
        try:
            with open(SHELBY_SCHEDULER_LOG) as f:
                log_data = json.load(f)
        except Exception:
            pass

    # Handle both list format and dict format
    if isinstance(log_data, list):
        log_entries = log_data
    elif isinstance(log_data, dict):
        log_entries = log_data.get("today_log", [])
    else:
        log_entries = []

    now = datetime.now(ET)
    today_str = now.strftime("%Y-%m-%d")
    today_entries = [e for e in log_entries if isinstance(e, dict) and e.get("date", "")[:10] == today_str]

    schedule = {
        "07:00": {"name": "Morning Brief", "completed": False},
        "14:00": {"name": "Midday Content Review", "completed": False},
        "18:00": {"name": "Trading Report", "completed": False},
        "22:00": {"name": "End of Day Summary", "completed": False},
    }

    for entry in today_entries:
        time_key = entry.get("time_key", "") or entry.get("time", "")
        if time_key in schedule:
            schedule[time_key]["completed"] = True
            schedule[time_key]["result"] = entry.get("summary", "") or entry.get("result", "")
            schedule[time_key]["ran_at"] = entry.get("ran_at", "") or entry.get("executed_at", "")

    return jsonify({
        "schedule": schedule,
        "today_log": today_entries[-10:],
        "current_time": now.strftime("%H:%M"),
    })


@shelby_bp.route("/api/shelby/economics")
def api_shelby_economics():
    """Agent economics data."""
    period = request.args.get("period", "month")

    ledger = []
    ledger_file = SHELBY_ROOT_DIR / "data" / "agent_economics.jsonl"
    if ledger_file.exists():
        try:
            with open(ledger_file) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            ledger.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        except Exception:
            pass

    now = datetime.now(ET)
    if period == "today":
        cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    elif period == "week":
        cutoff = (now - timedelta(days=7)).isoformat()
    elif period == "month":
        cutoff = (now - timedelta(days=30)).isoformat()
    else:
        cutoff = "2000-01-01"

    filtered = [e for e in ledger if e.get("timestamp", "") >= cutoff]

    agents_data = {}
    for entry in filtered:
        agent = entry.get("agent", "unknown")
        if agent not in agents_data:
            agents_data[agent] = {"costs": 0, "revenue": 0, "transactions": 0}
        if entry.get("type") == "cost":
            agents_data[agent]["costs"] += entry.get("amount", 0)
        elif entry.get("type") == "revenue":
            agents_data[agent]["revenue"] += entry.get("amount", 0)
        agents_data[agent]["transactions"] += 1

    total_cost = sum(a["costs"] for a in agents_data.values())
    total_revenue = sum(a["revenue"] for a in agents_data.values())
    roi = ((total_revenue - total_cost) / total_cost * 100) if total_cost > 0 else 0

    return jsonify({
        "period": period,
        "agents": agents_data,
        "total_cost": round(total_cost, 2),
        "total_revenue": round(total_revenue, 2),
        "net": round(total_revenue - total_cost, 2),
        "roi_pct": round(roi, 1),
        "total_transactions": len(filtered),
    })


@shelby_bp.route("/api/shelby/assessments")
def api_shelby_assessments():
    """Shelby's opinion on each agent."""
    if SHELBY_ASSESSMENTS_FILE.exists():
        try:
            with open(SHELBY_ASSESSMENTS_FILE) as f:
                data = json.load(f)
            return jsonify(data)
        except Exception:
            pass
    # Create defaults
    SHELBY_ASSESSMENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(SHELBY_ASSESSMENTS_FILE, "w") as f:
            json.dump(_DEFAULT_ASSESSMENTS, f, indent=2)
    except Exception:
        pass
    return jsonify(_DEFAULT_ASSESSMENTS)


@shelby_bp.route("/api/shelby/hire", methods=["POST"])
def api_shelby_hire():
    """Create a new agent entry."""
    data = request.get_json()
    if not data or not data.get("name"):
        return jsonify({"error": "Agent name is required"}), 400

    name = data["name"].lower().strip()
    role = data.get("role", "General")
    description = data.get("description", "")

    registry = {}
    if SHELBY_AGENT_REGISTRY_FILE.exists():
        try:
            with open(SHELBY_AGENT_REGISTRY_FILE) as f:
                registry = json.load(f)
        except Exception:
            pass

    if name in registry:
        return jsonify({"error": "Agent already exists"}), 409

    agent_entry = {
        "name": name,
        "role": role,
        "description": description,
        "created_at": datetime.now(ET).isoformat(),
        "status": "inactive",
    }
    registry[name] = agent_entry

    SHELBY_AGENT_REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(SHELBY_AGENT_REGISTRY_FILE, "w") as f:
            json.dump(registry, f, indent=2)
    except Exception as e:
        return jsonify({"error": f"Failed to write registry: {e}"}), 500

    return jsonify({"success": True, "agent": agent_entry})


@shelby_bp.route("/api/shelby/activity-brief")
def api_shelby_activity_brief():
    """Last-30-min activity summary per agent."""
    now = time.time()
    cutoff = now - 1800  # 30 min

    # Garves: recent trades
    trades = _load_trades()
    recent_trades = [t for t in trades if t.get("timestamp", 0) >= cutoff]
    garves_wins = sum(1 for t in recent_trades if t.get("resolved") and t.get("won"))
    garves_losses = sum(1 for t in recent_trades if t.get("resolved") and not t.get("won") and t.get("outcome") != "unknown")

    # Soren: queue changes
    soren_pending = 0
    soren_generated = 0
    if SOREN_QUEUE_FILE.exists():
        try:
            with open(SOREN_QUEUE_FILE) as f:
                queue = json.load(f)
            soren_pending = sum(1 for q in queue if q.get("status") == "pending")
            soren_generated = sum(1 for q in queue if q.get("status") in ("approved", "generated"))
        except Exception:
            pass

    # Atlas: background state
    atlas_state = "idle"
    atlas_cycles = 0
    atlas_status_file = ATLAS_ROOT / "data" / "background_status.json"
    if atlas_status_file.exists():
        try:
            with open(atlas_status_file) as f:
                bg = json.load(f)
            atlas_state = bg.get("state", "idle")
            atlas_cycles = bg.get("cycles", 0)
        except Exception:
            pass

    # Mercury: recent posts + review stats
    mercury_recent = 0
    mercury_review_avg = None
    mercury_review_total = 0
    if MERCURY_POSTING_LOG.exists():
        try:
            with open(MERCURY_POSTING_LOG) as f:
                posts = json.load(f)
            cutoff_iso = datetime.fromtimestamp(cutoff, tz=ET).isoformat()
            mercury_recent = sum(1 for p in posts if p.get("posted_at", "") >= cutoff_iso)
            reviewed = [p for p in posts if p.get("review_score") is not None and p.get("review_score", -1) != -1]
            mercury_review_total = len(reviewed)
            if reviewed:
                mercury_review_avg = round(sum(p["review_score"] for p in reviewed) / len(reviewed), 1)
        except Exception:
            pass

    # Robotox: last scan info
    sentinel_info = "idle"
    try:
        from bot.routes.sentinel import _get_sentinel
        s = _get_sentinel()
        status = s.get_status()
        sentinel_info = "online" if status.get("agents_online", 0) > 0 else "idle"
    except Exception:
        pass

    return jsonify({
        "garves": {"trades_30m": len(recent_trades), "wins": garves_wins, "losses": garves_losses},
        "soren": {"pending": soren_pending, "generated": soren_generated},
        "atlas": {"state": atlas_state, "cycles": atlas_cycles},
        "mercury": {"posts_30m": mercury_recent, "review_avg": mercury_review_avg, "reviews_total": mercury_review_total},
        "sentinel": {"status": sentinel_info},
    })


@shelby_bp.route("/api/shelby/export")
def api_shelby_export():
    """Export agent task data + 24h metrics as CSV."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Agent", "Metric", "Value"])

    # Garves metrics
    trades = _load_trades()
    now_ts = time.time()
    day_ago = now_ts - 86400
    day_trades = [t for t in trades if t.get("timestamp", 0) >= day_ago]
    resolved_day = [t for t in day_trades if t.get("resolved") and t.get("outcome") not in ("unknown", None)]
    wins_day = sum(1 for t in resolved_day if t.get("won"))
    writer.writerow(["Garves", "Trades (24h)", len(day_trades)])
    writer.writerow(["Garves", "Wins (24h)", wins_day])
    writer.writerow(["Garves", "Losses (24h)", len(resolved_day) - wins_day])
    wr = (wins_day / len(resolved_day) * 100) if resolved_day else 0
    writer.writerow(["Garves", "Win Rate (24h)", str(round(wr, 1)) + "%"])

    # Soren metrics
    if SOREN_QUEUE_FILE.exists():
        try:
            with open(SOREN_QUEUE_FILE) as f:
                queue = json.load(f)
            writer.writerow(["Soren", "Queue Total", len(queue)])
            writer.writerow(["Soren", "Pending", sum(1 for q in queue if q.get("status") == "pending")])
            writer.writerow(["Soren", "Posted", sum(1 for q in queue if q.get("status") == "posted")])
        except Exception:
            pass

    # Atlas metrics
    atlas_status_file = ATLAS_ROOT / "data" / "background_status.json"
    if atlas_status_file.exists():
        try:
            with open(atlas_status_file) as f:
                bg = json.load(f)
            writer.writerow(["Atlas", "Cycles", bg.get("cycles", 0)])
            writer.writerow(["Atlas", "State", bg.get("state", "unknown")])
        except Exception:
            pass

    # Lisa metrics
    if MERCURY_POSTING_LOG.exists():
        try:
            with open(MERCURY_POSTING_LOG) as f:
                posts = json.load(f)
            writer.writerow(["Lisa", "Total Posts", len(posts)])
            reviewed = [p for p in posts if p.get("review_score") is not None and p.get("review_score", -1) != -1]
            if reviewed:
                scores = [p["review_score"] for p in reviewed]
                writer.writerow(["Lisa", "Reviews Total", len(reviewed)])
                writer.writerow(["Lisa", "Avg Review Score", str(round(sum(scores) / len(scores), 1))])
                writer.writerow(["Lisa", "Reviews Passed", sum(1 for s in scores if s >= 7)])
                writer.writerow(["Lisa", "Reviews Warned", sum(1 for s in scores if 4 <= s < 7)])
                writer.writerow(["Lisa", "Reviews Failed", sum(1 for s in scores if s < 4)])
        except Exception:
            pass

    # Shelby tasks
    if SHELBY_TASKS_FILE.exists():
        try:
            with open(SHELBY_TASKS_FILE) as f:
                tasks = json.load(f)
            writer.writerow(["Shelby", "Total Tasks", len(tasks)])
            writer.writerow(["Shelby", "Pending Tasks", sum(1 for t in tasks if t.get("status") == "pending")])
            writer.writerow(["Shelby", "Done Tasks", sum(1 for t in tasks if t.get("status") in ("done", "completed"))])
        except Exception:
            pass

    csv_content = output.getvalue()
    return Response(
        csv_content,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=agent_report.csv"}
    )


@shelby_bp.route("/api/shelby/system")
def api_shelby_system():
    """Mac system info + weather."""
    from bot.shared import _system_cache, _weather_cache, _updates_cache
    now = time.time()
    result = {}

    # CPU load
    try:
        result["load_avg"] = list(os.getloadavg())
    except Exception:
        result["load_avg"] = [0, 0, 0]

    # Memory
    try:
        import psutil
        mem = psutil.virtual_memory()
        result["memory"] = {"total_gb": round(mem.total / (1024**3), 1), "used_pct": mem.percent}
    except ImportError:
        try:
            vm = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5)
            result["memory"] = {"raw": vm.stdout[:200], "used_pct": -1}
        except Exception:
            result["memory"] = {"used_pct": -1}

    # Disk
    try:
        usage = shutil.disk_usage("/")
        result["disk"] = {
            "total_gb": round(usage.total / (1024**3), 1),
            "free_gb": round(usage.free / (1024**3), 1),
            "used_pct": round(usage.used / usage.total * 100, 1),
        }
    except Exception:
        result["disk"] = {"free_gb": -1, "used_pct": -1}

    # macOS updates (cached, max once per hour)
    if now - _updates_cache["ts"] > 3600:
        try:
            upd = subprocess.run(
                ["softwareupdate", "-l", "--no-scan"],
                capture_output=True, text=True, timeout=10,
            )
            lines = [l.strip() for l in upd.stdout.split("\n") if l.strip() and "Software Update" not in l]
            _updates_cache["data"] = lines[:5]
            _updates_cache["ts"] = now
        except Exception:
            _updates_cache["data"] = []
            _updates_cache["ts"] = now
    result["updates"] = _updates_cache["data"]

    # Weather (cached 30 min)
    if now - _weather_cache["ts"] > 1800:
        try:
            import urllib.request
            req_obj = urllib.request.Request(
                "https://wttr.in/Portsmouth+NH?format=j1",
                headers={"User-Agent": "curl/7.68.0"}
            )
            with urllib.request.urlopen(req_obj, timeout=8) as resp:
                weather_data = json.loads(resp.read().decode())
            current = weather_data.get("current_condition", [{}])[0]
            _weather_cache["data"] = {
                "temp_f": current.get("temp_F", "?"),
                "feels_like_f": current.get("FeelsLikeF", "?"),
                "desc": current.get("weatherDesc", [{}])[0].get("value", "?"),
                "humidity": current.get("humidity", "?"),
                "wind_mph": current.get("windspeedMiles", "?"),
            }
            _weather_cache["ts"] = now
        except Exception:
            _weather_cache["data"] = {"temp_f": "?", "desc": "unavailable"}
            _weather_cache["ts"] = now
    result["weather"] = _weather_cache["data"]

    return jsonify(result)


@shelby_bp.route("/api/shelby/decisions")
def api_shelby_decisions():
    """Shelby decision memory — recent decisions and stats."""
    try:
        sys.path.insert(0, str(SHELBY_ROOT_DIR))
        from core.decisions import DecisionMemory
        dm = DecisionMemory()
        tag = request.args.get("tag")
        q = request.args.get("q")
        limit = int(request.args.get("limit", "30"))
        if tag:
            decisions = dm.search_by_tag(tag)[:limit]
        elif q:
            decisions = dm.recall(q, limit=limit)
        else:
            decisions = dm.list_recent(n=limit)
        return jsonify({"decisions": decisions, "stats": dm.stats()})
    except Exception as e:
        return jsonify({"error": str(e)[:200]})
