"""Brotherhood P&L — single source of truth for revenue vs costs.

Computes unified profitability across all agents:
- Revenue: Garves trades, Hawk trades, Soren estimated CPM
- Costs: from viper_costs.json (computed by cost_audit)

Output: data/brotherhood_pnl.json
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
PNL_FILE = DATA_DIR / "brotherhood_pnl.json"
COSTS_FILE = DATA_DIR / "viper_costs.json"
TRADES_FILE = DATA_DIR / "trades.jsonl"
HAWK_TRADES_FILE = DATA_DIR / "hawk_trades.jsonl"

from zoneinfo import ZoneInfo
ET = ZoneInfo("America/New_York")


def _read_json(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, IOError, OSError) as e:
            log.warning("Failed to read JSON file %s: %s", path, e)
        except Exception as e:
            log.error("Unexpected error reading %s: %s", path, e)
    return {}


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        result = []
        for line in path.read_text().strip().split("\n"):
            line = line.strip()
            if line:
                try:
                    result.append(json.loads(line))
                except Exception:
                    pass
        return result
    except Exception:
        return []


def _compute_trade_pnl(trades: list[dict]) -> dict:
    """Compute P&L from a list of trades."""
    total_pnl = 0.0
    resolved = 0
    wins = 0
    losses = 0

    for t in trades:
        pnl = t.get("profit", t.get("pnl", 0))
        if pnl is None:
            continue
        try:
            pnl = float(pnl)
        except (ValueError, TypeError):
            continue

        is_resolved = t.get("resolved") or t.get("status") == "resolved"
        if is_resolved:
            resolved += 1
            total_pnl += pnl
            if pnl > 0:
                wins += 1
            elif pnl < 0:
                losses += 1

    win_rate = wins / resolved if resolved > 0 else 0
    return {
        "total_pnl": round(total_pnl, 2),
        "resolved_trades": resolved,
        "wins": wins,
        "losses": losses,
        "win_rate": round(win_rate, 3),
    }


def compute_pnl() -> dict:
    """Compute Brotherhood P&L and save to data/brotherhood_pnl.json.

    Called every 12th cycle (hourly) from main loop.
    Returns the computed P&L data dictionary.
    """
    today = datetime.now(ET).strftime("%Y-%m-%d")

    # === REVENUE ===

    # Garves trades
    garves_trades = _read_jsonl(TRADES_FILE)
    garves_pnl = _compute_trade_pnl(garves_trades)

    # Hawk trades
    hawk_trades = _read_jsonl(HAWK_TRADES_FILE)
    hawk_pnl = _compute_trade_pnl(hawk_trades)

    # Soren estimated revenue (placeholder until Lisa X API works)
    soren_est = 0.0  # Will be computed from CPM model when X API is fixed

    total_revenue = garves_pnl["total_pnl"] + hawk_pnl["total_pnl"] + soren_est

    # === COSTS ===
    costs = _read_json(COSTS_FILE)
    total_monthly = costs.get("total_monthly", 0.0)
    daily_api = round(total_monthly / 30, 2)

    # Infrastructure costs (Pro M3 server estimate)
    infra_monthly = 200.0  # Claude Code subscription
    infra_daily = round(infra_monthly / 30, 2)

    total_daily_cost = daily_api + infra_daily
    
    # For a fair daily comparison, estimate daily from total
    agent_totals = costs.get("agent_totals", {}) or {}
    days_tracked = max(costs.get("days_tracked", 1), 1)
    
    garves_daily_pnl = round(garves_pnl["total_pnl"] / days_tracked, 2)
    hawk_daily_pnl = round(hawk_pnl["total_pnl"] / days_tracked, 2)
    daily_revenue = garves_daily_pnl + hawk_daily_pnl + soren_est

    net_daily = round(daily_revenue - total_daily_cost, 2)
    net_monthly_est = round(net_daily * 30, 2)

    # Best performer
    performers = {
        "garves": garves_pnl.get("total_pnl", 0.0),
        "hawk": hawk_pnl["total_pnl"],
    }
    best_performer = max(performers, key=performers.get) if performers else "none"

    # Biggest cost
    biggest_cost = "infrastructure"
    if agent_totals:
        biggest_cost = max(agent_totals, key=agent_totals.get)

    # Trend
    if net_daily > 0:
        trend = "profitable"
    elif net_daily > -5:
        trend = "near_breakeven"
    else:
        trend = "needs_improvement"

    pnl_data = {
        "date": today,
        "revenue": {
            "garves": garves_pnl.get("total_pnl", 0.0),
            "garves_daily": garves_daily_pnl,
            "garves_trades": garves_pnl.get("resolved_trades", 0),
            "garves_win_rate": garves_pnl.get("win_rate", 0.0),
            "hawk": hawk_pnl.get("total_pnl", 0.0),
            "hawk_daily": hawk_daily_pnl,
            "hawk_trades": hawk_pnl.get("resolved_trades", 0),
            "hawk_win_rate": hawk_pnl.get("win_rate", 0.0),
            "soren_est": round(soren_est, 2),
        },
        "costs": {
            "daily_api": daily_api,
            "infrastructure_daily": infra_daily,
            "total_monthly": total_monthly,
            "agent_breakdown": agent_totals,
        },
        "net_daily": net_daily,
        "net_monthly_est": net_monthly_est,
        "best_performer": best_performer,
        "biggest_cost": biggest_cost,
        "trend": trend,        "days_tracked": days_tracked,
        "computed_at": time.time(),
    }

    # Save
    DATA_DIR.mkdir(exist_ok=True)
    try:
        PNL_FILE.write_text(json.dumps(pnl_data, indent=2))
    except (IOError, OSError) as e:
        log.error("Failed to save P&L data: %s", e)
    except Exception as e:
        log.error("Unexpected error saving P&L data: %s", e)

    # Publish event
    try:
        from shared.events import publish as bus_publish
        bus_publish(
            agent="viper",
            event_type="pnl_computed",
            data={
                "net_daily": net_daily,
                "net_monthly_est": net_monthly_est,
                "trend": trend,
            },
            summary=f"Brotherhood P&L: ${net_daily:+.2f}/day ({trend})",
        )
    except ImportError:
        log.debug("Event bus not available for P&L events")
    except Exception as e:
        log.error("Failed to publish P&L event: %s", e)

    log.info("Brotherhood P&L computed: net_daily=$%.2f, trend=%s", net_daily, trend)
    return pnl_data