"""Discord Alpha Scraper routes: /api/discord/*"""
from __future__ import annotations

import json
import logging
import time
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


# ── Discord → Odin Pipeline Endpoints ──

ODIN_STATUS_FILE = Path.home() / "odin" / "data" / "odin_status.json"
DISCORD_APPROVALS_FILE = Path.home() / "odin" / "data" / "discord_approvals.json"


@discord_bp.route("/api/discord/pipeline")
def api_discord_pipeline():
    """Pipeline status from Odin's status file."""
    try:
        if ODIN_STATUS_FILE.exists():
            data = json.loads(ODIN_STATUS_FILE.read_text())
            pipeline = data.get("discord_pipeline")
            if pipeline:
                return jsonify(pipeline)
    except Exception as e:
        log.debug("Pipeline status error: %s", str(e)[:100])
    return jsonify({"attached": False, "stats": {}, "recent_signals": []})


@discord_bp.route("/api/discord/votes")
def api_discord_votes():
    """Recent voting results from pipeline status."""
    try:
        if ODIN_STATUS_FILE.exists():
            data = json.loads(ODIN_STATUS_FILE.read_text())
            pipeline = data.get("discord_pipeline", {})
            recent = pipeline.get("recent_signals", [])
            # Return only signals that went through voting
            voted = [s for s in recent if s.get("vote_result")]
            return jsonify(voted)
    except Exception as e:
        log.debug("Votes fetch error: %s", str(e)[:100])
    return jsonify([])


@discord_bp.route("/api/discord/approve/<signal_id>", methods=["POST"])
def api_discord_approve(signal_id):
    """Write approval for a manual-review signal."""
    try:
        approvals = {}
        if DISCORD_APPROVALS_FILE.exists():
            approvals = json.loads(DISCORD_APPROVALS_FILE.read_text())

        approvals[signal_id] = {
            "decision": "approve",
            "approved_at": time.time(),
            "processed": False,
        }

        DISCORD_APPROVALS_FILE.parent.mkdir(parents=True, exist_ok=True)
        DISCORD_APPROVALS_FILE.write_text(json.dumps(approvals, indent=2))
        log.info("Discord signal %s approved via dashboard", signal_id)
        return jsonify({"ok": True, "signal_id": signal_id})
    except Exception as e:
        log.error("Approve error: %s", str(e)[:100])
        return jsonify({"ok": False, "error": str(e)[:100]}), 500
