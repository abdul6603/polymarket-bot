"""Odin Conviction Engine — 10-component weighted scoring system.

Modeled after Garves: each component returns 0.0-1.0,
multiplied by its weight, summing to 100 max.
Safety rails apply after scoring to cap risk exposure.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from odin.intelligence.journal import OdinJournal
from odin.intelligence.brotherhood import BrotherhoodBridge

log = logging.getLogger("odin.conviction")
ET = ZoneInfo("America/New_York")

COMPONENT_WEIGHTS = {
    "regime_alignment":    15,  # CoinGlass regime supports direction
    "smc_quality":         15,  # BOS/FVG/OB patterns confirming
    "multi_tf_agreement":  12,  # How many timeframes (1D/4H/15m) agree
    "macro_support":       10,  # SPY/VIX/BTC.D alignment
    "funding_rate_edge":   10,  # Funding extreme = fade opportunity
    "sentiment_alignment":  8,  # Atlas news sentiment match
    "volume_confirmation":  8,  # Volume supporting the move
    "journal_fitness":     10,  # Historical WR for this combo
    "brother_alignment":    7,  # Garves crypto direction agreement
    "risk_reward_quality":  5,  # R:R ratio scoring
}  # Sum = 100

# Risk multiplier tiers from total score 0-100
# 1R cap: max risk = 1R regardless of conviction tier.
# Tiers scale within the 1R budget (0.0 to 1.0).
RISK_TIERS = [
    (0,  20, 0.00, "NO_TRADE"),      # Don't trade
    (20, 40, 0.25, "LOW"),            # 0.25R
    (40, 55, 0.50, "MODERATE"),       # 0.50R
    (55, 70, 0.75, "HIGH"),           # 0.75R
    (70, 100, 1.00, "FULL"),          # 1R (max)
]

# Minimum score to trade — 65 requires HIGH tier conviction.
# Prevents low-quality trades that were causing loss streaks.
MIN_TRADE_SCORE = 65


@dataclass
class OdinConvictionResult:
    """Result of conviction scoring."""
    total_score: float        # 0-100
    confidence_1_10: int      # 1-10 honest score
    risk_multiplier: float    # 0.0-1.0 (capped at 1R)
    tier: str                 # RISK_TIERS label
    components: dict          # Per-component breakdown
    safety_adjustments: list = field(default_factory=list)
    should_trade: bool = True


class OdinConvictionEngine:
    """10-component conviction scoring engine for Odin."""

    def __init__(self, journal: OdinJournal, brotherhood: BrotherhoodBridge):
        self._journal = journal
        self._brotherhood = brotherhood
        self._last_result: OdinConvictionResult | None = None
        self._rolling_results: list[dict] = []  # Last 15 trades for WR calc

    def score(
        self,
        signal: dict,
        regime: dict,
        macro: dict,
        smc_data: dict,
    ) -> OdinConvictionResult:
        """Score a potential trade across all 10 components."""
        direction = signal.get("direction", "LONG")
        symbol = signal.get("symbol", "BTCUSDT")

        components: dict[str, dict] = {}

        # 1. Regime alignment
        components["regime_alignment"] = self._score_regime(direction, regime)

        # 2. SMC quality
        components["smc_quality"] = self._score_smc(smc_data)

        # 3. Multi-TF agreement
        components["multi_tf_agreement"] = self._score_multi_tf(smc_data, signal)

        # 4. Macro support (FIX: actually use the macro data!)
        components["macro_support"] = self._score_macro(direction, macro)

        # 5. Funding rate edge (per-symbol from CoinGlass)
        components["funding_rate_edge"] = self._score_funding(direction, regime, symbol)

        # 6. Sentiment alignment (Atlas)
        components["sentiment_alignment"] = self._score_sentiment(symbol, direction)

        # 7. Volume confirmation
        components["volume_confirmation"] = self._score_volume(smc_data)

        # 8. Journal fitness
        components["journal_fitness"] = self._score_journal(symbol, direction, regime)

        # 9. Brother alignment (Garves)
        components["brother_alignment"] = self._score_brother(symbol, direction)

        # 10. Risk:Reward quality
        components["risk_reward_quality"] = self._score_rr(signal)

        # Calculate total weighted score
        total = 0.0
        for name, comp in components.items():
            weight = COMPONENT_WEIGHTS.get(name, 0)
            raw = comp.get("raw", 0.5)
            weighted = raw * weight
            comp["weight"] = weight
            comp["weighted"] = round(weighted, 2)
            total += weighted

        total = round(total, 1)

        # Map 0-100 to honest 1-10 confidence score
        confidence_1_10 = max(1, min(10, int(total / 10 + 0.5)))

        # Determine tier + base multiplier (capped at 1R)
        risk_mult = 0.0
        tier = "NO_TRADE"
        for low, high, mult, label in RISK_TIERS:
            if low <= total < high or (total == 100 and label == "FULL"):
                risk_mult = mult
                tier = label
                break

        # Apply safety rails
        safety_adjustments: list[str] = []
        risk_mult = self._apply_safety_rails(risk_mult, safety_adjustments)

        # Only trade if confidence >= 7/10 (score >= 70)
        should_trade = total >= MIN_TRADE_SCORE and risk_mult > 0.0

        result = OdinConvictionResult(
            total_score=total,
            confidence_1_10=confidence_1_10,
            risk_multiplier=round(risk_mult, 3),
            tier=tier,
            components={k: v for k, v in components.items()},
            safety_adjustments=safety_adjustments,
            should_trade=should_trade,
        )

        self._last_result = result
        log.info(
            "[CONVICTION] %s %s: %.0f/100 (%d/10) tier=%s mult=%.2f (1R cap) %s",
            direction, symbol, total, confidence_1_10, tier, risk_mult,
            f"| rails: {', '.join(safety_adjustments)}" if safety_adjustments else "",
        )
        return result

    # ── Component Scorers (each returns {"raw": 0.0-1.0, "reason": str}) ──

    def _score_regime(self, direction: str, regime: dict) -> dict:
        """Regime alignment: does CoinGlass support our direction?"""
        regime_val = regime.get("regime", "neutral").upper()
        bias = regime.get("direction_bias", "NONE").upper()

        if bias == direction:
            return {"raw": 1.0, "reason": f"Regime {regime_val} aligned with {direction}"}
        if regime_val == "NEUTRAL" or bias == "NONE":
            return {"raw": 0.5, "reason": "Neutral regime"}
        return {"raw": 0.0, "reason": f"Regime opposes: bias={bias} vs {direction}"}

    def _score_smc(self, smc_data: dict) -> dict:
        """SMC quality: how many patterns confirm?"""
        patterns = smc_data.get("patterns", [])
        count = len(patterns) if isinstance(patterns, list) else 0

        if count >= 3:
            return {"raw": 1.0, "reason": f"{count} SMC patterns confirm"}
        if count == 2:
            return {"raw": 0.75, "reason": "2 SMC patterns"}
        if count == 1:
            return {"raw": 0.5, "reason": "1 SMC pattern"}
        return {"raw": 0.0, "reason": "No SMC patterns"}

    def _score_multi_tf(self, smc_data: dict, signal: dict) -> dict:
        """Multi-timeframe agreement."""
        # Use confidence as proxy for TF agreement
        conf = smc_data.get("confidence", 0.5)
        alignment = signal.get("timeframe_alignment", "")

        if conf >= 0.85:
            return {"raw": 1.0, "reason": "All TFs agree (conf >= 85%)"}
        if conf >= 0.65:
            return {"raw": 0.7, "reason": "Most TFs agree (conf >= 65%)"}
        if conf >= 0.5:
            return {"raw": 0.5, "reason": "Partial TF agreement"}
        return {"raw": 0.2, "reason": "Poor TF alignment"}

    def _score_macro(self, direction: str, macro: dict) -> dict:
        """Macro support: SPY/VIX/BTC.D alignment."""
        regime_val = str(macro.get("regime", "")).upper()
        if hasattr(macro.get("regime"), "value"):
            regime_val = macro["regime"].value.upper()

        score_val = macro.get("score", 50)

        # bull/strong_bull + LONG = aligned, bear + SHORT = aligned
        is_bullish = regime_val in ("BULL", "STRONG_BULL", "BULLISH")
        is_bearish = regime_val in ("BEAR", "BEARISH")

        if (is_bullish and direction == "LONG") or (is_bearish and direction == "SHORT"):
            return {"raw": 1.0, "reason": f"Macro {regime_val} supports {direction} (score={score_val})"}

        if not is_bullish and not is_bearish:
            return {"raw": 0.5, "reason": f"Macro neutral (score={score_val})"}

        return {"raw": 0.0, "reason": f"Macro {regime_val} opposes {direction}"}

    def _score_funding(self, direction: str, regime: dict, symbol: str = "") -> dict:
        """Funding rate edge: extreme funding = mean-reversion opportunity.

        Positive funding → longs pay shorts → crowded long → SHORT edge
        Negative funding → shorts pay longs → crowded short → LONG edge
        Uses per-symbol funding_signal from CoinGlass regime brain (-1 to +1).
        """
        # Try per-symbol funding signal first (from regime opportunities)
        funding_signal = 0.0
        bare_sym = symbol.replace("USDT", "")
        for opp in regime.get("opportunities", []):
            if opp.get("symbol") == bare_sym:
                funding_signal = opp.get("funding_signal", 0)
                break

        # Fall back to top-level funding_rate
        funding = regime.get("funding_rate", 0)

        # If we have the processed signal from RegimeBrain, use it directly
        if funding_signal != 0:
            # funding_signal: negative = short signal (longs crowded), positive = long signal
            aligned = (
                (funding_signal > 0 and direction == "LONG") or
                (funding_signal < 0 and direction == "SHORT")
            )
            abs_sig = abs(funding_signal)

            if abs_sig >= 0.7:
                raw = 1.0 if aligned else 0.0
                level = "extreme"
            elif abs_sig >= 0.4:
                raw = 0.85 if aligned else 0.15
                level = "high"
            elif abs_sig >= 0.2:
                raw = 0.65 if aligned else 0.35
                level = "elevated"
            else:
                raw = 0.55 if aligned else 0.45
                level = "normal"

            return {
                "raw": raw,
                "reason": f"Funding signal {funding_signal:+.2f} ({level}) "
                          f"{'favors' if aligned else 'opposes'} {direction}",
            }

        # Fallback to raw funding rate
        if funding == 0:
            return {"raw": 0.5, "reason": "No funding data"}

        abs_fr = abs(funding)
        funding_favors = "SHORT" if funding > 0 else "LONG"
        aligned = (funding_favors == direction)

        if abs_fr >= 0.001:
            raw = 1.0 if aligned else 0.0
            level = "extreme"
        elif abs_fr >= 0.0005:
            raw = 0.85 if aligned else 0.15
            level = "high"
        elif abs_fr >= 0.0003:
            raw = 0.70 if aligned else 0.30
            level = "elevated"
        elif abs_fr >= 0.0001:
            raw = 0.55 if aligned else 0.45
            level = "normal"
        else:
            raw = 0.50
            level = "flat"

        return {
            "raw": raw,
            "reason": f"Funding {funding:.5f} ({level}) {'favors' if aligned else 'opposes'} {direction}",
        }

    def _score_sentiment(self, symbol: str, direction: str) -> dict:
        """Atlas news sentiment alignment."""
        sentiment = self._brotherhood.get_atlas_sentiment(symbol)
        sent_dir = sentiment.get("direction", "NEUTRAL")
        score = sentiment.get("score", 0)

        if sent_dir == direction:
            return {"raw": 1.0, "reason": f"Atlas sentiment aligned (score={score:.2f})"}
        if sent_dir == "NEUTRAL":
            return {"raw": 0.5, "reason": "Atlas sentiment neutral"}
        return {"raw": 0.0, "reason": f"Atlas sentiment opposes (score={score:.2f})"}

    def _score_volume(self, smc_data: dict) -> dict:
        """Volume confirmation."""
        vol_spike = smc_data.get("volume_spike", False)
        conf = smc_data.get("confidence", 0.5)

        if vol_spike:
            return {"raw": 1.0, "reason": "Volume spike detected"}
        if conf >= 0.7:
            return {"raw": 0.6, "reason": "Above average volume inferred"}
        return {"raw": 0.3, "reason": "Low/normal volume"}

    def _score_journal(self, symbol: str, direction: str, regime: dict) -> dict:
        """Journal fitness: historical WR for this combo."""
        regime_val = regime.get("regime", "neutral")
        if hasattr(regime_val, "value"):
            regime_val = regime_val.value
        now = datetime.now(ET)

        fitness = self._journal.get_journal_fitness(
            symbol=symbol,
            direction=direction,
            regime=str(regime_val),
            hour=now.hour,
        )

        wr = fitness.get("win_rate", 50)
        samples = fitness.get("sample_size", 0)
        rec = fitness.get("recommendation", "insufficient_data")

        if rec == "insufficient_data":
            return {"raw": 0.5, "reason": f"Insufficient data ({samples} samples)"}
        if wr >= 65 and samples >= 10:
            return {"raw": 1.0, "reason": f"Strong history: {wr:.0f}% WR ({samples} trades)"}
        if wr >= 50:
            return {"raw": 0.6, "reason": f"OK history: {wr:.0f}% WR ({samples} trades)"}
        if wr >= 35:
            return {"raw": 0.3, "reason": f"Weak history: {wr:.0f}% WR ({samples} trades)"}
        return {"raw": 0.0, "reason": f"Bad history: {wr:.0f}% WR ({samples} trades) — AVOID"}

    def _score_brother(self, symbol: str, direction: str) -> dict:
        """Garves crypto direction agreement."""
        alignment = self._brotherhood.get_brother_alignment(symbol, direction)
        score = alignment.get("alignment", 0.5)
        reason = alignment.get("reason", "no_data")

        return {"raw": score, "reason": f"Garves: {reason} (wr={alignment.get('garves_wr', '--')})"}

    def _score_rr(self, signal: dict) -> dict:
        """Risk:Reward quality."""
        rr = signal.get("risk_reward", 0)

        if rr >= 3.0:
            return {"raw": 1.0, "reason": f"Excellent R:R {rr:.1f}:1"}
        if rr >= 2.5:
            return {"raw": 0.8, "reason": f"Good R:R {rr:.1f}:1"}
        if rr >= 2.0:
            return {"raw": 0.6, "reason": f"OK R:R {rr:.1f}:1"}
        if rr >= 1.5:
            return {"raw": 0.3, "reason": f"Marginal R:R {rr:.1f}:1"}
        return {"raw": 0.0, "reason": f"Poor R:R {rr:.1f}:1"}

    # ── Safety Rails ──

    def _apply_safety_rails(self, mult: float, adjustments: list[str]) -> float:
        """Apply safety multipliers after scoring."""
        # Losing streak >= 3
        try:
            stats = self._journal.get_stats()
            recent = self._journal._memory.get_recent_decisions(limit=15, resolved_only=True)

            # Check consecutive losses
            streak = 0
            for d in recent:
                if d.get("outcome_score", 0) < 0:
                    streak += 1
                else:
                    break

            if streak >= 3:
                mult *= 0.75
                adjustments.append(f"losing_streak_{streak}_x0.75")

            # Rolling WR < 45% on last 15
            if len(recent) >= 10:
                wins = sum(1 for d in recent if d.get("outcome_score", 0) > 0)
                wr = wins / len(recent) * 100
                if wr < 45:
                    mult *= 0.70
                    adjustments.append(f"low_wr_{wr:.0f}%_x0.70")

            # Daily loss > 10% of balance (from circuit breaker stats)
            daily_loss_pct = stats.get("daily_loss_pct", 0)
            if daily_loss_pct > 10:
                mult = 0.0
                adjustments.append("daily_loss_>10%_STOP")

        except Exception as e:
            log.debug("[CONVICTION] Safety rail check error: %s", str(e)[:100])

        # Atlas anomaly
        if self._brotherhood._anomaly_active:
            mult *= 0.50
            adjustments.append("atlas_anomaly_x0.50")

        return round(mult, 3)

    def apply_calibration(self, new_weights: dict) -> bool:
        """Apply calibrated weights. Validates sum=100, returns True if applied."""
        total = sum(new_weights.values())
        if total != 100:
            log.warning("[CONVICTION] Calibration rejected: weights sum to %d, not 100", total)
            return False

        global COMPONENT_WEIGHTS
        old = dict(COMPONENT_WEIGHTS)
        COMPONENT_WEIGHTS.update(new_weights)
        changes = {k: new_weights[k] - old.get(k, 0) for k in new_weights if new_weights[k] != old.get(k, 0)}
        if changes:
            log.info("[CONVICTION] Weights calibrated: %s", ", ".join(f"{k}:{v:+d}" for k, v in changes.items()))
        return True

    def get_component_data_for_calibration(self) -> dict:
        """Returns per-component scores from last result for calibration tracking."""
        if not self._last_result:
            return {}
        return {
            k: {"raw": v.get("raw", 0), "weighted": v.get("weighted", 0)}
            for k, v in self._last_result.components.items()
        }

    def get_status(self) -> dict:
        """Dashboard-friendly status."""
        if not self._last_result:
            return {"active": False}

        r = self._last_result
        return {
            "active": True,
            "total_score": r.total_score,
            "confidence_1_10": r.confidence_1_10,
            "risk_multiplier": r.risk_multiplier,
            "risk_cap": "1R",
            "tier": r.tier,
            "should_trade": r.should_trade,
            "components": {
                k: {"raw": v.get("raw", 0), "weighted": v.get("weighted", 0),
                     "weight": v.get("weight", 0), "reason": v.get("reason", "")}
                for k, v in r.components.items()
            },
            "safety_adjustments": r.safety_adjustments,
        }
