"""Post-trade analysis: what went wrong, what went right.

Hawk tracks himself — this is the answer to "Who tracks Hawk?"
"""
from __future__ import annotations

import itertools
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
        # Won means our estimated direction was correct
        # For YES bets: won=True means event happened, actual_prob=1.0
        # For NO bets: won=True means event didn't happen, actual_prob for our bet=1.0
        actual = 1.0 if t.get("won") else 0.0
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

    # Enhanced analytics (Phase 1 — Win Rate Recovery)
    try:
        from hawk.learner import _load_accuracy
        dim_accuracy = _load_accuracy()
    except Exception:
        dim_accuracy = {}
    try:
        from hawk.clv import get_clv_stats
        clv_stats = get_clv_stats()
    except Exception:
        clv_stats = {}

    review_data["edge_source_effectiveness"] = analyze_edge_source_effectiveness(trades)
    review_data["calibration_curve"] = compute_calibration_curve(trades)
    review_data["failure_patterns"] = identify_failure_patterns(trades)
    review_data["dynamic_recommendations"] = generate_dynamic_recommendations(
        trades, dim_accuracy, clv_stats
    )

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
    if won:
        direction_correct = True
        verdict = "Correct call"
    else:
        direction_correct = False
        verdict = f"Wrong call — bet {direction.upper()}, lost"

    # Probability calibration
    actual = 1.0 if won else 0.0
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


def generate_dynamic_recommendations(
    trades: list[dict],
    dim_accuracy: dict | None = None,
    clv_stats: dict | None = None,
) -> list[dict]:
    """Actionable recs with type/severity/evidence/suggested_value."""
    resolved = [t for t in trades if t.get("resolved") and t.get("outcome")]
    if not resolved:
        return []

    recs = []
    wins = sum(1 for t in resolved if t.get("won"))
    overall_wr = wins / len(resolved) * 100 if resolved else 0

    # 1. Overall win rate check
    if overall_wr < 40:
        recs.append({
            "type": "min_edge", "severity": "high",
            "message": f"Overall WR {overall_wr:.1f}% — raise min_edge threshold",
            "evidence": f"{wins}W/{len(resolved)-wins}L from {len(resolved)} trades",
            "suggested_value": "min_edge +0.03",
        })

    # 2. Per-category recommendations from dim_accuracy
    if dim_accuracy:
        cat_data = dim_accuracy.get("category", {})
        for cat, stats in cat_data.items():
            total = stats.get("total", 0)
            acc = stats.get("accuracy", 0)
            if total >= 5 and acc < 0.25:
                recs.append({
                    "type": "disable_category", "severity": "critical",
                    "message": f"Category '{cat}' toxic: {acc*100:.0f}% accuracy over {total} trades",
                    "evidence": f"{stats.get('wins',0)}W/{stats.get('losses',0)}L",
                    "suggested_value": f"disable {cat}",
                })
            elif total >= 3 and acc < 0.35:
                recs.append({
                    "type": "raise_min_edge", "severity": "medium",
                    "message": f"Category '{cat}' underperforming: {acc*100:.0f}% accuracy",
                    "evidence": f"{stats.get('wins',0)}W/{stats.get('losses',0)}L over {total} trades",
                    "suggested_value": f"min_edge +0.02 for {cat}",
                })

    # 3. Edge source recommendations
    src_data = dim_accuracy.get("edge_source", {}) if dim_accuracy else {}
    for src, stats in src_data.items():
        total = stats.get("total", 0)
        acc = stats.get("accuracy", 0)
        if total >= 5 and acc > 0.60:
            recs.append({
                "type": "boost_source", "severity": "low",
                "message": f"Edge source '{src}' strong: {acc*100:.0f}% accuracy — lower min_edge",
                "evidence": f"{stats.get('wins',0)}W/{stats.get('losses',0)}L over {total} trades",
                "suggested_value": f"min_edge -0.02 for {src}",
            })

    # 4. CLV-based recommendations
    if clv_stats and clv_stats.get("resolved", 0) >= 3:
        avg_clv = clv_stats.get("avg_clv", 0)
        pos_rate = clv_stats.get("positive_clv_rate", 0)
        if avg_clv < -0.05:
            recs.append({
                "type": "entry_timing", "severity": "high",
                "message": f"Negative CLV ({avg_clv:+.4f}) — entries consistently worse than closing line",
                "evidence": f"Positive CLV rate: {pos_rate:.0f}%",
                "suggested_value": "use limit orders / improve entry timing",
            })
        elif avg_clv > 0.03:
            recs.append({
                "type": "sizing", "severity": "low",
                "message": f"Positive CLV ({avg_clv:+.4f}) — good entries, can increase sizing",
                "evidence": f"Positive CLV rate: {pos_rate:.0f}%",
                "suggested_value": "kelly_fraction +0.02",
            })

    # 5. Calibration check
    calibration_errors = []
    for t in resolved:
        ep = t.get("estimated_prob", 0.5)
        actual = 1.0 if t.get("won") else 0.0
        calibration_errors.append(ep - actual)
    avg_bias = sum(calibration_errors) / len(calibration_errors) if calibration_errors else 0
    if avg_bias > 0.15:
        recs.append({
            "type": "calibration", "severity": "medium",
            "message": f"Overconfident bias ({avg_bias:+.3f}) — model estimates too high",
            "evidence": f"Avg (est_prob - actual) = {avg_bias:+.3f} across {len(resolved)} trades",
            "suggested_value": "increase news_enrichment weight or add contrarian discount",
        })

    # Sort by severity
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    recs.sort(key=lambda x: severity_order.get(x.get("severity", "low"), 4))
    return recs


