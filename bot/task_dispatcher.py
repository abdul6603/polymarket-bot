"""
Shelby Task Dispatcher — makes assigned tasks real.

When a task is created and assigned to an agent with a matching action,
it spawns a daemon thread that executes the action and updates the task
with results.

Supported agents: Robotox, Atlas, Thor, Hawk, Viper.

Features:
  - Keyword matching: title keywords → agent action
  - Auto-routing: no @agent? Keywords pick the right agent automatically
  - AI fallback: if keywords don't match, GPT-4o-mini classifies the intent
"""
from __future__ import annotations

import json
import os
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
    (re.compile(r"\b(research|report|analyze|analysis|full.?report)\b", re.I), "atlas_full_report"),
    (re.compile(r"\b(improve|suggest|recommend)\b", re.I), "atlas_improvements"),
    (re.compile(r"\b(garves|trading.?analysis)\b", re.I), "atlas_garves_deep"),
    (re.compile(r"\b(soren|content.?analysis)\b", re.I), "atlas_soren_deep"),
]

_THOR_ACTIONS = [
    (re.compile(r"\b(fix|build|implement|refactor|code|create|update|add)\b", re.I), "thor_coding_task"),
]

_HAWK_ACTIONS = [
    (re.compile(r"\b(resolve|settle|check.?trade)\b", re.I), "hawk_resolve"),
    (re.compile(r"\b(scan|market|opportunit|find|predict|polymarket)\b", re.I), "hawk_scan"),
]

_VIPER_ACTIONS = [
    (re.compile(r"\b(cost|audit|expense|spend|waste|budget)\b", re.I), "viper_cost_audit"),
    (re.compile(r"\b(monetiz|revenue|soren.?metric|growth|cpm|brand)\b", re.I), "viper_soren_metrics"),
    (re.compile(r"\b(intel|news|gig|freelance|upwork|reddit|scan)\b", re.I), "viper_intel_scan"),
]

# Map of agent → action list (for auto-routing)
_AGENT_ACTIONS = {
    "robotox": _ROBOTOX_ACTIONS,
    "atlas": _ATLAS_ACTIONS,
    "thor": _THOR_ACTIONS,
    "hawk": _HAWK_ACTIONS,
    "viper": _VIPER_ACTIONS,
}

# Agents that support dispatch
_DISPATCHABLE_AGENTS = set(_AGENT_ACTIONS.keys())


def _match_action(agent: str, title: str) -> str | None:
    """Match a task title to an executable action for the given agent."""
    agent = (agent or "").lower()
    actions = _AGENT_ACTIONS.get(agent)
    if not actions:
        return None
    for pattern, action in actions:
        if pattern.search(title):
            return action
    return None


def _auto_route(title: str) -> tuple[str, str] | None:
    """Try all agents' keyword patterns to find the best match.

    Returns (agent, action) or None if no match.
    Priority order: robotox, atlas, hawk, viper, thor (thor last since its
    keywords are very generic — "fix", "add", "update" match too broadly).
    """
    priority_order = ["robotox", "atlas", "hawk", "viper", "thor"]
    for agent in priority_order:
        actions = _AGENT_ACTIONS[agent]
        for pattern, action in actions:
            if pattern.search(title):
                return (agent, action)
    return None


