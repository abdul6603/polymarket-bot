"""Risk Manager V8 for Hawk — compound bankroll, risk gate, game-level correlation guard."""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from pathlib import Path

from hawk.config import HawkConfig
from hawk.edge import TradeOpportunity
from hawk.tracker import HawkTracker
from hawk.scanner import _is_updown_price_market

log = logging.getLogger(__name__)

# Fix 3: Extract underlying asset from market questions to detect correlated positions
_ASSET_PATTERNS = [
    re.compile(r"(?:price\s+of\s+|will\s+)(bitcoin|ethereum|solana|xrp|bnb|cardano|dogecoin|avalanche|polkadot|polygon|chainlink|litecoin)", re.IGNORECASE),
    re.compile(r"\b(btc|eth|sol|xrp|bnb|ada|doge|avax|dot|matic|link|ltc)\b", re.IGNORECASE),
    re.compile(r"(?:will\s+the\s+|will\s+)([\w\s]+?)\s+(?:win|beat|defeat|cover|score)", re.IGNORECASE),
]

_ASSET_ALIASES = {
    "btc": "bitcoin", "eth": "ethereum", "sol": "solana",
    "ada": "cardano", "doge": "dogecoin", "avax": "avalanche",
    "dot": "polkadot", "matic": "polygon", "link": "chainlink",
    "ltc": "litecoin",
}


def extract_underlying(question: str) -> str | None:
    """Extract the underlying asset/entity from a market question."""
    for pattern in _ASSET_PATTERNS:
        m = pattern.search(question)
        if m:
            raw = m.group(1).strip().lower()
            return _ASSET_ALIASES.get(raw, raw)
    return None


# V8: Game-level correlation patterns — "Team A vs Team B", "Team A/Team B"
_GAME_PATTERNS = [
    re.compile(r"([\w\s]+?)\s+vs\.?\s+([\w\s]+?)(?:\s|$|\?|:)", re.IGNORECASE),
    re.compile(r"([\w\s]+?)/([\w\s]+?)(?:\s|$|\?|:)", re.IGNORECASE),
]


def extract_game_id(question: str, event_slug: str = "") -> str | None:
    """Extract a canonical game identifier to group correlated markets.

    V8: Uses event_slug (best signal — Polymarket groups all game markets
    under one slug) with regex team-pair fallback.

    Returns canonical string like "grizzlies_vs_kings" or the event_slug,
    or falls back to extract_underlying() for non-game markets.
    """
    # Best signal: Polymarket event_slug groups ML + spread + O/U together
    if event_slug:
        return event_slug

    # Fallback: regex "Team A vs Team B" → canonical sorted pair
    for pattern in _GAME_PATTERNS:
        m = pattern.search(question)
        if m:
            team_a = m.group(1).strip().lower()
            team_b = m.group(2).strip().lower()
            # Skip very short matches (likely false positives like "o/u")
            if len(team_a) < 3 or len(team_b) < 3:
                continue
            teams = sorted([team_a, team_b])
            return f"{teams[0]}_vs_{teams[1]}"

    # Final fallback: asset-level correlation (existing logic)
    return extract_underlying(question)

from zoneinfo import ZoneInfo
ET = ZoneInfo("America/New_York")


