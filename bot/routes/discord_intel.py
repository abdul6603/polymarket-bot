"""Discord Alpha Scraper routes: /api/discord/*"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from flask import Blueprint, jsonify, request

log = logging.getLogger(__name__)
discord_bp = Blueprint("discord_intel", __name__)

DB_PATH = Path.home() / "polymarket-bot" / "data" / "discord_intel.db"
DATA_DIR = Path.home() / "polymarket-bot" / "data"
VISION_COUNT_FILE = DATA_DIR / "discord_vision_count.json"


def _db_available() -> bool:
    return DB_PATH.exists()


def _conn():
    import sqlite3
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


@discord_bp.route("/api/discord")
def api_discord():
    """Discord scraper overview."""
    if not _db_available():
        return jsonify({"running": False, "total_messages": 0, "total_signals": 0})

    conn = _conn()
    msg_count = conn.execute("SELECT COUNT(*) as c FROM messages").fetchone()["c"]
    sig_count = conn.execute("SELECT COUNT(*) as c FROM signals").fetchone()["c"]
    disc_count = conn.execute("SELECT COUNT(*) as c FROM agent_discussions").fetchone()["c"]

    # Messages per channel
    channels = conn.execute("""
        SELECT channel_name, COUNT(*) as count
        FROM messages GROUP BY channel_name ORDER BY count DESC
    """).fetchall()

    # Recent signal count (last 24h)
    recent_sigs = conn.execute("""
        SELECT COUNT(*) as c FROM signals
        WHERE published_at > datetime('now', '-1 day')
    """).fetchone()["c"]

    # Vision count today
    vision_today = 0
    if VISION_COUNT_FILE.exists():
        try:
            from datetime import date
            vdata = json.loads(VISION_COUNT_FILE.read_text())
            if vdata.get("date") == date.today().isoformat():
                vision_today = vdata.get("count", 0)
        except Exception:
            pass

    conn.close()
    return jsonify({
        "running": True,
        "total_messages": msg_count,
        "total_signals": sig_count,
        "total_discussions": disc_count,
        "recent_signals_24h": recent_sigs,
        "vision_calls_today": vision_today,
        "channels": [dict(r) for r in channels],
    })


@discord_bp.route("/api/discord/feed")
def api_discord_feed():
    """Recent messages feed."""
    if not _db_available():
        return jsonify([])

    channel = request.args.get("channel")
    limit = min(int(request.args.get("limit", 50)), 100)

    conn = _conn()
    if channel:
        rows = conn.execute(
            """SELECT * FROM messages WHERE channel_name = ?
               ORDER BY id DESC LIMIT ?""",
            (channel, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM messages ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@discord_bp.route("/api/discord/signals")
def api_discord_signals():
    """Parsed trading signals."""
    if not _db_available():
        return jsonify([])

    limit = min(int(request.args.get("limit", 30)), 100)
    conn = _conn()
    rows = conn.execute("""
        SELECT s.*, m.author, m.channel_name, m.content as msg_content
        FROM signals s
        JOIN messages m ON s.message_id = m.id
        ORDER BY s.id DESC LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@discord_bp.route("/api/discord/leaderboard")
def api_discord_leaderboard():
    """Trader accuracy leaderboard."""
    if not _db_available():
        return jsonify([])

    conn = _conn()
    rows = conn.execute("""
        SELECT
            author,
            COUNT(*) as total_calls,
            SUM(CASE WHEN outcome = 'win' THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN outcome = 'loss' THEN 1 ELSE 0 END) as losses,
            SUM(CASE WHEN outcome = 'pending' THEN 1 ELSE 0 END) as pending,
            AVG(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct END) as avg_pnl,
            ROUND(
                CAST(SUM(CASE WHEN outcome = 'win' THEN 1 ELSE 0 END) AS FLOAT) /
                NULLIF(SUM(CASE WHEN outcome IN ('win', 'loss') THEN 1 ELSE 0 END), 0) * 100,
                1
            ) as win_rate
        FROM trader_scores
        GROUP BY author
        ORDER BY win_rate DESC, total_calls DESC
    """).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@discord_bp.route("/api/discord/discussions")
def api_discord_discussions():
    """Agent discussions about signals."""
    if not _db_available():
        return jsonify([])

    agent = request.args.get("agent")
    limit = min(int(request.args.get("limit", 30)), 100)

    conn = _conn()
    if agent:
        rows = conn.execute("""
            SELECT d.*, s.ticker, s.direction, s.strategy, m.author, m.channel_name
            FROM agent_discussions d
            LEFT JOIN signals s ON d.signal_id = s.id
            LEFT JOIN messages m ON d.message_id = m.id
            WHERE d.agent = ?
            ORDER BY d.id DESC LIMIT ?
        """, (agent, limit)).fetchall()
    else:
        rows = conn.execute("""
            SELECT d.*, s.ticker, s.direction, s.strategy, m.author, m.channel_name
            FROM agent_discussions d
            LEFT JOIN signals s ON d.signal_id = s.id
            LEFT JOIN messages m ON d.message_id = m.id
            ORDER BY d.id DESC LIMIT ?
        """, (limit,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@discord_bp.route("/api/discord/trader/<author>")
def api_discord_trader(author):
    """Detailed trader profile."""
    if not _db_available():
        return jsonify({"error": "DB not available"}), 404

    conn = _conn()
    stats = conn.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN outcome = 'win' THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN outcome = 'loss' THEN 1 ELSE 0 END) as losses,
            AVG(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct END) as avg_pnl
        FROM trader_scores WHERE author = ?
    """, (author,)).fetchone()

    recent = conn.execute("""
        SELECT ticker, direction, entry_price, outcome, pnl_pct, created_at
        FROM trader_scores WHERE author = ?
        ORDER BY created_at DESC LIMIT 10
    """, (author,)).fetchall()

    conn.close()
    stats = dict(stats) if stats else {}
    resolved = (stats.get("wins") or 0) + (stats.get("losses") or 0)
    return jsonify({
        "author": author,
        **stats,
        "win_rate": round((stats.get("wins") or 0) / resolved * 100, 1) if resolved > 0 else 0,
        "recent": [dict(r) for r in recent],
    })
