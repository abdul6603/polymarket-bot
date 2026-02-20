"""Garves — Polymarket Order Book Flow Tracking.

Tracks how the Polymarket order book CHANGES over time (not just snapshots):
- Bid/ask depth change velocity (is bid side growing?)
- Spread compression (tightening = conviction from market makers)
- Large order detection (sudden jumps = whale orders)
- Token price momentum on Poly (separate from Binance)

This is the most underexploited edge — Polymarket book data is proprietary to participants.
"""
from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass

from bot.indicators import IndicatorVote

log = logging.getLogger(__name__)

# Rolling history config
MAX_SNAPSHOTS = 60       # 60 snapshots = ~60 minutes at 1/min
MIN_SNAPSHOTS = 5        # Need at least 5 snapshots to generate signal
VELOCITY_WINDOW = 10     # Look at last 10 snapshots for velocity
WHALE_JUMP_THRESHOLD = 2.0  # 2x average depth change = whale activity


@dataclass
class BookSnapshot:
    """Single orderbook snapshot at a point in time."""
    timestamp: float
    buy_pressure: float     # Total bid depth (price * size)
    sell_pressure: float    # Total ask depth (price * size)
    best_bid: float
    best_ask: float
    spread: float
    mid_price: float


@dataclass
class FlowAnalysis:
    """Result of flow analysis."""
    bid_velocity: float       # Rate of bid depth change (positive = growing)
    ask_velocity: float       # Rate of ask depth change
    spread_trend: float       # Negative = compressing (bullish), positive = widening
    whale_detected: bool      # Sudden large depth change
    whale_direction: str      # "bid" or "ask" if whale detected
    price_momentum: float     # Poly token mid-price momentum
    depth_ratio_trend: float  # How bid/ask ratio is changing over time


