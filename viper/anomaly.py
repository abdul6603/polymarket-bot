"""Anomaly Detector — cross-agent health monitoring and alert system.

Every cycle, checks all agent status/data files for anomalies.
Pushes critical alerts through event bus + Shelby immediately.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
BASELINES_FILE = DATA_DIR / "viper_baselines.json"
COSTS_FILE = DATA_DIR / "viper_costs.json"

# Agent status files to monitor
STATUS_FILES = {
    "viper": DATA_DIR / "viper_status.json",
    "hawk": DATA_DIR / "hawk_status.json",
    "quant": DATA_DIR / "quant_status.json",
    "garves": DATA_DIR / "garves_status.json",
}

TRADES_FILE = DATA_DIR / "trades.jsonl"
HAWK_TRADES_FILE = DATA_DIR / "hawk_trades.jsonl"

# Thresholds
COST_SPIKE_MULTIPLIER = 2.0  # 2x 7-day avg = alert
WIN_RATE_FLOOR = 0.45  # Below 45% = alert
STALE_MINUTES = 30  # Status file older than 30 min = agent possibly down
MIN_TRADES_FOR_WINRATE = 20  # Need at least 20 trades for meaningful win rate


def _read_json(path: Path) -> dict | list:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, IOError, OSError) as e:
            log.warning("Failed to read JSON file %s: %s", path, e)
        except Exception as e:
            log.error("Unexpected error reading %s: %s", path, e)
    return {}


def _read_jsonl_tail(path: Path, n: int = 50) -> list[dict]:
    if not path.exists():
        return []
    try:
        lines = path.read_text().strip().split("\n")
        result = []
        for line in lines[-n:]:
            line = line.strip()
            if line:
                try:
                    result.append(json.loads(line))
                except Exception:
                    pass
        return result
    except Exception:
        return []


def _load_baselines() -> dict:
    if BASELINES_FILE.exists():
        try:
            return json.loads(BASELINES_FILE.read_text())
        except (json.JSONDecodeError, IOError, OSError) as e:
            log.warning("Failed to load baselines: %s", e)
        except Exception as e:
            log.error("Unexpected error loading baselines: %s", e)
    return {"agents": {}, "updated": None}


def _save_baselines(baselines: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    baselines["updated"] = datetime.now().isoformat()
    try:
        BASELINES_FILE.write_text(json.dumps(baselines, indent=2))
    except (IOError, OSError) as e:
        log.error("Failed to save baselines: %s", e)
    except Exception as e:
        log.error("Unexpected error saving baselines: %s", e)


def _make_alert(severity: str, agent: str, alert_type: str, message: str, **extra) -> dict:
    alert = {
        "severity": severity,  # critical, warning, info
        "agent": agent,
        "type": alert_type,
        "message": message,
        "ts": time.time(),
    }
    alert.update(extra)
    return alert


def _check_cost_spikes(baselines: dict) -> list[dict]:
    """Check if any agent's daily cost > 2x its 7-day moving average."""
    alerts = []
    costs = _read_json(COSTS_FILE)
    agent_totals = costs.get("agent_totals", {})

    agent_baselines = baselines.get("agents", {})

    for agent, monthly_cost in agent_totals.items():
        daily_cost = monthly_cost / 30.0
        baseline_key = f"{agent}_daily_cost"

        # Update rolling average
        prev = agent_baselines.get(baseline_key, {})
        prev_avg = prev.get("avg", daily_cost)
        prev_count = prev.get("count", 0)

        # Exponential moving average (7-day window)
        alpha = 2.0 / (7 + 1)
        new_avg = alpha * daily_cost + (1 - alpha) * prev_avg
        agent_baselines[baseline_key] = {
            "avg": round(new_avg, 4),
            "latest": round(daily_cost, 4),
            "count": prev_count + 1,
        }

        # Alert if > 2x baseline (only after enough data points)
        if prev_count >= 3 and daily_cost > prev_avg * COST_SPIKE_MULTIPLIER and daily_cost > 0.50:
            alerts.append(_make_alert(
                "critical", agent, "cost_spike",
                f"{agent} daily cost ${daily_cost:.2f} is {daily_cost/prev_avg:.1f}x baseline (${prev_avg:.2f})",
                daily_cost=round(daily_cost, 2),
                baseline=round(prev_avg, 2)
            ))

    baselines["agents"] = agent_baselines
    return alerts


