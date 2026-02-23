"""Self-learning module for the Snipe Timing Assistant.

Tracks timing recommendations vs actual outcomes in SQLite.
Provides accuracy stats that feed back into the scoring engine
so historical performance evolves the timing_score weight.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from pathlib import Path

log = logging.getLogger("garves.snipe")

DB_PATH = Path.home() / "shared" / "memory" / "snipe_assist.db"

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS timing_outcomes (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    agent TEXT NOT NULL,
    direction TEXT NOT NULL,
    timeframe TEXT DEFAULT '5m',
    regime TEXT DEFAULT 'neutral',
    timing_score INTEGER NOT NULL,
    action TEXT NOT NULL,
    size_pct REAL NOT NULL,
    entry_delay_s REAL DEFAULT 0.0,
    window_remaining_s REAL DEFAULT 0.0,
    resolved INTEGER DEFAULT 0,
    won INTEGER DEFAULT 0,
    pnl_usd REAL DEFAULT 0.0,
    resolved_at TEXT DEFAULT ''
);
"""


class TimingLearner:
    """SQLite-backed self-learning for timing recommendations."""

    def __init__(self, db_path: Path | None = None):
        self._db_path = db_path or DB_PATH
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _init_db(self) -> None:
        try:
            self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute(_CREATE_SQL)
            self._conn.commit()
        except Exception as e:
            log.warning("[TIMING-LEARNER] DB init error: %s", str(e)[:150])
            self._conn = None

    def _ensure_conn(self) -> sqlite3.Connection | None:
        if self._conn is None:
            self._init_db()
        return self._conn

    def record(
        self,
        agent: str,
        direction: str,
        timing_score: int,
        action: str,
        size_pct: float,
        timeframe: str = "5m",
        regime: str = "neutral",
        entry_delay_s: float = 0.0,
        window_remaining_s: float = 0.0,
    ) -> str:
        """Record a timing recommendation. Returns record ID."""
        conn = self._ensure_conn()
        if not conn:
            return ""
        record_id = f"ta_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        try:
            from datetime import datetime
            from zoneinfo import ZoneInfo
            ts = datetime.now(ZoneInfo("America/New_York")).isoformat()
            conn.execute(
                "INSERT INTO timing_outcomes "
                "(id, timestamp, agent, direction, timeframe, regime, timing_score, "
                "action, size_pct, entry_delay_s, window_remaining_s) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (record_id, ts, agent, direction, timeframe, regime,
                 timing_score, action, size_pct, entry_delay_s, window_remaining_s),
            )
            conn.commit()
        except Exception as e:
            log.debug("[TIMING-LEARNER] Record error: %s", str(e)[:100])
            return ""
        return record_id

    def resolve(self, record_id: str, won: bool, pnl: float = 0.0) -> None:
        """Mark a recommendation as resolved with outcome."""
        conn = self._ensure_conn()
        if not conn or not record_id:
            return
        try:
            from datetime import datetime
            from zoneinfo import ZoneInfo
            ts = datetime.now(ZoneInfo("America/New_York")).isoformat()
            conn.execute(
                "UPDATE timing_outcomes SET resolved=1, won=?, pnl_usd=?, resolved_at=? "
                "WHERE id=?",
                (1 if won else 0, pnl, ts, record_id),
            )
            conn.commit()
        except Exception as e:
            log.debug("[TIMING-LEARNER] Resolve error: %s", str(e)[:100])

    def get_accuracy(
        self,
        agent: str | None = None,
        direction: str | None = None,
        timeframe: str | None = None,
        regime: str | None = None,
        window: int = 50,
    ) -> dict:
        """Get win rate from last N resolved outcomes with optional filters."""
        conn = self._ensure_conn()
        if not conn:
            return {"win_rate": 0.5, "total": 0, "wins": 0}

        query = "SELECT won, timing_score FROM timing_outcomes WHERE resolved=1"
        params: list = []

        if agent:
            query += " AND agent=?"
            params.append(agent)
        if direction:
            query += " AND direction=?"
            params.append(direction)
        if timeframe:
            query += " AND timeframe=?"
            params.append(timeframe)
        if regime:
            query += " AND regime=?"
            params.append(regime)

        query += " ORDER BY rowid DESC LIMIT ?"
        params.append(window)

        try:
            rows = conn.execute(query, params).fetchall()
            if not rows:
                return {"win_rate": 0.5, "total": 0, "wins": 0}
            wins = sum(1 for r in rows if r[0] == 1)
            total = len(rows)
            avg_score = sum(r[1] for r in rows) / total if total else 0
            return {
                "win_rate": wins / total,
                "total": total,
                "wins": wins,
                "avg_score": round(avg_score, 1),
            }
        except Exception as e:
            log.debug("[TIMING-LEARNER] Accuracy query error: %s", str(e)[:100])
            return {"win_rate": 0.5, "total": 0, "wins": 0}

    def get_optimal_timing_range(self, direction: str | None = None, window: int = 100) -> dict:
        """Analyze which window_remaining_s ranges produce the most wins."""
        conn = self._ensure_conn()
        if not conn:
            return {"best_range_s": (30, 120), "sample_size": 0}

        query = "SELECT window_remaining_s, won FROM timing_outcomes WHERE resolved=1"
        params: list = []
        if direction:
            query += " AND direction=?"
            params.append(direction)
        query += " ORDER BY rowid DESC LIMIT ?"
        params.append(window)

        try:
            rows = conn.execute(query, params).fetchall()
            if len(rows) < 5:
                return {"best_range_s": (30, 120), "sample_size": len(rows)}

            # Bucket into 30s ranges and find best WR
            buckets: dict[int, list[int]] = {}
            for remaining_s, won in rows:
                bucket = int(remaining_s // 30) * 30
                buckets.setdefault(bucket, []).append(won)

            best_wr = 0.0
            best_bucket = 60
            for bucket, outcomes in buckets.items():
                if len(outcomes) >= 3:
                    wr = sum(outcomes) / len(outcomes)
                    if wr > best_wr:
                        best_wr = wr
                        best_bucket = bucket

            return {
                "best_range_s": (best_bucket, best_bucket + 30),
                "best_wr": round(best_wr, 3),
                "sample_size": len(rows),
            }
        except Exception as e:
            log.debug("[TIMING-LEARNER] Optimal range error: %s", str(e)[:100])
            return {"best_range_s": (30, 120), "sample_size": 0}

    def get_status(self) -> dict:
        """Dashboard-friendly status snapshot."""
        conn = self._ensure_conn()
        if not conn:
            return {"total_records": 0, "resolved": 0, "overall_wr": None}

        try:
            row = conn.execute(
                "SELECT COUNT(*), SUM(resolved), SUM(CASE WHEN resolved=1 AND won=1 THEN 1 ELSE 0 END), "
                "SUM(CASE WHEN resolved=1 THEN pnl_usd ELSE 0 END) FROM timing_outcomes"
            ).fetchone()
            total = row[0] or 0
            resolved = row[1] or 0
            wins = row[2] or 0
            pnl = row[3] or 0.0
            return {
                "total_records": total,
                "resolved": resolved,
                "wins": wins,
                "overall_wr": round(wins / resolved * 100, 1) if resolved > 0 else None,
                "total_pnl": round(pnl, 2),
            }
        except Exception as e:
            log.debug("[TIMING-LEARNER] Status error: %s", str(e)[:100])
            return {"total_records": 0, "resolved": 0, "overall_wr": None}
