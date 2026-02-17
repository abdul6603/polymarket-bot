"""
Shelby Task Dispatcher — makes assigned tasks real.

When a task is created and assigned to an agent with a matching action,
it spawns a daemon thread that executes the action and updates the task
with results.

Supported agents: Robotox (health/bug/dep scans), Atlas (reports/improvements),
Thor (coding tasks via task queue).
"""
from __future__ import annotations

import json
import re
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path

# Shelby core for task updates
_SHELBY_DIR = Path("/Users/abdallaalhamdan/shelby")
if str(_SHELBY_DIR) not in sys.path:
    sys.path.insert(0, str(_SHELBY_DIR))


def _update_task(task_id: int, **fields):
    """Update a Shelby task by ID."""
    try:
        from core.tasks import update_task
        update_task(task_id, **fields)
    except Exception as e:
        print(f"[dispatcher] Failed to update task #{task_id}: {e}")


def _summarize(text: str, max_len: int = 500) -> str:
    """Truncate text to max_len with ellipsis."""
    if not text:
        return ""
    text = str(text).strip()
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


# ── Action matching ──

# Each entry: (keywords_regex, action_function_name)
_ROBOTOX_ACTIONS = [
    (re.compile(r"\b(scan|health|check|monitor)\b", re.I), "robotox_health_scan"),
    (re.compile(r"\b(bug|lint|code.?scan)\b", re.I), "robotox_bug_scan"),
    (re.compile(r"\b(dep|dependency|version)\b", re.I), "robotox_dep_check"),
]

_ATLAS_ACTIONS = [
    (re.compile(r"\b(research|report|analyze|analysis)\b", re.I), "atlas_full_report"),
    (re.compile(r"\b(improve|suggest|recommend)\b", re.I), "atlas_improvements"),
    (re.compile(r"\b(garves|trading.?analysis)\b", re.I), "atlas_garves_deep"),
    (re.compile(r"\b(soren|content.?analysis)\b", re.I), "atlas_soren_deep"),
]

_THOR_ACTIONS = [
    (re.compile(r"\b(fix|build|implement|refactor|code|create|update|add)\b", re.I), "thor_coding_task"),
]


def _match_action(agent: str, title: str) -> str | None:
    """Match a task title to an executable action for the given agent."""
    agent = (agent or "").lower()
    if agent == "robotox":
        for pattern, action in _ROBOTOX_ACTIONS:
            if pattern.search(title):
                return action
    elif agent == "atlas":
        for pattern, action in _ATLAS_ACTIONS:
            if pattern.search(title):
                return action
    elif agent == "thor":
        for pattern, action in _THOR_ACTIONS:
            if pattern.search(title):
                return action
    return None


# ── Action executors ──

def _exec_robotox_health_scan() -> str:
    """Run Robotox full health scan."""
    from sentinel.core.monitor import HealthMonitor
    monitor = HealthMonitor()
    result = monitor.scan_all(skip_notifications=True)
    if isinstance(result, dict):
        online = result.get("agents_online", 0)
        total = result.get("agents_total", 0)
        issues = result.get("issues", [])
        issue_count = len(issues) if isinstance(issues, list) else 0
        summary = f"Health scan complete. {online}/{total} agents online."
        if issue_count:
            summary += f" {issue_count} issue(s) found."
            for iss in issues[:3]:
                if isinstance(iss, dict):
                    summary += f"\n- {iss.get('agent', '?')}: {iss.get('message', '')[:80]}"
                elif isinstance(iss, str):
                    summary += f"\n- {iss[:80]}"
        else:
            summary += " No issues detected."
        return summary
    return _summarize(str(result))


def _exec_robotox_bug_scan() -> str:
    """Run Robotox quick bug scan."""
    from bot.routes.sentinel import _get_sentinel
    s = _get_sentinel()
    result = s.quick_bug_scan()
    if isinstance(result, dict):
        total = result.get("total_issues", result.get("total", 0))
        issues = result.get("issues", [])
        if not total and not issues:
            return "Bug scan complete. No issues found."
        summary = f"Bug scan found {total} issue(s)."
        for iss in (issues[:5] if isinstance(issues, list) else []):
            if isinstance(iss, dict):
                summary += f"\n- [{iss.get('severity', '?')}] {iss.get('file', '')}: {iss.get('message', '')[:60]}"
        return _summarize(summary)
    return _summarize(str(result))


def _exec_robotox_dep_check() -> str:
    """Run Robotox dependency health check."""
    from bot.routes.sentinel import _get_sentinel
    s = _get_sentinel()
    result = s.check_dep_health()
    if isinstance(result, dict):
        healthy = result.get("healthy", 0)
        total = result.get("total", 0)
        failed = result.get("failed", [])
        summary = f"Dependency check: {healthy}/{total} healthy."
        if failed:
            for f_item in failed[:3]:
                if isinstance(f_item, dict):
                    summary += f"\n- {f_item.get('name', '?')}: {f_item.get('error', '')[:60]}"
        return _summarize(summary)
    return _summarize(str(result))


