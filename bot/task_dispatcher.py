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
    # Order matters — more specific patterns first
    (re.compile(r"\b(bugs?|lint|code.?scan)\b", re.I), "robotox_bug_scan"),
    (re.compile(r"\b(dep|dependency|version)\b", re.I), "robotox_dep_check"),
    (re.compile(r"\b(scan|health|check|monitor)\b", re.I), "robotox_health_scan"),
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


def _exec_robotox_bug_scan_detailed() -> tuple[str, list[dict]]:
    """Run Robotox detailed bug scan. Returns (summary, issues_list).

    Uses the scanner directly to get file paths, line numbers, and messages
    so chains can pass actionable details to Thor.
    """
    from sentinel.core.scanner import BugScanner
    scanner = BugScanner()

    all_issues = []
    agent_counts = {}
    from sentinel.core.scanner import SCAN_ROOTS
    for agent_id, root in SCAN_ROOTS.items():
        if not root.exists():
            continue
        count = 0
        for py_file in root.rglob("*.py"):
            parts = py_file.parts
            if any(skip in parts for skip in ("__pycache__", ".venv", "venv", ".git")):
                continue
            try:
                content = py_file.read_text(errors="replace")
                file_issues = scanner._scan_file(py_file, content)
                for iss in file_issues:
                    iss["agent"] = agent_id
                all_issues.extend(file_issues)
                count += len(file_issues)
            except Exception:
                pass
        agent_counts[agent_id] = count

    total = len(all_issues)
    if not total:
        return "Bug scan complete. No issues found.", []

    # Build summary
    summary = f"Bug scan found {total} issue(s) across {len(agent_counts)} project(s)."
    # Count by severity
    by_sev = {}
    for iss in all_issues:
        sev = iss.get("severity", "info")
        by_sev[sev] = by_sev.get(sev, 0) + 1
    if by_sev:
        summary += " " + ", ".join(f"{v} {k}" for k, v in sorted(by_sev.items()))
    # Show top issues
    fixable = [i for i in all_issues if i.get("severity") in ("critical", "warning")]
    for iss in fixable[:5]:
        f_short = str(iss.get("file", ""))
        # Shorten path for readability
        if "/Users/" in f_short:
            f_short = "~/" + f_short.split("/Users/abdallaalhamdan/", 1)[-1]
        summary += f"\n- [{iss['severity']}] {f_short}:{iss.get('line', '?')} — {iss.get('message', '')[:60]}"

    return _summarize(summary), all_issues


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


def _exec_thor_coding_task(task_dict: dict) -> tuple[str, str]:
    """Submit a coding task to Thor's queue. Returns (message, thor_task_id)."""
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
    thor_task_id = queue.submit(task)
    return f"Submitted to Thor as task {thor_task_id}. Thor will pick it up shortly.", thor_task_id


def _wait_for_thor_result(thor_task_id: str, shelby_task_id: int, timeout: int = 600):
    """Poll Thor's task file until it completes, then update the Shelby task.

    Polls every 5 seconds for up to `timeout` seconds (default 10 min).
    """
    import time
    from thor.config import ThorConfig
    cfg = ThorConfig()
    task_file = Path(cfg.tasks_dir) / f"{thor_task_id}.json"
    results_dir = Path(cfg.results_dir)

    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(5)
        try:
            if not task_file.exists():
                continue
            with open(task_file) as f:
                thor_task = json.load(f)
            status = thor_task.get("status", "")
            if status not in ("completed", "failed"):
                continue

            # Thor finished — read the result
            result_id = thor_task.get("result_id", "")
            if status == "failed":
                error = thor_task.get("error", "Unknown error")
                _update_task(shelby_task_id, status="done",
                             notes=f"Thor failed: {_summarize(error, 400)}")
                print(f"[dispatcher] Thor task {thor_task_id} failed for Shelby #{shelby_task_id}")
                return

            if result_id:
                result_file = results_dir / f"{result_id}.json"
                if result_file.exists():
                    with open(result_file) as f:
                        result = json.load(f)
                    summary = result.get("summary", "")
                    model = result.get("model_used", "")
                    files_written = result.get("files_written", {})
                    file_count = len(files_written)
                    notes = f"Thor completed ({model})."
                    if file_count:
                        notes += f" {file_count} file(s) modified."
                    if summary:
                        notes += f"\n{_summarize(summary, 400)}"
                    _update_task(shelby_task_id, status="done", notes=notes)
                    print(f"[dispatcher] Thor task {thor_task_id} done → Shelby #{shelby_task_id} updated")
                    return

            # Completed but no result file yet — mark done with basic info
            _update_task(shelby_task_id, status="done",
                         notes=f"Thor completed task {thor_task_id}.")
            return

        except Exception as e:
            print(f"[dispatcher] Error polling Thor {thor_task_id}: {e}")
            continue

    # Timeout
    _update_task(shelby_task_id, notes=f"Submitted to Thor as {thor_task_id}. Still running (timed out waiting for result).")


