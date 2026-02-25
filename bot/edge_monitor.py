"""Garves V2 — Edge Monitor.

Tracks indicator edge decay over time. Detects when edges are
shrinking (competitive pressure) and flags indicators losing accuracy.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
TRADES_FILE = DATA_DIR / "trades.jsonl"
INDICATOR_ACCURACY_FILE = DATA_DIR / "indicator_accuracy.json"


class EdgeMonitor:
    """Monitors edge health and competitive decay.

    Usage:
        monitor = EdgeMonitor()
        decay = monitor.check_edge_decay()
        competitive = monitor.weekly_competitive_check()
    """

    def check_edge_decay(self) -> list[dict]:
        """Compare last-20-trade indicator accuracy vs all-time.

        Flag indicators where accuracy dropped >10 percentage points.
        """
        if not INDICATOR_ACCURACY_FILE.exists():
            return []

        try:
            acc_data = json.loads(INDICATOR_ACCURACY_FILE.read_text())
        except Exception:
            return []

        resolved = self._load_resolved()
        if len(resolved) < 30:
            return []

        # Calculate recent accuracy per indicator from last 20 trades
        recent = resolved[-20:]
        recent_accuracy: dict[str, dict] = {}

        for trade in recent:
            votes = trade.get("indicator_votes", {})
            outcome = trade.get("outcome", "")
            if outcome not in ("up", "down"):
                continue
            for name, vote_data in votes.items():
                vote_dir = vote_data if isinstance(vote_data, str) else vote_data.get("direction", "")
                correct = (vote_dir == outcome)
                if name not in recent_accuracy:
                    recent_accuracy[name] = {"correct": 0, "total": 0}
                recent_accuracy[name]["total"] += 1
                if correct:
                    recent_accuracy[name]["correct"] += 1

        # Compare with all-time
        decaying = []
        for name, recent_stats in recent_accuracy.items():
            if recent_stats["total"] < 5:
                continue

            recent_acc = recent_stats["correct"] / recent_stats["total"]
            alltime_data = acc_data.get(name, {})
            alltime_acc = alltime_data.get("accuracy", 0.5)
            alltime_total = alltime_data.get("total_votes", 0)

            if alltime_total < 20:
                continue

            drop = alltime_acc - recent_acc
            if drop > 0.10:  # >10pp accuracy drop
                decaying.append({
                    "indicator": name,
                    "alltime_accuracy": round(alltime_acc, 3),
                    "recent_accuracy": round(recent_acc, 3),
                    "drop_pp": round(drop * 100, 1),
                    "alltime_samples": alltime_total,
                    "recent_samples": recent_stats["total"],
                    "severity": "high" if drop > 0.20 else "medium",
                })
                log.warning(
                    "[EDGE DECAY] %s: %.0f%% → %.0f%% (-%0.fpp)",
                    name, alltime_acc * 100, recent_acc * 100, drop * 100,
                )

        return sorted(decaying, key=lambda d: d["drop_pp"], reverse=True)

    def weekly_competitive_check(self) -> dict:
        """Check which edges are shrinking vs growing over recent history."""
        resolved = self._load_resolved()
        if len(resolved) < 40:
            return {"status": "insufficient_data", "total_trades": len(resolved)}

        # Split into two halves
        mid = len(resolved) // 2
        first_half = resolved[:mid]
        second_half = resolved[mid:]

        first_wr = sum(1 for t in first_half if t.get("won")) / len(first_half)
        second_wr = sum(1 for t in second_half if t.get("won")) / len(second_half)

        first_edge = sum(t.get("edge", 0) for t in first_half) / len(first_half)
        second_edge = sum(t.get("edge", 0) for t in second_half) / len(second_half)

        wr_trend = second_wr - first_wr
        edge_trend = second_edge - first_edge

        # By asset breakdown
        asset_trends = {}
        for asset_name in ("bitcoin", "ethereum", "solana", "xrp"):
            first_asset = [t for t in first_half if t.get("asset") == asset_name]
            second_asset = [t for t in second_half if t.get("asset") == asset_name]
            if len(first_asset) >= 5 and len(second_asset) >= 5:
                fa_wr = sum(1 for t in first_asset if t.get("won")) / len(first_asset)
                sa_wr = sum(1 for t in second_asset if t.get("won")) / len(second_asset)
                asset_trends[asset_name] = {
                    "first_half_wr": round(fa_wr, 3),
                    "second_half_wr": round(sa_wr, 3),
                    "trend": "improving" if sa_wr > fa_wr else "declining",
                }

        status = "stable"
        if wr_trend < -0.05:
            status = "declining"
        elif wr_trend > 0.05:
            status = "improving"

        return {
            "status": status,
            "total_trades": len(resolved),
            "first_half_wr": round(first_wr, 3),
            "second_half_wr": round(second_wr, 3),
            "wr_trend_pp": round(wr_trend * 100, 1),
            "first_half_avg_edge": round(first_edge * 100, 2),
            "second_half_avg_edge": round(second_edge * 100, 2),
            "edge_trend_pp": round(edge_trend * 100, 2),
            "by_asset": asset_trends,
        }

    @staticmethod
    def _load_resolved() -> list[dict]:
        """Load resolved trades from trades.jsonl."""
        if not TRADES_FILE.exists():
            return []
        resolved = []
        try:
            for line in TRADES_FILE.read_text().splitlines():
                if not line.strip():
                    continue
                t = json.loads(line)
                if t.get("resolved") and t.get("outcome") in ("up", "down"):
                    resolved.append(t)
        except Exception:
            pass
        return resolved