class HawkRiskManager:
    """Gate trades on risk limits: daily loss cap, max concurrent, risk score, losing streak."""

    def __init__(self, cfg: HawkConfig, tracker: HawkTracker):
        self.cfg = cfg
        self.tracker = tracker
        self._daily_pnl: float = 0.0
        self._daily_reset_date: str = ""
        self._shutdown: bool = False
        self._consecutive_losses: int = 0

        # Shared balance manager — cross-agent wallet coordination
        self._balance_mgr = None
        try:
            import sys as _bm_sys
            _bm_sys.path.insert(0, str(Path.home() / "shared"))
            from balance_manager import BalanceManager
            self._balance_mgr = None  # Disabled: Garves is paper mode, Hawk gets full wallet
            self._balance_mgr.register(float(os.environ.get("HAWK_ALLOCATION_WEIGHT", "3")))
        except Exception:
            pass

    def daily_reset(self) -> None:
        """Reset daily P&L tracking at midnight ET."""
        today = datetime.now(ET).strftime("%Y-%m-%d")
        if today != self._daily_reset_date:
            self._daily_pnl = 0.0
            self._daily_reset_date = today
            self._shutdown = False
            self._consecutive_losses = 0
            log.info("Hawk daily risk reset for %s", today)

    def record_pnl(self, pnl: float) -> None:
        """Record a realized P&L change."""
        self._daily_pnl += pnl

        # Track losing streak
        if pnl < 0:
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0

        if self._daily_pnl <= -self.cfg.daily_loss_cap:
            self._shutdown = True
            log.warning(
                "HAWK SHUTDOWN: Daily loss cap hit ($%.2f / -$%.2f)",
                self._daily_pnl, self.cfg.daily_loss_cap,
            )

    def is_shutdown(self) -> bool:
        """True if daily loss cap hit."""
        return self._shutdown

    @property
    def consecutive_losses(self) -> int:
        return self._consecutive_losses

    def effective_bankroll(self) -> float:
        """Base bankroll + cumulative realized profits. Floor at 50% of base."""
        if not self.cfg.compound_bankroll:
            return self.cfg.bankroll_usd
        cum_pnl = self.tracker.cumulative_pnl()
        return max(self.cfg.bankroll_usd * 0.5, self.cfg.bankroll_usd + cum_pnl)

    def check_trade(self, opp: TradeOpportunity) -> tuple[bool, str]:
        """Gate a trade on risk limits.

        Returns (allowed, reason).
        """
        if self._shutdown:
            return False, "Daily loss cap hit — trading paused until midnight ET"

        # Belt-and-suspenders: block crypto price markets even if scanner missed them
        if _is_updown_price_market(opp.market.question):
            return False, f"Blocked crypto price market: {opp.market.question[:60]}"

        if opp.edge < self.cfg.min_edge:
            return False, f"Edge {opp.edge:.3f} below minimum {self.cfg.min_edge:.3f}"

        # V2: Risk score gate
        if opp.risk_score > self.cfg.max_risk_score:
            return False, f"Risk score {opp.risk_score}/10 exceeds max {self.cfg.max_risk_score}"

        # V2: Losing streak protection — reduce max concurrent after 3 consecutive losses
        effective_max = self.cfg.max_concurrent
        if self._consecutive_losses >= 3:
            effective_max = max(2, self.cfg.max_concurrent - 2)
            log.info("Losing streak (%d): reducing max concurrent to %d", self._consecutive_losses, effective_max)

        if self.tracker.count >= effective_max:
            return False, f"Max concurrent positions reached ({self.tracker.count}/{effective_max})"

        eff_bankroll = self.effective_bankroll()
        new_exposure = self.tracker.total_exposure + opp.position_size_usd
        if new_exposure > eff_bankroll:
            return False, f"Would exceed bankroll: ${new_exposure:.2f} > ${eff_bankroll:.2f}"

        if self.tracker.has_position_for_market(opp.market.condition_id, opp.market.question):
            return False, f"Already have position in market {opp.market.condition_id[:12]}"

        # V8: Unified game-level correlation guard
        # Replaces separate event_slug cap + extract_underlying check
        event_slug = getattr(opp.market, 'event_slug', '') or ''
        new_game_id = extract_game_id(opp.market.question, event_slug)
        if new_game_id:
            game_exposure = 0.0
            for pos in self.tracker.open_positions:
                pos_slug = pos.get("event_slug", "")
                pos_game_id = pos.get("game_id") or extract_game_id(pos.get("question", ""), pos_slug)
                if pos_game_id and pos_game_id == new_game_id:
                    game_exposure += pos.get("size_usd", 0)

            if game_exposure + opp.position_size_usd > self.cfg.max_per_event_usd:
                return False, (
                    f"Game correlation cap: already ${game_exposure:.2f} on '{new_game_id}', "
                    f"adding ${opp.position_size_usd:.2f} would exceed ${self.cfg.max_per_event_usd:.2f} max"
                )

        if opp.position_size_usd > self.cfg.max_bet_usd:
            return False, f"Position size ${opp.position_size_usd:.2f} exceeds max bet ${self.cfg.max_bet_usd:.2f}"

        # Shared balance manager (cross-agent wallet coordination)
        if self._balance_mgr:
            try:
                self._balance_mgr.report_exposure(self.tracker.total_exposure)
                bm_ok, bm_reason = self._balance_mgr.can_trade(opp.position_size_usd)
                if not bm_ok:
                    return False, f"Balance manager: {bm_reason}"
            except Exception:
                pass

        log.info(
            "Hawk risk check passed: edge=%.3f, risk=%d/10, positions=%d/%d, exposure=$%.2f/$%.2f",
            opp.edge, opp.risk_score, self.tracker.count, effective_max,
            self.tracker.total_exposure, eff_bankroll,
        )
        return True, "ok"