def _exec_atlas_full_report() -> str:
    """Generate Atlas full report."""
    from bot.shared import get_atlas
    atlas = get_atlas()
    if not atlas:
        return "Atlas not available."
    result = atlas.api_full_report()
    if isinstance(result, dict):
        sections = []
        for key in ("summary", "garves_summary", "soren_summary", "recommendations"):
            val = result.get(key)
            if val and isinstance(val, str):
                sections.append(f"{key}: {val[:100]}")
            elif val and isinstance(val, list):
                sections.append(f"{key}: {len(val)} items")
        return _summarize("Full report generated.\n" + "\n".join(sections)) if sections else "Full report generated."
    return _summarize(str(result))


def _exec_atlas_improvements() -> str:
    """Generate Atlas improvement suggestions."""
    from bot.shared import get_atlas
    atlas = get_atlas()
    if not atlas:
        return "Atlas not available."
    result = atlas.api_improvements()
    if isinstance(result, dict):
        total = 0
        sections = []
        for key, val in result.items():
            if isinstance(val, list) and val:
                total += len(val)
                sections.append(f"{key}: {len(val)}")
        summary = f"Generated {total} improvement suggestion(s)."
        if sections:
            summary += " (" + ", ".join(sections) + ")"
        return _summarize(summary)
    return _summarize(str(result))


def _exec_atlas_garves_deep() -> str:
    """Atlas deep analysis of Garves."""
    from bot.shared import get_atlas
    atlas = get_atlas()
    if not atlas:
        return "Atlas not available."
    result = atlas.api_garves_deep()
    if isinstance(result, dict):
        wr = result.get("win_rate", result.get("overall_win_rate", "?"))
        total = result.get("total_trades", "?")
        return _summarize(f"Garves analysis: {total} trades, {wr}% win rate. " + str(result.get("summary", ""))[:200])
    return _summarize(str(result))


def _exec_atlas_soren_deep() -> str:
    """Atlas deep analysis of Soren."""
    from bot.shared import get_atlas
    atlas = get_atlas()
    if not atlas:
        return "Atlas not available."
    result = atlas.api_soren_deep()
    if isinstance(result, dict):
        return _summarize(f"Soren analysis complete. " + str(result.get("summary", ""))[:300])
    return _summarize(str(result))


def _exec_thor_coding_task(task_dict: dict) -> str:
    """Submit a coding task to Thor's queue."""
    from thor.core.task_queue import CodingTask, TaskQueue
    from thor.config import ThorConfig
    cfg = ThorConfig()
    queue = TaskQueue(cfg.tasks_dir, cfg.results_dir)

    title = task_dict.get("title", "Untitled task")
    task = CodingTask(
        title=title,
        description=f"Task from Shelby dispatch: {title}",
        target_files=[],
        context_files=[],
        agent="",
        priority="normal",
        assigned_by="shelby-dispatcher",
    )
    task_id = queue.submit(task)
    return f"Submitted to Thor as task {task_id}. Thor will pick it up shortly."


# ── Dispatch map ──

_ACTION_MAP = {
    "robotox_health_scan": _exec_robotox_health_scan,
    "robotox_bug_scan": _exec_robotox_bug_scan,
    "robotox_dep_check": _exec_robotox_dep_check,
    "atlas_full_report": _exec_atlas_full_report,
    "atlas_improvements": _exec_atlas_improvements,
    "atlas_garves_deep": _exec_atlas_garves_deep,
    "atlas_soren_deep": _exec_atlas_soren_deep,
    # Thor is handled specially (needs task_dict)
}


def _dispatch_thread(task_dict: dict, action: str):
    """Background thread that executes an action and updates the task."""
    task_id = task_dict.get("id")
    agent = (task_dict.get("agent") or "").lower()
    try:
        _update_task(task_id, status="in_progress", notes=f"Dispatching to {agent}...")

        if action == "thor_coding_task":
            result_text = _exec_thor_coding_task(task_dict)
            # Thor tasks stay in_progress — Thor updates its own results
            _update_task(task_id, status="in_progress", notes=result_text)
        else:
            executor = _ACTION_MAP.get(action)
            if not executor:
                _update_task(task_id, notes=f"Unknown action: {action}")
                return
            result_text = executor()
            _update_task(task_id, status="done", notes=result_text)

        print(f"[dispatcher] Task #{task_id} ({action}) completed: {result_text[:100]}")
    except Exception as e:
        error_msg = f"Dispatch failed: {str(e)[:300]}"
        print(f"[dispatcher] Task #{task_id} error: {error_msg}")
        traceback.print_exc()
        # On failure, keep pending and store the error
        _update_task(task_id, status="pending", notes=error_msg)


def dispatch_task(task_dict: dict) -> bool:
    """Attempt to dispatch a task. Returns True if dispatched, False if no match.

    Called right after a task is created or when manually re-dispatched.
    Non-blocking — spawns a daemon thread for the actual work.
    """
    agent = (task_dict.get("agent") or "").lower()
    title = task_dict.get("title") or ""

    # Only dispatch for agents that have executable actions
    if agent not in ("robotox", "atlas", "thor"):
        return False

    action = _match_action(agent, title)
    if not action:
        return False

    thread = threading.Thread(
        target=_dispatch_thread,
        args=(task_dict, action),
        daemon=True,
        name=f"dispatch-{task_dict.get('id', 0)}-{action}",
    )
    thread.start()
    return True
