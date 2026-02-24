"""Hawk Dimension Accuracy Learner — learns from every resolved trade.

Tracks accuracy across 6 decision dimensions:
  - edge_source: which edge sources actually win?
  - category: which categories to avoid?
  - confidence_band: is confidence calibrated?
  - risk_band: which risk levels win?
  - direction: is there a directional bias?
  - time_band: does timing matter?

Analogous to bot/weight_learner.py but for Hawk's prediction market trades.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
ACCURACY_FILE = DATA_DIR / "hawk_dimension_accuracy.json"
TRADES_FILE = DATA_DIR / "hawk_trades.jsonl"


def _load_accuracy() -> dict:
    """Load dimension accuracy data from disk."""
    if not ACCURACY_FILE.exists():
        return {}
    try:
        with open(ACCURACY_FILE) as f:
            return json.load(f)
    except Exception:
        log.exception("Failed to load hawk dimension accuracy")
        return {}


def _save_accuracy(data: dict) -> None:
    """Save dimension accuracy data (atomic via temp file)."""
    DATA_DIR.mkdir(exist_ok=True)
    tmp = ACCURACY_FILE.with_suffix(".json.tmp")
    try:
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(str(tmp), str(ACCURACY_FILE))
    except Exception:
        log.exception("Failed to save hawk dimension accuracy")


def _classify_confidence(confidence: float) -> str:
    if confidence < 0.5:
        return "low"
    elif confidence <= 0.7:
        return "medium"
    else:
        return "high"


def _classify_risk(risk_score: int) -> str:
    if risk_score <= 3:
        return "low"
    elif risk_score <= 6:
        return "medium"
    else:
        return "high"


def _classify_time(hours: float) -> str:
    if hours < 6:
        return "ending_soon"
    elif hours <= 24:
        return "today"
    elif hours <= 48:
        return "tomorrow"
    else:
        return "this_week"


def _extract_dimensions(trade: dict) -> dict:
    """Extract 6 dimension values from a trade record."""
    return {
        "edge_source": trade.get("edge_source", "unknown"),
        "category": trade.get("category", "other"),
        "confidence_band": _classify_confidence(trade.get("confidence", 0.5)),
        "risk_band": _classify_risk(trade.get("risk_score", 5)),
        "direction": trade.get("direction", "yes"),
        "time_band": _classify_time(trade.get("time_left_hours", 24)),
    }


def record_trade_outcome(trade: dict) -> None:
    """Record a resolved trade's outcome across all 6 dimensions."""
    won = trade.get("won", False)
    dims = _extract_dimensions(trade)
    data = _load_accuracy()

    for dim_name, dim_value in dims.items():
        if dim_name not in data:
            data[dim_name] = {}
        if dim_value not in data[dim_name]:
            data[dim_name][dim_value] = {"wins": 0, "losses": 0, "total": 0, "accuracy": 0.0}

        entry = data[dim_name][dim_value]
        entry["total"] += 1
        if won:
            entry["wins"] += 1
        else:
            entry["losses"] += 1
        entry["accuracy"] = entry["wins"] / entry["total"] if entry["total"] > 0 else 0.0

    _save_accuracy(data)
    log.info("[LEARNER] Recorded outcome (%s) across 6 dimensions: %s",
             "WIN" if won else "LOSS",
             " | ".join(f"{k}={v}" for k, v in dims.items()))


def get_dimension_adjustments(trade_context: dict) -> tuple[float, list[str]]:
    """Return edge adjustment and blocked dimensions based on historical accuracy.

    Returns:
        (adjustment, blocked_dimensions) where adjustment is clamped to [-0.06, +0.04]
        and blocked_dimensions lists truly toxic dimension values.
    """
    data = _load_accuracy()
    if not data:
        return 0.0, []

    dims = _extract_dimensions(trade_context)
    total_adj = 0.0
    blocked = []
    adjustments_log = []

    # Primary dimensions (category, edge_source) are genuinely segmented —
    # sports trades DON'T overlap with crypto trades here. These can be
    # trusted at lower sample sizes for blocking and penalties.
    #
    # Secondary dimensions (time_band, direction, confidence_band, risk_band)
    # overlap heavily across categories. With 16 trades (13 crypto losses),
    # a "today" time band picks up all the crypto garbage. These need many
    # more samples before we trust them.
    primary_dims = {"category", "edge_source"}
    min_samples_primary = 5   # penalties + blocks (lowered from 8 for faster detection)
    min_samples_secondary = 15  # penalties only, no blocks

    for dim_name, dim_value in dims.items():
        dim_data = data.get(dim_name, {})
        entry = dim_data.get(dim_value)
        is_primary = dim_name in primary_dims
        min_n = min_samples_primary if is_primary else min_samples_secondary
        if not entry or entry["total"] < min_n:
            continue

        accuracy = entry["accuracy"]
        total = entry["total"]

        # Truly toxic: block outright (only primary dims, need 8+ samples)
        if is_primary and total >= min_samples_primary and accuracy < 0.30:
            blocked.append(f"{dim_name}={dim_value}")
            adjustments_log.append(f"{dim_name}={dim_value}: BLOCKED (<30% acc={accuracy:.0%}, n={total})")
            continue

        # Penalty tiers
        if accuracy < 0.25:
            adj = -0.04
        elif accuracy < 0.40:
            adj = -0.02
        # Boost tiers
        elif total >= 8 and accuracy > 0.65:
            adj = 0.03
        elif accuracy > 0.60:
            adj = 0.02
        else:
            adj = 0.0

        if adj != 0:
            total_adj += adj
            adjustments_log.append(f"{dim_name}={dim_value}: {adj:+.2f} (acc={accuracy:.0%}, n={total})")

    # Clamp total adjustment
    total_adj = max(-0.06, min(0.04, total_adj))

    if adjustments_log:
        log.info("[LEARNER] Dimension adjustments: %s → total=%+.2f",
                 " | ".join(adjustments_log), total_adj)
    if blocked:
        log.warning("[LEARNER] BLOCKED dimensions: %s", ", ".join(blocked))

    return total_adj, blocked


