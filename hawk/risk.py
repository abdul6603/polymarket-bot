"""Risk Manager V2 for Hawk — compound bankroll, risk gate, losing streak detection."""
from __future__ import annotations

import logging
import re
from datetime import datetime

from hawk.config import HawkConfig
from hawk.edge import TradeOpportunity
from hawk.tracker import HawkTracker
from hawk.scanner import _is_updown_price_market

log = logging.getLogger(__name__)

# Fix 3: Extract underlying asset from market questions to detect correlated positions
_ASSET_PATTERNS = [
    # "price of Bitcoin" / "Will Bitcoin"
    re.compile(r"(?:price\s+of\s+|will\s+)(bitcoin|ethereum|solana|xrp|bnb|cardano|dogecoin|avalanche|polkadot|polygon|chainlink|litecoin)", re.IGNORECASE),
    # Ticker symbols: "BTC", "ETH", "SOL"
    re.compile(r"\b(btc|eth|sol|xrp|bnb|ada|doge|avax|dot|matic|link|ltc)\b", re.IGNORECASE),
    # Team names for sports
    re.compile(r"(?:will\s+the\s+|will\s+)([\w\s]+?)\s+(?:win|beat|defeat|cover|score)", re.IGNORECASE),
]

# Normalize ticker aliases to canonical asset names
_ASSET_ALIASES = {
    "btc": "bitcoin", "eth": "ethereum", "sol": "solana",
    "ada": "cardano", "doge": "dogecoin", "avax": "avalanche",
    "dot": "polkadot", "matic": "polygon", "link": "chainlink",
    "ltc": "litecoin",
}


def extract_underlying(question: str) -> str | None:
    """Extract the underlying asset/entity from a market question.

    Returns normalized lowercase string or None if no recognizable underlying.
    """
    for pattern in _ASSET_PATTERNS:
        m = pattern.search(question)
        if m:
            raw = m.group(1).strip().lower()
            return _ASSET_ALIASES.get(raw, raw)
    return None

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

        # Fix 6: Per-event exposure cap — don't pile $60+ into one match across O/U lines
        event_slug = getattr(opp.market, 'event_slug', '') or ''
        if event_slug:
            event_exposure = sum(
                p.get("size_usd", 0)
                for p in self.tracker.open_positions
                if p.get("event_slug", "") == event_slug
            )
            if event_exposure + opp.position_size_usd > self.cfg.max_per_event_usd:
                return False, (
                    f"Per-event cap: already ${event_exposure:.2f} on '{event_slug}', "
                    f"adding ${opp.position_size_usd:.2f} would exceed ${self.cfg.max_per_event_usd:.2f} max"
                )

        # Fix 3: Position correlation — block trades on same underlying asset
        new_underlying = extract_underlying(opp.market.question)
        if new_underlying:
            for pos in self.tracker.open_positions:
                existing_underlying = extract_underlying(pos.get("question", ""))
                if existing_underlying and existing_underlying == new_underlying:
                    return False, (
                        f"Correlated position blocked: already holding '{new_underlying}' "
                        f"via {pos.get('condition_id', '???')[:12]}"
                    )

        if opp.position_size_usd > self.cfg.max_bet_usd:
            return False, f"Position size ${opp.position_size_usd:.2f} exceeds max bet ${self.cfg.max_bet_usd:.2f}"

        log.info(
            "Hawk risk check passed: edge=%.3f, risk=%d/10, positions=%d/%d, exposure=$%.2f/$%.2f",
            opp.edge, opp.risk_score, self.tracker.count, effective_max,
            self.tracker.total_exposure, eff_bankroll,
        )
        return True, "ok"