def _ai_route(title: str) -> tuple[str, str] | None:
    """Use GPT-4o-mini to classify the task into an agent + action.

    Cheap fallback when keyword matching fails. Costs ~$0.0001 per call.
    Returns (agent, action) or None.
    """
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        # Try loading from .env files
        for env_path in [
            Path.home() / "polymarket-bot" / ".env",
            Path.home() / "soren-content" / ".env",
        ]:
            if env_path.exists():
                for line in env_path.read_text().splitlines():
                    if line.startswith("OPENAI_API_KEY="):
                        api_key = line.split("=", 1)[1].strip().strip("'\"")
                        break
            if api_key:
                break
    if not api_key:
        return None

    prompt = f"""Classify this task into one agent and action. Reply with ONLY "agent:action" (no explanation).

Task: "{title}"

Agents and their actions:
- robotox:health_scan — system health check, monitoring, uptime
- robotox:bug_scan — code scanning, linting, finding bugs
- robotox:dep_check — dependency audit, version checks
- atlas:full_report — research, generate reports, analysis
- atlas:improvements — suggest improvements, recommendations
- atlas:garves_deep — analyze Garves/trading performance
- atlas:soren_deep — analyze Soren/content performance
- hawk:scan — scan prediction markets, find opportunities
- hawk:resolve — resolve/settle paper trades
- viper:cost_audit — API cost audit, expense tracking, waste detection
- viper:soren_metrics — Soren monetization, revenue, growth metrics
- viper:intel_scan — intelligence scan, news, freelance gigs
- thor:coding_task — fix code, build features, implement, refactor
- none:none — doesn't match any agent (just a tracking item)

Reply format: agent:action"""

    try:
        import httpx
        resp = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 20,
                "temperature": 0,
            },
            timeout=10.0,
        )
        if resp.status_code != 200:
            print(f"[dispatcher] AI route failed: HTTP {resp.status_code}")
            return None

        reply = resp.json()["choices"][0]["message"]["content"].strip().lower()
        print(f"[dispatcher] AI route for '{title}': {reply}")

        if ":" not in reply or reply == "none:none":
            return None

        agent, action_key = reply.split(":", 1)
        agent = agent.strip()
        action_key = action_key.strip()

        # Map the AI response to our internal action names
        _AI_ACTION_MAP = {
            ("robotox", "health_scan"): "robotox_health_scan",
            ("robotox", "bug_scan"): "robotox_bug_scan",
            ("robotox", "dep_check"): "robotox_dep_check",
            ("atlas", "full_report"): "atlas_full_report",
            ("atlas", "improvements"): "atlas_improvements",
            ("atlas", "garves_deep"): "atlas_garves_deep",
            ("atlas", "soren_deep"): "atlas_soren_deep",
            ("hawk", "scan"): "hawk_scan",
            ("hawk", "resolve"): "hawk_resolve",
            ("viper", "cost_audit"): "viper_cost_audit",
            ("viper", "soren_metrics"): "viper_soren_metrics",
            ("viper", "intel_scan"): "viper_intel_scan",
            ("thor", "coding_task"): "thor_coding_task",
        }
        action = _AI_ACTION_MAP.get((agent, action_key))
        if action and agent in _DISPATCHABLE_AGENTS:
            return (agent, action)
        return None

    except Exception as e:
        print(f"[dispatcher] AI route error: {e}")
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
    """Run Robotox detailed bug scan. Returns (summary, issues_list)."""
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

    summary = f"Bug scan found {total} issue(s) across {len(agent_counts)} project(s)."
    by_sev = {}
    for iss in all_issues:
        sev = iss.get("severity", "info")
        by_sev[sev] = by_sev.get(sev, 0) + 1
    if by_sev:
        summary += " " + ", ".join(f"{v} {k}" for k, v in sorted(by_sev.items()))
    fixable = [i for i in all_issues if i.get("severity") in ("critical", "warning")]
    for iss in fixable[:5]:
        f_short = str(iss.get("file", ""))
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


# ── Hawk executors ──

