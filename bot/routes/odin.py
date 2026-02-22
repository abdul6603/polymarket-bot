"""Odin (futures trading) routes: /api/odin/*"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Blueprint, jsonify

from bot.routes._utils import read_fresh, read_fresh_jsonl

log = logging.getLogger(__name__)
odin_bp = Blueprint("odin", __name__)

ODIN_DIR = Path.home() / "odin"
DATA_DIR = ODIN_DIR / "data"
STATUS_FILE = DATA_DIR / "odin_status.json"
TRADES_FILE = DATA_DIR / "odin_trades.jsonl"
SIGNALS_FILE = DATA_DIR / "odin_signals.jsonl"
CB_FILE = DATA_DIR / "circuit_breaker.json"
ET = ZoneInfo("America/New_York")


def _load_status() -> dict:
    data = read_fresh(STATUS_FILE, "~/odin/data/odin_status.json")
    return data if data else {"running": False, "mode": "paper"}


def _load_trades() -> list[dict]:
    return read_fresh_jsonl(TRADES_FILE, "~/odin/data/odin_trades.jsonl")


def _load_signals() -> list[dict]:
    return read_fresh_jsonl(SIGNALS_FILE, "~/odin/data/odin_signals.jsonl")


@odin_bp.route("/api/odin")
def api_odin():
    """Odin overview — status + stats."""
    status = _load_status()
    trades = _load_trades()

    wins = [t for t in trades if t.get("is_win")]
    losses = [t for t in trades if not t.get("is_win")]
    total_pnl = sum(t.get("pnl_usd", 0) for t in trades)
    win_rate = len(wins) / len(trades) * 100 if trades else 0

    return jsonify({
        **status,
        "trade_count": len(trades),
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate": round(win_rate, 1),
        "total_pnl_trades": round(total_pnl, 2),
    })


@odin_bp.route("/api/odin/trades")
def api_odin_trades():
    """Recent trade history."""
    trades = _load_trades()
    return jsonify(trades[-50:] if len(trades) > 50 else trades)


@odin_bp.route("/api/odin/signals")
def api_odin_signals():
    """Recent signals (executed and filtered)."""
    signals = _load_signals()
    return jsonify(signals[-30:] if len(signals) > 30 else signals)


@odin_bp.route("/api/odin/positions")
def api_odin_positions():
    """Open paper positions."""
    status = _load_status()
    return jsonify(status.get("paper_positions", []))


@odin_bp.route("/api/odin/macro")
def api_odin_macro():
    """Current macro regime data."""
    status = _load_status()
    return jsonify(status.get("macro") or {
        "regime": "unknown", "score": 0, "multiplier": 0,
    })


@odin_bp.route("/api/odin/circuit-breaker")
def api_odin_circuit_breaker():
    """Circuit breaker state."""
    status = _load_status()
    return jsonify({
        "trading_allowed": status.get("trading_allowed", True),
        "reason": status.get("cb_reason", ""),
        "consecutive_losses": status.get("consecutive_losses", 0),
        "size_modifier": status.get("size_modifier", 1.0),
        "drawdown_pct": status.get("drawdown_pct", 0),
        "daily_pnl": status.get("daily_pnl", 0),
        "weekly_pnl": status.get("weekly_pnl", 0),
    })
