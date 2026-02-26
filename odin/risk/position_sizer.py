"""Structure-based position sizing with OB memory integration.

Sizing logic (V7 — LLM-driven risk):
  Step 1: Find nearest structure zone behind entry (OB/FVG/S&R)
  Step 2: Place SL behind structure + 0.1-0.2% buffer
  Step 3: Clamp SL distance: 2-4% genuine trades, 0.5-1% risky trades
  Step 4: Risk = LLM-decided ($5-50, based on conviction + setup quality)
  Step 5: Position size = risk / SL_distance
  Step 6: Cap notional (tiered: $200→$2K, $500→$5K, $1K→$10K)
  Step 7: Leverage = notional / allocated_margin (cross margin, auto-calc)
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from odin.skills.ob_memory import ZoneRecord

log = logging.getLogger(__name__)

# Hard limits
MIN_RISK_USD = 3.00           # Don't trade below $3 risk
MAX_LEVERAGE = 50             # Exchange hard cap
DEFAULT_RISK_USD = 25.0       # Default risk per trade (LLM overrides this)

# ── Dynamic Max Notional Tiers (10x capital) ──
_NOTIONAL_TIERS = [
    (1000, 10000),
    (500, 5000),
    (300, 3000),
    (200, 2000),
]
_DEFAULT_MAX_NOTIONAL = 5000


def get_max_notional(balance: float) -> float:
    """Max single-position notional scaled to account balance."""
    for threshold, cap in _NOTIONAL_TIERS:
        if balance >= threshold:
            return cap
    # Sub-$200 accounts: cap at 10x balance (not flat $5K)
    return max(100, balance * 10)

# SL distance bounds (% of entry price)
SL_MIN_GENUINE = 2.0          # Genuine trades: min 2% SL
SL_MAX_GENUINE = 4.0          # Genuine trades: max 4% SL
SL_MIN_RISKY = 0.5            # Low-conviction: min 0.5% SL
SL_MAX_RISKY = 1.5            # Low-conviction: max 1.5% SL
SL_STRUCTURE_BUFFER = 0.15    # 0.15% buffer beyond structure zone

# Conviction threshold for "genuine" vs "risky"
GENUINE_CONVICTION = 70       # 70+ = genuine trade with wider SL


@dataclass
class PositionSize:
    """Calculated position parameters."""
    margin_usd: float           # Capital allocated as margin
    notional_usd: float         # Total position value
    qty: float                  # Quantity in base asset
    leverage: int
    risk_usd: float             # Max loss on this trade
    risk_pct: float             # % of capital risked
    sl_distance_pct: float      # Stop-loss distance as %
    sl_price: float = 0.0       # Actual SL price
    sl_source: str = ""         # Where SL came from (OB/FVG/ATR)
    conviction_score: float = 0.0
    risk_multiplier: float = 1.0
    adjustments: list = field(default_factory=list)


class PositionSizer:
    """Structure-based position sizing with OB memory.

    Formula: find structure → set SL behind it → size = risk / SL_distance → cap at tiered max.
    """

    def __init__(
        self,
        risk_per_trade_usd: float = DEFAULT_RISK_USD,
        risk_per_trade_pct: float = 3.25,
        max_leverage: int = MAX_LEVERAGE,
        default_leverage: int = 10,
        max_exposure_pct: float = 50.0,
    ):
        self._risk_usd = risk_per_trade_usd
        self._risk_pct = risk_per_trade_pct
        self._max_leverage = min(max_leverage, MAX_LEVERAGE)
        self._default_leverage = default_leverage
        self._max_exposure_pct = max_exposure_pct

    def calculate(
        self,
        balance: float,
        entry_price: float,
        stop_loss: float,
        confidence: float = 1.0,
        macro_multiplier: float = 1.0,
        current_exposure: float = 0.0,
        leverage: int = 0,
        conviction_score: float = 0.0,
        structure_zones: list[ZoneRecord] | None = None,
        direction: str = "LONG",
        notional_cap_override: float = 0.0,
        risk_override: float = 0.0,
        **kwargs,
    ) -> PositionSize:
        """Calculate position size using structure-based SL placement.

        Args:
            balance: Account balance
            entry_price: Current/entry price
            stop_loss: Initial SL from SMC engine (may be overridden by structure)
            confidence: Conviction risk_multiplier (0-1)
            macro_multiplier: Macro scaling factor (0-1)
            current_exposure: Current total exposure in USD
            leverage: Override leverage (0 = auto-calculate)
            conviction_score: Raw conviction score (0-100)
            structure_zones: OB/FVG zones from OB memory for smart SL placement
            direction: "LONG" or "SHORT"
            notional_cap_override: PortfolioGuard tier cap (0 = use default $5K)
            risk_override: PortfolioGuard adjusted risk (0 = use normal calculation)
        """
        adjustments: list[str] = []

        if entry_price <= 0 or balance <= 0:
            return PositionSize(0, 0, 0, 1, 0, 0, 0, adjustments=["invalid_inputs"])

        # ── Step 1: Find best SL from structure zones ──
        structure_sl, sl_source = self._find_structure_sl(
            entry_price, direction, structure_zones, conviction_score,
        )

        # Use structure SL if found, otherwise use the passed-in SL
        if structure_sl > 0:
            final_sl = structure_sl
            adjustments.append(f"sl_from_{sl_source}")
        elif stop_loss > 0 and abs(stop_loss - entry_price) / entry_price > 0.001:
            final_sl = stop_loss
            sl_source = "smc_engine"
            adjustments.append("sl_from_smc")
        else:
            # No structure, no valid SMC SL → ATR fallback
            final_sl = self._atr_fallback_sl(entry_price, direction, conviction_score)
            sl_source = "atr_fallback"
            adjustments.append("sl_from_atr_fallback")

        # ── Step 2: Calculate and clamp SL distance ──
        sl_dist_abs = abs(entry_price - final_sl)
        sl_dist_pct = sl_dist_abs / entry_price * 100

        is_genuine = conviction_score >= GENUINE_CONVICTION
        if is_genuine:
            sl_min, sl_max = SL_MIN_GENUINE, SL_MAX_GENUINE
        else:
            sl_min, sl_max = SL_MIN_RISKY, SL_MAX_RISKY

        if sl_dist_pct < sl_min:
            sl_dist_pct = sl_min
            adjustments.append(f"sl_widened_to_{sl_min}%")
        elif sl_dist_pct > sl_max:
            sl_dist_pct = sl_max
            adjustments.append(f"sl_clamped_to_{sl_max}%")

        # Recalculate SL price from clamped distance
        sl_dist_abs = entry_price * sl_dist_pct / 100
        if direction == "LONG":
            final_sl = round(entry_price - sl_dist_abs, 2)
        else:
            final_sl = round(entry_price + sl_dist_abs, 2)

        # ── Step 3: Calculate risk (LLM-driven or config default) ──
        if risk_override > 0:
            # LLM brain already factored conviction + macro into risk_usd.
            # Do NOT scale by conviction or macro again (double-dipping).
            risk = risk_override
            adjustments.append(f"llm_risk_${risk_override:.0f}")
        else:
            # Fallback: min(config_risk, pct of balance)
            pct_risk = balance * self._risk_pct / 100
            base_risk = min(self._risk_usd, pct_risk)
            if base_risk < self._risk_usd:
                adjustments.append(f"balance_cap_{self._risk_pct}%=${pct_risk:.0f}")

            # Scale by conviction risk_multiplier (0-1)
            risk = base_risk * max(0.0, min(1.0, confidence))
            if confidence < 1.0:
                adjustments.append(f"conviction_x{confidence:.2f}")

            # Scale by macro
            macro_mult = max(0.0, min(1.0, macro_multiplier))
            risk *= macro_mult
            if macro_mult < 1.0:
                adjustments.append(f"macro_x{macro_mult:.2f}")

        # Discipline layer scalars (volatility, drawdown, edge — only reduce)
        vol_scalar = max(0.0, min(1.0, kwargs.get("volatility_scalar", 1.0)))
        dd_scalar = max(0.0, min(1.0, kwargs.get("drawdown_scalar", 1.0)))
        edge_scalar = max(0.0, min(1.0, kwargs.get("edge_scalar", 1.0)))

        if vol_scalar < 1.0:
            risk *= vol_scalar
            adjustments.append(f"volatility_x{vol_scalar:.2f}")
        if dd_scalar < 1.0:
            risk *= dd_scalar
            adjustments.append(f"drawdown_x{dd_scalar:.2f}")
        if edge_scalar < 1.0:
            risk *= edge_scalar
            adjustments.append(f"edge_x{edge_scalar:.2f}")

        # Funding rate bonus/penalty
        funding_rate_8h = kwargs.get("funding_rate_8h", 0.0)
        funding_collect_side = kwargs.get("funding_collect_side", "NONE")
        funding_bonus_pct = kwargs.get("funding_bonus_pct", 0.20)
        funding_penalty_pct = kwargs.get("funding_penalty_pct", 0.15)
        funding_arb_min = kwargs.get("funding_arb_min_rate", 0.0002)

        if abs(funding_rate_8h) >= funding_arb_min and funding_collect_side != "NONE":
            if direction == funding_collect_side:
                # Collecting funding — boost risk
                risk *= (1 + funding_bonus_pct)
                adjustments.append(f"funding_bonus_+{funding_bonus_pct:.0%} (collect {funding_rate_8h:+.4%}/8h)")
            elif abs(funding_rate_8h) >= 0.0005:
                # Paying heavy funding — reduce risk
                risk *= (1 - funding_penalty_pct)
                adjustments.append(f"funding_penalty_-{funding_penalty_pct:.0%} (pay {funding_rate_8h:+.4%}/8h)")

        # Scale down if already exposed
        if current_exposure > 0 and balance > 0:
            exposure_ratio = current_exposure / balance
            if exposure_ratio > 2.0:
                risk *= 0.5
                adjustments.append("high_exposure_x0.50")
            elif exposure_ratio > 1.0:
                risk *= 0.75
                adjustments.append("med_exposure_x0.75")

        # Reject tiny risk
        if risk < MIN_RISK_USD:
            adjustments.append(f"too_small_${risk:.2f}")
            return PositionSize(
                margin_usd=0, notional_usd=0, qty=0, leverage=1,
                risk_usd=round(risk, 2), risk_pct=0,
                sl_distance_pct=round(sl_dist_pct, 3),
                sl_price=final_sl, sl_source=sl_source,
                conviction_score=conviction_score,
                risk_multiplier=confidence,
                adjustments=adjustments,
            )

        # ── Step 4: Position size = risk / SL distance ──
        qty = risk / sl_dist_abs
        notional = qty * entry_price

        # ── Step 5: Cap notional (tier-aware, 10x capital) ──
        dynamic_cap = get_max_notional(balance)
        effective_cap = notional_cap_override if notional_cap_override > 0 else dynamic_cap
        if notional > effective_cap:
            qty = effective_cap / entry_price
            notional = effective_cap
            risk = qty * sl_dist_abs  # Risk shrinks when capped
            adjustments.append(f"notional_cap_${effective_cap:.0f}")

        # ── Step 6: Calculate leverage (cross margin) ──
        if leverage > 0:
            lev = min(leverage, self._max_leverage)
        else:
            # Auto-calculate: notional / balance, rounded up
            lev = max(1, min(self._max_leverage, math.ceil(notional / balance)))

        margin = notional / lev
        risk_pct = risk / balance * 100

        result = PositionSize(
            margin_usd=round(margin, 2),
            notional_usd=round(notional, 2),
            qty=round(qty, 6),
            leverage=lev,
            risk_usd=round(risk, 2),
            risk_pct=round(risk_pct, 3),
            sl_distance_pct=round(sl_dist_pct, 3),
            sl_price=round(final_sl, 2),
            sl_source=sl_source,
            conviction_score=conviction_score,
            risk_multiplier=round(confidence, 3),
            adjustments=adjustments,
        )

        log.info(
            "[SIZER] %s $%.0f entry | SL=$%.2f (%.1f%% %s) | risk=$%.0f → "
            "notional=$%.0f qty=%.6f lev=%dx | %s",
            direction, entry_price, final_sl, sl_dist_pct, sl_source,
            risk, notional, qty, lev,
            ", ".join(adjustments) if adjustments else "clean",
        )
        return result

    def _find_structure_sl(
        self,
        entry_price: float,
        direction: str,
        zones: list[ZoneRecord] | None,
        conviction_score: float,
    ) -> tuple[float, str]:
        """Find the best SL level from OB memory structure zones.

        For LONG: find nearest support (bullish OB/FVG) below entry.
        For SHORT: find nearest resistance (bearish OB/FVG) above entry.

        Returns (sl_price, source_label) or (0.0, "") if no suitable zone.
        """
        if not zones:
            return 0.0, ""

        is_genuine = conviction_score >= GENUINE_CONVICTION
        sl_max_pct = SL_MAX_GENUINE if is_genuine else SL_MAX_RISKY
        buffer_pct = SL_STRUCTURE_BUFFER / 100

        candidates: list[tuple[float, str, float]] = []  # (sl_price, source, distance_pct)

        for z in zones:
            if z.mitigated:
                continue

            if direction == "LONG":
                # For longs, we want support zones BELOW entry
                if z.bottom >= entry_price:
                    continue
                # SL goes below the zone bottom with buffer
                sl_price = z.bottom * (1 - buffer_pct)
                distance_pct = (entry_price - sl_price) / entry_price * 100
            else:
                # For shorts, we want resistance zones ABOVE entry
                if z.top <= entry_price:
                    continue
                # SL goes above the zone top with buffer
                sl_price = z.top * (1 + buffer_pct)
                distance_pct = (sl_price - entry_price) / entry_price * 100

            # Skip if too far or too close
            if distance_pct > sl_max_pct or distance_pct < 0.3:
                continue

            source = f"{z.zone_type}_{z.direction}"
            candidates.append((sl_price, source, distance_pct))

        if not candidates:
            return 0.0, ""

        # Pick the closest valid structure zone (tightest SL within bounds)
        candidates.sort(key=lambda c: c[2])  # Sort by distance ascending
        best_sl, best_source, best_dist = candidates[0]

        log.info(
            "[SIZER] Structure SL: %s at $%.2f (%.1f%% from entry) | "
            "%d candidates found",
            best_source, best_sl, best_dist, len(candidates),
        )
        return best_sl, best_source

    @staticmethod
    def _atr_fallback_sl(
        entry_price: float, direction: str, conviction_score: float,
    ) -> float:
        """ATR-like fallback when no structure zones available."""
        if conviction_score >= GENUINE_CONVICTION:
            pct = 2.5 / 100  # 2.5% for genuine trades
        else:
            pct = 0.5 / 100  # 0.5% for risky/low-conviction

        if direction == "LONG":
            return round(entry_price * (1 - pct), 2)
        return round(entry_price * (1 + pct), 2)
