"""Trade Executor — CLOB order placement for completeness arbitrage."""
from __future__ import annotations

import logging
import time

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY, SELL

from razor.config import RazorConfig
from bot.http_session import get_session

log = logging.getLogger(__name__)


class RazorExecutor:
    """Places buy-both-sides orders and handles exits via CLOB API."""

    def __init__(self, cfg: RazorConfig, client: ClobClient | None):
        self.cfg = cfg
        self.client = client

    def buy_both_sides(
        self,
        token_a_id: str,
        token_b_id: str,
        ask_a: float,
        ask_b: float,
        shares: float,
    ) -> tuple[str, str] | None:
        """Buy both sides of a binary market. Returns (order_a, order_b) or None.

        Uses FOK (Fill-Or-Kill) to avoid partial fills.
        If Leg B fails after Leg A succeeds, immediately unwinds Leg A.
        """
        if self.cfg.dry_run:
            ts = int(time.time())
            order_a = f"razor-dry-a-{ts}"
            order_b = f"razor-dry-b-{ts}"
            log.info("[DRY RUN] Buy both: A=%s @ $%.4f, B=%s @ $%.4f, shares=%.2f",
                     token_a_id[:12], ask_a, token_b_id[:12], ask_b, shares)
            return (order_a, order_b)

        if not self.client:
            log.error("No CLOB client for live execution")
            return None

        try:
            # Leg A — FOK buy
            args_a = OrderArgs(
                price=round(ask_a, 2),
                size=shares,
                side=BUY,
                token_id=token_a_id,
            )
            signed_a = self.client.create_order(args_a)
            resp_a = self.client.post_order(signed_a, OrderType.FOK)
            order_a = resp_a.get("orderID") or resp_a.get("id", "")
            if not order_a:
                log.warning("Leg A failed for %s — aborting", token_a_id[:12])
                return None
            log.info("Leg A filled: %s @ $%.4f", order_a, ask_a)

            # Leg B — FOK buy
            args_b = OrderArgs(
                price=round(ask_b, 2),
                size=shares,
                side=BUY,
                token_id=token_b_id,
            )
            signed_b = self.client.create_order(args_b)
            resp_b = self.client.post_order(signed_b, OrderType.FOK)
            order_b = resp_b.get("orderID") or resp_b.get("id", "")
            if not order_b:
                log.warning("Leg B failed — unwinding Leg A %s", order_a)
                self._unwind(token_a_id, shares)
                return None
            log.info("Leg B filled: %s @ $%.4f", order_b, ask_b)
            return (order_a, order_b)

        except Exception:
            log.exception("Failed to execute buy-both-sides")
            return None

    def sell_side(self, token_id: str, shares: float) -> str | None:
        """Sell one side at best bid (for early exit / profit lock)."""
        if self.cfg.dry_run:
            order_id = f"razor-dry-sell-{int(time.time())}"
            bid = self._fetch_best_bid(token_id)
            log.info("[DRY RUN] Sell %s: %.2f shares @ bid $%.4f",
                     token_id[:12], shares, bid)
            return order_id

        if not self.client:
            log.error("No CLOB client for sell")
            return None

        bid = self._fetch_best_bid(token_id)
        if bid <= 0:
            log.warning("No bids for %s — cannot sell", token_id[:12])
            return None

        try:
            args = OrderArgs(
                price=round(bid, 2),
                size=shares,
                side=SELL,
                token_id=token_id,
            )
            signed = self.client.create_order(args)
            resp = self.client.post_order(signed, OrderType.GTC)
            order_id = resp.get("orderID") or resp.get("id", "")
            log.info("Sell placed: %s @ $%.4f for %.2f shares", order_id, bid, shares)
            return order_id
        except Exception:
            log.exception("Failed to sell %s", token_id[:12])
            return None

    def sell_both_sides(self, token_a_id: str, token_b_id: str, shares: float) -> bool:
        """Emergency full exit — sell both sides."""
        a = self.sell_side(token_a_id, shares)
        b = self.sell_side(token_b_id, shares)
        return a is not None or b is not None

    def fetch_best_ask(self, token_id: str) -> tuple[float, float]:
        """Fetch best ask price and total depth from CLOB orderbook.

        Returns (best_ask, total_depth_shares).
        """
        try:
            resp = get_session().get(
                f"{self.cfg.clob_host}/book?token_id={token_id}",
                timeout=5,
            )
            if resp.status_code != 200:
                return (0.0, 0.0)
            book = resp.json()
            asks = book.get("asks", [])
            if not asks:
                return (0.0, 0.0)
            best_ask = float("inf")
            total_depth = 0.0
            for ask in asks:
                price = float(ask.get("price", 0))
                size = float(ask.get("size", 0))
                if price < best_ask:
                    best_ask = price
                total_depth += size
            if best_ask == float("inf"):
                return (0.0, 0.0)
            return (best_ask, total_depth)
        except Exception:
            log.debug("Failed to fetch asks for %s", token_id[:12])
            return (0.0, 0.0)

    def _fetch_best_bid(self, token_id: str) -> float:
        """Fetch best bid price from CLOB orderbook."""
        try:
            resp = get_session().get(
                f"{self.cfg.clob_host}/book?token_id={token_id}",
                timeout=5,
            )
            if resp.status_code != 200:
                return 0.0
            book = resp.json()
            bids = book.get("bids", [])
            if not bids:
                return 0.0
            return max(float(b.get("price", 0)) for b in bids)
        except Exception:
            log.debug("Failed to fetch bids for %s", token_id[:12])
            return 0.0

    def _unwind(self, token_id: str, shares: float) -> bool:
        """Emergency unwind — sell a leg that was orphaned."""
        if not self.client:
            return False
        bid = self._fetch_best_bid(token_id)
        if bid <= 0:
            bid = 0.01  # Fire sale
        try:
            args = OrderArgs(
                price=round(bid, 2),
                size=shares,
                side=SELL,
                token_id=token_id,
            )
            signed = self.client.create_order(args)
            self.client.post_order(signed, OrderType.GTC)
            log.info("Unwound orphan: %s @ $%.2f for %.2f shares",
                     token_id[:12], bid, shares)
            return True
        except Exception:
            log.exception("Failed to unwind %s", token_id[:12])
            return False
