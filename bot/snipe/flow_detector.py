"""CLOB Flow Detector — detects directional order flow in first 30-60s of a 5m window.

At window open (T-300), smart money starts positioning on CLOB. Buy pressure
surges on the UP token or sell pressure dumps on the DOWN token. By detecting
this flow in the first 30-60s and confirming with Binance L2 + price delta,
Garves can front-run the directional move before market makers stabilize.

Usage:
    detector = FlowDetector()
    detector.reset()          # At window start
    result = detector.feed(up_book, down_book)  # Every 2s tick
    if result.is_strong:
        # Strong directional flow detected — proceed to score confirmation
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

log = logging.getLogger("garves.snipe")

# Flow detection thresholds
FLOW_CHANGE_THRESHOLD = 0.20   # 20% change from baseline = minimum signal
FLOW_STRONG_STRENGTH = 0.60    # Strength >= 0.60 required for is_strong
FLOW_STRONG_SUSTAINED = 3      # 3+ consecutive ticks required for is_strong
MIN_SNAPSHOTS_FOR_SIGNAL = 5   # Need 5 snapshots (10s) before calculating
BASELINE_COUNT = 3             # First 3 snapshots form baseline


@dataclass
class FlowSnapshot:
    """Single CLOB book reading at a point in time."""
    timestamp: float
    up_buy_pressure: float
    up_sell_pressure: float
    down_buy_pressure: float
    down_sell_pressure: float


@dataclass
class FlowResult:
    """Flow detection result."""
    direction: str = "none"        # "up", "down", or "none"
    strength: float = 0.0          # 0.0-1.0
    sustained_ticks: int = 0       # consecutive ticks with aligned flow
    is_strong: bool = False        # strength >= 0.6 AND sustained >= 3
    detail: str = "waiting"        # human-readable


class FlowDetector:
    """Detects directional CLOB order flow in the first 30-60s of a 5m window."""

    def __init__(self):
        self._snapshots: list[FlowSnapshot] = []
        self._last_direction: str = "none"
        self._sustained_count: int = 0
        self._last_result: FlowResult = FlowResult()

    def reset(self):
        """Call at window start — fresh flow tracking."""
        self._snapshots.clear()
        self._last_direction = "none"
        self._sustained_count = 0
        self._last_result = FlowResult()

    def feed(self, up_book: dict | None, down_book: dict | None) -> FlowResult:
        """Feed one tick of CLOB data. Call every 2s.

        Args:
            up_book: CLOB orderbook for UP token (buy_pressure, sell_pressure, etc.)
            down_book: CLOB orderbook for DOWN token

        Returns:
            FlowResult with direction, strength, sustained ticks, and is_strong flag.
        """
        now = time.time()

        # Extract pressures (0.0 if book unavailable)
        up_buy = up_book.get("buy_pressure", 0.0) if up_book else 0.0
        up_sell = up_book.get("sell_pressure", 0.0) if up_book else 0.0
        down_buy = down_book.get("buy_pressure", 0.0) if down_book else 0.0
        down_sell = down_book.get("sell_pressure", 0.0) if down_book else 0.0

        self._snapshots.append(FlowSnapshot(
            timestamp=now,
            up_buy_pressure=up_buy,
            up_sell_pressure=up_sell,
            down_buy_pressure=down_buy,
            down_sell_pressure=down_sell,
        ))

        # Need minimum snapshots before calculating
        if len(self._snapshots) < MIN_SNAPSHOTS_FOR_SIGNAL:
            result = FlowResult(
                detail=f"collecting {len(self._snapshots)}/{MIN_SNAPSHOTS_FOR_SIGNAL}",
            )
            self._last_result = result
            return result

        # Baseline = average of first BASELINE_COUNT snapshots
        baseline_up_buy = sum(s.up_buy_pressure for s in self._snapshots[:BASELINE_COUNT]) / BASELINE_COUNT
        baseline_down_sell = sum(s.down_sell_pressure for s in self._snapshots[:BASELINE_COUNT]) / BASELINE_COUNT
        baseline_down_buy = sum(s.down_buy_pressure for s in self._snapshots[:BASELINE_COUNT]) / BASELINE_COUNT
        baseline_up_sell = sum(s.up_sell_pressure for s in self._snapshots[:BASELINE_COUNT]) / BASELINE_COUNT

        # Current = average of latest 2 snapshots
        recent = self._snapshots[-2:]
        current_up_buy = sum(s.up_buy_pressure for s in recent) / len(recent)
        current_down_sell = sum(s.down_sell_pressure for s in recent) / len(recent)
        current_down_buy = sum(s.down_buy_pressure for s in recent) / len(recent)
        current_up_sell = sum(s.up_sell_pressure for s in recent) / len(recent)

        # Calculate rate of change for each signal
        up_buy_change = (current_up_buy - baseline_up_buy) / baseline_up_buy if baseline_up_buy > 0 else 0.0
        down_sell_change = (current_down_sell - baseline_down_sell) / baseline_down_sell if baseline_down_sell > 0 else 0.0
        down_buy_change = (current_down_buy - baseline_down_buy) / baseline_down_buy if baseline_down_buy > 0 else 0.0
        up_sell_change = (current_up_sell - baseline_up_sell) / baseline_up_sell if baseline_up_sell > 0 else 0.0

        # Determine direction from flow
        # UP flow: up_buy_pressure increased OR down_sell_pressure increased
        up_signal = max(up_buy_change, down_sell_change)
        # DOWN flow: down_buy_pressure increased OR up_sell_pressure increased
        down_signal = max(down_buy_change, up_sell_change)

        direction = "none"
        raw_change = 0.0

        if up_signal > FLOW_CHANGE_THRESHOLD and up_signal > down_signal:
            direction = "up"
            raw_change = up_signal
        elif down_signal > FLOW_CHANGE_THRESHOLD and down_signal > up_signal:
            direction = "down"
            raw_change = down_signal

        # Strength: scaled from change magnitude
        # 20% = 0.4, 30% = 0.6, 40% = 0.8, 50%+ = 1.0
        if raw_change <= 0:
            strength = 0.0
        elif raw_change >= 0.50:
            strength = 1.0
        else:
            # Linear scale: 0.20 -> 0.4, 0.50 -> 1.0
            strength = 0.4 + (raw_change - 0.20) / 0.30 * 0.6
            strength = max(0.0, min(1.0, strength))

        # Track sustained ticks
        if direction != "none" and direction == self._last_direction:
            self._sustained_count += 1
        elif direction != "none":
            self._sustained_count = 1
        else:
            self._sustained_count = 0

        self._last_direction = direction

        is_strong = strength >= FLOW_STRONG_STRENGTH and self._sustained_count >= FLOW_STRONG_SUSTAINED

        detail_parts = [f"dir={direction}"]
        if direction != "none":
            detail_parts.append(f"change={raw_change:.1%}")
            detail_parts.append(f"str={strength:.2f}")
            detail_parts.append(f"sus={self._sustained_count}")
        detail = " | ".join(detail_parts)

        result = FlowResult(
            direction=direction,
            strength=strength,
            sustained_ticks=self._sustained_count,
            is_strong=is_strong,
            detail=detail,
        )
        self._last_result = result
        return result

    def get_status(self) -> dict:
        """Dashboard-friendly status."""
        r = self._last_result
        return {
            "direction": r.direction,
            "strength": round(r.strength, 3),
            "sustained_ticks": r.sustained_ticks,
            "is_strong": r.is_strong,
            "detail": r.detail,
            "snapshots": len(self._snapshots),
        }
