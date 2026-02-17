"""Post-trade analysis: what went wrong, what went right.

Hawk tracks himself — this is the answer to "Who tracks Hawk?"
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
TRADES_FILE = DATA_DIR / "hawk_trades.jsonl"
REVIEWS_FILE = DATA_DIR / "hawk_reviews.json"

ET = ZoneInfo("America/New_York")


def review_resolved_trades() -> dict:
    """Analyze all resolved Hawk trades and generate performance insights.

    Returns dict with:
    - win_rate_by_category, win_rate_by_risk_level, win_rate_by_edge_range
    - losing_patterns (common themes in losses)
    - calibration_score (how close GPT-4o estimates are to outcomes)
    - recommendations (parameter adjustment suggestions)
    """
    trades = _load_all_trades()
    resolved = [t for t in trades if t.get("resolved") and t.get("outcome")]

    if not resolved:
        return {"total_reviewed": 0}

    wins = [t for t in resolved if t.get("won")]
    losses = [t for t in resolved if not t.get("won")]

    # Win rate by category
    wr_by_cat: dict[str, dict] = {}
    for t in resolved:
        cat = t.get("category", "other")
        if cat not in wr_by_cat:
            wr_by_cat[cat] = {"wins": 0, "losses": 0, "pnl": 0.0}
        if t.get("won"):
            wr_by_cat[cat]["wins"] += 1
        else:
            wr_by_cat[cat]["losses"] += 1
        wr_by_cat[cat]["pnl"] += t.get("pnl", 0)
    for cat in wr_by_cat:
        total = wr_by_cat[cat]["wins"] + wr_by_cat[cat]["losses"]
        wr_by_cat[cat]["win_rate"] = round(wr_by_cat[cat]["wins"] / total * 100, 1) if total else 0
        wr_by_cat[cat]["pnl"] = round(wr_by_cat[cat]["pnl"], 2)

    # Win rate by risk level
    wr_by_risk: dict[str, dict] = {}
    for t in resolved:
        rs = t.get("risk_score", 5)
        bucket = "low (1-3)" if rs <= 3 else "medium (4-6)" if rs <= 6 else "high (7-10)"
        if bucket not in wr_by_risk:
            wr_by_risk[bucket] = {"wins": 0, "losses": 0, "pnl": 0.0}
        if t.get("won"):
            wr_by_risk[bucket]["wins"] += 1
        else:
            wr_by_risk[bucket]["losses"] += 1
        wr_by_risk[bucket]["pnl"] += t.get("pnl", 0)
    for bucket in wr_by_risk:
        total = wr_by_risk[bucket]["wins"] + wr_by_risk[bucket]["losses"]
        wr_by_risk[bucket]["win_rate"] = round(wr_by_risk[bucket]["wins"] / total * 100, 1) if total else 0
        wr_by_risk[bucket]["pnl"] = round(wr_by_risk[bucket]["pnl"], 2)

    # Win rate by edge range
    wr_by_edge: dict[str, dict] = {}
    for t in resolved:
        edge = t.get("edge", 0)
        bucket = "7-10%" if edge < 0.10 else "10-15%" if edge < 0.15 else "15-20%" if edge < 0.20 else "20%+"
        if bucket not in wr_by_edge:
            wr_by_edge[bucket] = {"wins": 0, "losses": 0, "pnl": 0.0}
        if t.get("won"):
            wr_by_edge[bucket]["wins"] += 1
        else:
            wr_by_edge[bucket]["losses"] += 1
        wr_by_edge[bucket]["pnl"] += t.get("pnl", 0)
    for bucket in wr_by_edge:
        total = wr_by_edge[bucket]["wins"] + wr_by_edge[bucket]["losses"]
        wr_by_edge[bucket]["win_rate"] = round(wr_by_edge[bucket]["wins"] / total * 100, 1) if total else 0
        wr_by_edge[bucket]["pnl"] = round(wr_by_edge[bucket]["pnl"], 2)

    # Calibration: how close estimated_prob was to actual outcome
    calibration_errors = []
    for t in resolved:
        est_prob = t.get("estimated_prob", 0.5)
        actual = 1.0 if t.get("outcome") == "yes" else 0.0
        calibration_errors.append(abs(est_prob - actual))
    avg_calibration = round(sum(calibration_errors) / len(calibration_errors), 3) if calibration_errors else 0

    # Direction analysis
    direction_stats: dict[str, dict] = {}
    for t in resolved:
        d = t.get("direction", "unknown")
        if d not in direction_stats:
            direction_stats[d] = {"wins": 0, "losses": 0}
        if t.get("won"):
            direction_stats[d]["wins"] += 1
        else:
            direction_stats[d]["losses"] += 1

    # Losing patterns
    losing_categories = [t.get("category", "other") for t in losses]
    losing_directions = [t.get("direction", "?") for t in losses]
    losing_avg_edge = round(sum(t.get("edge", 0) for t in losses) / len(losses), 3) if losses else 0

    # Recommendations
    recommendations = []
    overall_wr = len(wins) / len(resolved) * 100 if resolved else 0

    if overall_wr < 50:
        recommendations.append("Win rate below 50% — consider raising min_edge threshold")
    if avg_calibration > 0.35:
        recommendations.append("GPT-4o calibration poor (>0.35 avg error) — consider using news enrichment more aggressively")

    # Check if NO-heavy bias
    no_count = sum(1 for t in resolved if t.get("direction") == "no")
    if no_count > len(resolved) * 0.75:
        recommendations.append("Heavy NO bias (>75%) — GPT may be too contrarian, review system prompt")

    # Check category weakness
    for cat, stats in wr_by_cat.items():
        if stats["win_rate"] < 30 and (stats["wins"] + stats["losses"]) >= 3:
            recommendations.append(f"Category '{cat}' has <30% win rate — consider excluding")

    review_data = {
        "total_reviewed": len(resolved),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(overall_wr, 1),
        "total_pnl": round(sum(t.get("pnl", 0) for t in resolved), 2),
        "win_rate_by_category": wr_by_cat,
        "win_rate_by_risk_level": wr_by_risk,
        "win_rate_by_edge_range": wr_by_edge,
        "direction_stats": direction_stats,
        "calibration_score": avg_calibration,
        "losing_patterns": {
            "avg_edge_on_losses": losing_avg_edge,
            "top_losing_categories": _top_counts(losing_categories),
            "direction_bias": _top_counts(losing_directions),
        },
        "recommendations": recommendations,
        "reviewed_at": datetime.now(ET).isoformat(),

        # Individual trade reviews
        "trade_reviews": [generate_trade_report(t) for t in resolved[-20:]],
    }

    # Save to file
    try:
        DATA_DIR.mkdir(exist_ok=True)
        REVIEWS_FILE.write_text(json.dumps(review_data, indent=2))
        log.info("Hawk reviewer: %d trades analyzed, %.1f%% win rate, calibration=%.3f",
                 len(resolved), overall_wr, avg_calibration)
    except Exception:
        log.exception("Failed to save hawk reviews")

    return review_data


def generate_trade_report(trade: dict) -> dict:
    """Human-readable post-mortem for a single resolved trade."""
    won = trade.get("won", False)
    direction = trade.get("direction", "?")
    outcome = trade.get("outcome", "?")
    est_prob = trade.get("estimated_prob", 0.5)
    entry = trade.get("entry_price", 0.5)
    edge = trade.get("edge", 0)
    pnl = trade.get("pnl", 0)
    risk = trade.get("risk_score", 5)

    # Was reasoning correct?
    if direction == outcome:
        direction_correct = True
        verdict = "Correct call"
    else:
        direction_correct = False
        verdict = f"Wrong call — bet {direction.upper()}, resolved {outcome.upper()}"

    # Probability calibration
    actual = 1.0 if outcome == "yes" else 0.0
    prob_error = abs(est_prob - actual)
    if prob_error < 0.15:
        calibration = "excellent"
    elif prob_error < 0.30:
        calibration = "decent"
    else:
        calibration = "poor"

    return {
        "question": trade.get("question", "")[:150],
        "category": trade.get("category", "other"),
        "direction": direction,
        "outcome": outcome,
        "won": won,
        "pnl": round(pnl, 2),
        "edge": round(edge, 4),
        "estimated_prob": est_prob,
        "entry_price": entry,
        "risk_score": risk,
        "verdict": verdict,
        "direction_correct": direction_correct,
        "prob_calibration": calibration,
        "prob_error": round(prob_error, 3),
        "reasoning": trade.get("reasoning", "")[:200],
        "edge_source": trade.get("edge_source", ""),
        "time_str": trade.get("time_str", ""),
    }


def _top_counts(items: list[str], top_n: int = 3) -> list[dict]:
    """Count occurrences and return top N."""
    counts: dict[str, int] = {}
    for item in items:
        counts[item] = counts.get(item, 0) + 1
    sorted_items = sorted(counts.items(), key=lambda x: x[1], reverse=True)
    return [{"item": k, "count": v} for k, v in sorted_items[:top_n]]


def _load_all_trades() -> list[dict]:
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
        log.exception("Failed to load trades for review")
    return trades
