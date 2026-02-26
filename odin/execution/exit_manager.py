"""Dynamic exit manager — trailing stops, partial TPs, time/regime exits.

Manages the full lifecycle of position exits:
  1. Early partial: 25% at 1.0R — "pay for the trade", SL → breakeven
  2. TP1: 25% at 1.5R — half the position banked
  3. TP2: 30% at 2.5R — 80% total banked
  4. TP3: 20% runner at 4.0R — the home run
  5. Trailing stops (ATR-based, activates at 2R, follows best price)
  6. Time-based exits (close stale trades after 12h)
  7. Regime-aware trailing (tighter in chop, wider in trends)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum

log = logging.getLogger("odin")


class ExitAction(Enum):
    HOLD = "hold"
    TRAIL_SL = "trail_sl"
    PARTIAL_EARLY = "partial_early"
    PARTIAL_TP1 = "partial_tp1"
    PARTIAL_TP2 = "partial_tp2"
    PARTIAL_TP3 = "partial_tp3"
    TIME_EXIT = "time_exit"
    STOP_LOSS = "stop_loss"


@dataclass
class ExitDecision:
    action: ExitAction
    new_sl: float = 0.0
    close_pct: float = 0.0       # Fraction of remaining qty to close (0-1)
    close_price: float = 0.0
    reason: str = ""


@dataclass
class PositionExitState:
    """Tracks exit management state per position."""
    highest_price: float = 0.0      # Best price seen (longs)
    lowest_price: float = float("inf")  # Best price seen (shorts)
    current_sl: float = 0.0
    original_sl: float = 0.0
    original_qty: float = 0.0
    remaining_qty: float = 0.0
    entry_price: float = 0.0
    atr: float = 0.0                # ATR at entry time
    early_hit: bool = False         # 25% early partial at 1R
    tp1_hit: bool = False
    tp2_hit: bool = False
    tp3_hit: bool = False
    partial_closes: list = field(default_factory=list)


class ExitManager:
    """Evaluates exit conditions for open positions each monitor tick."""

    def __init__(
        self,
        trail_atr_mult: float = 1.5,
        trail_breakeven_r: float = 1.0,
        trail_activate_r: float = 2.0,
        partial_early_pct: float = 0.25,
        partial_early_r: float = 1.0,
        partial_tp1_pct: float = 0.25,
        partial_tp1_r: float = 1.5,
        partial_tp2_pct: float = 0.30,
        partial_tp2_r: float = 2.5,
        partial_tp3_r: float = 4.0,
        max_stale_hours: float = 12.0,
        stale_threshold_r: float = 0.3,
        regime_chop_mult: float = 0.7,
        regime_trend_mult: float = 1.5,
    ):
        self._trail_atr_mult = trail_atr_mult
        self._trail_be_r = trail_breakeven_r
        self._trail_activate_r = trail_activate_r
        self._early_pct = partial_early_pct
        self._early_r = partial_early_r
        self._tp1_pct = partial_tp1_pct
        self._tp1_r = partial_tp1_r
        self._tp2_pct = partial_tp2_pct
        self._tp2_r = partial_tp2_r
        self._tp3_r = partial_tp3_r
        self._max_stale_hours = max_stale_hours
        self._stale_r = stale_threshold_r
        self._chop_mult = regime_chop_mult
        self._trend_mult = regime_trend_mult

    def init_exit_state(self, pos: dict) -> PositionExitState:
        """Initialize exit tracking state for a new position."""
        entry = pos["entry_price"]
        return PositionExitState(
            highest_price=entry,
            lowest_price=entry,
            current_sl=pos["stop_loss"],
            original_sl=pos["stop_loss"],
            original_qty=pos["qty"],
            remaining_qty=pos["qty"],
            entry_price=entry,
            atr=pos.get("atr", 0),
        )

    def update(
        self,
        pos: dict,
        state: PositionExitState,
        current_price: float,
        regime: str = "neutral",
    ) -> list[ExitDecision]:
        """Evaluate all exit conditions. Returns list of actions to take.

        Called every monitor tick (~60s) for each open position.
        """
        decisions: list[ExitDecision] = []
        direction = pos["direction"]
        entry = state.entry_price
        sl = state.current_sl

        # Calculate 1R distance
        r_distance = abs(entry - state.original_sl)
        if r_distance <= 0:
            return decisions

        # Update high-water mark
        if direction == "LONG":
            state.highest_price = max(state.highest_price, current_price)
        else:
            state.lowest_price = min(state.lowest_price, current_price)

        # Current R-multiple (how far price moved in our favor)
        if direction == "LONG":
            current_r = (current_price - entry) / r_distance
        else:
            current_r = (entry - current_price) / r_distance

        # Regime modifier for trailing distance
        regime_mult = self._get_regime_multiplier(regime)

        # ── 1. Check stop loss hit ──
        sl_hit = self._check_sl_hit(direction, current_price, state.current_sl)
        if sl_hit:
            decisions.append(ExitDecision(
                action=ExitAction.STOP_LOSS,
                close_pct=1.0,
                close_price=state.current_sl,
                reason=f"SL hit at ${state.current_sl:.2f} (R={current_r:.1f})",
            ))
            return decisions  # SL closes everything

        # ── 2. Check partial TPs (in order: TP3, TP2, TP1, Early) ──
        # Check highest first so we don't miss levels on big moves
        if not state.tp3_hit and current_r >= self._tp3_r:
            # Close remaining runner
            decisions.append(ExitDecision(
                action=ExitAction.PARTIAL_TP3,
                close_pct=1.0,  # All remaining
                close_price=current_price,
                reason=f"TP3 runner closed at {current_r:.1f}R",
            ))
            state.tp3_hit = True

        elif not state.tp2_hit and current_r >= self._tp2_r:
            # Close 30% of original (proportion of what's remaining)
            if state.remaining_qty > 0:
                # Already closed: early(25%) + TP1(25%) = 50%. Remaining = 50%.
                # TP2 = 30% of original = 60% of remaining 50%
                already_closed = self._early_pct + self._tp1_pct
                remaining_frac = max(1.0 - already_closed, 0.1)
                close_frac = min(self._tp2_pct / remaining_frac, 0.95)
                decisions.append(ExitDecision(
                    action=ExitAction.PARTIAL_TP2,
                    close_pct=close_frac,
                    close_price=current_price,
                    reason=f"TP2 partial close ({close_frac:.0%}) at {current_r:.1f}R",
                ))
                state.tp2_hit = True

        elif not state.tp1_hit and current_r >= self._tp1_r:
            # Close 25% of original at TP1
            decisions.append(ExitDecision(
                action=ExitAction.PARTIAL_TP1,
                close_pct=self._tp1_pct,
                close_price=current_price,
                reason=f"TP1 partial close ({self._tp1_pct:.0%}) at {current_r:.1f}R",
            ))
            state.tp1_hit = True
            # Also mark early as hit if it wasn't already
            state.early_hit = True

        elif not state.early_hit and current_r >= self._early_r:
            # Early partial: 25% at 1R — "pay for the trade", then SL to breakeven
            decisions.append(ExitDecision(
                action=ExitAction.PARTIAL_EARLY,
                close_pct=self._early_pct,
                close_price=current_price,
                reason=f"Early partial ({self._early_pct:.0%}) at {current_r:.1f}R — trade is free",
            ))
            state.early_hit = True

            # Move SL to breakeven — worst case is now $0 loss on remaining 75%
            new_sl = entry
            if self._sl_is_improvement(direction, new_sl, state.current_sl):
                state.current_sl = new_sl
                decisions.append(ExitDecision(
                    action=ExitAction.TRAIL_SL,
                    new_sl=new_sl,
                    reason="SL → breakeven after early partial",
                ))

        # ── 3. Trailing stop logic ──
        trail_decision = self._calc_trailing_sl(
            direction, entry, current_price, current_r, r_distance,
            state, regime_mult,
        )
        if trail_decision:
            decisions.append(trail_decision)

        # ── 4. Time-based exit ──
        time_decision = self._check_time_exit(pos, current_r, r_distance)
        if time_decision:
            decisions.append(time_decision)

        return decisions

    def _check_sl_hit(self, direction: str, price: float, sl: float) -> bool:
        if direction == "LONG":
            return price <= sl
        return price >= sl

    def _sl_is_improvement(self, direction: str, new_sl: float, old_sl: float) -> bool:
        """Check if new SL is better (tighter to profit) than old one."""
        if direction == "LONG":
            return new_sl > old_sl
        return new_sl < old_sl

    def _calc_trailing_sl(
        self,
        direction: str,
        entry: float,
        current_price: float,
        current_r: float,
        r_distance: float,
        state: PositionExitState,
        regime_mult: float,
    ) -> ExitDecision | None:
        """Calculate trailing stop adjustment."""
        # Not enough profit to trail yet
        if current_r < self._trail_be_r:
            return None

        # Determine trail distance
        if state.atr > 0:
            # ATR-based trailing, scaled by regime
            trail_dist = state.atr * self._trail_atr_mult * regime_mult
        else:
            # Fallback: trail at 1R distance, scaled by regime
            trail_dist = r_distance * regime_mult

        # After breakeven R but before full activation: just breakeven
        if current_r < self._trail_activate_r:
            new_sl = entry
        else:
            # Full trailing: SL follows best price minus trail distance
            if direction == "LONG":
                new_sl = state.highest_price - trail_dist
            else:
                new_sl = state.lowest_price + trail_dist

        # Never move SL backwards
        if not self._sl_is_improvement(direction, new_sl, state.current_sl):
            return None

        # Round to 2 decimal places
        new_sl = round(new_sl, 2)
        state.current_sl = new_sl

        return ExitDecision(
            action=ExitAction.TRAIL_SL,
            new_sl=new_sl,
            reason=f"Trail SL → ${new_sl:.2f} (R={current_r:.1f}, regime_mult={regime_mult:.1f})",
        )

    def _check_time_exit(
        self, pos: dict, current_r: float, r_distance: float,
    ) -> ExitDecision | None:
        """Close stale trades that haven't moved enough."""
        entry_time = pos.get("entry_time", 0)
        if entry_time <= 0:
            return None

        hours_held = (time.time() - entry_time) / 3600
        if hours_held < self._max_stale_hours:
            return None

        # If trade hasn't moved beyond threshold, close it
        if abs(current_r) < self._stale_r:
            return ExitDecision(
                action=ExitAction.TIME_EXIT,
                close_pct=1.0,
                close_price=0,  # Use current market price
                reason=f"Stale trade: {hours_held:.1f}h held, only {current_r:.2f}R moved",
            )

        return None

    def _get_regime_multiplier(self, regime: str) -> float:
        """Regime-aware trailing distance modifier."""
        chop_regimes = {"choppy", "manipulation", "neutral", "ranging"}
        trend_regimes = {"strong_bull", "strong_bear", "bull", "bear"}

        regime_lower = regime.lower()
        if regime_lower in chop_regimes:
            return self._chop_mult
        if regime_lower in trend_regimes:
            return self._trend_mult
        return 1.0