def _check_garves_performance() -> list[dict]:
    """Check Garves win rate over last 20+ trades."""
    alerts = []
    trades = _read_jsonl_tail(TRADES_FILE, MIN_TRADES_FOR_WINRATE)

    if len(trades) < MIN_TRADES_FOR_WINRATE:
        return alerts

    resolved = [t for t in trades if t.get("resolved") or t.get("status") == "resolved"]
    if len(resolved) < MIN_TRADES_FOR_WINRATE:
        return alerts
    
    wins = sum(1 for t in resolved if (t.get("profit", 0) or t.get("pnl", 0) or 0) > 0)
    win_rate = wins / len(resolved)

    if win_rate < WIN_RATE_FLOOR:
        alerts.append(_make_alert(
            "warning", "garves", "win_rate_drop",
            f"Garves win rate {win_rate:.1%} over last {len(resolved)} trades (floor: {WIN_RATE_FLOOR:.0%})",
            win_rate=round(win_rate, 3),
            sample_size=len(resolved),
        ))

    return alerts


def _check_hawk_performance() -> list[dict]:
    """Check Hawk win rate and P&L regression."""
    alerts = []
    trades = _read_jsonl_tail(HAWK_TRADES_FILE, MIN_TRADES_FOR_WINRATE)

    if len(trades) < MIN_TRADES_FOR_WINRATE:
        return alerts

    resolved = [t for t in trades if t.get("resolved") or t.get("status") == "resolved"]
    if len(resolved) < MIN_TRADES_FOR_WINRATE:
        return alerts

    wins = sum(1 for t in resolved if (t.get("profit", 0) or t.get("pnl", 0) or 0) > 0)
    win_rate = wins / len(resolved)

    if win_rate < WIN_RATE_FLOOR:
        alerts.append(_make_alert(
            "warning", "hawk", "win_rate_drop",
            f"Hawk win rate {win_rate:.1%} over last {len(resolved)} trades (floor: {WIN_RATE_FLOOR:.0%})",
            win_rate=round(win_rate, 3),
            sample_size=len(resolved),
        ))

    return alerts


def _check_agent_down() -> list[dict]:
    """Check if any agent's status file is older than STALE_MINUTES."""
    alerts = []
    now = time.time()

    for agent, status_file in STATUS_FILES.items():
        if not status_file.exists():
            continue
        try:
            mtime = status_file.stat().st_mtime
            age_minutes = (now - mtime) / 60
            if age_minutes > STALE_MINUTES:
                alerts.append(_make_alert(
                    "critical", agent, "agent_down",
                    f"{agent} status file is {age_minutes:.0f}m old (threshold: {STALE_MINUTES}m)",
                    age_minutes=round(age_minutes, 1),
                ))
        except Exception:
            pass

    return alerts


def _check_soren_queue() -> list[dict]:
    """Check if Soren's content queue is empty or all items failed."""
    alerts = []
    soren_queue_file = Path.home() / "soren-content" / "data" / "content_queue.json"

    if not soren_queue_file.exists():
        return alerts

    try:
        data = json.loads(soren_queue_file.read_text())
        items = data.get("items", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])

        if not items:
            alerts.append(_make_alert(
                "warning", "soren", "queue_empty",
                "Soren content queue is empty",
            ))
        else:
            failed = sum(1 for i in items if i.get("status") in ("failed", "error"))
            if failed == len(items):
                alerts.append(_make_alert(
                    "warning", "soren", "queue_all_failed",
                    f"All {len(items)} items in Soren queue have failed",
                    total=len(items),
                    failed=failed,
                ))
    except Exception:
        pass

    return alerts


def detect_anomalies() -> list[dict]:
    """Scan all agent data for anomalies. Returns list of alert dicts.

    Called every Viper cycle (lightweight — just reads status/data files).
    Critical anomalies also pushed to Shelby via event bus.
    """
    baselines = _load_baselines()
    alerts = []

    # Run all checks
    alerts.extend(_check_cost_spikes(baselines))
    alerts.extend(_check_garves_performance())
    alerts.extend(_check_hawk_performance())
    alerts.extend(_check_agent_down())
    alerts.extend(_check_soren_queue())

    # Save updated baselines
    _save_baselines(baselines)

    # Publish critical alerts to event bus
    critical = [a for a in alerts if a["severity"] == "critical"]
    if critical:
        try:
            from shared.events import publish as bus_publish
            for alert in critical:
                bus_publish(
                    agent="viper",
                    event_type="anomaly_detected",
                    data=alert,
                    summary=f"[ALERT] {alert['message']}",
                )
        except ImportError:
            log.debug("Event bus not available for anomaly alerts")
        except Exception as e:
            log.error("Failed to publish anomaly alert: %s", e)

    if alerts:
        log.info("Anomaly scan: %d alerts (%d critical)", len(alerts), len(critical))
    return alerts