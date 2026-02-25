"""Dashboard Auto-Reporter + Journal — writes to all tracking systems.

After every trade/update: writes to agents-registry.json,
Excel progress sheets, and maintains a trade journal with lessons.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger("odin.skills.auto_reporter")
ET = ZoneInfo("America/New_York")

JOURNAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS trade_journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id TEXT,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    entry_price REAL,
    exit_price REAL,
    pnl_usd REAL DEFAULT 0,
    conviction_score REAL DEFAULT 0,
    regime TEXT,
    smc_patterns TEXT DEFAULT '[]',
    entry_reason TEXT,
    exit_reason TEXT,
    hold_hours REAL DEFAULT 0,
    lesson TEXT DEFAULT '',
    tags TEXT DEFAULT '[]',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_journal_symbol ON trade_journal(symbol);
"""


class AutoReporter:
    """Automated reporting to Excel, registry, and trade journal."""

    def __init__(self, data_dir: Path | None = None):
        self._data_dir = data_dir or Path.home() / "odin" / "data"
        self._db_path = self._data_dir / "journal.db"
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.executescript(JOURNAL_SCHEMA)
        self._conn.commit()
        self._report_count = 0

    def report_trade(self, trade: dict) -> None:
        """Full reporting pipeline for a trade event."""
        self._report_count += 1

        # 1. Write to trade journal DB
        self._write_journal(trade)

        # 2. Update Excel progress sheet
        self._write_excel(trade)

        # 3. Update agents-registry.json
        self._update_registry(trade)

        log.info("[REPORTER] Trade reported: %s %s PnL=$%.2f",
                 trade.get("direction", ""), trade.get("symbol", ""),
                 trade.get("pnl_usd", 0))

    def _write_journal(self, trade: dict) -> None:
        """Record trade in SQLite journal with auto-generated lesson."""
        pnl = trade.get("pnl_usd", 0)
        direction = trade.get("direction", "")
        symbol = trade.get("symbol", "")
        regime = trade.get("regime", "")
        conviction = trade.get("conviction_score", 0)
        exit_reason = trade.get("exit_reason", "")

        # Auto-generate lesson from trade context
        lesson = self._generate_lesson(trade)

        try:
            self._conn.execute(
                """INSERT INTO trade_journal
                   (trade_id, symbol, direction, entry_price, exit_price,
                    pnl_usd, conviction_score, regime, smc_patterns,
                    entry_reason, exit_reason, hold_hours, lesson, tags, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    trade.get("trade_id", ""),
                    symbol, direction,
                    trade.get("entry_price", 0),
                    trade.get("exit_price", 0),
                    pnl, conviction, regime,
                    json.dumps(trade.get("smc_patterns", [])),
                    trade.get("entry_reason", ""),
                    exit_reason,
                    trade.get("hold_hours", 0),
                    lesson,
                    json.dumps(trade.get("tags", [symbol, direction, regime])),
                    time.time(),
                ),
            )
            self._conn.commit()
        except Exception as e:
            log.debug("[REPORTER] Journal write error: %s", str(e)[:100])

    def _generate_lesson(self, trade: dict) -> str:
        """Auto-generate a lesson from trade outcome."""
        pnl = trade.get("pnl_usd", 0)
        direction = trade.get("direction", "")
        symbol = trade.get("symbol", "")
        conviction = trade.get("conviction_score", 0)
        exit_reason = trade.get("exit_reason", "")
        regime = trade.get("regime", "")
        hold_h = trade.get("hold_hours", 0)

        parts = []
        if pnl > 0:
            if conviction >= 70:
                parts.append(f"High-conviction {direction} {symbol} worked. Regime: {regime}.")
            elif conviction < 40:
                parts.append(f"Low-conviction win — got lucky on {direction} {symbol}.")
            else:
                parts.append(f"Moderate conviction {direction} {symbol} was profitable.")
        else:
            if conviction >= 70:
                parts.append(f"High-conviction {direction} {symbol} FAILED. Review regime: {regime}.")
            if exit_reason == "stop_loss":
                parts.append("Hit SL — check if stop hunt was avoidable.")
            if hold_h < 0.5:
                parts.append("Very short hold — possible entry timing issue.")
            if "opposite" in exit_reason.lower():
                parts.append("Reversed direction — regime may have shifted.")

        return " ".join(parts) if parts else f"{direction} {symbol}: PnL=${pnl:+.2f}"

    def _write_excel(self, trade: dict) -> None:
        """Write to Excel progress sheet via shared/progress.py."""
        try:
            import sys
            sys.path.insert(0, str(Path.home() / "shared"))
            from progress import append_progress

            pnl = trade.get("pnl_usd", 0)
            direction = trade.get("direction", "")
            symbol = trade.get("symbol", "")
            status = "WIN" if pnl > 0 else "LOSS" if pnl < 0 else "PENDING"

            append_progress(
                agent="Odin",
                change_type="Trade",
                feature=f"{direction} {symbol}",
                description=f"PnL=${pnl:+.2f} | Conv={trade.get('conviction_score', 0):.0f} | {trade.get('exit_reason', '')}",
                status=status,
            )
        except Exception as e:
            log.debug("[REPORTER] Excel write error: %s", str(e)[:100])

    def _update_registry(self, trade: dict) -> None:
        """Update agents-registry.json with latest Odin stats."""
        registry_path = Path.home() / "polymarket-bot" / "data" / "agents-registry.json"
        try:
            if registry_path.exists():
                registry = json.loads(registry_path.read_text())
            else:
                registry = {}

            # Get journal stats
            stats = self.get_journal_stats()

            registry["odin"] = {
                "name": "Odin",
                "role": "Futures Trader",
                "status": "active",
                "version": "4.0.0",
                "skills": 13,
                "total_trades": stats.get("total_trades", 0),
                "win_rate": stats.get("win_rate", 0),
                "total_pnl": stats.get("total_pnl", 0),
                "last_trade": stats.get("last_trade_time", ""),
                "updated_at": datetime.now(ET).isoformat(),
            }

            registry_path.parent.mkdir(parents=True, exist_ok=True)
            registry_path.write_text(json.dumps(registry, indent=2))

        except Exception as e:
            log.debug("[REPORTER] Registry update error: %s", str(e)[:100])

    def get_journal_stats(self) -> dict:
        """Get overall journal statistics."""
        row = self._conn.execute(
            "SELECT COUNT(*), SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END), "
            "SUM(pnl_usd), AVG(conviction_score), MAX(created_at) "
            "FROM trade_journal"
        ).fetchone()

        total = row[0] or 0
        wins = row[1] or 0
        total_pnl = row[2] or 0
        avg_conv = row[3] or 0
        last_time = row[4] or 0

        return {
            "total_trades": total,
            "wins": wins,
            "losses": total - wins,
            "win_rate": round(wins / max(total, 1) * 100, 1),
            "total_pnl": round(total_pnl, 2),
            "avg_conviction": round(avg_conv, 1),
            "last_trade_time": datetime.fromtimestamp(last_time, ET).strftime("%Y-%m-%d %H:%M")
            if last_time > 0 else "",
        }

    def get_recent_lessons(self, limit: int = 10) -> list[dict]:
        """Get recent trade lessons for learning."""
        rows = self._conn.execute(
            "SELECT symbol, direction, pnl_usd, conviction_score, lesson, created_at "
            "FROM trade_journal ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()

        return [
            {
                "symbol": r[0], "direction": r[1], "pnl": r[2],
                "conviction": r[3], "lesson": r[4],
                "time": datetime.fromtimestamp(r[5], ET).strftime("%m/%d %H:%M"),
            }
            for r in rows
        ]

    def generate_weekly_report(self, edge_report: dict | None = None) -> dict:
        """Generate weekly performance report for Excel + brotherhood."""
        stats = self.get_journal_stats()
        lessons = self.get_recent_lessons(5)

        report = {
            "period": "weekly",
            "timestamp": datetime.now(ET).isoformat(),
            **stats,
            "recent_lessons": lessons,
        }
        if edge_report:
            report["edge_status"] = edge_report.get("edge_status", "unknown")
            report["sharpe"] = edge_report.get("overall", {}).get("sharpe", 0)
            report["recommendation"] = edge_report.get("recommendation", "")

        # Write to Excel
        try:
            import sys
            sys.path.insert(0, str(Path.home() / "shared"))
            from progress import append_progress
            append_progress(
                agent="Odin",
                change_type="Weekly Report",
                feature="Performance Review",
                description=(
                    f"WR={stats.get('win_rate', 0):.1f}% | "
                    f"PnL=${stats.get('total_pnl', 0):+.2f} | "
                    f"Trades={stats.get('total_trades', 0)}"
                ),
                status="REPORT",
            )
        except Exception as e:
            log.debug("[REPORTER] Weekly Excel error: %s", str(e)[:100])

        log.info("[REPORTER] Weekly report: WR=%.1f%% PnL=$%.2f (%d trades)",
                 stats.get("win_rate", 0), stats.get("total_pnl", 0),
                 stats.get("total_trades", 0))
        return report

    def get_status(self) -> dict:
        stats = self.get_journal_stats()
        return {
            "reports_sent": self._report_count,
            **stats,
        }
