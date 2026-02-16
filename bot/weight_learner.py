from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
ACCURACY_FILE = DATA_DIR / "indicator_accuracy.json"


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
    """Save per-indicator accuracy data to disk."""
    DATA_DIR.mkdir(exist_ok=True)
    try:
        with open(ACCURACY_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        log.exception("Failed to save indicator accuracy file")


def record_indicator_votes(trade_record, indicator_votes: dict) -> None:
    """Record which indicators voted correctly vs incorrectly after resolution.

    Args:
        trade_record: A resolved TradeRecord (must have .outcome set).
        indicator_votes: Dict of indicator_name -> direction ("up" or "down")
                         captured at signal time.
    """
    if not indicator_votes:
        return
    outcome = getattr(trade_record, "outcome", "")
    if outcome not in ("up", "down"):
        return

    data = _load_accuracy()

    for name, voted_direction in indicator_votes.items():
        if name not in data:
            data[name] = {"total_votes": 0, "correct_votes": 0, "accuracy": 0.0}

        entry = data[name]
        entry["total_votes"] += 1
        if voted_direction == outcome:
            entry["correct_votes"] += 1

        total = entry["total_votes"]
        entry["accuracy"] = entry["correct_votes"] / total if total > 0 else 0.0

    _save_accuracy(data)
    log.debug("Recorded indicator votes for trade outcome=%s (%d indicators)", outcome, len(indicator_votes))


def get_dynamic_weights(base_weights: dict) -> dict:
    """Return adjusted weights based on historical indicator accuracy.

    Rules (tightened Feb 16 — aggressive culling of anti-signals):
    - >50 samples and <40% accuracy: DISABLE (weight=0) — actively harmful anti-signal
    - >30 samples and <45% accuracy: reduce weight by 60%
    - >30 samples and >55% accuracy: boost weight by 30%
    - Clamp adjustments to [0.0x, 2.5x] of base weight
    """
    data = _load_accuracy()
    if not data:
        return dict(base_weights)

    adjusted = {}
    for name, base_w in base_weights.items():
        entry = data.get(name)
        if entry is None or entry["total_votes"] <= 20:
            adjusted[name] = base_w
            continue

        accuracy = entry["accuracy"]
        total = entry["total_votes"]
        new_w = base_w

        if total >= 50 and accuracy < 0.40:
            # Anti-signal: consistently wrong, disable entirely
            new_w = 0.0
            log.warning(
                "Weight DISABLED: %s accuracy=%.1f%% (%d samples) — anti-signal, weight zeroed",
                name, accuracy * 100, total,
            )
        elif total >= 30 and accuracy < 0.45:
            new_w = base_w * 0.40  # reduce by 60%
            log.info(
                "Weight reduced: %s accuracy=%.1f%% (%d samples) -> %.2f -> %.2f (-60%%)",
                name, accuracy * 100, total, base_w, new_w,
            )
        elif total >= 30 and accuracy > 0.55:
            new_w = base_w * 1.30  # boost by 30%
            log.info(
                "Weight boosted: %s accuracy=%.1f%% (%d samples) -> %.2f -> %.2f (+30%%)",
                name, accuracy * 100, total, base_w, new_w,
            )

        # Clamp to [0.0x, 2.5x] of base weight
        max_w = base_w * 2.5
        new_w = max(0.0, min(max_w, new_w))

        adjusted[name] = new_w

    return adjusted
