"""Multi-component signal scoring engine for 5m BTC binary snipe.

Combines Binance L2 orderflow + Polymarket CLOB orderbook data + SMC
structure analysis into a single 0-100 score.

Only fires when score >= threshold (default 75).

10 components, each scored 0.0-1.0, multiplied by weight:
  delta_magnitude(15) + delta_sustained(12) + binance_imbalance(15) +
  clob_spread_compression(10) + clob_yes_no_pressure(10) + volume_delta(8) +
  bos_choch_5m(12) + bos_choch_15m(8) + time_positioning(5) +
  implied_price_edge(5) = 100
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

log = logging.getLogger("garves.snipe")

DEFAULT_THRESHOLD = 75

COMPONENT_WEIGHTS = {
    "delta_magnitude":         15,
    "delta_sustained":         12,
    "binance_imbalance":       15,
    "clob_spread_compression": 10,
    "clob_yes_no_pressure":    10,
    "volume_delta":             8,
    "bos_choch_5m":            12,
    "bos_choch_15m":            8,
    "time_positioning":         5,
    "implied_price_edge":       5,
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
    """10-component signal scoring engine for 5m BTC binary snipe."""

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
    ) -> ScoreResult:
        """Score the signal across all 10 components.

        Args:
            direction: "up" or "down" (from delta analysis)
            delta_pct: absolute delta % (e.g. 0.12 for 0.12%)
            sustained_ticks: consecutive ticks in same direction
            ob_imbalance: Binance L2 imbalance (-1 to +1), None if disconnected
            ob_strength: Binance imbalance strength (0-1), None if no signal
            clob_book: CLOB orderbook for target token (YES for up, NO for down)
            clob_book_opposite: CLOB orderbook for opposite token
            structure_5m: {bos, choch, trend} from CandleStore 5m
            structure_15m: {bos, choch, trend} from CandleStore 15m
            remaining_s: seconds until window closes
            implied_price: current CLOB token price (0-1)

        Returns:
            ScoreResult with total 0-100, per-component breakdown, should_trade
        """
        components = {}

        # 1. Delta Magnitude (15) — how far BTC moved from window open
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

        # 2. Delta Sustained (12) — consecutive ticks confirming direction
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

        # 4. CLOB Spread Compression (10) — spread tightening = conviction
        raw = 0.3
        if clob_book:
            spread = clob_book.get("spread", 0)
            now = time.time()
            self._spread_history.append((now, spread))
            # Keep last 30s of spread readings
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

        # 5. CLOB YES/NO Pressure (10) — directional buying pressure
        raw = 0.3
        if clob_book and clob_book_opposite:
            target_buy = clob_book.get("buy_pressure", 0)
            opp_buy = clob_book_opposite.get("buy_pressure", 0)
            total = target_buy + opp_buy
            if total > 0:
                ratio = target_buy / total
                if ratio > 0.65:
                    raw = 1.0
                elif ratio > 0.55:
                    raw = 0.5 + (ratio - 0.55) / 0.10 * 0.5
                elif ratio > 0.45:
                    raw = 0.3 + (ratio - 0.45) / 0.10 * 0.2
                else:
                    raw = ratio / 0.45 * 0.3
        components["clob_yes_no_pressure"] = {
            "score": round(raw, 3),
            "detail": f"ratio={raw:.2f}",
        }

        # 6. Volume Delta (8) — buy/sell volume imbalance on target token
        raw = 0.3
        if clob_book:
            buy_p = clob_book.get("buy_pressure", 0)
            sell_p = clob_book.get("sell_pressure", 0)
            total = buy_p + sell_p
            if total > 0:
                vol_imb = (buy_p - sell_p) / total
                # Positive imbalance = more buyers on target token = confirms direction
                if vol_imb > 0.30:
                    raw = 1.0
                elif vol_imb > 0.15:
                    raw = 0.5 + (vol_imb - 0.15) / 0.15 * 0.5
                elif vol_imb > 0:
                    raw = 0.3 + vol_imb / 0.15 * 0.2
                else:
                    raw = max(0.0, 0.3 + vol_imb)
        components["volume_delta"] = {
            "score": round(raw, 3), "detail": "",
        }

        # 7. BOS/CHoCH 5m (12) — structure break on 5-minute chart
        raw = self._score_structure(structure_5m, direction)
        components["bos_choch_5m"] = {
            "score": round(raw, 3),
            "detail": (
                f"bos={structure_5m.get('bos')},choch={structure_5m.get('choch')}"
                if structure_5m else "no data"
            ),
        }

        # 8. BOS/CHoCH 15m (8) — structure alignment on 15-minute chart
        raw = self._score_structure(structure_15m, direction)
        components["bos_choch_15m"] = {
            "score": round(raw, 3),
            "detail": (
                f"trend={structure_15m.get('trend')}"
                if structure_15m else "no data"
            ),
        }

        # 9. Time Positioning (5) — sweet spot T-60s to T-30s
        if 30 <= remaining_s <= 60:
            raw = 1.0
        elif 60 < remaining_s <= 90:
            raw = 0.7
        elif 20 <= remaining_s < 30:
            raw = 0.6
        elif 90 < remaining_s <= 120:
            raw = 0.5
        elif remaining_s < 20:
            raw = 0.2
        else:
            raw = 0.3
        components["time_positioning"] = {
            "score": round(raw, 3), "detail": f"T-{remaining_s:.0f}s",
        }

        # 10. Implied Price Edge (5) — cheaper token = better risk/reward
        raw = 0.3
        if implied_price is not None:
            if implied_price < 0.45:
                raw = 1.0
            elif implied_price < 0.50:
                raw = 0.8
            elif implied_price < 0.55:
                raw = 0.6
            elif implied_price < 0.60:
                raw = 0.4
            elif implied_price < 0.65:
                raw = 0.2
            else:
                raw = 0.0
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
            "delta=%.1f sus=%.1f ob=%.1f clob_sp=%.1f clob_p=%.1f "
            "vol=%.1f bos5=%.1f bos15=%.1f time=%.1f edge=%.1f",
            direction.upper(), total, self.threshold,
            "FIRE" if should_trade else "SKIP",
            components["delta_magnitude"]["weighted"],
            components["delta_sustained"]["weighted"],
            components["binance_imbalance"]["weighted"],
            components["clob_spread_compression"]["weighted"],
            components["clob_yes_no_pressure"]["weighted"],
            components["volume_delta"]["weighted"],
            components["bos_choch_5m"]["weighted"],
            components["bos_choch_15m"]["weighted"],
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

        # BOS confirms direction = strong
        if bos == "bullish" and direction == "up":
            return 1.0
        if bos == "bearish" and direction == "down":
            return 1.0

        # CHoCH opposes direction = very bad
        if choch == "bearish" and direction == "up":
            return 0.0
        if choch == "bullish" and direction == "down":
            return 0.0

        # Trend alignment without BOS
        if trend == direction or (trend == "bullish" and direction == "up") \
                or (trend == "bearish" and direction == "down"):
            return 0.6

        # Opposing trend
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
