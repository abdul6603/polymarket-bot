"""Discord Alpha Scraper — SQLite storage."""
from __future__ import annotations

import json
import sqlite3
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
log = logging.getLogger(__name__)

DB_PATH = Path.home() / "polymarket-bot" / "data" / "discord_intel.db"


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    """Create tables if they don't exist."""
    conn = _conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            discord_msg_id TEXT UNIQUE,
            channel_id TEXT NOT NULL,
            channel_name TEXT NOT NULL,
            author TEXT NOT NULL,
            author_id TEXT NOT NULL,
            content TEXT,
            has_image INTEGER DEFAULT 0,
            image_urls TEXT,
            priority TEXT,
            created_at TEXT NOT NULL,
            fetched_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id INTEGER REFERENCES messages(id),
            ticker TEXT,
            direction TEXT,
            entry_price REAL,
            stop_loss REAL,
            take_profit REAL,
            strategy TEXT,
            approach TEXT,
            confidence REAL,
            raw_analysis TEXT,
            priority TEXT,
            consumers TEXT,
            published_at TEXT
        );

        CREATE TABLE IF NOT EXISTS trader_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            author TEXT NOT NULL,
            author_id TEXT NOT NULL,
            signal_id INTEGER REFERENCES signals(id),
            ticker TEXT,
            direction TEXT,
            entry_price REAL,
            outcome TEXT DEFAULT 'pending',
            pnl_pct REAL,
            resolved_at TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS agent_discussions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent TEXT NOT NULL,
            signal_id INTEGER REFERENCES signals(id),
            message_id INTEGER REFERENCES messages(id),
            reaction TEXT,
            reasoning TEXT,
            action_taken TEXT,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_messages_channel ON messages(channel_id);
        CREATE INDEX IF NOT EXISTS idx_messages_author ON messages(author);
        CREATE INDEX IF NOT EXISTS idx_signals_ticker ON signals(ticker);
        CREATE INDEX IF NOT EXISTS idx_trader_scores_author ON trader_scores(author);
        CREATE INDEX IF NOT EXISTS idx_agent_discussions_signal ON agent_discussions(signal_id);
    """)
    conn.close()
    log.info("[DISCORD] Database initialized at %s", DB_PATH)


def save_message(
    discord_msg_id: str,
    channel_id: str,
    channel_name: str,
    author: str,
    author_id: str,
    content: str,
    has_image: bool,
    image_urls: list[str],
    priority: str,
    created_at: str,
) -> int | None:
    """Save a Discord message. Returns row ID or None if duplicate."""
    conn = _conn()
    try:
        cur = conn.execute(
            """INSERT OR IGNORE INTO messages
               (discord_msg_id, channel_id, channel_name, author, author_id,
                content, has_image, image_urls, priority, created_at, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                discord_msg_id, channel_id, channel_name, author, author_id,
                content, int(has_image), json.dumps(image_urls), priority,
                created_at, datetime.now(ET).isoformat(),
            ),
        )
        conn.commit()
        if cur.rowcount == 0:
            return None  # duplicate
        return cur.lastrowid
    except Exception as e:
        log.warning("[DISCORD] DB save error: %s", e)
        return None
    finally:
        conn.close()


def save_signal(
    message_id: int,
    ticker: str | None,
    direction: str | None,
    entry_price: float | None,
    stop_loss: float | None,
    take_profit: float | None,
    strategy: str | None,
    approach: str | None,
    confidence: float | None,
    raw_analysis: str,
    priority: str,
    consumers: list[str],
) -> int:
    """Save a parsed trading signal."""
    conn = _conn()
    cur = conn.execute(
        """INSERT INTO signals
           (message_id, ticker, direction, entry_price, stop_loss, take_profit,
            strategy, approach, confidence, raw_analysis, priority, consumers, published_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            message_id, ticker, direction, entry_price, stop_loss, take_profit,
            strategy, approach, confidence, raw_analysis, priority,
            json.dumps(consumers), datetime.now(ET).isoformat(),
        ),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def save_trader_call(
    author: str,
    author_id: str,
    signal_id: int,
    ticker: str | None,
    direction: str | None,
    entry_price: float | None,
) -> int:
    """Record a trader's call for leaderboard tracking."""
    conn = _conn()
    cur = conn.execute(
        """INSERT INTO trader_scores
           (author, author_id, signal_id, ticker, direction, entry_price, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (author, author_id, signal_id, ticker, direction, entry_price,
         datetime.now(ET).isoformat()),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def save_agent_discussion(
    agent: str,
    signal_id: int | None,
    message_id: int | None,
    reaction: str,
    reasoning: str,
    action_taken: str,
) -> int:
    """Record an agent's discussion/reaction to a signal."""
    conn = _conn()
    cur = conn.execute(
        """INSERT INTO agent_discussions
           (agent, signal_id, message_id, reaction, reasoning, action_taken, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (agent, signal_id, message_id, reaction, reasoning, action_taken,
         datetime.now(ET).isoformat()),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def get_recent_messages(limit: int = 50, channel_name: str | None = None) -> list[dict]:
    """Get recent messages, optionally filtered by channel."""
    conn = _conn()
    if channel_name:
        rows = conn.execute(
            "SELECT * FROM messages WHERE channel_name = ? ORDER BY id DESC LIMIT ?",
            (channel_name, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM messages ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_recent_signals(limit: int = 30) -> list[dict]:
    """Get recent parsed signals with message info."""
    conn = _conn()
    rows = conn.execute("""
        SELECT s.*, m.author, m.channel_name, m.content as msg_content
        FROM signals s
        JOIN messages m ON s.message_id = m.id
        ORDER BY s.id DESC LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_leaderboard() -> list[dict]:
    """Get trader accuracy leaderboard."""
    conn = _conn()
    rows = conn.execute("""
        SELECT
            author,
            COUNT(*) as total_calls,
            SUM(CASE WHEN outcome = 'win' THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN outcome = 'loss' THEN 1 ELSE 0 END) as losses,
            SUM(CASE WHEN outcome = 'pending' THEN 1 ELSE 0 END) as pending,
            AVG(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE NULL END) as avg_pnl,
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
    return [dict(r) for r in rows]


def get_agent_discussions(limit: int = 30, agent: str | None = None) -> list[dict]:
    """Get agent discussions about signals."""
    conn = _conn()
    if agent:
        rows = conn.execute("""
            SELECT d.*, s.ticker, s.direction, m.author, m.channel_name
            FROM agent_discussions d
            LEFT JOIN signals s ON d.signal_id = s.id
            LEFT JOIN messages m ON d.message_id = m.id
            WHERE d.agent = ?
            ORDER BY d.id DESC LIMIT ?
        """, (agent, limit)).fetchall()
    else:
        rows = conn.execute("""
            SELECT d.*, s.ticker, s.direction, m.author, m.channel_name
            FROM agent_discussions d
            LEFT JOIN signals s ON d.signal_id = s.id
            LEFT JOIN messages m ON d.message_id = m.id
            ORDER BY d.id DESC LIMIT ?
        """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]