def identify_failure_patterns(trades: list[dict]) -> list[dict]:
    """Cross-correlate (category, direction, edge_source) combos, flag toxic ones."""
    resolved = [t for t in trades if t.get("resolved") and t.get("outcome")]
    if not resolved:
        return []

    # Build combos for each trade
    trade_combos: list[tuple[dict, list[tuple[str, ...]]]] = []
    for t in resolved:
        cat = t.get("category", "unknown") or "unknown"
        direction = t.get("direction", "unknown") or "unknown"
        src = t.get("edge_source", "unknown") or "unknown"
        dims = {"category": cat, "direction": direction, "edge_source": src}
        # Generate all 2-dim and 3-dim combos
        keys = list(dims.keys())
        combos = []
        for r in (2, 3):
            for combo_keys in itertools.combinations(keys, r):
                combo = tuple((k, dims[k]) for k in combo_keys)
                combos.append(combo)
        trade_combos.append((t, combos))

    # Count wins/losses per combo
    combo_stats: dict[tuple, dict] = {}
    for t, combos in trade_combos:
        for combo in combos:
            if combo not in combo_stats:
                combo_stats[combo] = {"wins": 0, "losses": 0, "total_pnl": 0.0}
            if t.get("won"):
                combo_stats[combo]["wins"] += 1
            else:
                combo_stats[combo]["losses"] += 1
            combo_stats[combo]["total_pnl"] += t.get("pnl", 0)

    # Flag toxic combos: <25% WR with 3+ trades
    toxic = []
    for combo, stats in combo_stats.items():
        total = stats["wins"] + stats["losses"]
        if total < 3:
            continue
        wr = stats["wins"] / total * 100
        if wr < 25:
            combo_desc = " + ".join(f"{k}={v}" for k, v in combo)
            toxic.append({
                "combo": combo_desc,
                "wins": stats["wins"], "losses": stats["losses"],
                "win_rate": round(wr, 1),
                "total_pnl": round(stats["total_pnl"], 2),
                "severity": "critical" if wr == 0 and total >= 3 else "warning",
            })

    # Sort by severity then by loss count
    toxic.sort(key=lambda x: (0 if x["severity"] == "critical" else 1, -x["losses"]))
    return toxic


