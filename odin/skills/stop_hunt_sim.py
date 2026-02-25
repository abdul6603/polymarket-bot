"""Stop Hunt Simulator — tests SL placement against historical wicks.

Before placing a stop-loss, simulates 100 historical wick patterns
to find the optimal SL that avoids stop hunts while staying tight.
"""
from __future__ import annotations

import logging
import numpy as np
from dataclasses import dataclass

log = logging.getLogger("odin.skills.stop_hunt_sim")


@dataclass
class SLSimResult:
    """Result of stop-loss simulation."""
    original_sl: float
    optimized_sl: float
    survival_rate: float       # % of wicks that didn't hit optimized SL
    original_survival: float   # % of wicks that didn't hit original SL
    improvement_pct: float     # How much better the optimized SL is
    wick_stats: dict           # Historical wick statistics
    recommendation: str        # "keep", "widen", "tighten"


class StopHuntSimulator:
    """Simulates historical wick patterns to optimize SL placement."""

    def __init__(self, min_simulations: int = 100):
        self._min_sims = min_simulations
        self._sim_count = 0
        self._saved_pips = 0.0

    def simulate(
        self,
        entry_price: float,
        original_sl: float,
        direction: str,
        historical_data: list[dict] | None = None,
        candle_df=None,
    ) -> SLSimResult:
        """Simulate wicks against SL to find optimal placement.

        Args:
            entry_price: Trade entry price
            original_sl: Proposed stop-loss price
            direction: "LONG" or "SHORT"
            historical_data: List of candle dicts with high/low/close
            candle_df: Pandas DataFrame (alternative to historical_data)
        """
        self._sim_count += 1

        # Get wick data
        wicks = self._extract_wicks(direction, historical_data, candle_df)
        if len(wicks) < 20:
            return SLSimResult(
                original_sl=original_sl,
                optimized_sl=original_sl,
                survival_rate=50,
                original_survival=50,
                improvement_pct=0,
                wick_stats={"samples": len(wicks)},
                recommendation="insufficient_data",
            )

        sl_distance = abs(entry_price - original_sl)
        sl_distance_pct = sl_distance / entry_price * 100

        # Analyze wick distribution
        wick_pcts = np.array(wicks)
        p50 = float(np.percentile(wick_pcts, 50))
        p75 = float(np.percentile(wick_pcts, 75))
        p90 = float(np.percentile(wick_pcts, 90))
        p95 = float(np.percentile(wick_pcts, 95))
        p99 = float(np.percentile(wick_pcts, 99))
        mean_wick = float(np.mean(wick_pcts))
        max_wick = float(np.max(wick_pcts))

        # Test original SL survival
        original_hits = sum(1 for w in wick_pcts if w >= sl_distance_pct)
        original_survival = (1 - original_hits / len(wick_pcts)) * 100

        # Find optimal SL: survive 90%+ of wicks with minimal distance
        # Strategy: place SL just beyond 90th percentile wick
        optimal_distance_pct = p90 * 1.10  # 10% buffer beyond P90

        # Enforce bounds
        min_sl = sl_distance_pct * 0.7  # Don't tighten more than 30%
        max_sl = sl_distance_pct * 1.5  # Don't widen more than 50%
        optimal_distance_pct = max(min_sl, min(max_sl, optimal_distance_pct))

        # Calculate optimized SL price
        if direction.upper() == "LONG":
            optimized_sl = entry_price * (1 - optimal_distance_pct / 100)
        else:
            optimized_sl = entry_price * (1 + optimal_distance_pct / 100)

        optimized_hits = sum(1 for w in wick_pcts if w >= optimal_distance_pct)
        optimized_survival = (1 - optimized_hits / len(wick_pcts)) * 100

        improvement = optimized_survival - original_survival

        # Recommendation
        if abs(improvement) < 2:
            recommendation = "keep"
        elif optimized_survival > original_survival:
            recommendation = "widen"
            self._saved_pips += abs(optimized_sl - original_sl)
        else:
            recommendation = "tighten"

        result = SLSimResult(
            original_sl=round(original_sl, 2),
            optimized_sl=round(optimized_sl, 2),
            survival_rate=round(optimized_survival, 1),
            original_survival=round(original_survival, 1),
            improvement_pct=round(improvement, 1),
            wick_stats={
                "samples": len(wicks),
                "mean_wick_pct": round(mean_wick, 3),
                "p50": round(p50, 3),
                "p75": round(p75, 3),
                "p90": round(p90, 3),
                "p95": round(p95, 3),
                "p99": round(p99, 3),
                "max_wick_pct": round(max_wick, 3),
                "original_distance_pct": round(sl_distance_pct, 3),
                "optimized_distance_pct": round(optimal_distance_pct, 3),
            },
            recommendation=recommendation,
        )

        log.info(
            "[SL_SIM] %s @ $%.2f: SL $%.2f → $%.2f (survival %.0f%% → %.0f%%) [%s]",
            direction, entry_price, original_sl, optimized_sl,
            original_survival, optimized_survival, recommendation,
        )

        return result

    def _extract_wicks(
        self,
        direction: str,
        historical: list[dict] | None,
        df=None,
    ) -> list[float]:
        """Extract wick percentages from historical data.

        For LONG: measures downside wicks (how far price dips below open/close)
        For SHORT: measures upside wicks (how far price spikes above open/close)
        """
        wicks = []

        if df is not None and len(df) > 0:
            highs = df["high"].values
            lows = df["low"].values
            opens = df["open"].values
            closes = df["close"].values

            for i in range(len(df)):
                body_low = min(opens[i], closes[i])
                body_high = max(opens[i], closes[i])
                mid = (body_low + body_high) / 2

                if mid <= 0:
                    continue

                if direction.upper() == "LONG":
                    wick_pct = (body_low - lows[i]) / mid * 100
                else:
                    wick_pct = (highs[i] - body_high) / mid * 100

                if wick_pct > 0:
                    wicks.append(wick_pct)

        elif historical:
            for candle in historical:
                h = candle.get("high", 0)
                l = candle.get("low", 0)
                o = candle.get("open", 0)
                c = candle.get("close", 0)
                body_low = min(o, c)
                body_high = max(o, c)
                mid = (body_low + body_high) / 2

                if mid <= 0:
                    continue

                if direction.upper() == "LONG":
                    wick_pct = (body_low - l) / mid * 100
                else:
                    wick_pct = (h - body_high) / mid * 100

                if wick_pct > 0:
                    wicks.append(wick_pct)

        return wicks

    def get_status(self) -> dict:
        return {
            "simulations_run": self._sim_count,
            "saved_pips": round(self._saved_pips, 2),
        }
