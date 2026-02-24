"""Flow-confirmation signal scoring engine for BTC 5m CLOB flow sniper.

9 components, each scored 0.0-1.0, multiplied by weight:
  flow_strength(25) + delta_magnitude(15) + binance_imbalance(15) +
  clob_spread_compression(12) + flow_sustained(10) + delta_sustained(8) +
  bos_choch_5m(5) + time_positioning(5) + implied_price_edge(5) = 100

Primary signal is flow_strength from FlowDetector. Score confirms flow
with Binance L2, price delta, and CLOB spread compression. Only fires
when score >= threshold (default 72).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

log = logging.getLogger("garves.snipe")

DEFAULT_THRESHOLD = 72

COMPONENT_WEIGHTS = {
    "flow_strength":           25,  # Primary signal from FlowDetector
    "delta_magnitude":         15,  # BTC price move confirms flow
    "binance_imbalance":       15,  # Binance L2 confirms direction
    "clob_spread_compression": 12,  # Spread tightening = conviction
    "flow_sustained":          10,  # Consecutive flow ticks
    "delta_sustained":          8,  # Price direction persistence
    "bos_choch_5m":             5,  # Background structure (bonus)
    "time_positioning":         5,  # Sweet spot T-270 to T-240
    "implied_price_edge":       5,  # Risk/reward at <= $0.52
}  # Sum = 100


@dataclass
class ScoreResult:
    """Result of signal scoring."""
    total_score: float        # 0-100
    direction: str            # "up" or "down"
    components: dict          # {name: {score, weighted, detail}}
    should_trade: bool        # score >= threshold
    threshold: int


class SignalScorer:
    """9-component flow-confirmation scoring engine for BTC 5m snipe."""

    def __init__(self, threshold: int = DEFAULT_THRESHOLD):
        self.threshold = threshold
        self._spread_history: list[tuple[float, float]] = []
        self._last_score: ScoreResult | None = None

    def score(
        self,
        direction: str,
        delta_pct: float,
        sustained_ticks: int,
        ob_imbalance: float | None,
        ob_strength: float | None,
        clob_book: dict | None,
        clob_book_opposite: dict | None,
        structure_5m: dict | None,
        structure_15m: dict | None,
        remaining_s: float,
        implied_price: float | None,
        flow_strength: float = 0.0,
        flow_sustained_ticks: int = 0,
    ) -> ScoreResult:
        """Score the signal across all 9 components.

        Args:
            direction: "up" or "down" (from flow detection)
            delta_pct: absolute delta % (e.g. 0.12 for 0.12%)
            sustained_ticks: consecutive price ticks in same direction
            ob_imbalance: Binance L2 imbalance (-1 to +1), None if disconnected
            ob_strength: Binance imbalance strength (0-1), None if no signal
            clob_book: CLOB orderbook for target token
            clob_book_opposite: CLOB orderbook for opposite token
            structure_5m: {bos, choch, trend} from CandleStore 5m
            structure_15m: unused (kept for API compat)
            remaining_s: seconds until window closes
            implied_price: current CLOB token price (0-1)
            flow_strength: FlowDetector strength (0.0-1.0)
            flow_sustained_ticks: FlowDetector sustained tick count

        Returns:
            ScoreResult with total 0-100, per-component breakdown, should_trade
        """
        components = {}

        # 1. Flow Strength (25) — primary signal from FlowDetector
        raw = min(1.0, flow_strength)
        components["flow_strength"] = {
            "score": round(raw, 3),
            "detail": f"flow={flow_strength:.2f}",
        }

        # 2. Delta Magnitude (15) — how far BTC moved from window open
        abs_delta = abs(delta_pct)
        if abs_delta >= 0.15:
            raw = 1.0
        elif abs_delta >= 0.08:
            raw = 0.5 + (abs_delta - 0.08) / 0.07 * 0.5
        elif abs_delta >= 0.05:
            raw = 0.2 + (abs_delta - 0.05) / 0.03 * 0.3
        else:
            raw = abs_delta / 0.05 * 0.2
        components["delta_magnitude"] = {
            "score": round(raw, 3), "detail": f"{abs_delta:.4f}%",
        }

        # 3. Binance L2 Imbalance (15) — orderflow direction from Binance
        if ob_imbalance is not None:
            ob_dir = "up" if ob_imbalance > 0 else "down"
            aligned = ob_dir == direction
            abs_imb = abs(ob_imbalance)
            if aligned:
                if abs_imb >= 0.40:
                    raw = 1.0
                elif abs_imb >= 0.25:
                    raw = 0.5 + (abs_imb - 0.25) / 0.15 * 0.5
                else:
                    raw = abs_imb / 0.25 * 0.5
            else:
                raw = max(0.0, 0.2 - abs_imb)
        else:
            raw = 0.3  # No data = neutral-low
        components["binance_imbalance"] = {
            "score": round(raw, 3),
            "detail": f"imb={ob_imbalance:.3f}" if ob_imbalance is not None else "no data",
        }

        # 4. CLOB Spread Compression (12) — spread tightening = conviction
        raw = 0.3
        if clob_book:
            spread = clob_book.get("spread", 0)
            now = time.time()
            self._spread_history.append((now, spread))
            cutoff = now - 30
            self._spread_history = [
                (t, s) for t, s in self._spread_history if t > cutoff
            ]
            if len(self._spread_history) >= 3:
                old_spread = self._spread_history[0][1]
                if old_spread > 0:
                    compression = (old_spread - spread) / old_spread
                    if compression > 0.30:
                        raw = 1.0
                    elif compression > 0.15:
                        raw = 0.5 + compression / 0.30 * 0.5
                    elif compression > 0:
                        raw = 0.3 + compression / 0.15 * 0.2
                    else:
                        raw = max(0.0, 0.3 - abs(compression))
        components["clob_spread_compression"] = {
            "score": round(raw, 3),
            "detail": f"spread={clob_book.get('spread', 0):.4f}" if clob_book else "N/A",
        }

        # 5. Flow Sustained (10) — consecutive flow ticks from FlowDetector
        if flow_sustained_ticks >= 5:
            raw = 1.0
        elif flow_sustained_ticks >= 3:
            raw = 0.6
        elif flow_sustained_ticks >= 2:
            raw = 0.3
        else:
            raw = 0.0
        components["flow_sustained"] = {
            "score": round(raw, 3),
            "detail": f"{flow_sustained_ticks} ticks",
        }

        # 6. Delta Sustained (8) — consecutive price ticks confirming direction
        if sustained_ticks >= 5:
            raw = 1.0
        elif sustained_ticks >= 3:
            raw = 0.6 + (sustained_ticks - 3) / 2 * 0.4
        elif sustained_ticks >= 2:
            raw = 0.4
        else:
            raw = 0.0
        components["delta_sustained"] = {
            "score": round(raw, 3), "detail": f"{sustained_ticks} ticks",
        }

        # 7. BOS/CHoCH 5m (5) — background structure (bonus)
        raw = self._score_structure(structure_5m, direction)
        components["bos_choch_5m"] = {
            "score": round(raw, 3),
            "detail": (
                f"bos={structure_5m.get('bos')},choch={structure_5m.get('choch')}"
                if structure_5m else "no data"
            ),
        }

        # 8. Time Positioning (5) — sweet spot T-270 to T-240 (30-60s into window)
        if 240 <= remaining_s <= 270:
            raw = 1.0    # 30-60s into window — peak
        elif 210 <= remaining_s < 240:
            raw = 0.7    # 60-90s — still good
        elif 270 < remaining_s <= 290:
            raw = 0.6    # first 10-30s — building
        elif 180 <= remaining_s < 210:
            raw = 0.4    # 90-120s — late flow
        else:
            raw = 0.2    # outside flow window
        components["time_positioning"] = {
            "score": round(raw, 3), "detail": f"T-{remaining_s:.0f}s",
        }

        # 9. Implied Price Edge (5) — tighter gate at <= $0.52
        raw = 0.0
        if implied_price is not None:
            if implied_price < 0.45:
                raw = 1.0
            elif implied_price < 0.48:
                raw = 0.8
            elif implied_price < 0.50:
                raw = 0.6
            elif implied_price <= 0.52:
                raw = 0.4
            else:
                raw = 0.0  # Above 0.52 = no edge
        components["implied_price_edge"] = {
            "score": round(raw, 3),
            "detail": f"${implied_price:.3f}" if implied_price else "N/A",
        }

        # Calculate total weighted score
        total = 0.0
        for comp_name, comp_data in components.items():
            weight = COMPONENT_WEIGHTS[comp_name]
            weighted = comp_data["score"] * weight
            comp_data["weighted"] = round(weighted, 2)
            total += weighted

        total = round(total, 1)
        should_trade = total >= self.threshold

        result = ScoreResult(
            total_score=total,
            direction=direction,
            components=components,
            should_trade=should_trade,
            threshold=self.threshold,
        )
        self._last_score = result

        log.info(
            "[SCORE] %s %.1f/100 (threshold=%d) %s | "
            "flow=%.1f delta=%.1f ob=%.1f spread=%.1f "
            "fsus=%.1f dsus=%.1f bos5=%.1f time=%.1f edge=%.1f",
            direction.upper(), total, self.threshold,
            "FIRE" if should_trade else "SKIP",
            components["flow_strength"]["weighted"],
            components["delta_magnitude"]["weighted"],
            components["binance_imbalance"]["weighted"],
            components["clob_spread_compression"]["weighted"],
            components["flow_sustained"]["weighted"],
            components["delta_sustained"]["weighted"],
            components["bos_choch_5m"]["weighted"],
            components["time_positioning"]["weighted"],
            components["implied_price_edge"]["weighted"],
        )

        return result

    @staticmethod
    def _score_structure(structure: dict | None, direction: str) -> float:
        """Score a structure analysis dict for a given direction."""
        if not structure:
            return 0.3

        bos = structure.get("bos")
        choch = structure.get("choch")
        trend = structure.get("trend", "neutral")

        if bos == "bullish" and direction == "up":
            return 1.0
        if bos == "bearish" and direction == "down":
            return 1.0

        if choch == "bearish" and direction == "up":
            return 0.0
        if choch == "bullish" and direction == "down":
            return 0.0

        if trend == direction or (trend == "bullish" and direction == "up") \
                or (trend == "bearish" and direction == "down"):
            return 0.6

        if trend == "bullish" and direction == "down":
            return 0.1
        if trend == "bearish" and direction == "up":
            return 0.1

        return 0.3  # Neutral

    def get_last_score(self) -> ScoreResult | None:
        """Return the last computed score for dashboard display."""
        return self._last_score

    def reset_spread_history(self) -> None:
        """Clear spread history between windows."""
        self._spread_history.clear()

    def get_status(self) -> dict:
        """Dashboard-friendly scoring status."""
        last = self._last_score
        if not last:
            return {"active": False, "threshold": self.threshold}
        return {
            "active": True,
            "threshold": self.threshold,
            "last_score": last.total_score,
            "last_direction": last.direction,
            "should_trade": last.should_trade,
            "components": {
                name: {"score": c["score"], "weighted": c["weighted"]}
                for name, c in last.components.items()
            },
        }
