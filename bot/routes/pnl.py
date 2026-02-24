"""Unified P&L dashboard — single view of all trading agent performance."""
from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Blueprint, jsonify

from bot.routes._utils import read_fresh_jsonl

log = logging.getLogger(__name__)

pnl_bp = Blueprint("pnl", __name__)

ET = ZoneInfo("America/New_York")

# Trade log locations (local mirrors; fetched from Pro if stale)
DATA_DIR = Path(__file__).parent.parent.parent / "data"
GARVES_TRADES = DATA_DIR / "trades.jsonl"
GARVES_ARCHIVE_DIR = DATA_DIR / "archives"
GARVES_STATIC_FILES = [
    DATA_DIR / "trades_old_strategy_feb15.jsonl",
    DATA_DIR / "trades_pre_fix_20260214_2359.jsonl",
]
HAWK_TRADES = DATA_DIR / "hawk_trades.jsonl"
ODIN_DATA = Path.home() / "odin" / "data"
ODIN_TRADES = ODIN_DATA / "odin_trades.jsonl"
LLM_COSTS_FILE = Path.home() / "shared" / "llm_costs.jsonl"


def _load_garves_trades() -> list[dict]:
    """Load all Garves trades (main + static + archives), resolved only."""
    all_trades = []
    seen = set()

    # Main file
    trades = read_fresh_jsonl(GARVES_TRADES, "~/polymarket-bot/data/trades.jsonl")
    for t in trades:
        tid = t.get("trade_id", "")
        if tid and tid not in seen and t.get("resolved"):
            seen.add(tid)
            all_trades.append(t)

    # Static files
    for fpath in GARVES_STATIC_FILES:
        if fpath.exists():
            try:
                for line in fpath.read_text().strip().split("\n"):
                    if not line.strip():
                        continue
                    t = json.loads(line)
                    tid = t.get("trade_id", "")
                    if tid and tid not in seen and t.get("resolved"):
                        seen.add(tid)
                        all_trades.append(t)
            except Exception:
                pass

    # Archives
    if GARVES_ARCHIVE_DIR.exists():
        for fpath in sorted(GARVES_ARCHIVE_DIR.glob("trades_*.jsonl")):
            try:
                for line in fpath.read_text().strip().split("\n"):
                    if not line.strip():
                        continue
                    t = json.loads(line)
                    tid = t.get("trade_id", "")
                    if tid and tid not in seen and t.get("resolved"):
                        seen.add(tid)
                        all_trades.append(t)
            except Exception:
                pass

    return all_trades


def _load_hawk_trades() -> list[dict]:
    """Load resolved Hawk trades."""
    trades = read_fresh_jsonl(HAWK_TRADES, "~/polymarket-bot/data/hawk_trades.jsonl")
    return [t for t in trades if t.get("resolved")]


def _load_odin_trades() -> list[dict]:
    """Load Odin paper/live trades."""
    return read_fresh_jsonl(ODIN_TRADES, "~/odin/data/odin_trades.jsonl")


def _compute_agent_pnl(trades: list[dict], agent: str) -> dict:
    """Compute P&L stats for one agent."""
    if not trades:
        return {
            "agent": agent, "total_pnl": 0, "total_trades": 0,
            "wins": 0, "losses": 0, "win_rate": 0,
            "daily_pnl": {}, "by_source": {},
        }

    total_pnl = 0.0
    wins = losses = 0
    daily_pnl: dict[str, float] = defaultdict(float)
    by_source: dict[str, dict] = defaultdict(lambda: {"wins": 0, "losses": 0, "pnl": 0.0})

    for t in trades:
        pnl = t.get("pnl_usd", t.get("pnl", 0.0)) or 0.0
        total_pnl += pnl
        is_win = t.get("is_win", t.get("won", False))
        if is_win:
            wins += 1
        else:
            losses += 1

        # Daily bucket
        ts = t.get("exit_time", t.get("resolve_time", t.get("timestamp", 0)))
        if ts:
            day = datetime.fromtimestamp(ts, tz=ET).strftime("%Y-%m-%d")
            daily_pnl[day] += pnl

        # Source bucket
        source = t.get("edge_source", t.get("entry_signal", t.get("timeframe", "unknown")))
        by_source[source]["pnl"] += pnl
        if is_win:
            by_source[source]["wins"] += 1
        else:
            by_source[source]["losses"] += 1

    total = wins + losses
    # Sort daily P&L by date
    sorted_daily = dict(sorted(daily_pnl.items()))

    # Compute WR per source
    for src in by_source.values():
        t = src["wins"] + src["losses"]
        src["win_rate"] = round(src["wins"] / t * 100, 1) if t else 0
        src["pnl"] = round(src["pnl"], 2)

    return {
        "agent": agent,
        "total_pnl": round(total_pnl, 2),
        "total_trades": total,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / total * 100, 1) if total else 0,
        "daily_pnl": {k: round(v, 2) for k, v in sorted_daily.items()},
        "by_source": dict(by_source),
    }


