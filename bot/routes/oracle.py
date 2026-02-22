"""Oracle (weekly crypto) routes: /api/oracle/*"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

from flask import Blueprint, jsonify

from bot.routes._utils import read_fresh

log = logging.getLogger(__name__)
oracle_bp = Blueprint("oracle", __name__)

DATA_DIR = Path.home() / "polymarket-bot" / "data"
STATUS_FILE = DATA_DIR / "oracle_status.json"
DB_FILE = DATA_DIR / "oracle_predictions.db"


def _load_status() -> dict:
    data = read_fresh(STATUS_FILE, "~/polymarket-bot/data/oracle_status.json")
    return data if data else {}


def _query_db(query: str, params: tuple = ()) -> list[dict]:
    """Run a read-only query against the Oracle predictions DB."""
    if not DB_FILE.exists():
        return []
    try:
        conn = sqlite3.connect(str(DB_FILE))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        log.warning("Oracle DB query error: %s", e)
        return []


@oracle_bp.route("/api/oracle")
def api_oracle():
    """Oracle overview — status + latest cycle info."""
    status = _load_status()

    # Pull accuracy from status or compute from DB
    accuracy = status.get("accuracy", {})
    predictions = status.get("predictions", [])

    return jsonify({
        "running": bool(status),
        "dry_run": status.get("dry_run", True),
        "last_run": status.get("last_run", ""),
        "week_start": status.get("week_start", ""),
        "cycle_type": status.get("cycle_type", "WEEKLY"),
        "regime": status.get("regime", "unknown"),
        "confidence": status.get("confidence", 0),
        "markets_scanned": status.get("markets_scanned", 0),
        "tradeable_markets": status.get("tradeable_markets", 0),
        "trades_placed": status.get("trades_placed", 0),
        "total_wagered": status.get("total_wagered", 0),
        "total_expected_value": status.get("total_expected_value", 0),
        "btc_price_at_run": status.get("btc_price_at_run", 0),
        "emergency_triggered": status.get("emergency_triggered", False),
        "accuracy": accuracy,
        "predictions": predictions[:20],
    })


@oracle_bp.route("/api/oracle/positions")
def api_oracle_positions():
    """Open positions — pending predictions with size > 0."""
    rows = _query_db("""
        SELECT question, asset, market_type, side, size, oracle_prob,
               market_prob, edge, conviction, week_start, created_at
        FROM predictions
        WHERE outcome = 'pending' AND size > 0
        ORDER BY created_at DESC
    """)
    if not rows:
        return jsonify([])

    # Enrich with current market prices from status file
    status = _load_status()
    current_preds = {p.get("question", "")[:80]: p for p in status.get("predictions", [])}

    positions = []
    for r in rows:
        q_short = (r.get("question") or "")[:80]
        current = current_preds.get(q_short, {})
        now_price = current.get("market_prob", r.get("market_prob", 0))

        entry_price = r.get("market_prob", 0)
        size = r.get("size", 0)
        side = r.get("side", "YES")

        # Shares = size / entry_price (what you bought)
        if entry_price > 0:
            shares = size / entry_price
        else:
            shares = size

        # Current value depends on side
        if side == "YES":
            current_val = shares * now_price
        else:
            current_val = shares * (1 - now_price)

        pnl = current_val - size
        payout = shares  # max payout if correct

        positions.append({
            "question": r.get("question", ""),
            "asset": (r.get("asset") or "").upper(),
            "side": side,
            "cost": round(size, 2),
            "entry": round(entry_price, 3),
            "now": round(now_price, 3),
            "pnl": round(pnl, 2),
            "payout": round(payout, 2),
            "conviction": r.get("conviction", ""),
            "week": r.get("week_start", ""),
        })

    return jsonify(positions)


@oracle_bp.route("/api/oracle/predictions")
def api_oracle_predictions():
    """All predictions from the latest week."""
    status = _load_status()
    return jsonify(status.get("predictions", []))


@oracle_bp.route("/api/oracle/report")
def api_oracle_report():
    """Latest Oracle Weekly Report (markdown)."""
    status = _load_status()
    return jsonify({
        "report": status.get("report", "No report available yet."),
        "week_start": status.get("week_start", ""),
        "regime": status.get("regime", ""),
    })


@oracle_bp.route("/api/oracle/history")
def api_oracle_history():
    """Historical weekly reports from DB."""
    rows = _query_db("""
        SELECT week_start, regime, ensemble_confidence, total_markets_scanned,
               tradeable_markets, trades_placed, total_wagered, created_at
        FROM weekly_reports
        ORDER BY created_at DESC
        LIMIT 20
    """)
    return jsonify(rows)


@oracle_bp.route("/api/oracle/accuracy")
def api_oracle_accuracy():
    """Detailed accuracy stats from DB."""
    # Overall stats
    overall = _query_db("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN outcome = 'won' THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN outcome = 'lost' THEN 1 ELSE 0 END) as losses,
            SUM(pnl) as total_pnl
        FROM predictions
        WHERE outcome IN ('won', 'lost')
    """)

    # By market type
    by_type = _query_db("""
        SELECT market_type,
            COUNT(*) as total,
            SUM(CASE WHEN outcome = 'won' THEN 1 ELSE 0 END) as wins,
            SUM(pnl) as pnl
        FROM predictions
        WHERE outcome IN ('won', 'lost')
        GROUP BY market_type
    """)

    # Recent predictions
    recent = _query_db("""
        SELECT week_start, question, asset, market_type, oracle_prob,
               market_prob, edge, side, conviction, size, outcome, pnl
        FROM predictions
        ORDER BY created_at DESC
        LIMIT 30
    """)

    result = {"overall": overall[0] if overall else {}, "by_type": by_type, "recent": recent}
    if result["overall"]:
        total = result["overall"].get("total", 0)
        wins = result["overall"].get("wins", 0)
        result["overall"]["win_rate"] = round(wins / total * 100, 1) if total > 0 else 0

    return jsonify(result)
