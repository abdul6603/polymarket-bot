"""Delta Signal — generates snipe signals from BTC price movement.

Monitors BTC spot price relative to window open price.
Fires when delta exceeds threshold with sustained direction.
"""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass

log = logging.getLogger("garves.snipe")

# Minimum delta to consider direction locked
DEFAULT_DELTA_THRESHOLD = 0.00077  # 0.077%

# Sustained direction: need N consecutive ticks same direction
SUSTAINED_TICKS = 3


@dataclass
class SnipeSignal:
    """Signal to enter a 5m snipe trade."""
    direction: str          # "up" or "down"
    delta_pct: float        # Signed delta percentage (e.g. +0.12)
    confidence: float       # 0.0-1.0 based on delta magnitude
    sustained_ticks: int    # How many consecutive ticks in same direction
    current_price: float    # BTC spot now
    open_price: float       # BTC at window open
    remaining_s: float      # Seconds until window closes


class DeltaSignal:
    """Generates snipe signals from BTC delta vs window open price."""

    def __init__(self, threshold: float = DEFAULT_DELTA_THRESHOLD):
        self._threshold = threshold
        self._recent_dirs: deque[str] = deque(maxlen=10)

    def evaluate(
        self,
        current_price: float,
        open_price: float,
        remaining_s: float,
    ) -> SnipeSignal | None:
        """Evaluate whether to fire a snipe signal.

        Returns SnipeSignal if conditions met, None otherwise.
        """
        if open_price <= 0 or current_price <= 0:
            return None

        delta = (current_price - open_price) / open_price
        direction = "up" if delta > 0 else "down"
        abs_delta = abs(delta)

        # Track direction history
        self._recent_dirs.append(direction)

        # Check 1: Must be in snipe window (last 180s)
        if remaining_s > 185:
            return None

        # Check 2: Delta must exceed threshold
        if abs_delta < self._threshold:
            return None

        # Check 3: Direction must be sustained (N consecutive ticks)
        sustained = 0
        for d in reversed(self._recent_dirs):
            if d == direction:
                sustained += 1
            else:
                break

        if sustained < SUSTAINED_TICKS:
            return None

        # Compute confidence based on delta magnitude
        # 0.08% -> 0.50, 0.15% -> 0.75, 0.25%+ -> 0.95
        if abs_delta >= 0.0025:
            confidence = 0.95
        elif abs_delta >= 0.0015:
            confidence = 0.75 + (abs_delta - 0.0015) / 0.001 * 0.20
        else:
            confidence = 0.50 + (abs_delta - 0.0008) / 0.0007 * 0.25

        confidence = max(0.50, min(0.95, confidence))

        # Boost if very sustained
        if sustained >= 6:
            confidence = min(0.98, confidence + 0.05)

        return SnipeSignal(
            direction=direction,
            delta_pct=round(delta * 100, 4),
            confidence=round(confidence, 3),
            sustained_ticks=sustained,
            current_price=current_price,
            open_price=open_price,
            remaining_s=remaining_s,
        )

    def get_wave_threshold(self, wave_num: int) -> float:
        """Return delta threshold for each pyramid wave (same for all — delta already proven)."""
        return {
            1: self._threshold,              # 0.077% for Wave 1
            2: self._threshold,              # 0.077% for Wave 2 (same — delta already proven at entry)
            3: self._threshold * 1.15,       # 0.089% for Wave 3 (slight escalation for final wave)
        }.get(wave_num, self._threshold)

    def reset(self) -> None:
        """Reset direction history for new window."""
        self._recent_dirs.clear()