def _exec_hawk_scan() -> str:
    """Run full Hawk market scan + GPT-4o analysis + edge ranking."""
    from hawk.config import HawkConfig
    from hawk.scanner import scan_all_markets

    cfg = HawkConfig()
    markets = scan_all_markets(cfg)
    if not markets:
        return "Hawk scan: No eligible markets found on Polymarket."

    # Filter contested markets (12-88% YES price)
    contested = []
    for m in markets:
        yes_price = 0.5
        for t in m.tokens:
            if (t.get("outcome") or "").lower() in ("yes", "up"):
                try:
                    yes_price = float(t.get("price", 0.5))
                except (ValueError, TypeError):
                    pass
                break
        if 0.12 <= yes_price <= 0.88:
            contested.append(m)

    if not contested:
        return f"Hawk scanned {len(markets)} markets. None in contested range (12-88%)."

    contested.sort(key=lambda m: m.volume, reverse=True)
    target = contested[5:35] if len(contested) > 35 else contested

    # Analyze with GPT-4o
    try:
        from hawk.analyst import batch_analyze
        from hawk.edge import calculate_edge, rank_opportunities

        estimates = batch_analyze(cfg, target, max_concurrent=5)

        opportunities = []
        estimate_map = {e.market_id: e for e in estimates}
        for market in target:
            est = estimate_map.get(market.condition_id)
            if est:
                opp = calculate_edge(market, est, cfg)
                if opp:
                    opportunities.append(opp)

        ranked = rank_opportunities(opportunities)

        # Categorize
        categories = {}
        for m in markets:
            categories[m.category] = categories.get(m.category, 0) + 1

        summary = (
            f"Hawk scanned {len(markets)} markets ({len(contested)} contested). "
            f"Analyzed {len(target)} with GPT-4o. "
            f"Found {len(ranked)} opportunity(ies) with edge."
        )
        if categories:
            summary += "\nCategories: " + ", ".join(f"{k}: {v}" for k, v in sorted(categories.items(), key=lambda x: -x[1])[:5])
        for opp in ranked[:3]:
            q = opp.market.question[:60]
            edge_val = opp.edge * 100 if opp.edge < 1 else opp.edge
            summary += f"\n- {q} (edge: {edge_val:.1f}%)"

        # Save opportunities
        try:
            from hawk.briefing import generate_briefing
            opp_data = []
            for o in ranked:
                yes_price = 0.5
                for t in o.market.tokens:
                    if (t.get("outcome") or "").lower() in ("yes", "up"):
                        try:
                            yes_price = float(t.get("price", 0.5))
                        except (ValueError, TypeError):
                            pass
                est_prob = o.estimate.estimated_prob if hasattr(o.estimate, "estimated_prob") else 0.5
                edge_val = o.edge * 100 if o.edge < 1 else o.edge
                opp_data.append({
                    "question": o.market.question[:200],
                    "category": o.market.category,
                    "market_price": yes_price,
                    "estimated_prob": est_prob,
                    "edge_pct": edge_val,
                    "direction": o.direction,
                    "condition_id": o.market.condition_id,
                })
            opp_file = Path.home() / "polymarket-bot" / "data" / "hawk_opportunities.json"
            opp_file.write_text(json.dumps({
                "opportunities": opp_data,
                "scan_time": datetime.now().isoformat(),
                "markets_scanned": len(markets),
                "contested": len(contested),
                "analyzed": len(target),
            }, indent=2))
            generate_briefing(opp_data)
        except Exception as e:
            print(f"[dispatcher] Hawk save error: {e}")

        return _summarize(summary)

    except Exception as e:
        # Scan worked but analysis failed — still report scan results
        categories = {}
        for m in markets:
            categories[m.category] = categories.get(m.category, 0) + 1
        return _summarize(
            f"Hawk scanned {len(markets)} markets ({len(contested)} contested). "
            f"GPT-4o analysis failed: {str(e)[:100]}. "
            f"Categories: {', '.join(f'{k}: {v}' for k, v in sorted(categories.items(), key=lambda x: -x[1])[:5])}"
        )


def _exec_hawk_resolve() -> str:
    """Resolve Hawk paper trades."""
    try:
        from hawk.resolver import resolve_paper_trades
        result = resolve_paper_trades()
        if isinstance(result, dict):
            resolved = result.get("resolved", 0)
            wins = result.get("wins", 0)
            losses = result.get("losses", 0)
            if resolved == 0:
                return "Hawk resolve: No trades to resolve."
            return f"Hawk resolved {resolved} trade(s). {wins} win(s), {losses} loss(es)."
        return _summarize(str(result))
    except Exception as e:
        return f"Hawk resolve error: {str(e)[:200]}"


# ── Viper executors ──

def _exec_viper_intel_scan() -> str:
    """Run full Viper intelligence scan (Tavily + Reddit + Polymarket activity)."""
    from viper.config import ViperConfig
    from viper.main import run_single_scan

    cfg = ViperConfig()
    result = run_single_scan(cfg, cycle=0)  # cycle=0 forces Tavily to run

    intel_count = result.get("intel_count", 0)
    matched = result.get("matched", 0)
    sources = result.get("sources", {})
    briefing = result.get("briefing_active", False)

    summary = f"Viper intel scan: {intel_count} item(s) found, {matched} matched to markets."
    if sources:
        summary += " Sources: " + ", ".join(f"{k}: {v}" for k, v in sources.items())
    if briefing:
        summary += " (Hawk briefing active — targeted queries)"
    return _summarize(summary)


def _exec_viper_cost_audit() -> str:
    """Run Viper API cost audit across all agents."""
    from viper.cost_audit import audit_all, find_waste

    audit = audit_all()
    total = audit.get("total_monthly", 0)
    agents = audit.get("agent_totals", {})
    days = audit.get("days_tracked", 0)

    summary = f"Cost audit: ${total:.2f}/month total ({days} days tracked)."
    if agents:
        top = sorted(agents.items(), key=lambda x: x[1], reverse=True)[:5]
        summary += "\nBy agent: " + ", ".join(f"{k}: ${v:.2f}" for k, v in top)

    waste = find_waste()
    if waste:
        summary += f"\n{len(waste)} waste flag(s):"
        for w in waste[:3]:
            summary += f"\n- {w['agent']}/{w['service']}: ${w['monthly']:.2f}/mo"

    return _summarize(summary)


