"""Traders tab — unified cross-agent portfolio view."""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests
from flask import Blueprint, jsonify

log = logging.getLogger(__name__)

traders_bp = Blueprint("traders", __name__)

BASE = "http://127.0.0.1:8877"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _fetch(path: str):
    """Fetch JSON from an internal dashboard API endpoint."""
    try:
        resp = requests.get(f"{BASE}{path}", timeout=5)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        log.debug("traders _fetch %s failed: %s", path, exc)
        return None


def _fetch_all() -> dict:
    """Parallel-fetch all trading data from existing endpoints."""
    paths = {
        "garves": "/api/garves/positions",
        "hawk": "/api/hawk/positions",
        "odin": "/api/odin/positions",
        "odin_status": "/api/odin",
        "oracle": "/api/oracle/positions",
        "balance": "/api/garves/balance",
        "allocation": "/api/portfolio-allocation",
        "pnl": "/api/pnl",
    }
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = {k: ex.submit(_fetch, v) for k, v in paths.items()}
    return {k: f.result() for k, f in futures.items()}


def _agent_mode(raw: dict) -> dict[str, str]:
    """Determine live vs paper mode per agent from .env and status data."""
    import os
    modes = {}
    # Hawk: HAWK_DRY_RUN env
    modes["hawk"] = "paper" if os.getenv("HAWK_DRY_RUN", "true").lower() == "true" else "live"
    # Garves: DRY_RUN env
    modes["garves"] = "paper" if os.getenv("DRY_RUN", "true").lower() == "true" else "live"
    # Odin: from status
    odin_status = raw.get("odin_status") or {}
    modes["odin"] = odin_status.get("mode", "paper")
    # Oracle: always paper for now (no live execution)
    modes["oracle"] = "paper"
    return modes


def _direction_class(direction: str) -> str:
    """Map direction string to CSS class: 'long' or 'short'."""
    d = (direction or "").upper()
    if d in ("UP", "YES", "LONG"):
        return "long"
    return "short"


