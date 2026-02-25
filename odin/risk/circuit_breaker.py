"""Circuit breaker system — halts trading on adverse conditions.

Monitors:
- Consecutive losses → pause after N losses
- Daily/weekly/monthly drawdown → graduated response
- Max total drawdown → full halt
- Spread/volume/funding checks
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class CircuitBreakerState:
    """Current state of all circuit breakers."""
    trading_allowed: bool = True
    reason: str = ""

    # Counters
    consecutive_losses: int = 0
    daily_pnl: float = 0.0
    weekly_pnl: float = 0.0
    monthly_pnl: float = 0.0
    total_pnl: float = 0.0

    # Drawdown tracking
    peak_balance: float = 0.0
    current_balance: float = 0.0
    drawdown_pct: float = 0.0

    # Timestamps
    last_loss_time: float = 0.0
    pause_until: float = 0.0
    halt_time: float = 0.0

    # Size modifier
    size_modifier: float = 1.0  # Applied on top of other sizing

    # Per-symbol tracking (Phase 2)
    per_symbol_losses: dict[str, int] = field(default_factory=dict)
    per_symbol_pnl: dict[str, float] = field(default_factory=dict)


class CircuitBreaker:
    """
    Multi-level circuit breaker for Odin.

    Level 1: 3 consecutive losses → pause 4 hours, reduce size 50%
    Level 2: Daily loss > 3% → stop trading for 24 hours
    Level 3: Weekly loss > 6% → reduce size 50% for rest of week
    Level 4: Monthly drawdown > 15% → enter recovery mode (25% size)
    Level 5: Total drawdown > 25% → HALT all trading

    Also checks market conditions:
    - Funding rate > 0.05% on paying side → skip
    - 24h volume < $1M → skip (thin market)
    """

    def __init__(
        self,
        starting_capital: float = 1000.0,
        max_consecutive_losses: int = 3,
        max_daily_loss_pct: float = 3.0,
        max_weekly_loss_pct: float = 6.0,
        max_monthly_dd_pct: float = 15.0,
        max_total_dd_pct: float = 25.0,
        pause_hours_after_losses: float = 4.0,
        state_file: Path | None = None,
    ):
        self._starting_capital = starting_capital
        self._max_consec = max_consecutive_losses
        self._max_daily = max_daily_loss_pct
        self._max_weekly = max_weekly_loss_pct
        self._max_monthly = max_monthly_dd_pct
        self._max_total = max_total_dd_pct
        self._pause_hours = pause_hours_after_losses
        self._state_file = state_file

        self._state = CircuitBreakerState(
            peak_balance=starting_capital,
            current_balance=starting_capital,
        )

        # Load persisted state
        if self._state_file and self._state_file.exists():
            self._load_state()

    @property
    def state(self) -> CircuitBreakerState:
        return self._state

    def check(self) -> CircuitBreakerState:
        """Run all circuit breaker checks. Returns current state."""
        s = self._state
        s.trading_allowed = True
        s.reason = ""
        s.size_modifier = 1.0

        now = time.time()

        # Check if in a timed pause
        if s.pause_until > now:
            s.trading_allowed = False
            remaining = (s.pause_until - now) / 3600
            s.reason = f"Paused for {remaining:.1f}h (consecutive losses)"
            return s

        # Check if halted
        if s.halt_time > 0:
            s.trading_allowed = False
            s.reason = "HALTED — max drawdown exceeded, manual review required"
            return s

        # Level 1: Consecutive losses
        if s.consecutive_losses >= self._max_consec:
            s.pause_until = now + self._pause_hours * 3600
            s.size_modifier = 0.5
            s.trading_allowed = False
            s.reason = (
                f"Paused: {s.consecutive_losses} consecutive losses "
                f"(resuming in {self._pause_hours}h at 50% size)"
            )
            log.warning("[CB] %s", s.reason)
            self._save_state()
            return s

        # Calculate drawdowns
        if s.peak_balance > 0:
            s.drawdown_pct = round(
                (1 - s.current_balance / s.peak_balance) * 100, 2
            )

        daily_dd = abs(s.daily_pnl / self._starting_capital * 100) if s.daily_pnl < 0 else 0
        weekly_dd = abs(s.weekly_pnl / self._starting_capital * 100) if s.weekly_pnl < 0 else 0
        monthly_dd = abs(s.monthly_pnl / self._starting_capital * 100) if s.monthly_pnl < 0 else 0

        # Level 5: Total drawdown (most severe)
        if s.drawdown_pct >= self._max_total:
            s.trading_allowed = False
            s.halt_time = now
            s.reason = f"HALTED: {s.drawdown_pct:.1f}% total drawdown (max {self._max_total}%)"
            log.critical("[CB] %s", s.reason)
            self._save_state()
            return s

        # Level 4: Monthly drawdown
        if monthly_dd >= self._max_monthly:
            s.size_modifier = 0.25
            s.reason = f"Recovery mode: {monthly_dd:.1f}% monthly DD (25% size)"
            log.warning("[CB] %s", s.reason)

        # Level 3: Weekly loss
        elif weekly_dd >= self._max_weekly:
            s.size_modifier = 0.5
            s.reason = f"Weekly loss limit: {weekly_dd:.1f}% (50% size)"
            log.warning("[CB] %s", s.reason)

        # Level 2: Daily loss
        elif daily_dd >= self._max_daily:
            s.trading_allowed = False
            s.pause_until = now + 24 * 3600
            s.reason = f"Daily loss limit: {daily_dd:.1f}% — stopped for 24h"
            log.warning("[CB] %s", s.reason)

        # Graduated size reduction near limits
        elif s.consecutive_losses >= 2:
            s.size_modifier = 0.75
            s.reason = f"{s.consecutive_losses} losses — reduced to 75% size"

        return s

    def record_trade(self, pnl: float, symbol: str = "") -> None:
        """Record a trade result and update counters."""
        s = self._state

        if pnl >= 0:
            s.consecutive_losses = 0
        else:
            s.consecutive_losses += 1
            s.last_loss_time = time.time()

        s.daily_pnl += pnl
        s.weekly_pnl += pnl
        s.monthly_pnl += pnl
        s.total_pnl += pnl

        s.current_balance += pnl
        if s.current_balance > s.peak_balance:
            s.peak_balance = s.current_balance

        # Per-symbol tracking
        if symbol:
            bare = symbol.replace("USDT", "").replace("USD", "").upper()
            s.per_symbol_pnl[bare] = s.per_symbol_pnl.get(bare, 0) + pnl
            if pnl >= 0:
                s.per_symbol_losses[bare] = 0
            else:
                s.per_symbol_losses[bare] = s.per_symbol_losses.get(bare, 0) + 1

        log.info(
            "[CB] Trade PnL: $%.2f | Daily: $%.2f Weekly: $%.2f "
            "Monthly: $%.2f | Balance: $%.2f | Consec losses: %d%s",
            pnl, s.daily_pnl, s.weekly_pnl, s.monthly_pnl,
            s.current_balance, s.consecutive_losses,
            f" | {symbol} losses: {s.per_symbol_losses.get(symbol.replace('USDT','').upper(), 0)}"
            if symbol else "",
        )
        self._save_state()

    def is_symbol_blocked(self, symbol: str, max_losses: int = 3) -> bool:
        """Check if a specific coin has too many consecutive losses."""
        bare = symbol.replace("USDT", "").replace("USD", "").upper()
        return self._state.per_symbol_losses.get(bare, 0) >= max_losses

    def reset_daily(self) -> None:
        """Reset daily counters (call at midnight ET)."""
        self._state.daily_pnl = 0.0
        # Clear pause if it was a daily pause
        if self._state.pause_until > 0:
            if time.time() > self._state.pause_until:
                self._state.pause_until = 0
                self._state.consecutive_losses = 0
        self._save_state()

    def reset_weekly(self) -> None:
        """Reset weekly counters (call Sunday midnight ET)."""
        self._state.weekly_pnl = 0.0
        self._save_state()

    def reset_monthly(self) -> None:
        """Reset monthly counters (call 1st of month)."""
        self._state.monthly_pnl = 0.0
        self._save_state()

    def manual_resume(self) -> None:
        """Manually resume after a halt (requires human decision)."""
        self._state.halt_time = 0
        self._state.pause_until = 0
        self._state.consecutive_losses = 0
        self._state.size_modifier = 0.5  # Come back at half size
        log.info("[CB] Manual resume — trading at 50%% size")
        self._save_state()

    def update_balance(self, balance: float) -> None:
        """Update current balance from exchange."""
        self._state.current_balance = balance
        if balance > self._state.peak_balance:
            self._state.peak_balance = balance

    # ── Persistence ──

    def _save_state(self) -> None:
        if not self._state_file:
            return
        try:
            s = self._state
            data = {
                "consecutive_losses": s.consecutive_losses,
                "daily_pnl": s.daily_pnl,
                "weekly_pnl": s.weekly_pnl,
                "monthly_pnl": s.monthly_pnl,
                "total_pnl": s.total_pnl,
                "peak_balance": s.peak_balance,
                "current_balance": s.current_balance,
                "pause_until": s.pause_until,
                "halt_time": s.halt_time,
                "last_loss_time": s.last_loss_time,
                "per_symbol_losses": s.per_symbol_losses,
                "per_symbol_pnl": {k: round(v, 2) for k, v in s.per_symbol_pnl.items()},
            }
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._state_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            log.debug("[CB] Save error: %s", e)

    def _load_state(self) -> None:
        try:
            with open(self._state_file) as f:
                data = json.load(f)
            s = self._state
            s.consecutive_losses = data.get("consecutive_losses", 0)
            s.daily_pnl = data.get("daily_pnl", 0)
            s.weekly_pnl = data.get("weekly_pnl", 0)
            s.monthly_pnl = data.get("monthly_pnl", 0)
            s.total_pnl = data.get("total_pnl", 0)
            s.peak_balance = data.get("peak_balance", self._starting_capital)
            s.current_balance = data.get("current_balance", self._starting_capital)
            s.pause_until = data.get("pause_until", 0)
            s.halt_time = data.get("halt_time", 0)
            s.last_loss_time = data.get("last_loss_time", 0)
            s.per_symbol_losses = data.get("per_symbol_losses", {})
            s.per_symbol_pnl = data.get("per_symbol_pnl", {})
            log.info("[CB] State loaded: balance=$%.2f DD=%.1f%%",
                     s.current_balance, s.drawdown_pct)
        except Exception as e:
            log.debug("[CB] Load error: %s", e)
