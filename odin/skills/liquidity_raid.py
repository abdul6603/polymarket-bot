"""Liquidity Raid Predictor — predicts stop hunts at key levels.

Combines SMC liquidity zones, CoinGlass OI/liquidation data,
and price proximity to predict upcoming stop hunts.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

log = logging.getLogger("odin.skills.liquidity_raid")


@dataclass
class RaidPrediction:
    """A predicted liquidity raid."""
    level: float
    direction: str          # "above" or "below" (where the raid targets)
    probability: float      # 0-100
    target_type: str        # equal_highs, equal_lows, prev_day_high, prev_day_low, ob_zone
    reasons: list[str] = field(default_factory=list)
    estimated_magnitude_pct: float = 0.0
    time_horizon_minutes: int = 60


class LiquidityRaidPredictor:
    """Predicts stop hunts using multi-source confluence."""

    def __init__(self):
        self._predictions: list[RaidPrediction] = []
        self._hit_count = 0
        self._miss_count = 0

    def predict_raids(
        self,
        symbol: str,
        current_price: float,
        smc_data: dict,
        coinglass_data: dict | None = None,
        prev_day: dict | None = None,
    ) -> list[RaidPrediction]:
        """Generate raid predictions from multiple data sources.

        Args:
            symbol: Trading pair
            current_price: Current price
            smc_data: SMC analysis with liquidity_zones, active_obs
            coinglass_data: Optional CoinGlass data (OI, liquidations, L/S)
            prev_day: Optional dict with prev_day_high, prev_day_low
        """
        predictions: list[RaidPrediction] = []

        # Source 1: Equal highs/lows from SMC
        predictions.extend(
            self._score_liquidity_zones(current_price, smc_data)
        )

        # Source 2: Previous day extremes
        if prev_day:
            predictions.extend(
                self._score_prev_day(current_price, prev_day)
            )

        # Source 3: CoinGlass OI/liquidation buildup
        if coinglass_data:
            self._enhance_with_coinglass(predictions, coinglass_data, current_price)

        # Source 4: OB zones as magnets
        predictions.extend(
            self._score_ob_magnets(current_price, smc_data)
        )

        # Sort by probability
        predictions.sort(key=lambda p: p.probability, reverse=True)
        self._predictions = predictions[:10]

        if predictions:
            top = predictions[0]
            log.info(
                "[RAID] %s: top prediction=%s @ $%.2f (%.0f%%) %s",
                symbol, top.target_type, top.level,
                top.probability, top.direction,
            )

        return self._predictions

    def _score_liquidity_zones(self, price: float, smc_data: dict) -> list[RaidPrediction]:
        """Score SMC liquidity zones as raid targets."""
        preds = []
        zones = smc_data.get("liquidity_zones", [])

        for zone in zones:
            level = zone.get("price_level", 0)
            if level <= 0:
                continue

            direction = zone.get("direction", "")
            if hasattr(direction, "name"):
                direction = direction.name

            zone_type = zone.get("details", {}).get("type", "unknown")
            touches = zone.get("details", {}).get("touches", 0)
            strength = zone.get("strength", 0)

            distance_pct = abs(level - price) / price * 100
            if distance_pct > 5:
                continue

            # Closer = higher probability
            proximity_score = max(0, 50 - distance_pct * 10)
            # More touches = more liquidity sitting there
            touch_score = min(touches * 10, 30)
            # Zone strength
            strength_score = strength * 0.2

            prob = min(proximity_score + touch_score + strength_score, 95)

            is_above = level > price
            reasons = [
                f"{touches} equal {'highs' if is_above else 'lows'} cluster",
                f"{distance_pct:.1f}% away",
            ]

            preds.append(RaidPrediction(
                level=level,
                direction="above" if is_above else "below",
                probability=round(prob, 1),
                target_type=f"equal_{'highs' if is_above else 'lows'}",
                reasons=reasons,
                estimated_magnitude_pct=round(distance_pct * 1.2, 2),
            ))

        return preds

    def _score_prev_day(self, price: float, prev_day: dict) -> list[RaidPrediction]:
        """Previous day high/low as raid targets."""
        preds = []

        pdh = prev_day.get("high", 0)
        pdl = prev_day.get("low", 0)

        if pdh > 0 and pdh > price:
            dist = (pdh - price) / price * 100
            if dist < 3:
                prob = max(0, 60 - dist * 15)
                preds.append(RaidPrediction(
                    level=pdh,
                    direction="above",
                    probability=round(prob, 1),
                    target_type="prev_day_high",
                    reasons=[f"PDH @ ${pdh:.2f}", f"{dist:.1f}% above"],
                    estimated_magnitude_pct=round(dist * 0.5, 2),
                ))

        if pdl > 0 and pdl < price:
            dist = (price - pdl) / price * 100
            if dist < 3:
                prob = max(0, 60 - dist * 15)
                preds.append(RaidPrediction(
                    level=pdl,
                    direction="below",
                    probability=round(prob, 1),
                    target_type="prev_day_low",
                    reasons=[f"PDL @ ${pdl:.2f}", f"{dist:.1f}% below"],
                    estimated_magnitude_pct=round(dist * 0.5, 2),
                ))

        return preds

    def _enhance_with_coinglass(
        self, predictions: list[RaidPrediction], cg: dict, price: float
    ) -> None:
        """Enhance predictions with CoinGlass data."""
        funding = cg.get("funding_rate", 0)
        oi_change = cg.get("oi_change_1h", 0)
        long_ratio = cg.get("long_ratio", 0.5)
        liq_long = cg.get("liq_long_24h", 0)
        liq_short = cg.get("liq_short_24h", 0)

        for pred in predictions:
            boost = 0
            # High funding + above raid = more likely (stops above are targets)
            if pred.direction == "above" and funding > 0.005:
                boost += 10
                pred.reasons.append("High funding = longs overleveraged")
            # Negative funding + below raid = more likely
            if pred.direction == "below" and funding < -0.003:
                boost += 10
                pred.reasons.append("Negative funding = shorts overleveraged")
            # OI rising rapidly = more stops being placed
            if oi_change > 5:
                boost += 8
                pred.reasons.append(f"OI surge +{oi_change:.1f}%")
            # Crowded longs + above raid
            if pred.direction == "above" and long_ratio > 0.60:
                boost += 7
                pred.reasons.append(f"Longs crowded {long_ratio:.0%}")
            # Heavy liq on one side = cascade potential
            total_liq = liq_long + liq_short
            if total_liq > 0:
                if pred.direction == "below" and liq_long / total_liq > 0.7:
                    boost += 5
                    pred.reasons.append("Long liquidation cascade risk")
                if pred.direction == "above" and liq_short / total_liq > 0.7:
                    boost += 5
                    pred.reasons.append("Short squeeze potential")

            pred.probability = min(pred.probability + boost, 95)

    def _score_ob_magnets(self, price: float, smc_data: dict) -> list[RaidPrediction]:
        """Unmitigated OBs act as price magnets."""
        preds = []
        obs = smc_data.get("active_obs", [])

        for ob in obs:
            level = ob.get("price_level", 0)
            if level <= 0:
                continue

            strength = ob.get("strength", 0)
            if strength < 60:
                continue

            dist_pct = abs(level - price) / price * 100
            if dist_pct > 4:
                continue

            is_above = level > price
            prob = min(strength * 0.4 + max(0, 30 - dist_pct * 8), 85)

            preds.append(RaidPrediction(
                level=level,
                direction="above" if is_above else "below",
                probability=round(prob, 1),
                target_type="ob_zone",
                reasons=[
                    f"Unmitigated OB (str={strength:.0f})",
                    f"{dist_pct:.1f}% away — price magnet",
                ],
                estimated_magnitude_pct=round(dist_pct, 2),
            ))

        return preds

    def record_outcome(self, was_hit: bool) -> None:
        """Record whether a prediction was hit."""
        if was_hit:
            self._hit_count += 1
        else:
            self._miss_count += 1

    def get_status(self) -> dict:
        total = self._hit_count + self._miss_count
        return {
            "active_predictions": len(self._predictions),
            "hit_count": self._hit_count,
            "miss_count": self._miss_count,
            "accuracy": round(self._hit_count / max(total, 1) * 100, 1),
            "top_prediction": {
                "level": self._predictions[0].level,
                "probability": self._predictions[0].probability,
                "type": self._predictions[0].target_type,
            } if self._predictions else None,
        }
