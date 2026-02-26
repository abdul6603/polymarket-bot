"""Resolution Learner — tracks predictions, calibration, and triggers Opus reflection.

SQLite at ~/shared/memory/resolution_scalper.db.
Every 20 trades: compute calibration score.
Every 50 trades: write Opus reflection request for async review.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path

log = logging.getLogger("garves.snipe")

DB_PATH = Path.home() / "shared" / "memory" / "resolution_scalper.db"
REFLECTION_DIR = Path(__file__).parent.parent.parent / "data"
CALIBRATION_INTERVAL = 20
REFLECTION_INTERVAL = 50


class ResolutionLearner:
    """Self-learning module for resolution scalper predictions."""

    def __init__(self):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()
        self._trade_count = self._get_total_count()

    def _init_db(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                asset TEXT NOT NULL,
                direction TEXT NOT NULL,
                window_id TEXT,
                probability REAL NOT NULL,
                market_price REAL NOT NULL,
                edge REAL NOT NULL,
                z_score REAL,
                sigma REAL,
                remaining_s REAL,
                bet_size REAL,
                won INTEGER,
                pnl REAL,
                resolved_at REAL,
                calibration_bucket TEXT
            );
            CREATE TABLE IF NOT EXISTS calibration (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                bucket TEXT NOT NULL,
                predicted_avg REAL NOT NULL,
                actual_wr REAL NOT NULL,
                sample_size INTEGER NOT NULL,
                drift REAL NOT NULL
            );
        """)
        self._conn.commit()

    def _get_total_count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM predictions").fetchone()
        return row[0] if row else 0

    def record(
        self,
        asset: str,
        direction: str,
        window_id: str,
        probability: float,
        market_price: float,
        edge: float,
        z_score: float,
        sigma: float,
        remaining_s: float,
        bet_size: float,
    ) -> int:
        """Record a new prediction. Returns record ID."""
        bucket = self._prob_bucket(probability)
        cur = self._conn.execute(
            """INSERT INTO predictions
               (timestamp, asset, direction, window_id, probability, market_price,
                edge, z_score, sigma, remaining_s, bet_size, calibration_bucket)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (time.time(), asset, direction, window_id, probability, market_price,
             edge, z_score, sigma, remaining_s, bet_size, bucket),
        )
        self._conn.commit()
        self._trade_count += 1
        return cur.lastrowid

    def resolve(self, record_id: int, won: bool, pnl: float) -> None:
        """Mark a prediction as resolved."""
        self._conn.execute(
            "UPDATE predictions SET won = ?, pnl = ?, resolved_at = ? WHERE id = ?",
            (1 if won else 0, pnl, time.time(), record_id),
        )
        self._conn.commit()

        # Check milestones
        resolved_count = self._get_resolved_count()
        if resolved_count > 0 and resolved_count % CALIBRATION_INTERVAL == 0:
            self._compute_calibration()
        if resolved_count > 0 and resolved_count % REFLECTION_INTERVAL == 0:
            self._trigger_opus_reflection()

    def _get_resolved_count(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM predictions WHERE won IS NOT NULL"
        ).fetchone()
        return row[0] if row else 0

    @staticmethod
    def _prob_bucket(prob: float) -> str:
        """Bucket probability into ranges for calibration."""
        if prob < 0.80:
            return "75-80"
        elif prob < 0.85:
            return "80-85"
        elif prob < 0.90:
            return "85-90"
        elif prob < 0.95:
            return "90-95"
        else:
            return "95-100"

    def _compute_calibration(self) -> None:
        """Compute calibration: compare predicted vs actual win rates per bucket."""
        rows = self._conn.execute(
            """SELECT calibration_bucket, AVG(probability) as avg_prob,
                      AVG(CASE WHEN won = 1 THEN 1.0 ELSE 0.0 END) as actual_wr,
                      COUNT(*) as n
               FROM predictions WHERE won IS NOT NULL
               GROUP BY calibration_bucket"""
        ).fetchall()

        now = time.time()
        for row in rows:
            bucket = row["calibration_bucket"]
            avg_prob = row["avg_prob"]
            actual_wr = row["actual_wr"]
            n = row["n"]
            drift = actual_wr - avg_prob

            self._conn.execute(
                """INSERT INTO calibration
                   (timestamp, bucket, predicted_avg, actual_wr, sample_size, drift)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (now, bucket, avg_prob, actual_wr, n, drift),
            )
            log.info(
                "[RES-LEARN] Calibration %s: predicted=%.1f%% actual=%.1f%% drift=%+.1f%% (n=%d)",
                bucket, avg_prob * 100, actual_wr * 100, drift * 100, n,
            )
        self._conn.commit()

    def _trigger_opus_reflection(self) -> None:
        """Write reflection request for async Opus review."""
        stats = self.get_stats()
        recent = self._conn.execute(
            """SELECT asset, direction, probability, market_price, edge,
                      z_score, remaining_s, won, pnl
               FROM predictions WHERE won IS NOT NULL
               ORDER BY resolved_at DESC LIMIT 50"""
        ).fetchall()

        request = {
            "type": "resolution_scalper_reflection",
            "timestamp": time.time(),
            "stats": stats,
            "recent_trades": [dict(r) for r in recent],
            "questions": [
                "Are the probability estimates well-calibrated?",
                "Should MIN_EDGE or MIN_PROBABILITY thresholds be adjusted?",
                "Are there asset-specific patterns (e.g., ETH more volatile)?",
                "Is the quarter-Kelly sizing appropriate given the calibration?",
                "Any systematic biases in z-score vs outcomes?",
            ],
        }

        out_path = REFLECTION_DIR / "resolution_reflection_request.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(request, indent=2))
        log.info("[RES-LEARN] Opus reflection requested (%d trades)", stats["total_trades"])

    def get_stats(self) -> dict:
        """Dashboard stats: total trades, WR, avg edge, calibration, PnL."""
        row = self._conn.execute(
            """SELECT COUNT(*) as total,
                      SUM(CASE WHEN won = 1 THEN 1 ELSE 0 END) as wins,
                      SUM(CASE WHEN won = 0 THEN 1 ELSE 0 END) as losses,
                      AVG(edge) as avg_edge,
                      SUM(pnl) as total_pnl,
                      AVG(pnl) as avg_pnl
               FROM predictions WHERE won IS NOT NULL"""
        ).fetchone()

        total = row["total"] or 0
        wins = row["wins"] or 0
        losses = row["losses"] or 0
        wr = (wins / total * 100) if total > 0 else 0.0

        # Latest calibration score
        cal_rows = self._conn.execute(
            """SELECT bucket, drift FROM calibration
               WHERE id IN (SELECT MAX(id) FROM calibration GROUP BY bucket)"""
        ).fetchall()
        cal_score = 1.0
        if cal_rows:
            avg_abs_drift = sum(abs(r["drift"]) for r in cal_rows) / len(cal_rows)
            cal_score = round(1.0 - avg_abs_drift, 3)

        pending = self._conn.execute(
            "SELECT COUNT(*) FROM predictions WHERE won IS NULL"
        ).fetchone()[0]

        return {
            "total_trades": total,
            "pending": pending,
            "wins": wins,
            "losses": losses,
            "win_rate": round(wr, 1),
            "avg_edge": round((row["avg_edge"] or 0) * 100, 1),
            "total_pnl": round(row["total_pnl"] or 0, 2),
            "avg_pnl": round(row["avg_pnl"] or 0, 3),
            "calibration_score": cal_score,
        }

    def get_recent(self, limit: int = 10) -> list[dict]:
        """Return recent predictions for dashboard."""
        rows = self._conn.execute(
            """SELECT id, timestamp, asset, direction, probability, market_price,
                      edge, z_score, remaining_s, bet_size, won, pnl
               FROM predictions ORDER BY id DESC LIMIT ?""",
            (limit,),
        )
        return [dict(r) for r in rows.fetchall()]