def generate_audit_report() -> dict:
    """Per-dimension audit: accuracy, sample count, bootstrap 95% CI.

    Similar to Garves weight_learner.generate_audit_report() but for
    Hawk's 6 categorical dimensions instead of 17 indicators.
    """
    import random

    data = _load_accuracy()
    if not data:
        return {"dimensions": {}, "summary": "No Hawk dimension data yet"}

    result = {}
    for dim_name, values in data.items():
        dim_report = []
        for val_name, entry in values.items():
            total = entry.get("total", 0)
            wins = entry.get("wins", 0)
            if total < 1:
                continue

            accuracy = wins / total

            # Bootstrap 95% CI
            ci_low, ci_high = accuracy, accuracy
            if total >= 5:
                outcomes = [1] * wins + [0] * (total - wins)
                boot_accs = []
                for _ in range(1000):
                    sample = random.choices(outcomes, k=total)
                    boot_accs.append(sum(sample) / total)
                boot_accs.sort()
                ci_low = boot_accs[int(0.025 * len(boot_accs))]
                ci_high = boot_accs[int(0.975 * len(boot_accs))]

            includes_coinflip = ci_low <= 0.50 <= ci_high

            dim_report.append({
                "value": val_name,
                "total": total,
                "wins": wins,
                "losses": total - wins,
                "accuracy": round(accuracy, 4),
                "ci_95_low": round(ci_low, 4),
                "ci_95_high": round(ci_high, 4),
                "includes_coinflip": includes_coinflip,
                "verdict": "EDGE" if not includes_coinflip and accuracy > 0.50 else
                           "TOXIC" if not includes_coinflip and accuracy < 0.50 else
                           "NOISE",
            })
        dim_report.sort(key=lambda x: -x["total"])
        result[dim_name] = dim_report

    return {"dimensions": result}


def get_accuracy_report() -> dict:
    """Return full accuracy data for dashboard/debugging."""
    return _load_accuracy()


def backfill_from_trades() -> dict:
    """One-time seed from existing resolved trades."""
    if not TRADES_FILE.exists():
        return {"backfilled": 0}

    trades = []
    try:
        with open(TRADES_FILE) as f:
            for line in f:
                line = line.strip()
                if line:
                    trades.append(json.loads(line))
    except Exception:
        log.exception("Failed to load trades for backfill")
        return {"backfilled": 0}

    resolved = [t for t in trades if t.get("resolved") and t.get("outcome")]
    if not resolved:
        return {"backfilled": 0}

    # Clear existing data and rebuild from scratch
    data = {}
    for trade in resolved:
        won = trade.get("won", False)
        dims = _extract_dimensions(trade)
        for dim_name, dim_value in dims.items():
            if dim_name not in data:
                data[dim_name] = {}
            if dim_value not in data[dim_name]:
                data[dim_name][dim_value] = {"wins": 0, "losses": 0, "total": 0, "accuracy": 0.0}
            entry = data[dim_name][dim_value]
            entry["total"] += 1
            if won:
                entry["wins"] += 1
            else:
                entry["losses"] += 1
            entry["accuracy"] = entry["wins"] / entry["total"] if entry["total"] > 0 else 0.0

    _save_accuracy(data)
    log.info("[LEARNER] Backfilled %d resolved trades into dimension accuracy", len(resolved))
    return {"backfilled": len(resolved), "dimensions": {k: len(v) for k, v in data.items()}}
