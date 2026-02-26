"""Regime detection brain — determines BEAR/BULL/NEUTRAL from CoinGlass data.

Reads the market state like the Hyperliquid whales:
  - Funding rates extreme → crowded trade, fade it
  - OI building while price drops → trapped longs, short them
  - OI flushed + price stabilizes → accumulation, look for longs
  - Liquidation cascades → momentum, ride it
  - L/S ratio extremes → crowd is wrong, fade them

Outputs:
  - Global regime (BEAR/BULL/NEUTRAL)
  - Per-symbol trade signals (which coins are ripe)
  - Direction bias with confidence
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum

from odin.macro.coinglass import CoinMetrics, MarketSnapshot

log = logging.getLogger(__name__)


class Regime(Enum):
    STRONG_BEAR = "strong_bear"
    BEAR = "bear"
    NEUTRAL = "neutral"
    BULL = "bull"
    STRONG_BULL = "strong_bull"
    CHOPPY = "choppy"               # Mean-reverting, no clear direction
    MANIPULATION = "manipulation"   # Sudden wicks, liquidation cascades
    NEWS = "news"                   # High volatility from macro events


class Direction(Enum):
    SHORT = "SHORT"
    LONG = "LONG"
    NONE = "NONE"


@dataclass
class SymbolOpportunity:
    """A tradeable opportunity detected by the regime brain."""
    symbol: str
    direction: Direction = Direction.NONE
    score: float = 0.0          # 0-100, higher = more confident
    reasons: list[str] = field(default_factory=list)

    # Raw signal components
    funding_signal: float = 0.0     # -1 (short) to +1 (long)
    oi_signal: float = 0.0          # -1 (short) to +1 (long)
    ls_signal: float = 0.0          # -1 (short) to +1 (long)
    liq_signal: float = 0.0         # -1 (short) to +1 (long)
    momentum_signal: float = 0.0    # -1 (short) to +1 (long)

    @property
    def tradeable(self) -> bool:
        return self.score >= 50 and self.direction != Direction.NONE


@dataclass
class FundingArbInfo:
    """Funding rate arbitrage opportunity for a symbol."""
    symbol: str = ""
    active: bool = False
    collect_side: str = "NONE"
    rate_8h: float = 0.0
    daily_income_est: float = 0.0
    annualized_pct: float = 0.0


@dataclass
class RegimeState:
    """Current market regime."""
    regime: Regime = Regime.NEUTRAL
    global_score: float = 50.0      # 0=max bear, 100=max bull
    direction_bias: Direction = Direction.NONE
    opportunities: list[SymbolOpportunity] = field(default_factory=list)
    top_short: SymbolOpportunity | None = None
    top_long: SymbolOpportunity | None = None
    timestamp: float = 0.0
    funding_arbs: dict[str, FundingArbInfo] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "regime": self.regime.value,
            "global_score": self.global_score,
            "direction_bias": self.direction_bias.value,
            "opportunity_count": len(self.opportunities),
            "opportunities": [
                {"symbol": o.symbol, "direction": o.direction.value,
                 "score": o.score, "reasons": o.reasons,
                 "funding_signal": o.funding_signal}
                for o in self.opportunities
            ],
            "top_short": self.top_short.symbol if self.top_short else None,
            "top_long": self.top_long.symbol if self.top_long else None,
            "funding_arbs": {
                sym: {"active": fa.active, "collect_side": fa.collect_side,
                      "rate_8h": fa.rate_8h, "daily_income_est": fa.daily_income_est,
                      "annualized_pct": fa.annualized_pct}
                for sym, fa in self.funding_arbs.items()
            },
        }


class RegimeBrain:
    """Detects market regime and finds trade opportunities from CoinGlass data.

    Scoring philosophy (fade the crowd):
      - High funding + retail long heavy → SHORT signal
      - Negative funding + shorts crowded → LONG signal
      - OI rising while price dropping → trapped longs → SHORT
      - OI dropping + price stable → leverage flushed → LONG setup
      - Massive long liquidations → momentum SHORT (or exhaustion LONG)
    """

    # Thresholds
    FUNDING_EXTREME_HIGH = 0.01     # 1% funding = very expensive longs
    FUNDING_EXTREME_LOW = -0.005    # -0.5% = shorts paying a lot
    FUNDING_ELEVATED = 0.005        # 0.5% = notably positive
    LS_CROWDED_LONG = 0.60          # 60% long = crowded
    LS_CROWDED_SHORT = 0.60         # 60% short = crowded
    OI_SURGE_THRESH = 5.0           # 5% OI change in 1h = surge
    LIQ_DOMINANCE = 0.70            # 70% of liquidations on one side

    def funding_arb_opportunity(
        self, symbol: str, rate_8h: float, notional: float, min_rate: float = 0.0002,
    ) -> FundingArbInfo:
        """Detect funding arb opportunity for a symbol.

        Args:
            symbol: Bare symbol (BTC, ETH)
            rate_8h: Current HL 8-hour funding rate (signed)
            notional: Estimated position notional for income calc
            min_rate: Minimum |rate| to flag as arb (default 0.02%)
        """
        info = FundingArbInfo(symbol=symbol, rate_8h=rate_8h)

        if abs(rate_8h) < min_rate:
            return info

        # Negative funding = longs collect; positive = shorts collect
        info.collect_side = "LONG" if rate_8h < 0 else "SHORT"
        info.active = True
        info.daily_income_est = abs(rate_8h) * notional * 3
        info.annualized_pct = abs(rate_8h) * 3 * 365 * 100

        log.info(
            "[REGIME] Funding arb: %s COLLECT_%s rate=%.4f%%/8h daily=$%.2f annual=%.1f%%",
            symbol, info.collect_side, rate_8h * 100,
            info.daily_income_est, info.annualized_pct,
        )
        return info

    def analyze(self, snapshot: MarketSnapshot) -> RegimeState:
        """Analyze market snapshot and determine regime + opportunities."""
        state = RegimeState(timestamp=snapshot.scan_time)

        if not snapshot.coins:
            log.warning("[REGIME] Empty snapshot, staying neutral")
            return state

        # Step 1: Analyze BTC for global regime (BTC leads the market)
        btc = snapshot.coins.get("BTC")
        if btc:
            state.global_score = self._global_score(btc)

            # Check for special regimes BEFORE standard scoring
            special = self._detect_special_regime(btc, snapshot)
            if special:
                state.regime = special
                # Special regimes have reduced direction bias
                if special == Regime.CHOPPY:
                    state.direction_bias = Direction.NONE
                elif special == Regime.MANIPULATION:
                    state.direction_bias = Direction.NONE
                elif special == Regime.NEWS:
                    # News events: bias from score but reduced confidence
                    state.direction_bias = (
                        Direction.SHORT if state.global_score < 35
                        else Direction.LONG if state.global_score > 65
                        else Direction.NONE
                    )
            else:
                state.regime = self._score_to_regime(state.global_score)
                state.direction_bias = (
                    Direction.SHORT if state.global_score < 40
                    else Direction.LONG if state.global_score > 60
                    else Direction.NONE
                )

        # Step 2: Score coins with detailed data (priority symbols)
        for sym, coin in snapshot.coins.items():
            if self._has_detailed_data(coin):
                opp = self._score_symbol(coin)
                if opp.tradeable:
                    state.opportunities.append(opp)
            elif self._has_liq_data(coin):
                # Liq-only coins: score with reduced confidence
                opp = self._score_liq_only(coin, state)
                if opp and opp.tradeable:
                    state.opportunities.append(opp)

        # Counter-trend protection at regime level: if BTC is rallying hard
        # but contrarian signals push direction_bias SHORT, clamp it
        if btc:
            if btc.price_change_4h > 3 and state.direction_bias == Direction.SHORT:
                log.info("[REGIME] Counter-trend override: bias SHORT → NONE (BTC +%.1f%% 4h)",
                         btc.price_change_4h)
                state.direction_bias = Direction.NONE
            elif btc.price_change_4h < -3 and state.direction_bias == Direction.LONG:
                log.info("[REGIME] Counter-trend override: bias LONG → NONE (BTC %.1f%% 4h)",
                         btc.price_change_4h)
                state.direction_bias = Direction.NONE

        # Sort by score (highest first)
        state.opportunities.sort(key=lambda o: o.score, reverse=True)

        # Track best short and long
        shorts = [o for o in state.opportunities if o.direction == Direction.SHORT]
        longs = [o for o in state.opportunities if o.direction == Direction.LONG]
        state.top_short = shorts[0] if shorts else None
        state.top_long = longs[0] if longs else None

        log.info(
            "[REGIME] %s (score=%.0f) | %d opportunities | "
            "best SHORT=%s best LONG=%s",
            state.regime.value, state.global_score,
            len(state.opportunities),
            state.top_short.symbol if state.top_short else "none",
            state.top_long.symbol if state.top_long else "none",
        )

        return state

    def _detect_special_regime(self, btc: CoinMetrics, snapshot: MarketSnapshot) -> Regime | None:
        """Detect CHOPPY, MANIPULATION, or NEWS regimes.

        These override normal BEAR/BULL/NEUTRAL when conditions match.
        """
        # MANIPULATION: sudden extreme wicks + liquidation cascade
        # Indicators: massive liquidations on BOTH sides, extreme OI change
        total_liq = btc.liq_long_24h + btc.liq_short_24h
        if total_liq > 0:
            liq_balance = abs(btc.liq_long_24h - btc.liq_short_24h) / total_liq
            # Both sides getting liquidated = manipulation
            if liq_balance < 0.3 and total_liq > 50_000_000:
                log.info("[REGIME] MANIPULATION detected: bilateral liquidations $%.0fM", total_liq / 1e6)
                return Regime.MANIPULATION

        # Rapid OI whipsaw = manipulation
        if abs(btc.oi_change_1h) > 10:
            log.info("[REGIME] MANIPULATION detected: OI whipsaw %.1f%%", btc.oi_change_1h)
            return Regime.MANIPULATION

        # NEWS: extreme price moves in short period
        # |4h change| > 5% or |1h change| > 3% = likely news-driven
        if abs(btc.price_change_4h) > 5 or abs(btc.price_change_1h) > 3:
            log.info(
                "[REGIME] NEWS detected: 4h=%.1f%% 1h=%.1f%%",
                btc.price_change_4h, btc.price_change_1h,
            )
            return Regime.NEWS

        # CHOPPY: small ranges, no conviction, OI flat, mixed L/S
        # Indicators: |4h change| < 0.5%, |funding| low, L/S near 50/50, OI stable
        is_flat_price = abs(btc.price_change_4h) < 0.5 and abs(btc.price_change_1h) < 0.3
        is_flat_funding = abs(btc.funding_rate) < 0.002
        is_balanced_ls = 0.45 <= btc.long_ratio <= 0.55
        is_flat_oi = abs(btc.oi_change_1h) < 1.5

        if is_flat_price and is_flat_funding and is_balanced_ls and is_flat_oi:
            log.info("[REGIME] CHOPPY detected: flat price, balanced L/S, stable OI")
            return Regime.CHOPPY

        return None

    def _global_score(self, btc: CoinMetrics) -> float:
        """Global regime score from BTC metrics. 0 = max bear, 100 = max bull."""
        score = 50.0  # Start neutral

        # Funding rate signal (-15 to +15)
        if btc.funding_rate > self.FUNDING_EXTREME_HIGH:
            score -= 15  # Overleveraged longs → bearish
        elif btc.funding_rate > self.FUNDING_ELEVATED:
            score -= 8
        elif btc.funding_rate < self.FUNDING_EXTREME_LOW:
            score += 12  # Shorts paying → bullish squeeze potential
        elif btc.funding_rate < -0.001:
            score += 5

        # OI structure (-15 to +15)
        if btc.oi_change_1h > self.OI_SURGE_THRESH and btc.price_change_1h < -1:
            score -= 15  # OI up + price down = trapped longs
        elif btc.oi_change_1h > self.OI_SURGE_THRESH and btc.price_change_1h > 1:
            score += 10  # OI up + price up = healthy trend
        elif btc.oi_change_1h < -self.OI_SURGE_THRESH:
            score += 5   # Leverage flushed = healthier market

        # L/S ratio (-10 to +10)
        if btc.long_ratio > self.LS_CROWDED_LONG:
            score -= 10  # Retail long heavy → bearish
        elif btc.short_ratio > self.LS_CROWDED_SHORT:
            score += 10  # Retail short heavy → bullish

        # Liquidation flow (-10 to +10)
        total_liq = btc.liq_long_24h + btc.liq_short_24h
        if total_liq > 0:
            long_pct = btc.liq_long_24h / total_liq
            if long_pct > self.LIQ_DOMINANCE:
                score -= 10  # Longs getting liquidated → bear
            elif long_pct < (1 - self.LIQ_DOMINANCE):
                score += 8   # Shorts getting liquidated → bull

        # Price momentum (-20 to +20) — doubled weight to respect actual price action
        if btc.price_change_24h < -5:
            score -= 20  # Strong sell-off
        elif btc.price_change_24h < -2:
            score -= 10
        elif btc.price_change_24h > 5:
            score += 20  # Strong rally
        elif btc.price_change_24h > 2:
            score += 10

        # Short-term momentum alignment bonus (-8 to +8)
        # If 1h and 4h both agree on direction, add conviction
        if btc.price_change_1h > 0.5 and btc.price_change_4h > 1:
            score += 8   # Both timeframes bullish
        elif btc.price_change_1h < -0.5 and btc.price_change_4h < -1:
            score -= 8   # Both timeframes bearish

        return max(0, min(100, score))

    @staticmethod
    def _has_detailed_data(coin: CoinMetrics) -> bool:
        """Check if coin has enough data for scoring (priority symbol)."""
        return (
            coin.funding_rate != 0
            or coin.oi_usd > 0
            or coin.long_ratio != 0.5
        )

    @staticmethod
    def _has_liq_data(coin: CoinMetrics) -> bool:
        """Check if coin has liquidation data (from bulk scan)."""
        return (coin.liq_long_24h + coin.liq_short_24h) > 0

    def _score_liq_only(self, coin: CoinMetrics, state: RegimeState) -> SymbolOpportunity | None:
        """Score a coin using only liquidation data + global regime direction.

        Lower confidence than full scoring. Only generates opportunity if
        the liquidation imbalance is extreme and aligns with regime.
        Score range: 0-70. Needs >= 50 to be tradeable (from property).
        """
        total_liq = coin.liq_long_24h + coin.liq_short_24h
        if total_liq < 1_000_000:  # Skip low-volume coins
            return None

        # Liquidation imbalance: if longs are getting rekt, price is going down
        liq_balance = (coin.liq_short_24h - coin.liq_long_24h) / total_liq
        # liq_balance > 0 = shorts rekt = price going UP (bullish)
        # liq_balance < 0 = longs rekt = price going DOWN (bearish)

        # Only flag if imbalance is significant (>50%)
        if abs(liq_balance) < 0.50:
            return None

        liq_direction = Direction.LONG if liq_balance > 0 else Direction.SHORT

        # Must align with global regime
        if state.direction_bias != Direction.NONE and state.direction_bias != liq_direction:
            return None

        # Score: 50% imbalance = 50, 80% = 56, 100% = 70
        # Uses total_liq as volume multiplier (>$10M gets bonus)
        base_score = abs(liq_balance) * 70
        vol_bonus = min(10, total_liq / 10_000_000 * 5)  # Up to +10 for high volume
        score = min(70, base_score + vol_bonus)

        opp = SymbolOpportunity(symbol=coin.symbol)
        opp.direction = liq_direction
        opp.liq_signal = liq_balance
        opp.score = score
        opp.reasons = [f"Liq-only: {liq_direction.value} (imbalance={liq_balance:.0%}, vol=${total_liq/1e6:.1f}M)"]
        return opp

    def _score_to_regime(self, score: float) -> Regime:
        if score <= 20:
            return Regime.STRONG_BEAR
        elif score <= 40:
            return Regime.BEAR
        elif score <= 60:
            return Regime.NEUTRAL
        elif score <= 80:
            return Regime.BULL
        return Regime.STRONG_BULL

    def _score_symbol(self, coin: CoinMetrics) -> SymbolOpportunity:
        """Score a single symbol for trade opportunity."""
        opp = SymbolOpportunity(symbol=coin.symbol)

        # 1. Funding rate signal
        opp.funding_signal = self._funding_score(coin)

        # 2. OI structure signal
        opp.oi_signal = self._oi_score(coin)

        # 3. L/S positioning signal
        opp.ls_signal = self._ls_score(coin)

        # 4. Liquidation flow signal
        opp.liq_signal = self._liq_score(coin)

        # 5. Price momentum signal
        opp.momentum_signal = self._momentum_score(coin)

        # Combine signals: negative = SHORT, positive = LONG
        # Weights: momentum 25%, funding 25%, OI 20%, L/S 15%, liq 15%
        # Momentum elevated to prevent shorting into rallies
        composite = (
            opp.momentum_signal * 0.25
            + opp.funding_signal * 0.25
            + opp.oi_signal * 0.20
            + opp.ls_signal * 0.15
            + opp.liq_signal * 0.15
        )

        # Counter-trend protection: block shorts during strong rallies, longs during dumps
        if composite < -0.2 and coin.price_change_4h > 2:
            # Contrarian signals say SHORT but price is rallying — reduce or block
            if coin.price_change_4h > 4:
                # Very strong rally — do NOT short, flip to neutral
                opp.direction = Direction.NONE
                opp.score = 0
                opp.reasons.append(f"Counter-trend block: SHORT blocked during +{coin.price_change_4h:.1f}% rally")
                return opp
            else:
                # Moderate rally — reduce short conviction significantly
                composite *= 0.3
                opp.reasons.append(f"Counter-trend dampen: SHORT reduced during +{coin.price_change_4h:.1f}% rally")

        if composite > 0.2 and coin.price_change_4h < -2:
            # Contrarian signals say LONG but price is dumping — reduce or block
            if coin.price_change_4h < -4:
                opp.direction = Direction.NONE
                opp.score = 0
                opp.reasons.append(f"Counter-trend block: LONG blocked during {coin.price_change_4h:.1f}% dump")
                return opp
            else:
                composite *= 0.3
                opp.reasons.append(f"Counter-trend dampen: LONG reduced during {coin.price_change_4h:.1f}% dump")

        # Direction from composite
        if composite < -0.2:
            opp.direction = Direction.SHORT
            opp.score = min(abs(composite) * 100, 100)
        elif composite > 0.2:
            opp.direction = Direction.LONG
            opp.score = min(abs(composite) * 100, 100)
        else:
            opp.direction = Direction.NONE
            opp.score = 0

        # Build reasons
        if abs(opp.funding_signal) > 0.3:
            opp.reasons.append(
                f"Funding {'extreme' if abs(coin.funding_rate) > 0.01 else 'elevated'}: "
                f"{coin.funding_rate:.4f}"
            )
        if abs(opp.oi_signal) > 0.3:
            opp.reasons.append(f"OI 1h change: {coin.oi_change_1h:+.1f}%")
        if abs(opp.ls_signal) > 0.3:
            opp.reasons.append(
                f"L/S: {coin.long_ratio:.0%}L/{coin.short_ratio:.0%}S"
            )
        if abs(opp.liq_signal) > 0.3:
            opp.reasons.append(
                f"Liq 24h: ${coin.liq_long_24h/1e6:.1f}M long / "
                f"${coin.liq_short_24h/1e6:.1f}M short"
            )

        return opp

    def _funding_score(self, coin: CoinMetrics) -> float:
        """Funding rate → signal. Positive funding = short signal (fade longs)."""
        fr = coin.funding_rate
        if fr > self.FUNDING_EXTREME_HIGH:
            return -0.9   # Very high funding → strong short
        elif fr > self.FUNDING_ELEVATED:
            return -0.5
        elif fr > 0.002:
            return -0.2
        elif fr < self.FUNDING_EXTREME_LOW:
            return 0.8    # Very negative → strong long (squeeze)
        elif fr < -0.002:
            return 0.4
        return 0.0

    def _oi_score(self, coin: CoinMetrics) -> float:
        """OI structure → signal. OI up + price down = trapped longs (short)."""
        oi_1h = coin.oi_change_1h
        price_1h = coin.price_change_1h

        if oi_1h > self.OI_SURGE_THRESH and price_1h < -1:
            return -0.8   # OI surge + price drop = trapped → short
        elif oi_1h > self.OI_SURGE_THRESH and price_1h > 1:
            return 0.3    # OI surge + price up = healthy trend
        elif oi_1h < -self.OI_SURGE_THRESH:
            return 0.2    # Leverage flushed = reset
        elif oi_1h > 3 and price_1h < 0:
            return -0.4   # Mild OI buildup + slight dip
        return 0.0

    def _ls_score(self, coin: CoinMetrics) -> float:
        """L/S ratio → signal. Crowd is usually wrong, fade them."""
        if coin.long_ratio > 0.65:
            return -0.7   # Very long heavy → short
        elif coin.long_ratio > self.LS_CROWDED_LONG:
            return -0.4
        elif coin.short_ratio > 0.65:
            return 0.6    # Very short heavy → long (squeeze)
        elif coin.short_ratio > self.LS_CROWDED_SHORT:
            return 0.3
        return 0.0

    def _liq_score(self, coin: CoinMetrics) -> float:
        """Liquidation flow → signal."""
        total = coin.liq_long_24h + coin.liq_short_24h
        if total <= 0:
            return 0.0

        long_pct = coin.liq_long_24h / total
        if long_pct > 0.80:
            return -0.6   # Longs getting destroyed → bear momentum
        elif long_pct > self.LIQ_DOMINANCE:
            return -0.3
        elif long_pct < 0.20:
            return 0.5    # Shorts getting destroyed → bull
        elif long_pct < (1 - self.LIQ_DOMINANCE):
            return 0.2
        return 0.0

    def _momentum_score(self, coin: CoinMetrics) -> float:
        """Price momentum → signal. Combines 4h and 1h for trend strength."""
        p4h = coin.price_change_4h
        p1h = coin.price_change_1h

        # Base score from 4h momentum
        if p4h < -5:
            base = -0.8
        elif p4h < -2:
            base = -0.4
        elif p4h > 5:
            base = 0.8
        elif p4h > 2:
            base = 0.4
        else:
            base = 0.0

        # 1h alignment bonus: if short-term confirms, strengthen signal
        if p1h > 0.5 and base > 0:
            base = min(base + 0.2, 1.0)   # 1h confirms bullish
        elif p1h < -0.5 and base < 0:
            base = max(base - 0.2, -1.0)  # 1h confirms bearish
        elif p1h > 0.5 and base < -0.2:
            base *= 0.5  # 1h contradicts short signal — weaken it
        elif p1h < -0.5 and base > 0.2:
            base *= 0.5  # 1h contradicts long signal — weaken it

        return base
