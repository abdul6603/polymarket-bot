"""Viper LLM Cost Governor — per-agent budget enforcement.

Tracks $/agent/day from shared/llm_costs.jsonl, enforces daily budget caps,
and auto-downgrades expensive agents to cheaper local models when spend
exceeds thresholds. Resets overrides at midnight ET.

Safety: _resolve_route() in llm_client.py only reads task_type and "default"
keys from agent_overrides — governor metadata keys (_governor, _original, etc.)
are harmlessly ignored. Config writes use atomic tmp+rename.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

# Paths
LLM_COSTS_FILE = Path.home() / "shared" / "llm_costs.jsonl"
LLM_CONFIG_FILE = Path.home() / "shared" / "llm_config.json"
DATA_DIR = Path(__file__).parent.parent / "data"
GOVERNOR_STATE_FILE = DATA_DIR / "viper_llm_governor.json"
PNL_FILE = DATA_DIR / "brotherhood_pnl.json"

# Budget caps ($/day per agent)
DEFAULT_BUDGETS: dict[str, float] = {
    "thor": 5.00,
    "shelby": 2.00,
    "hawk": 1.00,
    "atlas": 0.50,
    "robotox": 0.50,
    "garves": 0.25,
    "odin": 0.25,
    "viper": 0.25,
    "soren": 0.25,
    "quant": 0.25,
    "oracle": 0.25,
}
SYSTEM_DAILY_LIMIT = 12.00

# Thresholds
THROTTLE_PCT = 80   # At 80% → force local (free, all routes use local 14B)
BLOCK_PCT = 100     # At 100% → force local (free, all routes use local 14B)
SPIKE_MULTIPLIER = 3.0  # Hourly burn > 3x expected → alert

# Tail read limit for large JSONL files
_TAIL_BYTES = 3 * 1024 * 1024  # 3 MB


def _parse_today_costs() -> dict[str, float]:
    """Read tail of llm_costs.jsonl and sum cost_usd by agent for today (ET)."""
    if not LLM_COSTS_FILE.exists():
        return {}

    today_str = datetime.now(ET).strftime("%Y-%m-%d")
    costs: dict[str, float] = {}

    try:
        file_size = LLM_COSTS_FILE.stat().st_size
        with open(LLM_COSTS_FILE, "r") as f:
            # Seek to tail for speed on large files
            if file_size > _TAIL_BYTES:
                f.seek(file_size - _TAIL_BYTES)
                f.readline()  # discard partial line

            for line in f:
                line = line.strip()
                if not line:
                    continue
                # Fast date check before parsing JSON
                if today_str not in line[:35]:
                    continue
                try:
                    rec = json.loads(line)
                    ts = rec.get("ts", "")
                    if ts[:10] == today_str:
                        agent = rec.get("agent", "unknown")
                        costs[agent] = costs.get(agent, 0.0) + rec.get("cost_usd", 0.0)
                except (json.JSONDecodeError, KeyError):
                    continue
    except Exception:
        log.exception("Failed to parse llm_costs.jsonl")

    return costs


def _compute_budgets(today_costs: dict[str, float]) -> dict[str, dict]:
    """Compute budget status for each agent."""
    budgets: dict[str, dict] = {}
    for agent, limit in DEFAULT_BUDGETS.items():
        spent = today_costs.get(agent, 0.0)
        pct = (spent / limit * 100) if limit > 0 else 0.0
        if pct >= BLOCK_PCT:
            status = "blocked"
        elif pct >= THROTTLE_PCT:
            status = "throttled"
        else:
            status = "normal"
        budgets[agent] = {
            "daily_limit": limit,
            "spent_today": round(spent, 6),
            "pct": round(pct, 1),
            "status": status,
        }
    return budgets


def _enforce_overrides(budgets: dict[str, dict]) -> list[str]:
    """Apply or remove governor overrides in llm_config.json. Returns list of actions taken."""
    actions: list[str] = []
    try:
        cfg = json.loads(LLM_CONFIG_FILE.read_text())
    except Exception:
        log.exception("Cannot read llm_config.json")
        return actions

    overrides = cfg.setdefault("agent_overrides", {})
    changed = False

    for agent, budget in budgets.items():
        agent_ov = overrides.get(agent, {})
        has_governor = agent_ov.get("_governor", False)

        if budget["status"] == "blocked":
            if not has_governor or agent_ov.get("default") != "local_small":
                # Save original config before overwriting
                original = {k: v for k, v in agent_ov.items()
                            if not k.startswith("_")}
                overrides[agent] = {
                    **original,
                    "default": "local_small",
                    "_governor": True,
                    "_original": original,
                    "_reason": f"blocked at {budget['pct']:.0f}%",
                }
                actions.append(f"{agent}→local_small ({budget['pct']:.0f}%)")
                changed = True

        elif budget["status"] == "throttled":
            if not has_governor or agent_ov.get("default") != "local_small":
                original = {k: v for k, v in agent_ov.items()
                            if not k.startswith("_")}
                overrides[agent] = {
                    **original,
                    "default": "local_small",
                    "_governor": True,
                    "_original": original,
                    "_reason": f"throttled at {budget['pct']:.0f}%",
                }
                actions.append(f"{agent}→local_small ({budget['pct']:.0f}%)")
                changed = True

        elif budget["status"] == "normal" and has_governor:
            # Restore original config
            original = agent_ov.get("_original", {})
            if original:
                overrides[agent] = original
            else:
                overrides.pop(agent, None)
            actions.append(f"{agent}→restored")
            changed = True

    if changed:
        cfg["agent_overrides"] = overrides
        _atomic_write_json(LLM_CONFIG_FILE, cfg)

    return actions


def _compute_roi_scores(today_costs: dict[str, float]) -> dict[str, dict]:
    """Compute ROI for trading agents (garves, hawk, odin)."""
    roi: dict[str, dict] = {}
    pnl_data: dict = {}

    if PNL_FILE.exists():
        try:
            pnl_data = json.loads(PNL_FILE.read_text())
        except Exception:
            pass

    revenue = pnl_data.get("revenue", {})

    for agent in ("garves", "hawk", "odin"):
        cost = today_costs.get(agent, 0.0)
        daily_pnl = revenue.get(f"{agent}_daily", 0.0)

        if agent == "odin":
            roi[agent] = {"pnl": daily_pnl, "llm_cost": round(cost, 4),
                          "ratio": 0.0, "rating": "paper_mode"}
            continue

        if cost > 0.01:
            ratio = daily_pnl / cost
        elif daily_pnl > 0:
            ratio = 999.0  # Free LLM, positive PnL
        else:
            ratio = 0.0

        if ratio >= 5.0:
            rating = "excellent"
        elif ratio >= 1.0:
            rating = "good"
        elif ratio >= 0:
            rating = "poor"
        else:
            rating = "negative"

        roi[agent] = {
            "pnl": round(daily_pnl, 2),
            "llm_cost": round(cost, 4),
            "ratio": round(ratio, 2),
            "rating": rating,
        }

    return roi


def _detect_cost_spikes(today_costs: dict[str, float]) -> list[str]:
    """Detect agents burning LLM budget faster than expected. Returns alerts."""
    alerts: list[str] = []
    now = datetime.now(ET)
    hours_elapsed = now.hour + now.minute / 60.0
    if hours_elapsed < 0.5:
        return alerts  # Too early in the day to judge

    for agent, limit in DEFAULT_BUDGETS.items():
        spent = today_costs.get(agent, 0.0)
        if spent < 0.10:
            continue
        expected_rate = limit / 24.0
        actual_rate = spent / hours_elapsed
        if actual_rate > expected_rate * SPIKE_MULTIPLIER:
            msg = (f"{agent} cost spike: ${spent:.2f} in {hours_elapsed:.1f}h "
                   f"(rate ${actual_rate:.3f}/h vs expected ${expected_rate:.3f}/h)")
            alerts.append(msg)
            log.warning("Governor: %s", msg)

    # Publish spike alerts to event bus
    if alerts:
        try:
            from shared.events import publish as bus_publish
            bus_publish(
                agent="viper",
                event_type="llm_cost_spike",
                severity="warning",
                data={"alerts": alerts, "costs": {k: round(v, 4) for k, v in today_costs.items() if v > 0}},
                summary=f"LLM cost spike: {len(alerts)} agent(s) over budget pace",
            )
        except Exception:
            pass

    return alerts


def _check_daily_reset(state: dict) -> bool:
    """At midnight ET, remove all governor overrides and reset state. Returns True if reset happened."""
    today_str = datetime.now(ET).strftime("%Y-%m-%d")
    last_reset = state.get("last_reset", "")

    if last_reset == today_str:
        return False

    # New day — remove all governor overrides
    try:
        cfg = json.loads(LLM_CONFIG_FILE.read_text())
        overrides = cfg.get("agent_overrides", {})
        changed = False
        for agent in list(overrides.keys()):
            if overrides[agent].get("_governor"):
                original = overrides[agent].get("_original", {})
                if original:
                    overrides[agent] = original
                else:
                    del overrides[agent]
                changed = True
        if changed:
            cfg["agent_overrides"] = overrides
            _atomic_write_json(LLM_CONFIG_FILE, cfg)
            log.info("Governor: midnight reset — restored all overrides")
    except Exception:
        log.exception("Governor: midnight reset failed")

    state["last_reset"] = today_str
    state["overrides_applied"] = []
    state["alerts"] = []
    return True


def run_governor() -> dict:
    """Main entry point — called every Viper cycle (~5 min).

    Returns state dict with budgets, overrides, ROI, alerts.
    """
    DATA_DIR.mkdir(exist_ok=True)

    # Load persisted state
    state: dict = {}
    if GOVERNOR_STATE_FILE.exists():
        try:
            state = json.loads(GOVERNOR_STATE_FILE.read_text())
        except Exception:
            state = {}

    # Midnight reset
    _check_daily_reset(state)

    # Parse today's costs
    today_costs = _parse_today_costs()

    # Compute budgets
    budgets = _compute_budgets(today_costs)

    # Enforce overrides
    actions = _enforce_overrides(budgets)

    # Detect cost spikes
    alerts = _detect_cost_spikes(today_costs)

    # ROI scores (lightweight — just reads PNL file)
    now = datetime.now(ET)
    roi = {}
    if now.minute < 5:  # Only compute on the hour (when PNL is fresh)
        roi = _compute_roi_scores(today_costs)

    # System-level budget
    total_spent = sum(today_costs.values())
    system_budget = {
        "daily_limit": SYSTEM_DAILY_LIMIT,
        "spent_today": round(total_spent, 4),
        "pct": round(total_spent / SYSTEM_DAILY_LIMIT * 100, 1),
    }

    # Build result
    result = {
        "governor_active": True,
        "last_run": now.isoformat(),
        "budgets": budgets,
        "system_budget": system_budget,
        "overrides_applied": actions if actions else state.get("overrides_applied", []),
        "alerts": alerts if alerts else state.get("alerts", []),
        "roi": roi if roi else state.get("roi", {}),
        "last_reset": state.get("last_reset", now.strftime("%Y-%m-%d")),
    }

    # Persist state
    _atomic_write_json(GOVERNOR_STATE_FILE, result)

    return result


def _atomic_write_json(path: Path, data: dict) -> None:
    """Write JSON atomically via tmp file + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=path.parent, suffix=".tmp", prefix=path.stem
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.rename(tmp_path, path)
    except Exception:
        # Clean up tmp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