class PolymarketFlowTracker:
    """Tracks Polymarket orderbook flow changes over time."""

    def __init__(self):
        # token_id -> deque of BookSnapshot
        self._history: dict[str, deque[BookSnapshot]] = {}

    def record_snapshot(
        self,
        token_id: str,
        buy_pressure: float,
        sell_pressure: float,
        best_bid: float,
        best_ask: float,
        spread: float,
    ) -> None:
        """Record a new orderbook snapshot for flow analysis."""
        if token_id not in self._history:
            self._history[token_id] = deque(maxlen=MAX_SNAPSHOTS)

        mid = (best_bid + best_ask) / 2 if best_bid and best_ask else 0
        snap = BookSnapshot(
            timestamp=time.time(),
            buy_pressure=buy_pressure,
            sell_pressure=sell_pressure,
            best_bid=best_bid,
            best_ask=best_ask,
            spread=spread,
            mid_price=mid,
        )
        self._history[token_id].append(snap)

    def analyze(self, token_id: str) -> FlowAnalysis | None:
        """Analyze flow patterns for a token.

        Returns None if insufficient history.
        """
        history = self._history.get(token_id)
        if not history or len(history) < MIN_SNAPSHOTS:
            return None

        snapshots = list(history)
        recent = snapshots[-VELOCITY_WINDOW:] if len(snapshots) >= VELOCITY_WINDOW else snapshots

        # 1. Bid/Ask depth velocity (rate of change)
        if len(recent) >= 2:
            first_bid = recent[0].buy_pressure
            last_bid = recent[-1].buy_pressure
            first_ask = recent[0].sell_pressure
            last_ask = recent[-1].sell_pressure
            dt = max(recent[-1].timestamp - recent[0].timestamp, 1.0)

            bid_velocity = (last_bid - first_bid) / max(first_bid, 1) / dt * 60  # per minute
            ask_velocity = (last_ask - first_ask) / max(first_ask, 1) / dt * 60
        else:
            bid_velocity = 0.0
            ask_velocity = 0.0

        # 2. Spread trend (compression = conviction)
        spreads = [s.spread for s in recent if s.spread > 0]
        if len(spreads) >= 3:
            first_spread = sum(spreads[:3]) / 3
            last_spread = sum(spreads[-3:]) / 3
            spread_trend = (last_spread - first_spread) / max(first_spread, 0.001)
        else:
            spread_trend = 0.0

        # 3. Whale detection (sudden depth jumps)
        whale_detected = False
        whale_direction = ""
        if len(recent) >= 3:
            bid_changes = [abs(recent[i].buy_pressure - recent[i-1].buy_pressure)
                           for i in range(1, len(recent))]
            ask_changes = [abs(recent[i].sell_pressure - recent[i-1].sell_pressure)
                           for i in range(1, len(recent))]

            avg_bid_change = sum(bid_changes) / len(bid_changes) if bid_changes else 0
            avg_ask_change = sum(ask_changes) / len(ask_changes) if ask_changes else 0

            # Check last change vs average
            if bid_changes and avg_bid_change > 0:
                if bid_changes[-1] > avg_bid_change * WHALE_JUMP_THRESHOLD:
                    whale_detected = True
                    whale_direction = "bid"
            if ask_changes and avg_ask_change > 0:
                if ask_changes[-1] > avg_ask_change * WHALE_JUMP_THRESHOLD:
                    whale_detected = True
                    whale_direction = "ask" if not whale_detected else whale_direction

        # 4. Price momentum on Poly
        prices = [s.mid_price for s in recent if s.mid_price > 0]
        if len(prices) >= 3:
            price_momentum = (prices[-1] - prices[0]) / max(prices[0], 0.001)
        else:
            price_momentum = 0.0

        # 5. Depth ratio trend (how bid/ask imbalance is evolving)
        ratios = []
        for s in recent:
            total = s.buy_pressure + s.sell_pressure
            if total > 0:
                ratios.append(s.buy_pressure / total)
        if len(ratios) >= 3:
            first_ratio = sum(ratios[:3]) / 3
            last_ratio = sum(ratios[-3:]) / 3
            depth_ratio_trend = last_ratio - first_ratio
        else:
            depth_ratio_trend = 0.0

        return FlowAnalysis(
            bid_velocity=bid_velocity,
            ask_velocity=ask_velocity,
            spread_trend=spread_trend,
            whale_detected=whale_detected,
            whale_direction=whale_direction,
            price_momentum=price_momentum,
            depth_ratio_trend=depth_ratio_trend,
        )

    def get_signal(self, token_id: str) -> IndicatorVote | None:
        """Generate an IndicatorVote from flow analysis.

        Combines multiple flow signals into a single directional vote.
        """
        analysis = self.analyze(token_id)
        if analysis is None:
            return None

        # Score each component (-1 to +1 scale, positive = bullish)
        scores = []
        weights = []

        # Bid velocity: growing bids = bullish
        if abs(analysis.bid_velocity) > 0.001 or abs(analysis.ask_velocity) > 0.001:
            net_velocity = analysis.bid_velocity - analysis.ask_velocity
            scores.append(max(-1, min(1, net_velocity * 10)))
            weights.append(0.3)

        # Spread compression = conviction (bullish if bids growing)
        if abs(analysis.spread_trend) > 0.01:
            # Negative spread_trend (compression) is bullish if depth_ratio favors bids
            spread_signal = -analysis.spread_trend * (1 if analysis.depth_ratio_trend > 0 else -1)
            scores.append(max(-1, min(1, spread_signal * 5)))
            weights.append(0.2)

        # Whale detection
        if analysis.whale_detected:
            whale_signal = 1.0 if analysis.whale_direction == "bid" else -1.0
            scores.append(whale_signal)
            weights.append(0.25)

        # Price momentum
        if abs(analysis.price_momentum) > 0.001:
            scores.append(max(-1, min(1, analysis.price_momentum * 50)))
            weights.append(0.15)

        # Depth ratio trend
        if abs(analysis.depth_ratio_trend) > 0.01:
            scores.append(max(-1, min(1, analysis.depth_ratio_trend * 10)))
            weights.append(0.1)

        if not scores:
            return None

        # Weighted average
        total_weight = sum(weights)
        composite = sum(s * w for s, w in zip(scores, weights)) / total_weight

        if abs(composite) < 0.05:
            return None  # Too weak to signal

        direction = "up" if composite > 0 else "down"
        confidence = min(abs(composite), 1.0)

        log.debug(
            "[POLY_FLOW] %s: dir=%s conf=%.2f | bid_vel=%.3f ask_vel=%.3f "
            "spread_trend=%.3f whale=%s price_mom=%.3f ratio_trend=%.3f",
            token_id[:12], direction, confidence,
            analysis.bid_velocity, analysis.ask_velocity,
            analysis.spread_trend, analysis.whale_detected,
            analysis.price_momentum, analysis.depth_ratio_trend,
        )

        return IndicatorVote(
            direction=direction,
            confidence=confidence,
            raw_value=composite * 100,
        )

    def cleanup_stale(self, max_age: float = 3600) -> None:
        """Remove tokens with no recent data."""
        now = time.time()
        stale = []
        for token_id, history in self._history.items():
            if not history or (now - history[-1].timestamp) > max_age:
                stale.append(token_id)
        for token_id in stale:
            del self._history[token_id]


# Module singleton
_tracker: PolymarketFlowTracker | None = None


def get_flow_tracker() -> PolymarketFlowTracker:
    """Get or create the global flow tracker instance."""
    global _tracker
    if _tracker is None:
        _tracker = PolymarketFlowTracker()
    return _tracker
