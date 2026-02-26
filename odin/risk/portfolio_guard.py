"""Portfolio Risk Guard — portfolio-level constraints for multi-coin trading.

Prevents concentrated/correlated risk when trading 20+ coins simultaneously.
Fail-open design: if PortfolioGuard errors, log warning and allow the trade.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# Coin tiers for notional caps and risk scaling
MAJOR_COINS = {"BTC", "ETH"}
MID_COINS = {"SOL", "XRP", "BNB", "DOGE", "ADA", "AVAX", "LINK", "DOT"}

# High correlation pairs — treat as same-asset for direction limits
CORRELATION_GROUPS = [
    {"BTC", "ETH"},          # Crypto majors move together
    {"SOL", "AVAX", "DOT"},  # L1 alt group
    {"DOGE", "SHIB", "PEPE"},  # Meme group
    {"LINK", "AAVE", "UNI"},   # DeFi group
]


def coin_tier(symbol: str) -> str:
    """Return 'major', 'mid', or 'alt' for a coin symbol."""
    bare = symbol.replace("USDT", "").replace("USD", "").upper()
    if bare in MAJOR_COINS:
        return "major"
    if bare in MID_COINS:
        return "mid"
    return "alt"


def notional_cap_for_tier(tier: str, cfg) -> float:
    """Return max notional USD for a coin tier."""
    if tier == "major":
        return cfg.notional_cap_major
    if tier == "mid":
        return cfg.notional_cap_mid
    return cfg.notional_cap_alt


def _bare(symbol: str) -> str:
    return symbol.replace("USDT", "").replace("USD", "").upper()


@dataclass
class PortfolioState:
    """Snapshot of current portfolio for guard checks."""
    balance: float = 0.0
    positions: list[dict] = field(default_factory=list)
    # Derived (computed by PortfolioGuard)
    total_heat_usd: float = 0.0
    total_heat_pct: float = 0.0
    long_count: int = 0
    short_count: int = 0
    long_notional: float = 0.0
    short_notional: float = 0.0
    per_coin_notional: dict[str, float] = field(default_factory=dict)
    per_coin_direction: dict[str, str] = field(default_factory=dict)


@dataclass
class GuardDecision:
    """Result of a portfolio guard check for a proposed trade."""
    allowed: bool = True
    reasons: list[str] = field(default_factory=list)
    adjusted_risk_usd: float | None = None  # If risk was scaled down
    notional_cap: float | None = None        # Max notional for this coin


class PortfolioGuard:
    """Portfolio-level risk gatekeeper.

    Call check_trade() before every trade to get a GuardDecision.
    Call update_state() after each position change to refresh portfolio snapshot.
    """

    def __init__(self, cfg, data_dir: Path | None = None):
        self.cfg = cfg
        self._data_dir = data_dir or Path(cfg.data_dir)
        self._state = PortfolioState()
        self._blacklist: dict[str, dict] = {}  # symbol -> {losses, until}
        self._blacklist_path = self._data_dir / "portfolio_blacklist.json"
        self._load_blacklist()

    # ── Public API ──

    def update_state(self, balance: float, positions: list[dict]) -> PortfolioState:
        """Refresh portfolio state from current positions."""
        state = PortfolioState(balance=balance, positions=positions)

        for pos in positions:
            symbol = _bare(pos.get("symbol", ""))
            direction = (pos.get("direction", "") or pos.get("side", "")).lower()
            risk_usd = abs(float(pos.get("risk_usd", 0) or 0))
            notional = abs(float(pos.get("notional", 0) or pos.get("size_usd", 0) or 0))

            state.total_heat_usd += risk_usd
            state.per_coin_notional[symbol] = state.per_coin_notional.get(symbol, 0) + notional
            state.per_coin_direction[symbol] = direction

            if direction in ("long", "buy"):
                state.long_count += 1
                state.long_notional += notional
            elif direction in ("short", "sell"):
                state.short_count += 1
                state.short_notional += notional

        if balance > 0:
            state.total_heat_pct = (state.total_heat_usd / balance) * 100

        self._state = state
        return state

    def check_trade(
        self,
        symbol: str,
        direction: str,
        risk_usd: float,
        notional_usd: float,
        trade_type: str = "swing",
    ) -> GuardDecision:
        """Check if a proposed trade passes portfolio constraints.

        Returns GuardDecision with allowed=True/False and reasons.
        Fail-open: any exception returns allowed=True with a warning.
        """
        try:
            return self._check(symbol, direction, risk_usd, notional_usd, trade_type)
        except Exception as e:
            log.warning("[PORTFOLIO-GUARD] Error in check, fail-open: %s", e)
            return GuardDecision(allowed=True, reasons=[f"guard_error: {e}"])

    def record_loss(self, symbol: str) -> None:
        """Record a loss for per-coin blacklist tracking."""
        bare = _bare(symbol)
        entry = self._blacklist.get(bare, {"consecutive_losses": 0, "blocked_until": 0})
        entry["consecutive_losses"] = entry.get("consecutive_losses", 0) + 1
        threshold = self.cfg.coin_blacklist_after_losses

        if entry["consecutive_losses"] >= threshold:
            entry["blocked_until"] = time.time() + 3600  # Block for 1 hour
            log.warning(
                "[PORTFOLIO-GUARD] %s blacklisted: %d consecutive losses (threshold %d)",
                bare, entry["consecutive_losses"], threshold,
            )

        self._blacklist[bare] = entry
        self._save_blacklist()

    def record_win(self, symbol: str) -> None:
        """Reset consecutive loss counter on a win."""
        bare = _bare(symbol)
        if bare in self._blacklist:
            self._blacklist[bare]["consecutive_losses"] = 0
            self._save_blacklist()

    def is_blacklisted(self, symbol: str) -> bool:
        """Check if a coin is currently blacklisted."""
        bare = _bare(symbol)
        entry = self._blacklist.get(bare)
        if not entry:
            return False
        if entry.get("blocked_until", 0) > time.time():
            return True
        # Expired — clear it
        if entry.get("blocked_until", 0) > 0:
            entry["blocked_until"] = 0
            self._save_blacklist()
        return False

    def get_status(self) -> dict:
        """Return portfolio guard status for dashboard."""
        s = self._state
        return {
            "balance": s.balance,
            "total_heat_usd": round(s.total_heat_usd, 2),
            "total_heat_pct": round(s.total_heat_pct, 2),
            "max_heat_pct": self.cfg.portfolio_max_heat_pct,
            "long_count": s.long_count,
            "short_count": s.short_count,
            "long_notional": round(s.long_notional, 2),
            "short_notional": round(s.short_notional, 2),
            "max_same_direction": self.cfg.max_same_direction,
            "per_coin_notional": {k: round(v, 2) for k, v in s.per_coin_notional.items()},
            "blacklisted": {
                k: {
                    "losses": v.get("consecutive_losses", 0),
                    "blocked_until": v.get("blocked_until", 0),
                    "active": v.get("blocked_until", 0) > time.time(),
                }
                for k, v in self._blacklist.items()
                if v.get("consecutive_losses", 0) > 0
            },
            "open_positions": s.long_count + s.short_count,
            "max_positions": self.cfg.max_open_positions,
            "scalp_count": sum(1 for p in s.positions if p.get("trade_type") == "scalp"),
            "swing_count": sum(1 for p in s.positions if p.get("trade_type", "swing") == "swing"),
            "scalp_max": self.cfg.scalp_max_positions,
            "swing_max": self.cfg.swing_max_positions,
        }

    # ── Internal ──

    def _check(
        self, symbol: str, direction: str, risk_usd: float, notional_usd: float,
        trade_type: str = "swing",
    ) -> GuardDecision:
        s = self._state
        decision = GuardDecision()
        bare = _bare(symbol)
        tier = coin_tier(symbol)
        direction = direction.lower()

        # 1. Per-coin blacklist
        if self.is_blacklisted(symbol):
            entry = self._blacklist.get(bare, {})
            decision.allowed = False
            decision.reasons.append(
                f"{bare} blacklisted ({entry.get('consecutive_losses', 0)} consecutive losses)"
            )
            return decision

        # 2. Max open positions (scalp/swing separate limits)
        total_open = s.long_count + s.short_count
        if total_open >= self.cfg.max_open_positions:
            decision.allowed = False
            decision.reasons.append(
                f"max total positions reached ({total_open}/{self.cfg.max_open_positions})"
            )
            return decision

        # Count scalp vs swing from position metadata
        scalp_count = sum(1 for p in s.positions if p.get("trade_type") == "scalp")
        swing_count = total_open - scalp_count
        if trade_type == "scalp" and scalp_count >= self.cfg.scalp_max_positions:
            decision.allowed = False
            decision.reasons.append(
                f"max scalp positions reached ({scalp_count}/{self.cfg.scalp_max_positions})"
            )
            return decision
        if trade_type == "swing" and swing_count >= self.cfg.swing_max_positions:
            decision.allowed = False
            decision.reasons.append(
                f"max swing positions reached ({swing_count}/{self.cfg.swing_max_positions})"
            )
            return decision

        # 3. Portfolio heat check
        new_heat_pct = ((s.total_heat_usd + risk_usd) / max(s.balance, 1)) * 100
        if new_heat_pct > self.cfg.portfolio_max_heat_pct:
            # Scale risk down instead of blocking
            available_heat = max(
                0, (self.cfg.portfolio_max_heat_pct / 100) * s.balance - s.total_heat_usd
            )
            if available_heat < 5:  # Less than $5 available
                decision.allowed = False
                decision.reasons.append(
                    f"portfolio heat {s.total_heat_pct:.1f}% exceeds max {self.cfg.portfolio_max_heat_pct}%"
                )
                return decision
            decision.adjusted_risk_usd = min(risk_usd, available_heat)
            decision.reasons.append(
                f"risk scaled ${risk_usd:.0f} -> ${decision.adjusted_risk_usd:.0f} (heat cap)"
            )

        # 4. Direction balance
        if direction in ("long", "buy") and s.long_count >= self.cfg.max_same_direction:
            decision.allowed = False
            decision.reasons.append(
                f"max LONG positions reached ({s.long_count}/{self.cfg.max_same_direction})"
            )
            return decision
        if direction in ("short", "sell") and s.short_count >= self.cfg.max_same_direction:
            decision.allowed = False
            decision.reasons.append(
                f"max SHORT positions reached ({s.short_count}/{self.cfg.max_same_direction})"
            )
            return decision

        # 5. Correlation check — max 2 positions in same correlation group + same direction
        for group in CORRELATION_GROUPS:
            if bare in group:
                same_dir_in_group = sum(
                    1 for coin, d in s.per_coin_direction.items()
                    if coin in group and d == direction
                )
                if same_dir_in_group >= 2:
                    decision.allowed = False
                    decision.reasons.append(
                        f"correlated group {sorted(group)} already has {same_dir_in_group} "
                        f"{direction.upper()} positions"
                    )
                    return decision

        # 6. Per-coin notional cap
        cap = notional_cap_for_tier(tier, self.cfg)
        existing = s.per_coin_notional.get(bare, 0)
        if existing + notional_usd > cap:
            allowed_notional = max(0, cap - existing)
            if allowed_notional < 100:  # Less than $100 room
                decision.allowed = False
                decision.reasons.append(
                    f"{bare} notional ${existing + notional_usd:.0f} exceeds {tier} cap ${cap:.0f}"
                )
                return decision
            decision.notional_cap = allowed_notional
            decision.reasons.append(
                f"notional capped to ${allowed_notional:.0f} ({tier} tier cap ${cap:.0f})"
            )

        # 7. Risk scaling by open position count (swing only, scalps are fast)
        if trade_type == "swing" and total_open >= 3 and decision.adjusted_risk_usd is None:
            scale = max(0.50, 1.0 - (total_open - 2) * 0.15)
            scaled = risk_usd * scale
            decision.adjusted_risk_usd = scaled
            decision.reasons.append(
                f"risk scaled ${risk_usd:.0f} -> ${scaled:.0f} ({total_open} open positions)"
            )

        if decision.allowed and not decision.reasons:
            decision.reasons.append("all checks passed")

        return decision

    def _load_blacklist(self) -> None:
        try:
            if self._blacklist_path.exists():
                self._blacklist = json.loads(self._blacklist_path.read_text())
        except Exception:
            self._blacklist = {}

    def _save_blacklist(self) -> None:
        try:
            self._blacklist_path.write_text(json.dumps(self._blacklist, indent=2))
        except Exception as e:
            log.warning("[PORTFOLIO-GUARD] Failed to save blacklist: %s", e)
