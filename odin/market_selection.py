"""Market Selection Protocol — score and filter markets before analysis.

Principle: Trade probabilities, not narratives. Score every market on
5 objective dimensions before committing analysis cycles.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger("odin.market_selection")


@dataclass
class MarketScore:
    """5-dimension market quality score."""
    symbol: str
    liquidity: float = 0.0        # 24h volume relative depth
    volatility: float = 0.0       # ATR percentile (sweet spot scoring)
    regime_clarity: float = 0.0   # How clear is directional bias
    manipulation_risk: float = 0.0  # Thin books, whale OI, funding extremes
    historical_edge: float = 0.0  # Past WR on this symbol+regime combo
    composite: float = 0.0

    @property
    def passed(self) -> bool:
        return self.composite >= 50


# Weights for composite score
WEIGHTS = {
    "liquidity": 0.25,
    "volatility": 0.20,
    "regime_clarity": 0.25,
    "manipulation_risk": 0.15,
    "historical_edge": 0.15,
}


class MarketSelector:
    """Score markets on 5 dimensions before committing analysis cycles."""

    def __init__(self, min_score: int = 50):
        self._min_score = min_score

    def score(
        self,
        symbol: str,
        regime_data: dict | None = None,
        cg_metrics: dict | None = None,
        journal_fitness: dict | None = None,
    ) -> MarketScore:
        """Score a market on 5 sub-scores (0-100)."""
        regime_data = regime_data or {}
        cg_metrics = cg_metrics or {}
        journal_fitness = journal_fitness or {}

        ms = MarketScore(symbol=symbol)

        # 1. Liquidity: 24h volume scoring
        vol_24h = cg_metrics.get("volume_24h_usd", 0)
        if vol_24h >= 1_000_000_000:
            ms.liquidity = 95  # >$1B — very liquid
        elif vol_24h >= 500_000_000:
            ms.liquidity = 85
        elif vol_24h >= 100_000_000:
            ms.liquidity = 70
        elif vol_24h >= 50_000_000:
            ms.liquidity = 55
        elif vol_24h >= 10_000_000:
            ms.liquidity = 40
        else:
            ms.liquidity = 20  # Too thin

        # 2. Volatility: ATR percentile sweet spot
        # Too low = no edge, too high = noise. Sweet spot = 30-70th percentile.
        atr_pct = cg_metrics.get("atr_percentile", 50)
        if 30 <= atr_pct <= 70:
            ms.volatility = 85  # Sweet spot
        elif 20 <= atr_pct <= 80:
            ms.volatility = 65
        elif atr_pct < 15:
            ms.volatility = 25  # Dead market
        elif atr_pct > 90:
            ms.volatility = 30  # Too wild
        else:
            ms.volatility = 50

        # 3. Regime clarity: how clear is the directional bias
        confidence = regime_data.get("confidence", 0)
        bias = regime_data.get("direction_bias", "NONE")
        regime_val = regime_data.get("regime", "neutral")

        if bias != "NONE" and confidence >= 70:
            ms.regime_clarity = 90
        elif bias != "NONE" and confidence >= 50:
            ms.regime_clarity = 70
        elif regime_val in ("trending", "strong_bull", "strong_bear"):
            ms.regime_clarity = 60
        elif regime_val == "neutral":
            ms.regime_clarity = 40
        else:
            ms.regime_clarity = 30  # Choppy / unclear

        # 4. Manipulation risk: funding extremes, OI concentration
        funding = abs(cg_metrics.get("funding_rate", 0))
        oi_change_pct = abs(cg_metrics.get("oi_change_4h_pct", 0))
        ls_ratio = cg_metrics.get("long_short_ratio", 1.0)

        manip_score = 100  # Start perfect, deduct for risk flags
        if funding > 0.01:
            manip_score -= 30  # Extreme funding
        elif funding > 0.005:
            manip_score -= 15
        if oi_change_pct > 10:
            manip_score -= 20  # Sudden OI spike
        if ls_ratio > 2.0 or ls_ratio < 0.5:
            manip_score -= 20  # Crowded trade
        ms.manipulation_risk = max(0, manip_score)

        # 5. Historical edge: past WR on this combo
        wr = journal_fitness.get("win_rate", 50)
        samples = journal_fitness.get("sample_size", 0)
        if samples >= 10:
            ms.historical_edge = min(100, wr * 1.2)  # Scale WR
        elif samples >= 5:
            ms.historical_edge = min(100, wr * 1.0)
        else:
            ms.historical_edge = 50  # Insufficient data = neutral

        # Composite weighted average
        ms.composite = round(
            ms.liquidity * WEIGHTS["liquidity"] +
            ms.volatility * WEIGHTS["volatility"] +
            ms.regime_clarity * WEIGHTS["regime_clarity"] +
            ms.manipulation_risk * WEIGHTS["manipulation_risk"] +
            ms.historical_edge * WEIGHTS["historical_edge"],
            1,
        )

        return ms

    def rank(
        self,
        candidates: list[dict],
        regime_data: dict | None = None,
    ) -> list[tuple[str, MarketScore]]:
        """Score and rank all candidates. Filter below min_score."""
        scored = []
        for c in candidates:
            symbol = c.get("symbol", "")
            ms = self.score(
                symbol=symbol,
                regime_data=regime_data,
                cg_metrics=c.get("cg_metrics", {}),
                journal_fitness=c.get("journal_fitness", {}),
            )

            status = "PASS" if ms.passed else "SKIP"
            log.info(
                "[MARKET] %s: liq=%.0f vol=%.0f regime=%.0f manip=%.0f hist=%.0f → %.0f %s",
                symbol, ms.liquidity, ms.volatility, ms.regime_clarity,
                ms.manipulation_risk, ms.historical_edge, ms.composite, status,
            )

            if ms.composite >= self._min_score:
                scored.append((symbol, ms))

        scored.sort(key=lambda x: x[1].composite, reverse=True)
        return scored
