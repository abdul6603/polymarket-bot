"""Risk Manager for Hawk — bankroll management, daily loss cap, concurrent limits."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone, timedelta

from hawk.config import HawkConfig
from hawk.edge import TradeOpportunity
from hawk.tracker import HawkTracker

log = logging.getLogger(__name__)

ET = timezone(timedelta(hours=-5))


class HawkRiskManager:
    """Gate trades on risk limits: daily loss cap, max concurrent, exposure, duplicates."""

    def __init__(self, cfg: HawkConfig, tracker: HawkTracker):
        self.cfg = cfg
        self.tracker = tracker
        self._daily_pnl: float = 0.0
        self._daily_reset_date: str = ""
        self._shutdown: bool = False

    def daily_reset(self) -> None:
        """Reset daily P&L tracking at midnight ET."""
        today = datetime.now(ET).strftime("%Y-%m-%d")
        if today != self._daily_reset_date:
            self._daily_pnl = 0.0
            self._daily_reset_date = today
            self._shutdown = False
            log.info("Hawk daily risk reset for %s", today)

    def record_pnl(self, pnl: float) -> None:
        """Record a realized P&L change."""
        self._daily_pnl += pnl
        if self._daily_pnl <= -self.cfg.daily_loss_cap:
            self._shutdown = True
            log.warning(
                "HAWK SHUTDOWN: Daily loss cap hit ($%.2f / -$%.2f)",
                self._daily_pnl, self.cfg.daily_loss_cap,
            )

    def is_shutdown(self) -> bool:
        """True if daily loss cap hit."""
        return self._shutdown

    def check_trade(self, opp: TradeOpportunity) -> tuple[bool, str]:
        """Gate a trade on risk limits.

        Returns (allowed, reason).
        """
        if self._shutdown:
            return False, "Daily loss cap hit — trading paused until midnight ET"

        if opp.edge < self.cfg.min_edge:
            return False, f"Edge {opp.edge:.3f} below minimum {self.cfg.min_edge:.3f}"

        if self.tracker.count >= self.cfg.max_concurrent:
            return False, f"Max concurrent positions reached ({self.cfg.max_concurrent})"

        new_exposure = self.tracker.total_exposure + opp.position_size_usd
        if new_exposure > self.cfg.bankroll_usd:
            return False, f"Would exceed bankroll: ${new_exposure:.2f} > ${self.cfg.bankroll_usd:.2f}"

        if self.tracker.has_position_for_market(opp.market.condition_id):
            return False, f"Already have position in market {opp.market.condition_id[:12]}"

        if opp.position_size_usd > self.cfg.max_bet_usd:
            return False, f"Position size ${opp.position_size_usd:.2f} exceeds max bet ${self.cfg.max_bet_usd:.2f}"

        log.info(
            "Hawk risk check passed: edge=%.3f, positions=%d/%d, exposure=$%.2f/$%.2f",
            opp.edge, self.tracker.count, self.cfg.max_concurrent,
            self.tracker.total_exposure, self.cfg.bankroll_usd,
        )
        return True, "ok"
