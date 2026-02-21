"""Razor Learner — ML-enhanced decision making for completeness arbitrage.

Learns from every trade (executed or skipped) to get smarter over time:
- Which markets have arbs more frequently (hotspot detection)
- Time-of-day patterns (when spreads open up)
- Optimal hold times before early exit
- Spread velocity (how fast a spread closes — skip if too fast to capture)
- Market characteristics that predict profitable arbs
- Exit timing optimization (when to sell loser side)

Uses lightweight SQLite storage — no heavy ML frameworks needed.
Pure statistics + pattern recognition. The Mathematician learns from data.
"""
from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
LEARNER_DB = DATA_DIR / "razor_learner.json"
ET = ZoneInfo("America/New_York")


class RazorLearner:
    """Learns patterns from arb opportunities and trades to improve decisions."""

    def __init__(self):
        self._data = {
            "hotspots": {},       # condition_id -> count of times arb appeared
            "hourly_freq": {},    # hour (0-23) -> count of arbs seen
            "spread_history": [], # recent spreads for velocity tracking (ring buffer)
            "exit_outcomes": [],  # exit timing -> actual PnL (learn optimal exit)
            "market_traits": {},  # condition_id -> {avg_spread, avg_depth, times_seen, last_seen}
            "skip_list": {},      # condition_id -> reason (markets that consistently fail CLOB check)
            "stats": {
                "total_scanned": 0,
                "total_opportunities": 0,
                "total_executed": 0,
                "total_skipped_learner": 0,
                "best_spread_ever": 0.0,
                "last_opportunity_time": 0,
            },
        }
        self._load()

    def record_opportunity(self, condition_id: str, question: str,
                           spread: float, depth: float, hour: int) -> None:
        """Record a detected arb opportunity (pre-execution)."""
        self._data["stats"]["total_opportunities"] += 1
        self._data["stats"]["last_opportunity_time"] = time.time()
        if spread > self._data["stats"]["best_spread_ever"]:
            self._data["stats"]["best_spread_ever"] = spread

        # Hotspot tracking
        if condition_id not in self._data["hotspots"]:
            self._data["hotspots"][condition_id] = 0
        self._data["hotspots"][condition_id] += 1

        # Hourly frequency
        h = str(hour)
        if h not in self._data["hourly_freq"]:
            self._data["hourly_freq"][h] = 0
        self._data["hourly_freq"][h] += 1

        # Market traits
        if condition_id not in self._data["market_traits"]:
            self._data["market_traits"][condition_id] = {
                "question": question[:120],
                "times_seen": 0,
                "avg_spread": 0.0,
                "avg_depth": 0.0,
                "last_seen": 0,
                "spreads": [],
            }
        traits = self._data["market_traits"][condition_id]
        traits["times_seen"] += 1
        traits["last_seen"] = time.time()
        traits["spreads"] = (traits.get("spreads", []) + [spread])[-20:]  # Keep last 20
        traits["avg_spread"] = sum(traits["spreads"]) / len(traits["spreads"])
        # Running average for depth
        n = traits["times_seen"]
        traits["avg_depth"] = ((traits["avg_depth"] * (n - 1)) + depth) / n

        # Spread history (ring buffer, last 200)
        self._data["spread_history"] = (self._data["spread_history"] + [{
            "t": time.time(), "spread": spread, "cid": condition_id[:12],
        }])[-200:]

    def record_execution(self, condition_id: str, spread: float, position_usd: float) -> None:
        """Record a successful arb execution."""
        self._data["stats"]["total_executed"] += 1

    def record_exit(self, condition_id: str, hold_time_s: float,
                    exit_type: str, pnl: float) -> None:
        """Record exit outcome for learning optimal exit timing."""
        self._data["exit_outcomes"] = (self._data["exit_outcomes"] + [{
            "cid": condition_id[:12],
            "hold_s": hold_time_s,
            "exit_type": exit_type,
            "pnl": pnl,
            "time": time.time(),
        }])[-500:]

    def record_skip(self, condition_id: str, reason: str) -> None:
        """Record a skipped market (failed CLOB check, no depth, etc)."""
        if condition_id not in self._data["skip_list"]:
            self._data["skip_list"][condition_id] = {"count": 0, "reason": reason}
        self._data["skip_list"][condition_id]["count"] += 1
        self._data["stats"]["total_skipped_learner"] += 1

    def record_scan(self, markets_checked: int) -> None:
        """Record a scan cycle."""
        self._data["stats"]["total_scanned"] += markets_checked

    def should_skip(self, condition_id: str) -> bool:
        """Check if a market should be skipped based on learned patterns.

        Skip if: failed CLOB check 5+ times consecutively with no success.
        """
        skip = self._data["skip_list"].get(condition_id)
        if not skip:
            return False
        # If it's been a hotspot (arb appeared before), don't skip
        if self._data["hotspots"].get(condition_id, 0) > 0:
            return False
        return skip["count"] >= 5

    def is_hotspot(self, condition_id: str) -> bool:
        """Check if a market is a known arb hotspot (appeared 2+ times)."""
        return self._data["hotspots"].get(condition_id, 0) >= 2

    def get_hot_hours(self) -> list[int]:
        """Get hours of day with above-average arb frequency."""
        freq = self._data["hourly_freq"]
        if not freq:
            return list(range(24))
        avg = sum(freq.values()) / max(len(freq), 1)
        return [int(h) for h, c in freq.items() if c >= avg]

    def get_optimal_exit_time(self) -> float | None:
        """Estimate optimal hold time before exit based on past exits."""
        exits = self._data["exit_outcomes"]
        if len(exits) < 5:
            return None
        # Find hold time of exits with positive PnL
        good_exits = [e for e in exits if e["pnl"] > 0]
        if not good_exits:
            return None
        return sum(e["hold_s"] for e in good_exits) / len(good_exits)

    def get_spread_velocity(self) -> float:
        """Calculate how fast spreads are closing (spreads per minute).

        High velocity = spreads close fast = need to be faster.
        """
        history = self._data["spread_history"]
        if len(history) < 2:
            return 0.0
        # Look at last 10 entries
        recent = history[-10:]
        time_span = recent[-1]["t"] - recent[0]["t"]
        if time_span <= 0:
            return 0.0
        return len(recent) / (time_span / 60)

    def get_top_hotspots(self, n: int = 10) -> list[dict]:
        """Get top N markets by arb frequency."""
        hotspots = sorted(
            self._data["hotspots"].items(),
            key=lambda x: x[1],
            reverse=True,
        )[:n]
        result = []
        for cid, count in hotspots:
            traits = self._data["market_traits"].get(cid, {})
            result.append({
                "condition_id": cid,
                "question": traits.get("question", ""),
                "times_seen": count,
                "avg_spread": round(traits.get("avg_spread", 0), 4),
                "avg_depth": round(traits.get("avg_depth", 0), 0),
            })
        return result

    def summary(self) -> dict:
        """Summary stats for dashboard."""
        stats = self._data["stats"]
        return {
            "total_scanned": stats["total_scanned"],
            "total_opportunities": stats["total_opportunities"],
            "total_executed": stats["total_executed"],
            "best_spread_ever": round(stats["best_spread_ever"], 4),
            "last_opportunity_ago_s": round(time.time() - stats["last_opportunity_time"])
                if stats["last_opportunity_time"] > 0 else None,
            "hotspot_count": len(self._data["hotspots"]),
            "active_hours": self.get_hot_hours(),
            "avg_spread_velocity": round(self.get_spread_velocity(), 4),
            "optimal_exit_s": self.get_optimal_exit_time(),
            "hourly_frequency": {int(h): c for h, c in self._data["hourly_freq"].items()},
            "top_hotspots": self.get_top_hotspots(10),
        }

    def save(self) -> None:
        """Persist learner state to disk."""
        DATA_DIR.mkdir(exist_ok=True)
        try:
            LEARNER_DB.write_text(json.dumps(self._data, default=str))
        except Exception:
            log.exception("Failed to save learner data")

    def _load(self) -> None:
        """Load learner state from disk."""
        if not LEARNER_DB.exists():
            return
        try:
            loaded = json.loads(LEARNER_DB.read_text())
            # Merge loaded data into defaults (handles new fields gracefully)
            for key in self._data:
                if key in loaded:
                    self._data[key] = loaded[key]
            log.info("Learner loaded: %d hotspots, %d opportunities seen",
                     len(self._data["hotspots"]),
                     self._data["stats"]["total_opportunities"])
        except Exception:
            log.exception("Failed to load learner data")