def _load_llm_costs() -> dict:
    """Aggregate LLM costs by agent and day."""
    per_agent: dict[str, float] = defaultdict(float)
    per_day: dict[str, float] = defaultdict(float)
    total = 0.0

    if not LLM_COSTS_FILE.exists():
        return {"total": 0, "per_agent": {}, "per_day": {}}

    try:
        with open(LLM_COSTS_FILE) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                cost = rec.get("cost_usd", 0) or 0
                total += cost
                agent = rec.get("agent", "unknown")
                per_agent[agent] += cost
                ts_str = rec.get("ts", "")
                if ts_str:
                    day = ts_str[:10]
                    per_day[day] += cost
    except Exception:
        log.debug("Failed to load LLM costs")

    return {
        "total": round(total, 2),
        "per_agent": {k: round(v, 4) for k, v in sorted(per_agent.items())},
        "per_day": {k: round(v, 4) for k, v in sorted(per_day.items())},
    }


@pnl_bp.route("/api/pnl/indicators")
def api_pnl_indicators():
    """Indicator audit reports — CI data for all Garves and Hawk indicators."""
    garves_report = {}
    hawk_report = {}

    try:
        from bot.weight_learner import generate_audit_report
        garves_report = generate_audit_report()
    except Exception as e:
        garves_report = {"error": str(e)}

    try:
        from hawk.learner import generate_audit_report as hawk_audit
        hawk_report = hawk_audit()
    except Exception as e:
        hawk_report = {"error": str(e)}

    return jsonify({
        "garves": garves_report,
        "hawk": hawk_report,
        "timestamp": time.time(),
    })


@pnl_bp.route("/api/pnl")
def api_pnl():
    """Unified P&L across all trading agents."""
    garves = _compute_agent_pnl(_load_garves_trades(), "garves")
    hawk = _compute_agent_pnl(_load_hawk_trades(), "hawk")
    odin = _compute_agent_pnl(_load_odin_trades(), "odin")

    # Slippage report (Garves only — P1-1)
    slippage = {"raw_pnl": 0, "adjusted_pnl": 0, "slippage_cost": 0}
    try:
        garves_trades = _load_garves_trades()
        raw = sum(t.get("pnl", 0) for t in garves_trades)
        adj = sum(t.get("slippage_adjusted_pnl", t.get("pnl", 0)) for t in garves_trades)
        slippage = {
            "raw_pnl": round(raw, 2),
            "adjusted_pnl": round(adj, 2),
            "slippage_cost": round(raw - adj, 2),
        }
    except Exception:
        pass

    costs = _load_llm_costs()

    # Combined daily P&L
    combined_daily: dict[str, float] = defaultdict(float)
    for agent_data in [garves, hawk, odin]:
        for day, pnl in agent_data["daily_pnl"].items():
            combined_daily[day] += pnl
    combined_daily_sorted = {k: round(v, 2) for k, v in sorted(combined_daily.items())}

    total_pnl = garves["total_pnl"] + hawk["total_pnl"] + odin["total_pnl"]
    total_trades = garves["total_trades"] + hawk["total_trades"] + odin["total_trades"]
    total_wins = garves["wins"] + hawk["wins"] + odin["wins"]

    return jsonify({
        "agents": {"garves": garves, "hawk": hawk, "odin": odin},
        "combined": {
            "total_pnl": round(total_pnl, 2),
            "total_trades": total_trades,
            "win_rate": round(total_wins / total_trades * 100, 1) if total_trades else 0,
            "daily_pnl": combined_daily_sorted,
            "net_after_costs": round(total_pnl - costs["total"], 2),
        },
        "slippage": slippage,
        "llm_costs": costs,
        "timestamp": time.time(),
    })