# ── Chains — automatic follow-up tasks ──

def _create_and_dispatch(title: str, agent: str, notes: str = None) -> dict | None:
    """Create a Shelby task and dispatch it. Returns the new task dict or None."""
    try:
        from core.tasks import add_task
        task = add_task(title=title, agent=agent, notes=notes, benefit=3)
        dispatched = dispatch_task(task)
        action = "dispatched" if dispatched else "created (no auto-dispatch match)"
        print(f"[chain] {action}: '{title}' → {agent} (task #{task['id']})")
        return task
    except Exception as e:
        print(f"[chain] Failed to create follow-up task: {e}")
        return None


def _chain_bug_scan(shelby_task_id: int, issues: list[dict]) -> dict | None:
    """After a bug scan, if fixable issues exist, create a Thor task to fix them."""
    fixable = [i for i in issues if i.get("severity") in ("critical", "warning")]
    if not fixable:
        return None

    # Build a detailed description for Thor
    target_files = []
    lines = []
    for iss in fixable[:15]:  # Cap at 15 to keep description reasonable
        f_path = str(iss.get("file", ""))
        if f_path and f_path not in target_files:
            target_files.append(f_path)
        f_short = f_path
        if "/Users/" in f_short:
            f_short = "~/" + f_short.split("/Users/abdallaalhamdan/", 1)[-1]
        lines.append(f"- [{iss['severity']}] {f_short}:{iss.get('line', '?')} — {iss.get('message', '')[:80]}")

    description = (
        f"Robotox bug scan found {len(fixable)} fixable issue(s). "
        f"Fix the critical and warning issues below:\n"
        + "\n".join(lines)
    )

    # Create Thor task with detailed info — submit directly to Thor's queue
    # so it gets the target_files for context
    try:
        from thor.core.task_queue import CodingTask, TaskQueue
        from thor.config import ThorConfig
        cfg = ThorConfig()
        queue = TaskQueue(cfg.tasks_dir, cfg.results_dir)

        thor_task = CodingTask(
            title=f"Fix {len(fixable)} bug(s) from Robotox scan",
            description=description,
            target_files=target_files[:10],
            context_files=[],
            agent="",
            priority="high" if any(i["severity"] == "critical" for i in fixable) else "normal",
            assigned_by="shelby-dispatcher-chain",
        )
        thor_task_id = queue.submit(thor_task)

        # Create the Shelby tracking task
        from core.tasks import add_task
        shelby_thor_task = add_task(
            title=f"Fix {len(fixable)} bug(s) from scan",
            agent="thor",
            notes=f"Chained from task #{shelby_task_id}. Submitted to Thor as {thor_task_id}.",
            benefit=3,
        )
        # Update it to in_progress and start the feedback loop
        _update_task(shelby_thor_task["id"], status="in_progress",
                     notes=f"Chained from task #{shelby_task_id}. Submitted to Thor as {thor_task_id}. Waiting for result...")

        # Also note the chain on the original scan task
        _update_task(shelby_task_id,
                     notes=_get_current_notes(shelby_task_id) + f"\n→ Chained: Thor task #{shelby_thor_task['id']} created to fix {len(fixable)} issue(s).")

        # Start feedback loop for the Thor task
        threading.Thread(
            target=_wait_for_thor_result,
            args=(thor_task_id, shelby_thor_task["id"]),
            daemon=True,
            name=f"chain-thor-{thor_task_id}",
        ).start()

        print(f"[chain] Bug scan #{shelby_task_id} → Thor task #{shelby_thor_task['id']} ({thor_task_id}), {len(fixable)} issues")
        return shelby_thor_task
    except Exception as e:
        print(f"[chain] Failed to create Thor follow-up: {e}")
        traceback.print_exc()
        return None


def _get_current_notes(task_id: int) -> str:
    """Read current notes from a task."""
    try:
        from core.tasks import get_task
        t = get_task(task_id)
        return (t.get("notes") or "") if t else ""
    except Exception:
        return ""


# ── Dispatch map ──

_ACTION_MAP = {
    "robotox_health_scan": _exec_robotox_health_scan,
    # robotox_bug_scan handled specially in _dispatch_thread (chain support)
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
            result_text, thor_task_id = _exec_thor_coding_task(task_dict)
            _update_task(task_id, status="in_progress", notes=result_text)
            # Poll until Thor finishes and update Shelby task to done
            _wait_for_thor_result(thor_task_id, task_id)
        elif action == "robotox_bug_scan":
            # Detailed bug scan with chain support
            result_text, issues = _exec_robotox_bug_scan_detailed()
            _update_task(task_id, status="done", notes=result_text)
            # Chain: if fixable issues found, auto-create Thor task
            fixable = [i for i in issues if i.get("severity") in ("critical", "warning")]
            if fixable:
                _chain_bug_scan(task_id, issues)
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
