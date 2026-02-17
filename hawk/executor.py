"""Trade Executor — place orders via CLOB API (reuses bot/execution.py pattern)."""
from __future__ import annotations

import logging
import time

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY

from hawk.config import HawkConfig
from hawk.edge import TradeOpportunity
from hawk.tracker import HawkTracker

log = logging.getLogger(__name__)


class HawkExecutor:
    """Order placement and management via py-clob-client."""

    def __init__(self, cfg: HawkConfig, client: ClobClient | None, tracker: HawkTracker):
        self.cfg = cfg
        self.client = client
        self.tracker = tracker

    def place_order(self, opp: TradeOpportunity) -> str | None:
        """Place a GTC limit buy via py_clob_client, dry-run mode support."""
        price = _get_entry_price(opp)
        size = opp.position_size_usd / price if price > 0 else 0
        if size <= 0:
            return None

        log.info(
            "Order: %s %s | size=%.2f tokens @ $%.2f | edge=%.1f%% | market=%s",
            opp.direction.upper(),
            opp.token_id[:16],
            size,
            price,
            opp.edge * 100,
            opp.market.condition_id[:12],
        )

        if self.cfg.dry_run:
            order_id = f"hawk-dry-{opp.market.condition_id[:8]}-{int(time.time())}"
            log.info("[DRY RUN] Simulated order: %s", order_id)
            self.tracker.record_trade(opp, order_id)
            return order_id

        if not self.client:
            log.error("No CLOB client available for live trading")
            return None

        try:
            order_args = OrderArgs(
                price=round(price, 2),
                size=size,
                side=BUY,
                token_id=opp.token_id,
            )
            signed_order = self.client.create_order(order_args)
            resp = self.client.post_order(signed_order, OrderType.GTC)
            order_id = resp.get("orderID") or resp.get("id", "unknown")
            log.info("Order placed: %s", order_id)
            self.tracker.record_trade(opp, order_id)
            return order_id
        except Exception:
            log.exception("Failed to place order")
            return None

    def check_fills(self) -> None:
        """Poll order status."""
        if self.cfg.dry_run:
            # Paper trades stay open until market resolves (handled by resolver.py)
            return

        if not self.client:
            return

        for pos in list(self.tracker.open_positions):
            try:
                order = self.client.get_order(pos["order_id"])
                status = order.get("status", "").lower()
                if status in ("matched", "filled", "canceled", "expired"):
                    log.info("Order %s status: %s", pos["order_id"], status)
                    self.tracker.remove_position(pos["order_id"])
            except Exception:
                log.debug("Could not check order %s", pos.get("order_id", "?"))

    def cancel_all(self) -> None:
        """Cleanup on shutdown."""
        if self.cfg.dry_run:
            log.info("[DRY RUN] Would cancel %d open orders", len(self.tracker.open_positions))
            for pos in list(self.tracker.open_positions):
                self.tracker.remove_position(pos.get("order_id", ""))
            return

        if not self.client:
            return

        for pos in list(self.tracker.open_positions):
            try:
                self.client.cancel(pos["order_id"])
                log.info("Cancelled order %s", pos["order_id"])
                self.tracker.remove_position(pos["order_id"])
            except Exception:
                log.exception("Failed to cancel order %s", pos.get("order_id", "?"))


def _get_entry_price(opp: TradeOpportunity) -> float:
    """Get the entry price for the trade direction."""
    for t in opp.market.tokens:
        tok_outcome = (t.get("outcome") or "").lower()
        if tok_outcome == opp.direction:
            try:
                return max(0.01, min(0.99, float(t.get("price", 0.5))))
            except (ValueError, TypeError):
                return 0.5
    return 0.5