def _exec_viper_soren_metrics() -> str:
    """Get Soren monetization metrics from Viper."""
    from viper.config import ViperConfig
    from viper.monetize import get_soren_metrics

    cfg = ViperConfig()
    data = get_soren_metrics(cfg)

    followers = data.get("total_followers", 0)
    engagement = data.get("engagement_rate", 0)
    avg_cpm = data.get("avg_cpm", 0)
    brand_ready = data.get("brand_ready", False)
    opps = data.get("opportunities", [])

    summary = (
        f"Soren metrics: {followers:,} followers, {engagement:.1f}% engagement, "
        f"${avg_cpm:.2f} avg CPM."
    )
    if brand_ready:
        summary += " Brand-ready!"
    if opps:
        ready = [o for o in opps if o.get("ready")]
        summary += f"\n{len(opps)} monetization path(s) ({len(ready)} ready now)."
        for o in ready[:3]:
            summary += f"\n- {o.get('type', '?')}: {o.get('description', '')[:60]}"

    return _summarize(summary)


# ── Thor feedback loop ──

def _wait_for_thor_result(thor_task_id: str, shelby_task_id: int, timeout: int = 600):
    """Poll Thor's task file until it completes, then update the Shelby task."""
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

            _update_task(shelby_task_id, status="done",
                         notes=f"Thor completed task {thor_task_id}.")
            return

        except Exception as e:
            print(f"[dispatcher] Error polling Thor {thor_task_id}: {e}")
            continue

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

    target_files = []
    lines = []
    for iss in fixable[:15]:
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

        from core.tasks import add_task
        shelby_thor_task = add_task(
            title=f"Fix {len(fixable)} bug(s) from scan",
            agent="thor",
            notes=f"Chained from task #{shelby_task_id}. Submitted to Thor as {thor_task_id}.",
            benefit=3,
        )
        _update_task(shelby_thor_task["id"], status="in_progress",
                     notes=f"Chained from task #{shelby_task_id}. Submitted to Thor as {thor_task_id}. Waiting for result...")

        _update_task(shelby_task_id,
                     notes=_get_current_notes(shelby_task_id) + f"\n\u2192 Chained: Thor task #{shelby_thor_task['id']} created to fix {len(fixable)} issue(s).")

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
    "hawk_scan": _exec_hawk_scan,
    "hawk_resolve": _exec_hawk_resolve,
    "viper_intel_scan": _exec_viper_intel_scan,
    "viper_cost_audit": _exec_viper_cost_audit,
    "viper_soren_metrics": _exec_viper_soren_metrics,
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
            _wait_for_thor_result(thor_task_id, task_id)
        elif action == "robotox_bug_scan":
            result_text, issues = _exec_robotox_bug_scan_detailed()
            _update_task(task_id, status="done", notes=result_text)
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
        # Mark as "done" with error instead of "pending" to prevent infinite retry loop
        _update_task(task_id, status="done", notes=error_msg)


def dispatch_task(task_dict: dict) -> bool:
    """Attempt to dispatch a task. Returns True if dispatched, False if no match.

    Routing priority:
      1. If agent specified → keyword match for that agent
      2. If no agent → keyword match across ALL agents (auto-route)
      3. If still no match and no agent → AI classification (GPT-4o-mini)

    Non-blocking — spawns a daemon thread for the actual work.
    """
    agent = (task_dict.get("agent") or "").lower()
    title = task_dict.get("title") or ""
    action = None

    if agent and agent in _DISPATCHABLE_AGENTS:
        # Agent specified — match action for that agent
        action = _match_action(agent, title)
    elif agent and agent not in _DISPATCHABLE_AGENTS:
        # Agent specified but not dispatchable (soren, lisa, etc.)
        return False

    # No agent or no keyword match — try auto-routing
    if not action:
        route = _auto_route(title)
        if route:
            agent, action = route
            # Update the task with the auto-detected agent
            try:
                _update_task(task_dict["id"], agent=agent)
                task_dict["agent"] = agent
            except Exception:
                pass
            print(f"[dispatcher] Auto-routed '{title}' → {agent}:{action}")

    # Still no match — try AI classification
    if not action:
        route = _ai_route(title)
        if route:
            agent, action = route
            try:
                _update_task(task_dict["id"], agent=agent)
                task_dict["agent"] = agent
            except Exception:
                pass
            print(f"[dispatcher] AI-routed '{title}' → {agent}:{action}")

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
