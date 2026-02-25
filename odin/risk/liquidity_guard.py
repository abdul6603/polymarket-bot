"""Dynamic risk scaling — volatility, drawdown, and liquidity.

Principle: Survival first. These scalars only REDUCE risk, never increase.
All capped at 1.0 max — multiplicative on base risk.
"""
from __future__ import annotations

import logging

log = logging.getLogger("odin.risk.liquidity_guard")


class LiquidityGuard:
    """Dynamic risk scaling based on market conditions."""

    def get_volatility_scalar(self, regime: str, atr_percentile: float = 50) -> float:
        """Scale risk down in high-volatility environments.

        Returns 0.5 (extreme vol) to 1.0 (normal). Never increases risk.
        """
        regime_lower = regime.lower() if regime else "neutral"

        # Regime-based baseline
        if regime_lower in ("extreme_fear", "extreme_greed"):
            base = 0.6
        elif regime_lower in ("choppy",):
            base = 0.7
        elif regime_lower in ("trending", "strong_bull", "strong_bear"):
            base = 1.0
        else:
            base = 0.85

        # ATR percentile adjustment
        if atr_percentile > 90:
            atr_mult = 0.5  # Extreme volatility
        elif atr_percentile > 75:
            atr_mult = 0.7
        elif atr_percentile > 60:
            atr_mult = 0.85
        else:
            atr_mult = 1.0

        scalar = min(1.0, base * atr_mult)

        if scalar < 1.0:
            log.debug("[LIQUIDITY] Volatility scalar: %.2f (regime=%s, atr_pct=%.0f)",
                       scalar, regime, atr_percentile)

        return round(scalar, 2)

    def get_drawdown_scalar(self, daily_pnl_pct: float, weekly_pnl_pct: float) -> float:
        """Scale risk down when approaching loss limits.

        Returns 0.3 (near limit) to 1.0 (fresh). Smooth degradation.
        """
        # Daily PnL based (limits: -3% daily, -6% weekly)
        if daily_pnl_pct <= -8:
            scalar = 0.3  # Near daily limit
        elif daily_pnl_pct <= -5:
            scalar = 0.5
        elif daily_pnl_pct <= -3:
            scalar = 0.7
        elif daily_pnl_pct <= -1:
            scalar = 0.85
        else:
            scalar = 1.0

        # Weekly PnL overlay
        if weekly_pnl_pct <= -5:
            scalar = min(scalar, 0.4)
        elif weekly_pnl_pct <= -3:
            scalar = min(scalar, 0.6)

        if scalar < 1.0:
            log.debug("[LIQUIDITY] Drawdown scalar: %.2f (daily=%.1f%%, weekly=%.1f%%)",
                       scalar, daily_pnl_pct, weekly_pnl_pct)

        return round(scalar, 2)

    def check_exit_liquidity(self, symbol: str, position_size_usd: float,
                              volume_24h: float = 0) -> str:
        """Check if position can exit cleanly.

        Returns: HEALTHY / CAUTION / EXIT_NOW
        """
        if volume_24h <= 0:
            return "HEALTHY"  # No data = assume OK

        # Position as % of 24h volume
        pct_of_volume = position_size_usd / volume_24h * 100 if volume_24h > 0 else 0

        if pct_of_volume > 1.0:
            log.warning("[LIQUIDITY] %s EXIT_NOW: position is %.2f%% of 24h volume",
                         symbol, pct_of_volume)
            return "EXIT_NOW"
        elif pct_of_volume > 0.1:
            log.info("[LIQUIDITY] %s CAUTION: position is %.2f%% of 24h volume",
                      symbol, pct_of_volume)
            return "CAUTION"

        return "HEALTHY"
