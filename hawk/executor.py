"""Trade Executor — place orders via CLOB API (reuses bot/execution.py pattern)."""
from __future__ import annotations

import logging
import os
import time

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY

from hawk.config import HawkConfig
from hawk.edge import TradeOpportunity
from hawk.tracker import HawkTracker

log = logging.getLogger(__name__)

_TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
_TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")


def _notify_tg(text: str) -> None:
    if not _TG_TOKEN or not _TG_CHAT:
        return
    try:
        import requests
        requests.post(
            f"https://api.telegram.org/bot{_TG_TOKEN}/sendMessage",
            json={"chat_id": _TG_CHAT, "text": text, "parse_mode": "HTML"},
            timeout=5,
        )
    except Exception:
        pass


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
            _bus_trade_placed(opp, order_id)
            _notify_trade_placed(opp)
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
            _bus_trade_placed(opp, order_id)
            _notify_trade_placed(opp)
            return order_id
        except Exception:
            log.exception("Failed to place order")
            return None

    def check_fills(self) -> None:
        """Poll order status — only remove unfilled/dead orders.

        On Polymarket CLOB, "matched" means filled and active (we own tokens).
        These stay open until the MARKET resolves (handled by resolver.py).
        Only remove canceled/expired orders that never filled.
        """
        if self.cfg.dry_run:
            return

        if not self.client:
            return

        for pos in list(self.tracker.open_positions):
            try:
                order = self.client.get_order(pos["order_id"])
                status = order.get("status", "").lower()
                log.info("Order %s status: %s", pos["order_id"], status)
                if status in ("canceled", "expired"):
                    log.info("Removing dead order %s (status=%s)", pos["order_id"], status)
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


_YES_OUTCOMES = {"yes", "up", "over"}
_NO_OUTCOMES = {"no", "down", "under"}


def _get_entry_price(opp: TradeOpportunity) -> float:
    """Get the entry price for the trade direction.

    Adds a 2-cent taker premium so the limit order crosses the spread
    and fills immediately instead of sitting unfilled in the order book.
    """
    TAKER_PREMIUM = 0.02  # 2 cents above mid-price to ensure fill

    raw_price = 0.5
    target = _YES_OUTCOMES if opp.direction == "yes" else _NO_OUTCOMES
    for t in opp.market.tokens:
        tok_outcome = (t.get("outcome") or "").lower()
        if tok_outcome in target:
            try:
                raw_price = float(t.get("price", 0.5))
                break
            except (ValueError, TypeError):
                pass
    else:
        # Fallback: first token for yes, second for no
        tokens = opp.market.tokens
        if len(tokens) == 2:
            idx = 0 if opp.direction == "yes" else 1
            try:
                raw_price = float(tokens[idx].get("price", 0.5))
            except (ValueError, TypeError):
                pass

    # Add taker premium to cross the spread
    aggressive_price = raw_price + TAKER_PREMIUM
    return max(0.01, min(0.99, round(aggressive_price, 2)))


def _notify_trade_placed(opp: TradeOpportunity) -> None:
    """Send Telegram notification when Hawk places a trade."""
    price = _get_entry_price(opp)
    _notify_tg(
        f"\U0001f985 <b>Hawk Trade Placed</b>\n"
        f"{opp.market.question[:100]}\n"
        f"<b>{opp.direction.upper()}</b> ${opp.position_size_usd:.2f} @ ${price:.2f} | "
        f"Edge: {opp.edge*100:.1f}% | Risk: {opp.risk_score}/10"
    )


def _bus_trade_placed(opp: TradeOpportunity, order_id: str) -> None:
    """Publish trade_placed event to the shared event bus (fire-and-forget)."""
    try:
        from shared.events import publish as bus_publish
        bus_publish(
            agent="hawk",
            event_type="trade_placed",
            data={
                "order_id": order_id,
                "market_question": opp.market.question[:200],
                "direction": opp.direction,
                "size_usd": round(opp.position_size_usd, 2),
                "edge": round(opp.edge, 4),
                "probability": round(opp.estimate.estimated_prob, 4),
                "category": opp.market.category,
                "condition_id": opp.market.condition_id,
            },
            summary=f"Hawk placed ${opp.position_size_usd:.2f} {opp.direction.upper()} on: {opp.market.question[:80]}",
        )
    except Exception:
        log.debug("Event bus publish failed for trade_placed (non-fatal)")