def _parse_asset_from_question(question: str) -> str:
    """Best-effort asset extraction from a market question string."""
    q = (question or "").upper()
    for tok in ("BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "MATIC", "AVAX"):
        if tok in q:
            return tok
    return "OTHER"


def _safe_float(val, default=0.0) -> float:
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Normalization per agent
# ---------------------------------------------------------------------------

def _normalize_garves(data: dict | None, mode: str = "live") -> list[dict]:
    """Normalize Garves holdings into unified position schema."""
    if not data or not isinstance(data, dict):
        return []
    positions = []
    for h in data.get("holdings", []):
        cost = _safe_float(h.get("cost"))
        value = _safe_float(h.get("value"))
        pnl = _safe_float(h.get("pnl"))
        outcome = (h.get("outcome") or "Up").upper()
        direction = outcome if outcome in ("UP", "DOWN") else "UP"
        positions.append({
            "id": f"garves_{h.get('market', '')[:20]}",
            "agent": "garves",
            "mode": mode,
            "market": h.get("market", "Unknown"),
            "asset": h.get("asset", "?"),
            "platform": "polymarket",
            "direction": direction,
            "direction_class": _direction_class(direction),
            "category": "crypto",
            "size_usd": round(cost, 2),
            "entry_price": _safe_float(h.get("avg_price")),
            "current_price": _safe_float(h.get("cur_price")),
            "value": round(value, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": _safe_float(h.get("pnl_pct")),
            "status": h.get("status", "active"),
            "end_date": h.get("end_date"),
            "leverage": None,
            "tp_price": None,
            "sl_price": None,
            "tp_distance_pct": None,
            "sl_distance_pct": None,
            "edge": None,
            "conviction": None,
            "payout": None,
        })
    return positions


def _normalize_hawk(data: dict | None, mode: str = "live") -> list[dict]:
    """Normalize Hawk positions into unified position schema."""
    if not data or not isinstance(data, dict):
        return []
    positions = []
    for p in data.get("positions", []):
        direction = (p.get("direction") or "yes").upper()
        size = _safe_float(p.get("size_usd"))
        value = _safe_float(p.get("value"))
        pnl = _safe_float(p.get("pnl"))
        positions.append({
            "id": f"hawk_{p.get('condition_id', '')[:16]}",
            "agent": "hawk",
            "mode": mode,
            "market": p.get("question", "Unknown"),
            "asset": _parse_asset_from_question(p.get("question", "")),
            "platform": "polymarket",
            "direction": direction,
            "direction_class": _direction_class(direction),
            "category": p.get("category", "unknown"),
            "size_usd": round(size, 2),
            "entry_price": _safe_float(p.get("entry_price")),
            "current_price": _safe_float(p.get("cur_price")),
            "value": round(value, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": _safe_float(p.get("pnl_pct")),
            "status": p.get("status", "active"),
            "end_date": p.get("end_date"),
            "leverage": None,
            "tp_price": None,
            "sl_price": None,
            "tp_distance_pct": None,
            "sl_distance_pct": None,
            "edge": _safe_float(p.get("edge")),
            "conviction": None,
            "payout": _safe_float(p.get("payout")),
        })
    return positions


def _normalize_odin(data, mode: str = "paper") -> list[dict]:
    """Normalize Odin positions into unified position schema.

    Handles both /api/odin/positions list format and paper_positions dict format.
    """
    if not data:
        return []
    # API may return a list or a dict of {id: pos}
    items = data if isinstance(data, list) else list(data.values()) if isinstance(data, dict) else []
    positions = []
    for p in items:
        if not isinstance(p, dict):
            continue
        symbol = p.get("symbol", "BTC")
        side = (p.get("side") or p.get("direction") or "LONG").upper()
        entry = _safe_float(p.get("entry_price"))
        current = _safe_float(p.get("current_price", p.get("mark_price")))
        pnl = _safe_float(p.get("pnl", p.get("pnl_usd")))
        size = _safe_float(p.get("size", p.get("notional")))
        leverage = _safe_float(p.get("leverage"), default=1.0)
        # Paper positions use take_profit_1/stop_loss, live uses tp_price/sl_price
        tp = p.get("tp_price") or p.get("take_profit_1")
        sl = p.get("sl_price") or p.get("stop_loss")
        tp_dist = None
        sl_dist = None
        if tp is not None and current > 0:
            tp_dist = round((_safe_float(tp) - current) / current * 100, 2)
        if sl is not None and current > 0:
            sl_dist = round((_safe_float(sl) - current) / current * 100, 2)
        # Determine mode from position ID — paper_ prefix = paper regardless of config
        pos_id = p.get("id", p.get("trade_id", ""))
        pos_mode = "paper" if str(pos_id).startswith("paper_") else mode
        positions.append({
            "id": f"odin_{pos_id or symbol}",
            "agent": "odin",
            "mode": pos_mode,
            "market": f"{symbol.replace('USDT', '')} Perp",
            "asset": symbol.replace("USDT", "").replace("USD", "").replace("/", ""),
            "platform": "hyperliquid",
            "direction": side,
            "direction_class": _direction_class(side),
            "category": "futures",
            "size_usd": round(size, 2),
            "entry_price": round(entry, 2),
            "current_price": round(current, 2),
            "value": round(size + pnl, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl / size * 100, 1) if size > 0 else 0.0,
            "status": "active",
            "end_date": None,
            "leverage": leverage,
            "tp_price": _safe_float(tp) if tp is not None else None,
            "sl_price": _safe_float(sl) if sl is not None else None,
            "tp_distance_pct": tp_dist,
            "sl_distance_pct": sl_dist,
            "edge": None,
            "conviction": p.get("confidence"),
            "payout": None,
        })
    return positions


def _normalize_oracle(data, mode: str = "paper") -> list[dict]:
    """Normalize Oracle positions into unified position schema."""
    if not data or not isinstance(data, list):
        return []
    positions = []
    for p in data:
        cost = _safe_float(p.get("cost"))
        entry = _safe_float(p.get("entry"))
        now = _safe_float(p.get("now"))
        pnl = _safe_float(p.get("pnl"))
        side = (p.get("side") or "YES").upper()
        positions.append({
            "id": f"oracle_{p.get('asset', 'UNK')}_{p.get('week', '')}",
            "agent": "oracle",
            "mode": mode,
            "market": p.get("question", "Unknown"),
            "asset": (p.get("asset") or "?").upper(),
            "platform": "polymarket",
            "direction": side,
            "direction_class": _direction_class(side),
            "category": "crypto",
            "size_usd": round(cost, 2),
            "entry_price": round(entry, 4),
            "current_price": round(now, 4),
            "value": round(cost + pnl, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl / cost * 100, 1) if cost > 0 else 0.0,
            "status": "active",
            "end_date": None,
            "leverage": None,
            "tp_price": None,
            "sl_price": None,
            "tp_distance_pct": None,
            "sl_distance_pct": None,
            "edge": None,
            "conviction": p.get("conviction"),
            "payout": _safe_float(p.get("payout")),
        })
    return positions


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------

@traders_bp.route("/api/traders/positions")
def api_traders_positions():
    """Unified positions from all 4 trading agents."""
    raw = _fetch_all()
    modes = _agent_mode(raw)

    garves_pos = _normalize_garves(raw.get("garves"), modes["garves"])
    hawk_pos = _normalize_hawk(raw.get("hawk"), modes["hawk"])
    odin_pos = _normalize_odin(raw.get("odin"), modes["odin"])
    oracle_pos = _normalize_oracle(raw.get("oracle"), modes["oracle"])

    all_positions = garves_pos + hawk_pos + odin_pos + oracle_pos

    # Per-agent summary
    by_agent = {}
    for agent_name, agent_positions in [
        ("garves", garves_pos), ("hawk", hawk_pos),
        ("odin", odin_pos), ("oracle", oracle_pos),
    ]:
        by_agent[agent_name] = {
            "count": len(agent_positions),
            "exposure": round(sum(p["size_usd"] for p in agent_positions), 2),
            "pnl": round(sum(p["pnl"] for p in agent_positions), 2),
            "value": round(sum(p["value"] for p in agent_positions), 2),
        }

    total_exposure = sum(a["exposure"] for a in by_agent.values())
    total_pnl = sum(a["pnl"] for a in by_agent.values())
    total_value = sum(a["value"] for a in by_agent.values())

    return jsonify({
        "positions": all_positions,
        "totals": {
            "count": len(all_positions),
            "exposure": round(total_exposure, 2),
            "pnl": round(total_pnl, 2),
            "value": round(total_value, 2),
            "by_agent": by_agent,
        },
        "timestamp": time.time(),
    })


@traders_bp.route("/api/traders/overview")
def api_traders_overview():
    """Hero metrics + per-agent summaries."""
    raw = _fetch_all()

    balance = raw.get("balance") or {}
    pnl_data = raw.get("pnl") or {}
    alloc = raw.get("allocation") or {}
    combined = pnl_data.get("combined", {})

    modes = _agent_mode(raw)
    garves_pos = _normalize_garves(raw.get("garves"), modes["garves"])
    hawk_pos = _normalize_hawk(raw.get("hawk"), modes["hawk"])
    odin_pos = _normalize_odin(raw.get("odin"), modes["odin"])
    oracle_pos = _normalize_oracle(raw.get("oracle"), modes["oracle"])
    all_positions = garves_pos + hawk_pos + odin_pos + oracle_pos

    total_unrealized = sum(p["pnl"] for p in all_positions)
    total_value = sum(p["value"] for p in all_positions)

    hero = {
        "portfolio_value": round(balance.get("portfolio", 0), 2),
        "cash": round(balance.get("cash", 0), 2),
        "positions_value": round(total_value, 2),
        "unrealized_pnl": round(total_unrealized, 2),
        "realized_pnl": round(combined.get("total_pnl", 0), 2),
        "win_rate": combined.get("win_rate", 0),
        "total_trades": combined.get("total_trades", 0),
        "active_positions": len(all_positions),
    }

    agents_summary = {}
    for name, positions in [
        ("garves", garves_pos), ("hawk", hawk_pos),
        ("odin", odin_pos), ("oracle", oracle_pos),
    ]:
        agent_pnl = pnl_data.get("agents", {}).get(name, {})
        agents_summary[name] = {
            "open_positions": len(positions),
            "exposure": round(sum(p["size_usd"] for p in positions), 2),
            "unrealized_pnl": round(sum(p["pnl"] for p in positions), 2),
            "realized_pnl": round(agent_pnl.get("total_pnl", 0), 2),
            "win_rate": agent_pnl.get("win_rate", 0),
            "total_trades": agent_pnl.get("total_trades", 0),
        }

    return jsonify({
        "hero": hero,
        "agents": agents_summary,
        "allocation": alloc,
        "timestamp": time.time(),
    })


@traders_bp.route("/api/traders/performance")
def api_traders_performance():
    """Forward combined P&L + LLM cost data."""
    pnl_data = _fetch("/api/pnl")
    if not pnl_data:
        return jsonify({"error": "Failed to fetch P&L data"}), 502

    return jsonify({
        "combined": pnl_data.get("combined", {}),
        "agents": pnl_data.get("agents", {}),
        "llm_costs": pnl_data.get("llm_costs", {}),
        "slippage": pnl_data.get("slippage", {}),
        "timestamp": time.time(),
    })


@traders_bp.route("/api/traders/risk")
def api_traders_risk():
    """Portfolio allocation + correlation warnings."""
    raw = _fetch_all()
    alloc = raw.get("allocation") or {}

    # Detect correlation: multiple agents holding same asset
    modes = _agent_mode(raw)
    garves_pos = _normalize_garves(raw.get("garves"), modes["garves"])
    hawk_pos = _normalize_hawk(raw.get("hawk"), modes["hawk"])
    odin_pos = _normalize_odin(raw.get("odin"), modes["odin"])
    oracle_pos = _normalize_oracle(raw.get("oracle"), modes["oracle"])
    all_positions = garves_pos + hawk_pos + odin_pos + oracle_pos

    asset_agents: dict[str, list[dict]] = {}
    for p in all_positions:
        asset = p["asset"].upper()
        if asset not in asset_agents:
            asset_agents[asset] = []
        asset_agents[asset].append({
            "agent": p["agent"],
            "direction": p["direction"],
            "size_usd": p["size_usd"],
        })

    correlations = []
    for asset, holders in asset_agents.items():
        if len(holders) >= 2:
            total_exposure = sum(h["size_usd"] for h in holders)
            agents_involved = list({h["agent"] for h in holders})
            directions = list({h["direction"] for h in holders})
            correlations.append({
                "asset": asset,
                "agents": agents_involved,
                "directions": directions,
                "total_exposure": round(total_exposure, 2),
                "hedged": len(directions) > 1,
            })

    return jsonify({
        "allocation": alloc,
        "correlations": correlations,
        "timestamp": time.time(),
    })
