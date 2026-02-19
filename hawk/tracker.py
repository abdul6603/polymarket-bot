"""Position + P&L + Category Tracker for Hawk V2."""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from hawk.edge import TradeOpportunity

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
TRADES_FILE = DATA_DIR / "hawk_trades.jsonl"

ET = ZoneInfo("America/New_York")


class HawkTracker:
    """Track open positions, trade history, and per-category stats."""

    def __init__(self):
        DATA_DIR.mkdir(exist_ok=True)
        self._positions: list[dict] = []
        self._decision_ids: dict[str, str] = {}
        self._load_positions()

    def _load_positions(self) -> None:
        """Load unresolved trades from disk on startup."""
        if not TRADES_FILE.exists():
            return
        try:
            with open(TRADES_FILE) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    if not rec.get("resolved"):
                        self._positions.append(rec)
            log.info("Loaded %d open Hawk positions", len(self._positions))
        except Exception:
            log.exception("Failed to load Hawk trade history")

    @property
    def open_positions(self) -> list[dict]:
        return list(self._positions)

    @property
    def total_exposure(self) -> float:
        return sum(p.get("size_usd", 0) for p in self._positions)

    @property
    def count(self) -> int:
        return len(self._positions)

    def has_position_for_market(self, condition_id: str) -> bool:
        """Check by condition_id (V2 standardized) with fallback to market_id."""
        return any(
            p.get("condition_id") == condition_id or p.get("market_id") == condition_id
            for p in self._positions
        )

    def record_trade(self, opp: TradeOpportunity, order_id: str) -> None:
        """Append trade to JSONL and track in memory."""
        if self.has_position_for_market(opp.market.condition_id):
            log.warning("Duplicate trade blocked: already have position for %s", opp.market.condition_id[:12])
            return
        rec = {
            "trade_id": f"hawk_{opp.market.condition_id[:8]}_{int(time.time())}",
            "order_id": order_id,
            "condition_id": opp.market.condition_id,
            "question": opp.market.question[:200],
            "category": opp.market.category,
            "direction": opp.direction,
            "token_id": opp.token_id,
            "size_usd": opp.position_size_usd,
            "entry_price": _get_price(opp),
            "edge": opp.edge,
            "estimated_prob": opp.estimate.estimated_prob,
            "confidence": opp.estimate.confidence,
            "reasoning": opp.estimate.reasoning[:500],
            "kelly_fraction": opp.kelly_fraction,
            "expected_value": opp.expected_value,
            # V2 new fields
            "risk_score": opp.risk_score,
            "edge_source": opp.estimate.edge_source,
            "time_left_hours": opp.time_left_hours,
            "urgency_label": opp.urgency_label,
            "money_thesis": opp.estimate.money_thesis[:300],
            "news_factor": opp.estimate.news_factor[:300],
            # Timestamps
            "timestamp": time.time(),
            "opened_at": time.time(),
            "time_str": datetime.now(ET).strftime("%Y-%m-%d %I:%M%p"),
            # Resolution fields
            "resolved": False,
            "outcome": "",
            "won": False,
            "resolve_time": 0.0,
            "pnl": 0.0,
        }
        self._positions.append(rec)
        self._append_to_file(rec)
        log.info(
            "Tracked Hawk trade: %s %s | $%.2f | edge=%.1f%% | risk=%d/10 | %s | %s",
            opp.direction.upper(), opp.market.condition_id[:12],
            opp.position_size_usd, opp.edge * 100,
            opp.risk_score, opp.urgency_label or "no-urgency", opp.market.category,
        )

    def set_decision_id(self, condition_id: str, decision_id: str) -> None:
        """Map a condition_id to a brain decision_id for outcome tracking."""
        self._decision_ids[condition_id] = decision_id

    def get_decision_id(self, condition_id: str) -> str:
        """Get brain decision_id for a condition_id, or empty string."""
        return self._decision_ids.get(condition_id, "")

    def remove_position(self, order_id: str) -> None:
        """Remove a position by order ID."""
        self._positions = [p for p in self._positions if p.get("order_id") != order_id]

    def cumulative_pnl(self) -> float:
        """Total realized P&L across all resolved trades (for compound bankroll)."""
        all_trades = self._load_all_trades()
        return sum(t.get("pnl", 0) for t in all_trades if t.get("resolved"))

    def category_stats(self) -> dict:
        """Returns {category: {wins, losses, pnl, win_rate}} for heatmap."""
        all_trades = self._load_all_trades()
        resolved = [t for t in all_trades if t.get("resolved")]
        cats: dict[str, dict] = {}
        for t in resolved:
            cat = t.get("category", "other")
            if cat not in cats:
                cats[cat] = {"wins": 0, "losses": 0, "pnl": 0.0}
            if t.get("won"):
                cats[cat]["wins"] += 1
            else:
                cats[cat]["losses"] += 1
            cats[cat]["pnl"] += t.get("pnl", 0.0)
        for cat in cats:
            total = cats[cat]["wins"] + cats[cat]["losses"]
            cats[cat]["win_rate"] = (cats[cat]["wins"] / total * 100) if total > 0 else 0
        return cats

    def summary(self) -> dict:
        """Overall stats for dashboard."""
        all_trades = self._load_all_trades()
        resolved = [t for t in all_trades if t.get("resolved") and t.get("outcome")]
        wins = sum(1 for t in resolved if t.get("won"))
        losses = len(resolved) - wins
        total_pnl = sum(t.get("pnl", 0) for t in resolved)
        wr = (wins / len(resolved) * 100) if resolved else 0
        return {
            "total_trades": len(all_trades),
            "resolved": len(resolved),
            "wins": wins,
            "losses": losses,
            "win_rate": round(wr, 1),
            "pnl": round(total_pnl, 2),
            "open_positions": self.count,
            "total_exposure": round(self.total_exposure, 2),
            "daily_pnl": self._daily_pnl(resolved),
            "cumulative_pnl": round(total_pnl, 2),
        }

    def _daily_pnl(self, resolved: list[dict]) -> float:
        """Calculate today's P&L."""
        today = datetime.now(ET).strftime("%Y-%m-%d")
        daily = [t for t in resolved if t.get("time_str", "").startswith(today)]
        return round(sum(t.get("pnl", 0) for t in daily), 2)

    def _load_all_trades(self) -> list[dict]:
        """Load all trades from JSONL file."""
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
            log.exception("Failed to load Hawk trades")
        return trades

    def _append_to_file(self, rec: dict) -> None:
        """Append a single trade record to the JSONL file."""
        try:
            with open(TRADES_FILE, "a") as f:
                f.write(json.dumps(rec) + "\n")
        except Exception:
            log.exception("Failed to write Hawk trade record")


_YES_OUTCOMES = {"yes", "up", "over"}
_NO_OUTCOMES = {"no", "down", "under"}


def _get_price(opp: TradeOpportunity) -> float:
    """Get entry price for the trade direction (handles Over/Under/team outcomes)."""
    target = _YES_OUTCOMES if opp.direction == "yes" else _NO_OUTCOMES
    for t in opp.market.tokens:
        tok_outcome = (t.get("outcome") or "").lower()
        if tok_outcome in target:
            try:
                return max(0.01, min(0.99, float(t.get("price", 0.5))))
            except (ValueError, TypeError):
                return 0.5
    # Fallback: first token = yes, second = no
    tokens = opp.market.tokens
    if len(tokens) == 2:
        idx = 0 if opp.direction == "yes" else 1
        try:
            return max(0.01, min(0.99, float(tokens[idx].get("price", 0.5))))
        except (ValueError, TypeError):
            return 0.5
    return 0.5
