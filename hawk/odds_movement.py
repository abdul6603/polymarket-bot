"""Odds Movement Tracker — detect line shifts and steam moves across Hawk cycles.

Tracks sportsbook consensus odds for each market across cycles.
Key signals:
  - Steady movement in our direction = strengthening edge (boost size)
  - Movement against us = weakening edge (reduce size)
  - Steam move (>3% in <15 min) = sharp money / insider action (boost urgency)
  - Reverse steam = we're on the wrong side (block)
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
MOVEMENT_FILE = DATA_DIR / "hawk_odds_movement.json"

# Thresholds
STEAM_THRESHOLD = 0.03   # >3% move = steam
STRONG_MOVE = 0.02       # >2% = meaningful movement
STALE_TTL = 7200         # 2 hours — discard old snapshots


@dataclass
class OddsMovement:
    condition_id: str
    current_prob: float
    prev_prob: float
    movement: float          # current - prev (positive = prob increased)
    movement_pct: float      # as percentage
    time_delta_s: float      # seconds between snapshots
    is_steam: bool           # sharp move in short time
    direction: str           # "strengthening", "weakening", "neutral"
    size_multiplier: float   # 1.0-1.3 for boost, 0.5-1.0 for penalty


def _load_snapshots() -> dict:
    """Load odds snapshots from disk."""
    try:
        if MOVEMENT_FILE.exists():
            return json.loads(MOVEMENT_FILE.read_text())
    except Exception:
        pass
    return {}


def _save_snapshots(data: dict) -> None:
    """Save odds snapshots to disk."""
    try:
        MOVEMENT_FILE.write_text(json.dumps(data, indent=2))
    except Exception:
        pass


def record_odds(condition_id: str, sportsbook_prob: float) -> None:
    """Record a sportsbook probability snapshot for a market."""
    data = _load_snapshots()
    now = time.time()

    entry = data.get(condition_id, {"snapshots": []})
    entry["snapshots"].append({
        "prob": round(sportsbook_prob, 4),
        "time": now,
    })

    # Keep only last 20 snapshots per market
    entry["snapshots"] = entry["snapshots"][-20:]

    # Prune stale entries (markets not seen in 2 hours)
    entry["last_seen"] = now
    data[condition_id] = entry

    # Prune globally
    stale_cutoff = now - STALE_TTL * 3  # 6 hours global TTL
    data = {k: v for k, v in data.items() if v.get("last_seen", 0) > stale_cutoff}

    _save_snapshots(data)


def get_movement(
    condition_id: str,
    current_prob: float,
    our_direction: str = "yes",
) -> OddsMovement:
    """Compute odds movement for a market.

    Args:
        condition_id: Market condition ID
        current_prob: Current sportsbook probability
        our_direction: "yes" or "no" — which side we're betting on

    Returns:
        OddsMovement with direction assessment and size multiplier.
    """
    data = _load_snapshots()
    entry = data.get(condition_id, {})
    snapshots = entry.get("snapshots", [])

    if len(snapshots) < 1:
        return _neutral_result(condition_id, current_prob)

    # Compare to most recent snapshot
    prev = snapshots[-1]
    prev_prob = prev["prob"]
    prev_time = prev["time"]
    now = time.time()
    time_delta = now - prev_time

    # Skip if snapshot is too old
    if time_delta > STALE_TTL:
        return _neutral_result(condition_id, current_prob)

    movement = current_prob - prev_prob
    movement_pct = abs(movement) / max(prev_prob, 0.01) * 100

    # Detect steam move
    is_steam = abs(movement) >= STEAM_THRESHOLD and time_delta < 900  # <15 min

    # Assess direction relative to our bet
    # For YES bets: prob going UP = strengthening our position
    # For NO bets: prob going DOWN = strengthening our position
    if our_direction == "yes":
        favorable = movement > 0
    else:
        favorable = movement < 0

    if abs(movement) < 0.005:
        direction = "neutral"
        multiplier = 1.0
    elif favorable:
        direction = "strengthening"
        if is_steam:
            multiplier = 1.3  # Strong boost for steam in our direction
        elif abs(movement) >= STRONG_MOVE:
            multiplier = 1.2
        else:
            multiplier = 1.1
    else:
        direction = "weakening"
        if is_steam:
            multiplier = 0.3  # Heavy penalty for reverse steam
        elif abs(movement) >= STRONG_MOVE:
            multiplier = 0.6
        else:
            multiplier = 0.8

    result = OddsMovement(
        condition_id=condition_id,
        current_prob=current_prob,
        prev_prob=prev_prob,
        movement=round(movement, 4),
        movement_pct=round(movement_pct, 2),
        time_delta_s=round(time_delta, 0),
        is_steam=is_steam,
        direction=direction,
        size_multiplier=multiplier,
    )

    if direction != "neutral":
        steam_tag = " [STEAM]" if is_steam else ""
        log.info("[ODDS-MOVE] %s%s: %.2f → %.2f (%+.1f%%) | %s | mult=%.1fx | %s",
                 direction.upper(), steam_tag, prev_prob, current_prob,
                 movement * 100, f"{time_delta/60:.0f}min", multiplier,
                 condition_id[:12])

    return result


def get_multi_cycle_trend(condition_id: str, lookback: int = 5) -> dict:
    """Analyze trend across multiple cycles.

    Returns:
        {"direction": "up"/"down"/"flat", "avg_move": float, "consistency": float}
    """
    data = _load_snapshots()
    entry = data.get(condition_id, {})
    snapshots = entry.get("snapshots", [])

    if len(snapshots) < 2:
        return {"direction": "flat", "avg_move": 0.0, "consistency": 0.0}

    recent = snapshots[-lookback:]
    moves = []
    for i in range(1, len(recent)):
        moves.append(recent[i]["prob"] - recent[i - 1]["prob"])

    if not moves:
        return {"direction": "flat", "avg_move": 0.0, "consistency": 0.0}

    avg_move = sum(moves) / len(moves)
    # Consistency: what fraction of moves are in the same direction as the average
    if avg_move > 0:
        consistency = sum(1 for m in moves if m > 0) / len(moves)
    elif avg_move < 0:
        consistency = sum(1 for m in moves if m < 0) / len(moves)
    else:
        consistency = 0.0

    direction = "up" if avg_move > 0.005 else "down" if avg_move < -0.005 else "flat"

    return {
        "direction": direction,
        "avg_move": round(avg_move, 4),
        "consistency": round(consistency, 2),
    }


def _neutral_result(condition_id: str, current_prob: float) -> OddsMovement:
    return OddsMovement(
        condition_id=condition_id,
        current_prob=current_prob,
        prev_prob=current_prob,
        movement=0.0,
        movement_pct=0.0,
        time_delta_s=0.0,
        is_steam=False,
        direction="neutral",
        size_multiplier=1.0,
    )
