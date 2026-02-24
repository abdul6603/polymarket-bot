"""Oracle tracker — 52-week prediction memory with accuracy scoring.

SQLite database tracks every prediction Oracle makes, its outcome,
and computes accuracy per market type and per model for self-calibration.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from oracle.config import OracleConfig
from oracle.edge_calculator import TradeSignal
from oracle.executor import OrderResult

log = logging.getLogger(__name__)


def _init_db(db_path: Path) -> sqlite3.Connection:
    """Initialize the predictions database."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            week_start TEXT NOT NULL,
            condition_id TEXT NOT NULL,
            question TEXT,
            asset TEXT,
            market_type TEXT,
            oracle_prob REAL,
            market_prob REAL,
            edge REAL,
            side TEXT,
            conviction TEXT,
            size REAL,
            order_id TEXT,
            fill_price REAL,
            outcome TEXT DEFAULT 'pending',  -- 'pending', 'won', 'lost'
            actual_result REAL,              -- 1.0 (YES won) or 0.0 (NO won)
            pnl REAL DEFAULT 0.0,
            model_outputs TEXT,              -- JSON of per-model predictions
            created_at TEXT DEFAULT (datetime('now')),
            resolved_at TEXT,
            UNIQUE(week_start, condition_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS weekly_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            week_start TEXT NOT NULL UNIQUE,
            regime TEXT,
            ensemble_confidence REAL,
            total_markets_scanned INTEGER,
            tradeable_markets INTEGER,
            trades_placed INTEGER,
            total_wagered REAL,
            report_markdown TEXT,
            context_json TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS model_accuracy (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_name TEXT NOT NULL,
            week_start TEXT NOT NULL,
            predictions_count INTEGER,
            avg_error REAL,
            brier_score REAL,
            weight_used REAL,
            UNIQUE(model_name, week_start)
        )
    """)
    conn.commit()
    return conn


class OracleTracker:
    """Tracks predictions, outcomes, and accuracy over 52 weeks."""

    def __init__(self, cfg: OracleConfig):
        self.cfg = cfg
        self.db = _init_db(cfg.db_path())

    def record_predictions(
        self,
        week_start: str,
        trades: list[TradeSignal],
        order_results: list[OrderResult],
        model_outputs: dict[str, dict],
    ) -> None:
        """Record all predictions and trade results for this week."""
        result_map = {r.signal.market.condition_id: r for r in order_results}

        for trade in trades:
            cid = trade.market.condition_id
            order = result_map.get(cid)

            self.db.execute("""
                INSERT OR REPLACE INTO predictions
                (week_start, condition_id, question, asset, market_type,
                 oracle_prob, market_prob, edge, side, conviction, size,
                 order_id, fill_price, model_outputs)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                week_start,
                cid,
                trade.market.question[:200],
                trade.market.asset,
                trade.market.market_type,
                trade.oracle_prob,
                trade.market_prob,
                trade.edge,
                trade.side,
                trade.conviction,
                trade.size,
                order.order_id if order else "",
                order.fill_price if order else 0.0,
                json.dumps({k: v.get("predictions", {}).get(cid[:12], None) for k, v in model_outputs.items()}),
            ))
        self.db.commit()
        log.info("Recorded %d predictions for week %s", len(trades), week_start)

    def record_weekly_report(
        self,
        week_start: str,
        regime: str,
        confidence: float,
        total_scanned: int,
        tradeable: int,
        trades_placed: int,
        total_wagered: float,
        report_md: str,
        context_json: str,
    ) -> None:
        """Save the weekly report summary."""
        self.db.execute("""
            INSERT OR REPLACE INTO weekly_reports
            (week_start, regime, ensemble_confidence, total_markets_scanned,
             tradeable_markets, trades_placed, total_wagered, report_markdown, context_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (week_start, regime, confidence, total_scanned, tradeable,
              trades_placed, total_wagered, report_md, context_json))
        self.db.commit()

    def resolve_predictions(self, week_start: str) -> dict[str, Any]:
        """Resolve pending predictions by checking market outcomes.

        Called after markets resolve (typically end of week).
        Returns resolution summary.
        """
        pending = self.db.execute("""
            SELECT condition_id, side, size, fill_price, oracle_prob
            FROM predictions
            WHERE week_start = ? AND outcome = 'pending' AND size > 0
        """, (week_start,)).fetchall()

        if not pending:
            return {"resolved": 0}

        import requests
        resolved = 0
        total_pnl = 0.0

        for cid, side, size, fill_price, oracle_prob in pending:
            try:
                resp = requests.get(f"https://clob.polymarket.com/markets/{cid}", timeout=5)
                if resp.status_code != 200:
                    continue
                market = resp.json()

                if not market.get("closed"):
                    continue

                # Determine outcome
                tokens = market.get("tokens", [])
                if len(tokens) < 2:
                    continue

                yes_winner = float(tokens[0].get("winner", 0)) == 1.0
                actual = 1.0 if yes_winner else 0.0

                # Calculate P&L
                if side == "YES":
                    won = yes_winner
                    pnl = (1.0 - fill_price) * (size / fill_price) if won else -size
                else:
                    won = not yes_winner
                    pnl = (1.0 - fill_price) * (size / fill_price) if won else -size

                outcome = "won" if won else "lost"
                self.db.execute("""
                    UPDATE predictions
                    SET outcome = ?, actual_result = ?, pnl = ?, resolved_at = datetime('now')
                    WHERE condition_id = ? AND week_start = ?
                """, (outcome, actual, pnl, cid, week_start))

                total_pnl += pnl
                resolved += 1

            except Exception as e:
                log.warning("Failed to resolve %s: %s", cid[:12], e)

        self.db.commit()
        log.info("Resolved %d predictions, total P&L: $%.2f", resolved, total_pnl)
        return {"resolved": resolved, "pnl": total_pnl}

    def get_open_condition_ids(self) -> set[str]:
        """Return condition_ids of predictions that are still pending (open positions)."""
        rows = self.db.execute(
            "SELECT condition_id FROM predictions WHERE outcome = 'pending' AND size > 0"
        ).fetchall()
        return {r[0] for r in rows}

    def get_accuracy_stats(self, weeks: int = 52) -> dict[str, Any]:
        """Get accuracy statistics over the last N weeks."""
        rows = self.db.execute("""
            SELECT market_type, outcome, COUNT(*), SUM(pnl)
            FROM predictions
            WHERE outcome IN ('won', 'lost')
            GROUP BY market_type, outcome
            ORDER BY market_type
        """).fetchall()

        by_type: dict[str, dict] = {}
        for mtype, outcome, count, pnl in rows:
            if mtype not in by_type:
                by_type[mtype] = {"won": 0, "lost": 0, "pnl": 0.0}
            by_type[mtype][outcome] = count
            by_type[mtype]["pnl"] += pnl or 0

        stats = {}
        total_won = 0
        total_lost = 0
        total_pnl = 0.0

        for mtype, data in by_type.items():
            w, l = data["won"], data["lost"]
            total = w + l
            wr = (w / total * 100) if total > 0 else 0
            stats[mtype] = {
                "win_rate": wr,
                "won": w,
                "lost": l,
                "total": total,
                "pnl": data["pnl"],
            }
            total_won += w
            total_lost += l
            total_pnl += data["pnl"]

        grand_total = total_won + total_lost
        return {
            "overall_win_rate": (total_won / grand_total * 100) if grand_total > 0 else 0,
            "total_predictions": grand_total,
            "total_pnl": total_pnl,
            "by_type": stats,
            "weeks_tracked": self._weeks_tracked(),
        }

    def _weeks_tracked(self) -> int:
        row = self.db.execute("SELECT COUNT(DISTINCT week_start) FROM predictions").fetchone()
        return row[0] if row else 0

    def get_status(self) -> dict[str, Any]:
        """Get current status for dashboard."""
        stats = self.get_accuracy_stats()
        latest = self.db.execute("""
            SELECT week_start, regime, trades_placed, total_wagered
            FROM weekly_reports ORDER BY created_at DESC LIMIT 1
        """).fetchone()

        return {
            "accuracy": stats,
            "latest_week": latest[0] if latest else None,
            "latest_regime": latest[1] if latest else None,
            "latest_trades": latest[2] if latest else 0,
            "latest_wagered": latest[3] if latest else 0.0,
            "weeks_active": stats.get("weeks_tracked", 0),
        }

    def close(self) -> None:
        self.db.close()