def compute_calibration_curve(trades: list[dict]) -> dict:
    """Bin estimated_prob into 5 buckets, compare to actual WR, compute Brier score."""
    resolved = [t for t in trades if t.get("resolved") and t.get("outcome")]
    if not resolved:
        return {"buckets": [], "brier_score": None, "overconfidence_bias": None}

    # 5 probability buckets
    bucket_edges = [(0.50, 0.60), (0.60, 0.70), (0.70, 0.80), (0.80, 0.90), (0.90, 1.01)]
    bucket_labels = ["50-60%", "60-70%", "70-80%", "80-90%", "90-100%"]
    buckets_data: list[list[dict]] = [[] for _ in range(5)]

    for t in resolved:
        ep = t.get("estimated_prob", 0.5)
        for i, (lo, hi) in enumerate(bucket_edges):
            if lo <= ep < hi:
                buckets_data[i].append(t)
                break
        else:
            # Below 50% — put in first bucket
            buckets_data[0].append(t)

    buckets = []
    brier_sum = 0.0
    brier_n = 0
    confidence_bias_sum = 0.0
    confidence_bias_n = 0

    for i, label in enumerate(bucket_labels):
        bt = buckets_data[i]
        if not bt:
            buckets.append({"label": label, "count": 0, "actual_wr": None, "avg_estimated": None})
            continue
        wins = sum(1 for t in bt if t.get("won"))
        actual_wr = round(wins / len(bt) * 100, 1)
        avg_est = round(sum(t.get("estimated_prob", 0.5) for t in bt) / len(bt) * 100, 1)
        buckets.append({
            "label": label, "count": len(bt),
            "actual_wr": actual_wr, "avg_estimated": avg_est,
        })
        # Brier score components
        for t in bt:
            ep = t.get("estimated_prob", 0.5)
            actual = 1.0 if t.get("won") else 0.0
            brier_sum += (ep - actual) ** 2
            brier_n += 1
            confidence_bias_sum += (ep - actual)
            confidence_bias_n += 1

    brier = round(brier_sum / brier_n, 4) if brier_n else None
    # Positive bias = overconfident, negative = underconfident
    overconfidence = round(confidence_bias_sum / confidence_bias_n, 4) if confidence_bias_n else None

    return {"buckets": buckets, "brier_score": brier, "overconfidence_bias": overconfidence}


def analyze_edge_source_effectiveness(trades: list[dict]) -> dict:
    """WR + avg PnL per edge_source, rolling 5-trade trend, best/worst source."""
    resolved = [t for t in trades if t.get("resolved") and t.get("outcome")]
    if not resolved:
        return {"sources": {}, "best": None, "worst": None}

    by_src: dict[str, list[dict]] = {}
    for t in resolved:
        src = t.get("edge_source", "unknown") or "unknown"
        by_src.setdefault(src, []).append(t)

    sources = {}
    for src, src_trades in by_src.items():
        wins = sum(1 for t in src_trades if t.get("won"))
        total = len(src_trades)
        wr = round(wins / total * 100, 1) if total else 0
        avg_pnl = round(sum(t.get("pnl", 0) for t in src_trades) / total, 2) if total else 0

        # Rolling 5-trade trend
        recent = src_trades[-5:]
        recent_wins = sum(1 for t in recent if t.get("won"))
        recent_wr = round(recent_wins / len(recent) * 100, 1)
        if len(src_trades) >= 5:
            older = src_trades[-10:-5] if len(src_trades) >= 10 else src_trades[:-5]
            if older:
                older_wr = sum(1 for t in older if t.get("won")) / len(older) * 100
                if recent_wr > older_wr + 10:
                    trend = "improving"
                elif recent_wr < older_wr - 10:
                    trend = "declining"
                else:
                    trend = "stable"
            else:
                trend = "stable"
        else:
            trend = "insufficient_data"

        sources[src] = {
            "wins": wins, "losses": total - wins, "total": total,
            "win_rate": wr, "avg_pnl": avg_pnl,
            "recent_wr": recent_wr, "trend": trend,
        }

    # Best/worst by win rate (min 2 trades)
    qualified = {k: v for k, v in sources.items() if v["total"] >= 2}
    best = max(qualified, key=lambda k: qualified[k]["win_rate"]) if qualified else None
    worst = min(qualified, key=lambda k: qualified[k]["win_rate"]) if qualified else None

    return {"sources": sources, "best": best, "worst": worst}


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
