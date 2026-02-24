"""Hawk Auto-Tuner — per-category parameter tuning from performance data.

Uses Wilson score confidence intervals for statistical significance.
Reads dimension accuracy + CLV stats + reviews, recommends parameter changes.
"""
from __future__ import annotations

import json
import logging
import math
import os
from pathlib import Path

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
OVERRIDES_FILE = DATA_DIR / "hawk_category_overrides.json"

MIN_SAMPLES = 5


def statistical_significance_check(wins: int, total: int, baseline_wr: float) -> bool:
    """Wilson score interval — returns True if category WR is significantly different from baseline.

    Pure math, no scipy needed.
    """
    if total < MIN_SAMPLES:
        return False

    p_hat = wins / total
    z = 1.96  # 95% confidence

    # Wilson score interval
    denominator = 1 + z * z / total
    centre = (p_hat + z * z / (2 * total)) / denominator
    spread = z * math.sqrt((p_hat * (1 - p_hat) + z * z / (4 * total)) / total) / denominator

    lower = centre - spread
    upper = centre + spread

    # Significant if baseline falls outside the interval
    return baseline_wr < lower or baseline_wr > upper


def compute_tuning_recommendations(
    dim_accuracy: dict,
    reviews: dict | None = None,
    clv_stats: dict | None = None,
) -> list[dict]:
    """Analyze all data and return parameterized recs per category.

    Returns list of dicts: {category, action, severity, win_rate, total,
                            suggested_min_edge, suggested_enabled, significant, reason}
    """
    recs = []
    cat_data = dim_accuracy.get("category", {})
    if not cat_data:
        return recs

    # Compute global baseline WR
    total_wins = sum(v.get("wins", 0) for v in cat_data.values())
    total_all = sum(v.get("total", 0) for v in cat_data.values())
    baseline_wr = total_wins / total_all if total_all > 0 else 0.5

    # CLV by category (if available)
    clv_by_cat = {}
    if clv_stats:
        clv_by_cat = clv_stats.get("by_category", {})

    for cat, stats in cat_data.items():
        total = stats.get("total", 0)
        wins = stats.get("wins", 0)
        if total < MIN_SAMPLES:
            continue

        wr = wins / total
        significant = statistical_significance_check(wins, total, baseline_wr)
        cat_clv = clv_by_cat.get(cat, {})
        avg_clv = cat_clv.get("avg_clv", 0) if cat_clv else 0

        # Rule: WR >50% + positive CLV → lower min_edge by 0.02
        if wr > 0.50 and avg_clv >= 0:
            recs.append({
                "category": cat,
                "action": "lower_min_edge",
                "severity": "low",
                "win_rate": round(wr * 100, 1),
                "total": total,
                "suggested_min_edge_delta": -0.02,
                "suggested_enabled": True,
                "significant": significant,
                "reason": f"Strong WR {wr*100:.0f}% + CLV {avg_clv:+.4f}",
            })

        # Rule: WR <30% + 8+ trades → disable or raise min_edge by 0.05
        elif wr < 0.30 and total >= 8:
            recs.append({
                "category": cat,
                "action": "disable_category" if significant else "raise_min_edge",
                "severity": "critical" if significant else "high",
                "win_rate": round(wr * 100, 1),
                "total": total,
                "suggested_min_edge_delta": 0.05,
                "suggested_enabled": False if significant else True,
                "significant": significant,
                "reason": f"Toxic WR {wr*100:.0f}% over {total} trades",
            })

        # Rule: WR 30-50% → raise min_edge by 0.02
        elif wr < 0.50:
            recs.append({
                "category": cat,
                "action": "raise_min_edge",
                "severity": "medium",
                "win_rate": round(wr * 100, 1),
                "total": total,
                "suggested_min_edge_delta": 0.02,
                "suggested_enabled": True,
                "significant": significant,
                "reason": f"Below-average WR {wr*100:.0f}%",
            })

    # Sort by severity
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    recs.sort(key=lambda x: severity_order.get(x.get("severity", "low"), 4))
    return recs


def apply_overrides(recommendations: list[dict], auto_only: bool = True) -> dict:
    """Write approved recs to hawk_category_overrides.json (atomic write).

    If auto_only=True, only apply when statistically significant.
    Returns {applied: [...], skipped: [...]}.
    """
    # Load existing overrides
    existing = {}
    if OVERRIDES_FILE.exists():
        try:
            existing = json.loads(OVERRIDES_FILE.read_text())
        except Exception:
            pass

    applied = []
    skipped = []

    for rec in recommendations:
        cat = rec["category"]
        if auto_only and not rec.get("significant"):
            skipped.append({"category": cat, "reason": "not statistically significant"})
            continue

        # Get or create category override
        cat_override = existing.get(cat, {"enabled": True})

        if rec["action"] == "disable_category":
            cat_override["enabled"] = False
            applied.append({"category": cat, "action": "disabled"})
        elif rec["action"] in ("raise_min_edge", "lower_min_edge"):
            current = cat_override.get("min_edge")
            if current is None:
                # Use 0.15 as default global min_edge
                current = 0.15
            delta = rec.get("suggested_min_edge_delta", 0)
            new_val = round(max(0.05, min(0.30, current + delta)), 4)
            cat_override["min_edge"] = new_val
            applied.append({"category": cat, "action": f"min_edge → {new_val}"})

        existing[cat] = cat_override

    # Atomic write
    DATA_DIR.mkdir(exist_ok=True)
    tmp = OVERRIDES_FILE.with_suffix(".json.tmp")
    try:
        with open(tmp, "w") as f:
            json.dump(existing, f, indent=2)
        os.replace(str(tmp), str(OVERRIDES_FILE))
        log.info("[TUNER] Applied %d overrides, skipped %d", len(applied), len(skipped))
    except Exception:
        log.exception("[TUNER] Failed to write overrides")

    return {"applied": applied, "skipped": skipped}


def apply_single_override(category: str) -> dict:
    """Apply tuning for a single category based on current recommendations."""
    try:
        from hawk.learner import _load_accuracy
        dim_accuracy = _load_accuracy()
    except Exception:
        return {"ok": False, "error": "Failed to load accuracy data"}

    try:
        from hawk.clv import get_clv_stats, get_clv_by_dimension
        clv_stats = get_clv_stats()
        clv_dims = get_clv_by_dimension()
        clv_stats["by_category"] = clv_dims.get("by_category", {})
    except Exception:
        clv_stats = {}

    recs = compute_tuning_recommendations(dim_accuracy, clv_stats=clv_stats)
    cat_recs = [r for r in recs if r["category"] == category]

    if not cat_recs:
        return {"ok": False, "error": f"No recommendation for category '{category}'"}

    result = apply_overrides(cat_recs, auto_only=False)
    return {"ok": True, **result}
