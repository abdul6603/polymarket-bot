"""Garves V2 — Market Quality Scoring.

Scores every market on 6 dimensions BEFORE signal generation.
Low-quality markets never enter the signal pipeline.

Dimensions:
1. Liquidity — book depth (reuses orderbook_check.py)
2. Spread — bid-ask tightness
3. Volatility — ATR in sweet spot (not flat, not chaotic)
4. Time to Resolution — 30-70% of window remaining = best
5. Information Clarity — price stability (no whipsawing)
6. Manipulation Risk — thin book + extreme imbalance = suspicious
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from bot.orderbook_check import check_orderbook_depth, OrderbookAnalysis

log = logging.getLogger(__name__)

# Quality thresholds
QUALITY_FLOOR = 25          # Minimum total score to enter signal pipeline (out of 60)
QUALITY_CACHE_TTL = 60      # Cache quality scores for 60 seconds per market


@dataclass
class MarketQualityScore:
    """Quality assessment for a single market."""
    market_id: str
    total_score: float = 0.0          # 0-60
    liquidity_score: float = 0.0      # 0-10
    spread_score: float = 0.0         # 0-10
    volatility_score: float = 0.0     # 0-10
    time_to_resolution: float = 0.0   # 0-10
    information_clarity: float = 0.0  # 0-10
    manipulation_risk: float = 0.0    # 0-10
    passed: bool = False
    reason: str = ""
    scored_at: float = field(default_factory=time.time)


class MarketQualityScorer:
    """Scores market quality on 6 dimensions before signal generation.

    Usage:
        scorer = MarketQualityScorer(clob_host)
        score = scorer.score(market_id, token_id, remaining_s, tf, asset)
        if score.passed:
            # Proceed to signal generation
    """

    def __init__(self, clob_host: str, price_cache=None):
        self.clob_host = clob_host
        self.price_cache = price_cache
        self._cache: dict[str, MarketQualityScore] = {}

    def score(
        self,
        market_id: str,
        token_id: str,
        remaining_s: float,
        tf: str,
        asset: str,
    ) -> MarketQualityScore:
        """Score a market on 6 quality dimensions.

        Args:
            market_id: Polymarket condition_id
            token_id: UP token_id for orderbook lookup
            remaining_s: Seconds until market resolution
            tf: Timeframe name ("5m", "15m", "1h", "4h", "weekly")
            asset: Asset name ("bitcoin", "ethereum", etc.)

        Returns:
            MarketQualityScore with total 0-60 and per-dimension breakdowns.
        """
        now = time.time()

        # Check cache
        cached = self._cache.get(market_id)
        if cached and (now - cached.scored_at) < QUALITY_CACHE_TTL:
            return cached

        result = MarketQualityScore(market_id=market_id, scored_at=now)

        # 1. Liquidity + 2. Spread — from orderbook check
        ob_ok, ob_reason, ob_analysis = check_orderbook_depth(
            clob_host=self.clob_host,
            token_id=token_id,
            order_size_usd=10.0,
            target_price=0.50,
        )
        result.liquidity_score = self._score_liquidity(ob_analysis)
        result.spread_score = self._score_spread(ob_analysis)

        # 3. Volatility — from price cache ATR
        result.volatility_score = self._score_volatility(asset)

        # 4. Time to Resolution — sweet spot is 30-70% of window
        result.time_to_resolution = self._score_time(remaining_s, tf)

        # 5. Information Clarity — price stability from orderbook
        result.information_clarity = self._score_clarity(ob_analysis)

        # 6. Manipulation Risk — thin book + extreme imbalance
        result.manipulation_risk = self._score_manipulation(ob_analysis)

        # Total
        result.total_score = (
            result.liquidity_score
            + result.spread_score
            + result.volatility_score
            + result.time_to_resolution
            + result.information_clarity
            + result.manipulation_risk
        )
        result.passed = result.total_score >= QUALITY_FLOOR

        if not result.passed:
            # Find the weakest dimension for the reason
            dimensions = {
                "liquidity": result.liquidity_score,
                "spread": result.spread_score,
                "volatility": result.volatility_score,
                "time": result.time_to_resolution,
                "clarity": result.information_clarity,
                "manipulation": result.manipulation_risk,
            }
            weakest = min(dimensions, key=dimensions.get)
            result.reason = f"quality_floor: {result.total_score:.0f}/{QUALITY_FLOOR} (weakest: {weakest}={dimensions[weakest]:.1f})"

        # Cache
        self._cache[market_id] = result
        return result

    @staticmethod
    def _score_liquidity(ob: OrderbookAnalysis | None) -> float:
        """Score 0-10 based on total book depth."""
        if ob is None:
            return 5.0  # Unknown — neutral
        liq = ob.total_liquidity_usd
        if liq >= 1000:
            return 10.0
        if liq >= 500:
            return 8.0
        if liq >= 300:
            return 6.0
        if liq >= 150:
            return 4.0
        if liq >= 50:
            return 2.0
        return 0.0

    @staticmethod
    def _score_spread(ob: OrderbookAnalysis | None) -> float:
        """Score 0-10 based on bid-ask spread tightness."""
        if ob is None:
            return 5.0
        spread = ob.spread
        if spread <= 0.01:
            return 10.0
        if spread <= 0.02:
            return 8.0
        if spread <= 0.03:
            return 7.0
        if spread <= 0.04:
            return 5.0
        if spread <= 0.06:
            return 3.0
        return 0.0

    def _score_volatility(self, asset: str) -> float:
        """Score 0-10: ATR in sweet spot (not flat, not chaotic)."""
        if self.price_cache is None:
            return 5.0

        candles = self.price_cache.get_candles(asset, 20)
        if not candles or len(candles) < 5:
            return 5.0

        # Calculate simple ATR from candles
        atr_sum = 0.0
        for c in candles[-14:]:
            atr_sum += c.high - c.low
        atr = atr_sum / min(len(candles[-14:]), 14)
        avg_price = candles[-1].close if candles[-1].close > 0 else 1.0
        atr_pct = atr / avg_price

        if atr_pct < 0.0003:
            return 2.0   # Too flat — random
        if atr_pct < 0.001:
            return 5.0   # Low vol — okay
        if atr_pct < 0.003:
            return 9.0   # Sweet spot — trending
        if atr_pct < 0.008:
            return 7.0   # High vol — still tradeable
        if atr_pct < 0.015:
            return 4.0   # Very high — choppy
        return 1.0       # Extreme — unreliable

    @staticmethod
    def _score_time(remaining_s: float, tf: str) -> float:
        """Score 0-10: best when 30-70% of window remaining."""
        # Map timeframe to total window duration
        tf_windows = {
            "5m": 300, "15m": 900, "1h": 3600,
            "4h": 14400, "weekly": 604800,
        }
        total = tf_windows.get(tf, 900)
        pct_remaining = remaining_s / total if total > 0 else 0.5

        if 0.30 <= pct_remaining <= 0.70:
            return 10.0  # Sweet spot
        if 0.20 <= pct_remaining < 0.30 or 0.70 < pct_remaining <= 0.80:
            return 7.0   # Decent
        if 0.10 <= pct_remaining < 0.20 or 0.80 < pct_remaining <= 0.90:
            return 4.0   # Early or late
        return 2.0       # Too early or too late

    @staticmethod
    def _score_clarity(ob: OrderbookAnalysis | None) -> float:
        """Score 0-10: price stability (balanced book = stable price)."""
        if ob is None:
            return 5.0

        # If bid and ask liquidity are balanced, price is stable
        if ob.bid_liquidity_usd <= 0 or ob.ask_liquidity_usd <= 0:
            return 2.0

        ratio = min(ob.bid_liquidity_usd, ob.ask_liquidity_usd) / max(ob.bid_liquidity_usd, ob.ask_liquidity_usd)
        # ratio 1.0 = perfectly balanced, 0.0 = completely one-sided
        if ratio >= 0.7:
            return 9.0
        if ratio >= 0.5:
            return 7.0
        if ratio >= 0.3:
            return 5.0
        if ratio >= 0.15:
            return 3.0
        return 1.0

    @staticmethod
    def _score_manipulation(ob: OrderbookAnalysis | None) -> float:
        """Score 0-10: higher = LESS manipulation risk (inverted for consistency)."""
        if ob is None:
            return 5.0

        # Thin book + extreme imbalance = suspicious
        total_liq = ob.total_liquidity_usd
        if total_liq <= 0:
            return 0.0

        bid_pct = ob.bid_liquidity_usd / total_liq
        imbalance = abs(bid_pct - 0.5)  # 0 = balanced, 0.5 = completely one-sided

        # Thin book is worse
        if total_liq < 100:
            if imbalance > 0.3:
                return 0.0  # Very suspicious
            return 2.0
        if total_liq < 200:
            if imbalance > 0.3:
                return 3.0
            return 5.0
        # Thick book — manipulation harder
        if imbalance > 0.4:
            return 4.0
        if imbalance > 0.3:
            return 6.0
        return 9.0

    def get_cache_stats(self) -> dict:
        """Return cache statistics for monitoring."""
        now = time.time()
        active = sum(1 for v in self._cache.values() if (now - v.scored_at) < QUALITY_CACHE_TTL)
        passed = sum(1 for v in self._cache.values() if v.passed and (now - v.scored_at) < QUALITY_CACHE_TTL)
        return {
            "cached": len(self._cache),
            "active": active,
            "passed": passed,
            "quality_floor": QUALITY_FLOOR,
        }
