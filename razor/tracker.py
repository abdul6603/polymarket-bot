"""Position tracker — in-memory state + JSONL persistence + dashboard status."""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
TRADES_FILE = DATA_DIR / "razor_trades.jsonl"
STATUS_FILE = DATA_DIR / "razor_status.json"
ET = ZoneInfo("America/New_York")


@dataclass
class ArbPosition:
    """Tracks a live completeness arbitrage position."""
    arb_id: str
    condition_id: str
    question: str
    token_a_id: str
    token_b_id: str
    outcome_a: str
    outcome_b: str
    ask_a: float
    ask_b: float
    combined_cost: float
    shares: float
    position_usd: float
    expected_profit: float
    order_a: str = ""
    order_b: str = ""
    status: str = "open"  # open, exiting, closed, settled
    entry_time: float = 0.0
    exit_recovery: float = 0.0  # USD recovered from early exit
    exit_pnl: float = 0.0
    dry_run: bool = True

    def age_s(self) -> float:
        return time.time() - self.entry_time if self.entry_time > 0 else 0.0


class RazorTracker:
    """Manages arb positions and persists trades to JSONL."""

    def __init__(self):
        self.positions: list[ArbPosition] = []
        self._closed: list[dict] = []
        DATA_DIR.mkdir(exist_ok=True)
        self._load_open_positions()

    @property
    def open_positions(self) -> list[ArbPosition]:
        return [p for p in self.positions if p.status in ("open", "exiting")]

    @property
    def open_count(self) -> int:
        return len(self.open_positions)

    @property
    def exposure(self) -> float:
        return sum(p.position_usd - p.exit_recovery for p in self.open_positions)

    def has_position(self, condition_id: str) -> bool:
        return any(p.condition_id == condition_id and p.status in ("open", "exiting")
                    for p in self.positions)

    def add_position(self, pos: ArbPosition) -> None:
        self.positions.append(pos)
        self._append_trade(self._pos_to_record(pos))
        log.info("[TRACKER] New arb: %s | cost=$%.2f | shares=%.2f | %s",
                 pos.arb_id, pos.position_usd, pos.shares, pos.question[:60])

    def update_position(self, pos: ArbPosition) -> None:
        """Update a position's status and persist."""
        self._update_trade_in_file(pos)

    def close_position(self, pos: ArbPosition, pnl: float, reason: str) -> None:
        """Mark a position as closed with final PnL."""
        pos.status = "closed"
        pos.exit_pnl = pnl
        rec = self._pos_to_record(pos)
        rec["close_reason"] = reason
        rec["close_time"] = time.time()
        self._closed.append(rec)
        self._update_trade_in_file(pos, extra={"close_reason": reason, "close_time": time.time()})
        log.info("[TRACKER] Closed: %s | PnL=$%.2f | reason=%s | %s",
                 pos.arb_id, pnl, reason, pos.question[:60])

    def stats(self) -> dict:
        all_trades = self._load_all_trades()
        closed = [t for t in all_trades if t.get("status") == "closed"]
        total_pnl = sum(t.get("exit_pnl", 0) for t in closed)
        wins = [t for t in closed if t.get("exit_pnl", 0) > 0]
        return {
            "total_arbs": len(all_trades),
            "open_count": self.open_count,
            "closed_count": len(closed),
            "win_count": len(wins),
            "win_rate": round(len(wins) / max(len(closed), 1), 3),
            "total_pnl": round(total_pnl, 2),
            "avg_profit": round(total_pnl / max(len(closed), 1), 2),
            "exposure": round(self.exposure, 2),
        }

    def save_status(self, extra: dict | None = None) -> None:
        """Write JSON status file for dashboard consumption."""
        s = self.stats()
        status = {
            **s,
            "positions": [
                {
                    "arb_id": p.arb_id,
                    "condition_id": p.condition_id,
                    "question": p.question[:120],
                    "outcome_a": p.outcome_a,
                    "outcome_b": p.outcome_b,
                    "ask_a": p.ask_a,
                    "ask_b": p.ask_b,
                    "combined_cost": p.combined_cost,
                    "shares": p.shares,
                    "position_usd": p.position_usd,
                    "status": p.status,
                    "age_s": round(p.age_s()),
                    "exit_recovery": round(p.exit_recovery, 2),
                }
                for p in self.open_positions
            ],
            "last_update": datetime.now(ET).isoformat(),
        }
        if extra:
            status.update(extra)
        try:
            STATUS_FILE.write_text(json.dumps(status, indent=2))
        except Exception:
            log.exception("Failed to save razor status")

    def _pos_to_record(self, pos: ArbPosition) -> dict:
        rec = asdict(pos)
        rec["timestamp"] = time.time()
        rec["time_str"] = datetime.now(ET).strftime("%Y-%m-%d %I:%M%p")
        return rec

    def _append_trade(self, rec: dict) -> None:
        try:
            with open(TRADES_FILE, "a") as f:
                f.write(json.dumps(rec) + "\n")
        except Exception:
            log.exception("Failed to write razor trade")

    def _update_trade_in_file(self, pos: ArbPosition, extra: dict | None = None) -> None:
        if not TRADES_FILE.exists():
            return
        try:
            lines = []
            with open(TRADES_FILE) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    if rec.get("arb_id") == pos.arb_id:
                        updated = self._pos_to_record(pos)
                        if extra:
                            updated.update(extra)
                        lines.append(json.dumps(updated))
                    else:
                        lines.append(line)
            with open(TRADES_FILE, "w") as f:
                f.write("\n".join(lines) + "\n")
        except Exception:
            log.exception("Failed to update trade in file")

    def _load_all_trades(self) -> list[dict]:
        if not TRADES_FILE.exists():
            return []
        trades = []
        try:
            with open(TRADES_FILE) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        trades.append(json.loads(line))
        except Exception:
            log.exception("Failed to load razor trades")
        return trades

    def _load_open_positions(self) -> None:
        """Restore open positions from JSONL on startup."""
        if not TRADES_FILE.exists():
            return
        try:
            with open(TRADES_FILE) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    if rec.get("status") in ("open", "exiting"):
                        pos = ArbPosition(
                            arb_id=rec["arb_id"],
                            condition_id=rec["condition_id"],
                            question=rec.get("question", ""),
                            token_a_id=rec["token_a_id"],
                            token_b_id=rec["token_b_id"],
                            outcome_a=rec.get("outcome_a", "A"),
                            outcome_b=rec.get("outcome_b", "B"),
                            ask_a=rec.get("ask_a", 0),
                            ask_b=rec.get("ask_b", 0),
                            combined_cost=rec.get("combined_cost", 0),
                            shares=rec.get("shares", 0),
                            position_usd=rec.get("position_usd", 0),
                            expected_profit=rec.get("expected_profit", 0),
                            order_a=rec.get("order_a", ""),
                            order_b=rec.get("order_b", ""),
                            status=rec.get("status", "open"),
                            entry_time=rec.get("entry_time", rec.get("timestamp", 0)),
                            exit_recovery=rec.get("exit_recovery", 0),
                            dry_run=rec.get("dry_run", True),
                        )
                        self.positions.append(pos)
            if self.positions:
                log.info("Restored %d open arb positions from disk", len(self.positions))
        except Exception:
            log.exception("Failed to restore open positions")
