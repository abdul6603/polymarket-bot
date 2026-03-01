"""Arbiter (cross-market arb) routes: /api/arbiter/*"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from flask import Blueprint, jsonify

from bot.routes._utils import read_fresh, read_fresh_jsonl

log = logging.getLogger(__name__)
arbiter_bp = Blueprint("arbiter", __name__)

DATA_DIR = Path(__file__).parent.parent.parent / "data"
STATUS_FILE = DATA_DIR / "arbiter_status.json"
POSITIONS_FILE = DATA_DIR / "arbiter_positions.json"
TRADES_FILE = DATA_DIR / "arbiter_trades.jsonl"
ORDERS_FILE = DATA_DIR / "arbiter_orders.jsonl"


def _load_status() -> dict:
    data = read_fresh(STATUS_FILE, "~/polymarket-bot/data/arbiter_status.json")
    return data if data else {"running": False}


@arbiter_bp.route("/api/arbiter")
def api_arbiter():
    """Main arbiter status endpoint."""
    status = _load_status()
    return jsonify(status)


@arbiter_bp.route("/api/arbiter/positions")
def api_arbiter_positions():
    """Active arb positions."""
    data = read_fresh(POSITIONS_FILE, "~/polymarket-bot/data/arbiter_positions.json")
    arbs = data.get("arbs", []) if data else []
    return jsonify({"arbs": arbs, "count": len(arbs)})


@arbiter_bp.route("/api/arbiter/history")
def api_arbiter_history():
    """Completed arb trades."""
    trades = read_fresh_jsonl(TRADES_FILE, "~/polymarket-bot/data/arbiter_trades.jsonl")
    resolved = [t for t in trades if t.get("resolved")]
    total_pnl = sum(t.get("pnl", 0) for t in resolved)
    return jsonify({
        "trades": resolved[-50:],  # Last 50
        "total": len(resolved),
        "total_pnl": round(total_pnl, 2),
    })


@arbiter_bp.route("/api/arbiter/orders")
def api_arbiter_orders():
    """Recent order log."""
    orders = read_fresh_jsonl(ORDERS_FILE, "~/polymarket-bot/data/arbiter_orders.jsonl")
    return jsonify({"orders": orders[-50:], "total": len(orders)})
