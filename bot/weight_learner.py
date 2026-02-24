from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
ACCURACY_FILE = DATA_DIR / "indicator_accuracy.json"

# Cache to avoid reading from disk on every call (24+ times per tick)
_weights_cache: dict = {"weights": None, "timestamp": 0.0}
_WEIGHTS_CACHE_TTL = 30  # seconds

# Confidence bands for per-band accuracy tracking
_CONF_BANDS = {"low": (0.0, 0.33), "med": (0.33, 0.66), "high": (0.66, 1.01)}


def _load_accuracy() -> dict:
    """Load per-indicator accuracy data from disk."""
    if not ACCURACY_FILE.exists():
        return {}
    try:
        with open(ACCURACY_FILE) as f:
            return json.load(f)
    except Exception:
        log.exception("Failed to load indicator accuracy file")
        return {}


def _save_accuracy(data: dict) -> None:
    """Save per-indicator accuracy data to disk (atomic via temp file)."""
    DATA_DIR.mkdir(exist_ok=True)
    tmp_path = ACCURACY_FILE.with_suffix(".json.tmp")
    try:
        with open(tmp_path, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(str(tmp_path), str(ACCURACY_FILE))
    except Exception:
        log.exception("Failed to save indicator accuracy file")


def _conf_band(confidence: float) -> str:
    """Map confidence value to band name."""
    for band, (lo, hi) in _CONF_BANDS.items():
        if lo <= confidence < hi:
            return band
    return "high"


def _ensure_entry(data: dict, name: str) -> dict:
    """Ensure indicator entry has the full enriched schema."""
    if name not in data:
        data[name] = {}
    entry = data[name]
    # Global counters (backward compatible)
    entry.setdefault("total_votes", 0)
    entry.setdefault("correct_votes", 0)
    entry.setdefault("accuracy", 0.0)
    # Enriched dimensions
    entry.setdefault("by_asset", {})
    entry.setdefault("by_regime", {})
    entry.setdefault("by_confidence_band", {})
    entry.setdefault("confidence_weighted_accuracy", 0.0)
    # Running sums for confidence-weighted accuracy
    entry.setdefault("_cw_correct", 0.0)
    entry.setdefault("_cw_total", 0.0)
    return entry


def _update_sub_bucket(bucket: dict, key: str, correct: bool) -> None:
    """Increment a sub-bucket (by_asset, by_regime, by_confidence_band)."""
    if key not in bucket:
        bucket[key] = {"total": 0, "correct": 0, "accuracy": 0.0}
    sub = bucket[key]
    sub["total"] += 1
    if correct:
        sub["correct"] += 1
    sub["accuracy"] = sub["correct"] / sub["total"]


def record_indicator_votes(trade_record, indicator_votes: dict) -> None:
    """Record which indicators voted correctly vs incorrectly after resolution.

    Tracks per-asset, per-regime, per-confidence-band, and confidence-weighted
    accuracy. Handles both old format (str direction) and new format (dict with
    direction/confidence/raw_value).

    Args:
        trade_record: A resolved TradeRecord (must have .outcome set).
        indicator_votes: Dict of indicator_name -> direction_str or
                         {direction, confidence, raw_value}.
    """
    if not indicator_votes:
        return
    outcome = getattr(trade_record, "outcome", "")
    if outcome not in ("up", "down"):
        return

    asset = getattr(trade_record, "asset", "unknown")
    regime = getattr(trade_record, "regime_label", "unknown") or "unknown"

    data = _load_accuracy()

    for name, vote_data in indicator_votes.items():
        # Normalize: old format is plain string, new format is dict
        if isinstance(vote_data, str):
            direction = vote_data
            confidence = 0.5  # default for old-format votes
        elif isinstance(vote_data, dict):
            direction = vote_data.get("direction", "")
            confidence = float(vote_data.get("confidence", 0.5))
        else:
            continue

        if direction not in ("up", "down"):
            continue

        entry = _ensure_entry(data, name)
        correct = direction == outcome

        # Global counters
        entry["total_votes"] += 1
        if correct:
            entry["correct_votes"] += 1
        total = entry["total_votes"]
        entry["accuracy"] = entry["correct_votes"] / total if total > 0 else 0.0

        # Per-asset
        _update_sub_bucket(entry["by_asset"], asset, correct)

        # Per-regime
        _update_sub_bucket(entry["by_regime"], regime, correct)

        # Per-confidence-band
        band = _conf_band(confidence)
        _update_sub_bucket(entry["by_confidence_band"], band, correct)

        # Confidence-weighted accuracy (higher confidence votes count more)
        entry["_cw_correct"] += confidence if correct else 0.0
        entry["_cw_total"] += confidence
        if entry["_cw_total"] > 0:
            entry["confidence_weighted_accuracy"] = entry["_cw_correct"] / entry["_cw_total"]

    _save_accuracy(data)
    log.debug(
        "Recorded indicator votes for trade outcome=%s asset=%s regime=%s (%d indicators)",
        outcome, asset, regime, len(indicator_votes),
    )


def generate_audit_report() -> dict:
    """Per-indicator audit: accuracy, sample count, bootstrap 95% CI, breakdowns.

    Flags indicators where the confidence interval includes 50% (coin-flip).
    """
    import random

    data = _load_accuracy()
    if not data:
        return {"indicators": [], "summary": "No indicator data yet"}

    report = []
    for name, entry in data.items():
        total = entry.get("total_votes", 0)
        correct = entry.get("correct_votes", 0)
        if total < 1:
            continue

        accuracy = correct / total

        # Bootstrap 95% CI (1000 resamples)
        ci_low, ci_high = accuracy, accuracy
        if total >= 5:
            boot_accs = []
            outcomes = [1] * correct + [0] * (total - correct)
            for _ in range(1000):
                sample = random.choices(outcomes, k=total)
                boot_accs.append(sum(sample) / total)
            boot_accs.sort()
            ci_low = boot_accs[int(0.025 * len(boot_accs))]
            ci_high = boot_accs[int(0.975 * len(boot_accs))]

        includes_coinflip = ci_low <= 0.50 <= ci_high
        cw_acc = entry.get("confidence_weighted_accuracy", accuracy)

        indicator = {
            "name": name,
            "total_votes": total,
            "correct_votes": correct,
            "accuracy": round(accuracy, 4),
            "cw_accuracy": round(cw_acc, 4),
            "ci_95_low": round(ci_low, 4),
            "ci_95_high": round(ci_high, 4),
            "includes_coinflip": includes_coinflip,
            "verdict": "EDGE" if not includes_coinflip and accuracy > 0.50 else
                       "ANTI" if not includes_coinflip and accuracy < 0.50 else
                       "NOISE",
            "by_asset": entry.get("by_asset", {}),
            "by_regime": entry.get("by_regime", {}),
            "by_confidence_band": entry.get("by_confidence_band", {}),
        }
        report.append(indicator)

    # Sort by verdict priority (EDGE first, then NOISE, then ANTI)
    verdict_order = {"EDGE": 0, "NOISE": 1, "ANTI": 2}
    report.sort(key=lambda x: (verdict_order.get(x["verdict"], 1), -x["total_votes"]))

    edge_count = sum(1 for i in report if i["verdict"] == "EDGE")
    anti_count = sum(1 for i in report if i["verdict"] == "ANTI")
    noise_count = sum(1 for i in report if i["verdict"] == "NOISE")

    return {
        "indicators": report,
        "summary": {
            "total_indicators": len(report),
            "edge": edge_count,
            "anti_signal": anti_count,
            "noise": noise_count,
        },
    }


def get_dynamic_weights(base_weights: dict) -> dict:
    """Return adjusted weights based on historical indicator accuracy.

    Uses confidence_weighted_accuracy when enough samples exist (>=30),
    falls back to simple accuracy otherwise.

    Rules (tightened Feb 16 — aggressive culling of anti-signals):
    - >50 samples and <40% accuracy: DISABLE (weight=0) — actively harmful anti-signal
    - >30 samples and <45% accuracy: reduce weight by 60%
    - >30 samples and >55% accuracy: boost weight by 30%
    - Clamp adjustments to [0.0x, 2.5x] of base weight
    """
    now = time.time()
    if _weights_cache["weights"] is not None and now - _weights_cache["timestamp"] < _WEIGHTS_CACHE_TTL:
        return _weights_cache["weights"]

    data = _load_accuracy()
    if not data:
        return dict(base_weights)

    adjusted = {}
    for name, base_w in base_weights.items():
        entry = data.get(name)
        if entry is None or entry.get("total_votes", 0) <= 20:
            adjusted[name] = base_w
            continue

        total = entry.get("total_votes", 0)
        # Prefer confidence-weighted accuracy when enough data
        if total >= 30 and entry.get("_cw_total", 0) > 0:
            accuracy = entry["confidence_weighted_accuracy"]
        else:
            accuracy = entry.get("accuracy", 0.5)

        new_w = base_w

        if total >= 50 and accuracy < 0.40:
            new_w = 0.0
            log.warning(
                "Weight DISABLED: %s cw_accuracy=%.1f%% (%d samples) — anti-signal, weight zeroed",
                name, accuracy * 100, total,
            )
        elif total >= 30 and accuracy < 0.45:
            new_w = base_w * 0.40
            log.info(
                "Weight reduced: %s cw_accuracy=%.1f%% (%d samples) -> %.2f -> %.2f (-60%%)",
                name, accuracy * 100, total, base_w, new_w,
            )
        elif total >= 30 and accuracy > 0.55:
            new_w = base_w * 1.30
            log.info(
                "Weight boosted: %s cw_accuracy=%.1f%% (%d samples) -> %.2f -> %.2f (+30%%)",
                name, accuracy * 100, total, base_w, new_w,
            )

        max_w = base_w * 2.5
        new_w = max(0.0, min(max_w, new_w))
        adjusted[name] = new_w

    _weights_cache["weights"] = adjusted
    _weights_cache["timestamp"] = now
    return adjusted
