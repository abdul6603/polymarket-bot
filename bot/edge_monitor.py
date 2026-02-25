"""Garves V2 — Edge Monitor.

Tracks indicator edge decay over time. Detects when edges are
shrinking (competitive pressure) and flags indicators losing accuracy.
Weekly competitive advantage check runs every Sunday.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
TRADES_FILE = DATA_DIR / "trades.jsonl"
INDICATOR_ACCURACY_FILE = DATA_DIR / "indicator_accuracy.json"
WEEKLY_REPORT_FILE = DATA_DIR / "weekly_competitive_report.json"


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
        """Full weekly competitive advantage check.

        Answers three questions every Sunday:
        1. Is our edge disappearing?
        2. What new inefficiency appeared?
        3. What can be automated?
        """
        resolved = self._load_resolved()
        if len(resolved) < 40:
            return {"status": "insufficient_data", "total_trades": len(resolved)}

        # Split into two halves for trend comparison
        mid = len(resolved) // 2
        first_half = resolved[:mid]
        second_half = resolved[mid:]

        first_wr = sum(1 for t in first_half if t.get("won")) / len(first_half)
        second_wr = sum(1 for t in second_half if t.get("won")) / len(second_half)

        first_edge = sum(t.get("edge", 0) for t in first_half) / len(first_half)
        second_edge = sum(t.get("edge", 0) for t in second_half) / len(second_half)

        wr_trend = second_wr - first_wr
        edge_trend = second_edge - first_edge

        # 1. Edge health by asset
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

        # 2. Inefficiency detection — find exploitable patterns
        inefficiencies = self._detect_inefficiencies(resolved)

        # 3. Per-indicator edge health
        indicator_health = self._indicator_edge_health(resolved)

        # 4. EV capture trend
        ev_trend = self._ev_capture_trend(first_half, second_half)

        # 5. Actionable recommendations
        recommendations = self._generate_recommendations(
            wr_trend, edge_trend, asset_trends, inefficiencies, indicator_health, ev_trend
        )

        status = "stable"
        if wr_trend < -0.05:
            status = "declining"
        elif wr_trend > 0.05:
            status = "improving"

        report = {
            "status": status,
            "timestamp": time.time(),
            "total_trades": len(resolved),
            "first_half_wr": round(first_wr, 3),
            "second_half_wr": round(second_wr, 3),
            "wr_trend_pp": round(wr_trend * 100, 1),
            "first_half_avg_edge": round(first_edge * 100, 2),
            "second_half_avg_edge": round(second_edge * 100, 2),
            "edge_trend_pp": round(edge_trend * 100, 2),
            "by_asset": asset_trends,
            "inefficiencies": inefficiencies,
            "indicator_health": indicator_health,
            "ev_capture_trend": ev_trend,
            "recommendations": recommendations,
        }

        # Persist weekly report
        try:
            WEEKLY_REPORT_FILE.write_text(json.dumps(report, indent=2))
        except Exception:
            pass

        return report

    @staticmethod
    def _detect_inefficiencies(resolved: list[dict]) -> list[dict]:
        """Find exploitable patterns in recent trades."""
        inefficiencies = []
        recent = resolved[-50:] if len(resolved) >= 50 else resolved

        # Pattern 1: Time-of-day edge — which hours win most
        hour_stats: dict[int, dict] = {}
        for t in recent:
            ts = t.get("timestamp", 0)
            if ts <= 0:
                continue
            h = datetime.fromtimestamp(ts, tz=ZoneInfo("America/New_York")).hour
            if h not in hour_stats:
                hour_stats[h] = {"wins": 0, "total": 0}
            hour_stats[h]["total"] += 1
            if t.get("won"):
                hour_stats[h]["wins"] += 1

        for h, stats in hour_stats.items():
            if stats["total"] >= 5:
                wr = stats["wins"] / stats["total"]
                if wr >= 0.70:
                    inefficiencies.append({
                        "type": "time_of_day",
                        "detail": f"Hour {h}:00 ET has {wr:.0%} WR ({stats['total']} trades)",
                        "action": f"Increase sizing at hour {h}",
                        "confidence": min(0.9, 0.5 + stats["total"] * 0.02),
                    })
                elif wr <= 0.30 and stats["total"] >= 5:
                    inefficiencies.append({
                        "type": "time_of_day_avoid",
                        "detail": f"Hour {h}:00 ET has {wr:.0%} WR ({stats['total']} trades)",
                        "action": f"Reduce sizing or skip hour {h}",
                        "confidence": min(0.9, 0.5 + stats["total"] * 0.02),
                    })

        # Pattern 2: Regime-specific edge
        regime_stats: dict[str, dict] = {}
        for t in recent:
            r = t.get("regime_label", "unknown")
            if r not in regime_stats:
                regime_stats[r] = {"wins": 0, "total": 0}
            regime_stats[r]["total"] += 1
            if t.get("won"):
                regime_stats[r]["wins"] += 1

        for regime, stats in regime_stats.items():
            if stats["total"] >= 5:
                wr = stats["wins"] / stats["total"]
                if wr >= 0.70:
                    inefficiencies.append({
                        "type": "regime_edge",
                        "detail": f"Regime '{regime}' has {wr:.0%} WR ({stats['total']} trades)",
                        "action": f"Increase confidence multiplier in {regime}",
                        "confidence": min(0.9, 0.5 + stats["total"] * 0.02),
                    })

        # Pattern 3: Spread-based edge — do we win more when spreads are tight?
        with_spread = [t for t in recent if t.get("ob_spread", 0) > 0]
        if len(with_spread) >= 10:
            median_spread = sorted(t["ob_spread"] for t in with_spread)[len(with_spread) // 2]
            tight = [t for t in with_spread if t["ob_spread"] <= median_spread]
            wide = [t for t in with_spread if t["ob_spread"] > median_spread]
            if tight and wide:
                tight_wr = sum(1 for t in tight if t.get("won")) / len(tight)
                wide_wr = sum(1 for t in wide if t.get("won")) / len(wide)
                if tight_wr > wide_wr + 0.15:
                    inefficiencies.append({
                        "type": "spread_edge",
                        "detail": f"Tight spread WR={tight_wr:.0%} vs wide={wide_wr:.0%}",
                        "action": "Tighten max_spread filter in market quality",
                        "confidence": 0.7,
                    })

        return inefficiencies

    @staticmethod
    def _indicator_edge_health(resolved: list[dict]) -> list[dict]:
        """Per-indicator accuracy in recent vs all-time."""
        if len(resolved) < 30:
            return []

        recent = resolved[-20:]
        all_acc: dict[str, dict] = {}
        recent_acc: dict[str, dict] = {}

        # All-time accuracy
        for t in resolved:
            votes = t.get("indicator_votes", {})
            outcome = t.get("outcome", "")
            if outcome not in ("up", "down"):
                continue
            for name, vote_data in votes.items():
                vote_dir = vote_data if isinstance(vote_data, str) else vote_data.get("direction", "")
                if name not in all_acc:
                    all_acc[name] = {"correct": 0, "total": 0}
                all_acc[name]["total"] += 1
                if vote_dir == outcome:
                    all_acc[name]["correct"] += 1

        # Recent accuracy
        for t in recent:
            votes = t.get("indicator_votes", {})
            outcome = t.get("outcome", "")
            if outcome not in ("up", "down"):
                continue
            for name, vote_data in votes.items():
                vote_dir = vote_data if isinstance(vote_data, str) else vote_data.get("direction", "")
                if name not in recent_acc:
                    recent_acc[name] = {"correct": 0, "total": 0}
                recent_acc[name]["total"] += 1
                if vote_dir == outcome:
                    recent_acc[name]["correct"] += 1

        health = []
        for name in all_acc:
            at = all_acc[name]
            rt = recent_acc.get(name, {"correct": 0, "total": 0})
            if at["total"] < 10:
                continue
            at_pct = at["correct"] / at["total"]
            rt_pct = rt["correct"] / rt["total"] if rt["total"] >= 5 else None
            status = "healthy"
            if rt_pct is not None:
                if rt_pct < at_pct - 0.10:
                    status = "decaying"
                elif rt_pct > at_pct + 0.10:
                    status = "improving"
            health.append({
                "indicator": name,
                "alltime_accuracy": round(at_pct, 3),
                "recent_accuracy": round(rt_pct, 3) if rt_pct is not None else None,
                "alltime_samples": at["total"],
                "recent_samples": rt["total"],
                "status": status,
            })

        return sorted(health, key=lambda h: h.get("recent_accuracy") or 0)

    @staticmethod
    def _ev_capture_trend(first_half: list[dict], second_half: list[dict]) -> dict:
        """Compare EV capture between two halves."""
        def _ev_cap(trades: list[dict]) -> float:
            ev_pred = sum(t.get("ev_predicted", 0) for t in trades)
            actual = sum(t.get("pnl", 0) for t in trades)
            if ev_pred > 0:
                return actual / ev_pred
            return 0.0

        first_ev = _ev_cap(first_half)
        second_ev = _ev_cap(second_half)
        return {
            "first_half_ev_capture": round(first_ev, 3),
            "second_half_ev_capture": round(second_ev, 3),
            "trend": "improving" if second_ev > first_ev else "declining",
        }

    @staticmethod
    def _generate_recommendations(
        wr_trend: float, edge_trend: float, asset_trends: dict,
        inefficiencies: list, indicator_health: list, ev_trend: dict,
    ) -> list[dict]:
        """Generate actionable recommendations based on all checks."""
        recs = []

        # WR declining
        if wr_trend < -0.05:
            recs.append({
                "priority": "high",
                "action": "Raise edge floor by 2-3pp or tighten conviction threshold",
                "reason": f"WR declining by {abs(wr_trend)*100:.1f}pp",
            })

        # Edge shrinking
        if edge_trend < -0.02:
            recs.append({
                "priority": "high",
                "action": "Markets becoming more efficient — look for new inefficiencies",
                "reason": f"Avg edge shrinking by {abs(edge_trend)*100:.2f}pp",
            })

        # Underperforming assets
        for asset, data in asset_trends.items():
            if data.get("trend") == "declining" and data.get("second_half_wr", 1) < 0.45:
                recs.append({
                    "priority": "medium",
                    "action": f"Consider reducing {asset} exposure or disabling temporarily",
                    "reason": f"{asset} WR dropped to {data['second_half_wr']:.0%}",
                })

        # Decaying indicators
        decaying = [h for h in indicator_health if h.get("status") == "decaying"]
        if decaying:
            names = ", ".join(h["indicator"] for h in decaying[:3])
            recs.append({
                "priority": "medium",
                "action": f"Review indicator weights for: {names}",
                "reason": "Recent accuracy dropped >10pp vs all-time",
            })

        # EV capture declining
        if ev_trend.get("trend") == "declining" and ev_trend.get("second_half_ev_capture", 1) < 0.3:
            recs.append({
                "priority": "high",
                "action": "Execution quality degrading — check spread limits and order pricing",
                "reason": f"EV capture dropped to {ev_trend['second_half_ev_capture']:.0%}",
            })

        # Exploitable inefficiencies
        for ineff in inefficiencies:
            if ineff.get("confidence", 0) >= 0.7:
                recs.append({
                    "priority": "low",
                    "action": ineff.get("action", ""),
                    "reason": ineff.get("detail", ""),
                })

        if not recs:
            recs.append({
                "priority": "info",
                "action": "Edge is stable — continue current strategy",
                "reason": "No significant changes detected",
            })

        return recs

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
