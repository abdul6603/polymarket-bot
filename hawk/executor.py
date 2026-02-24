"""Trade Executor V8 — limit orders + fill monitoring + stale cancellation."""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY, SELL

from hawk.config import HawkConfig
from hawk.edge import TradeOpportunity
from hawk.tracker import HawkTracker

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
FILL_METRICS_FILE = DATA_DIR / "hawk_fill_metrics.jsonl"

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
        """Place a GTC limit buy via py_clob_client, dry-run mode support.

        V8: Uses limit discount (rests in book) instead of taker premium.
        Stores order_placed_at and market_price_at_entry for fill tracking.
        """
        raw_price = _get_raw_price(opp)
        price = _get_entry_price(opp, self.cfg)
        size = opp.position_size_usd / price if price > 0 else 0
        if size <= 0:
            return None

        mode_tag = "LIMIT" if not self.cfg.aggressive_fallback else "TAKER"
        log.info(
            "Order [%s]: %s %s | size=%.2f tokens @ $%.2f (raw=$%.2f) | edge=%.1f%% | market=%s",
            mode_tag, opp.direction.upper(), opp.token_id[:16],
            size, price, raw_price, opp.edge * 100, opp.market.condition_id[:12],
        )

        if self.cfg.dry_run:
            order_id = f"hawk-dry-{opp.market.condition_id[:8]}-{int(time.time())}"
            log.info("[DRY RUN] Simulated order: %s", order_id)
            self.tracker.record_trade(opp, order_id,
                                      order_placed_at=time.time(),
                                      market_price_at_entry=raw_price)
            _bus_trade_placed(opp, order_id)
            _notify_trade_placed(opp, self.cfg)
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
            self.tracker.record_trade(opp, order_id,
                                      order_placed_at=time.time(),
                                      market_price_at_entry=raw_price)
            _bus_trade_placed(opp, order_id)
            _notify_trade_placed(opp, self.cfg)
            return order_id
        except Exception:
            log.exception("Failed to place order")
            return None

    def sell_position(self, pos: dict, reason: str = "") -> str | None:
        """Sell tokens to exit a position. Returns sell order ID or None.

        V9: Live in-play exit. Creates a SELL order at current market bid
        to close the position quickly. Uses aggressive pricing for fast fill.
        """
        token_id = pos.get("token_id", "")
        entry_price = pos.get("entry_price", 0.5)
        size_usd = pos.get("size_usd", 0)
        if not token_id or size_usd <= 0:
            log.warning("[LIVE] Cannot sell — missing token_id or size")
            return None

        # Calculate shares held (size_usd / entry_price)
        shares = size_usd / entry_price if entry_price > 0 else 0
        if shares <= 0:
            return None

        log.info("[LIVE] Selling %s | %.1f shares | reason: %s | %s",
                 pos.get("condition_id", "")[:12], shares, reason,
                 pos.get("question", "")[:60])

        if self.cfg.dry_run:
            sell_id = f"hawk-sell-dry-{pos.get('condition_id', '')[:8]}-{int(time.time())}"
            log.info("[LIVE DRY] Simulated sell: %s", sell_id)
            return sell_id

        if not self.client:
            log.error("[LIVE] No CLOB client for sell")
            return None

        try:
            # Get current market price for aggressive sell
            mid = self.client.get_midpoint(token_id)
            mid_price = float(mid) if mid else entry_price
            # Sell at mid - 0.02 for fast fill (willing to take slightly less)
            sell_price = max(0.01, round(mid_price - 0.02, 2))

            order_args = OrderArgs(
                price=sell_price,
                size=round(shares, 2),
                side=SELL,
                token_id=token_id,
            )
            signed_order = self.client.create_order(order_args)
            resp = self.client.post_order(signed_order, OrderType.GTC)
            sell_id = resp.get("orderID") or resp.get("id", "unknown")
            log.info("[LIVE] Sell order placed: %s @ $%.2f (%d shares) | reason: %s",
                     sell_id, sell_price, shares, reason)

            # Notify TG
            pnl_est = (sell_price - entry_price) * shares
            _notify_tg(
                f"\U0001f6a8 <b>Hawk LIVE EXIT</b>\n"
                f"{pos.get('question', '')[:100]}\n"
                f"<b>SOLD</b> {shares:.1f} shares @ ${sell_price:.2f} "
                f"(entry ${entry_price:.2f})\n"
                f"Est P&L: ${pnl_est:+.2f} | Reason: {reason}"
            )

            # Publish to event bus
            try:
                from shared.events import publish as bus_publish
                bus_publish(
                    agent="hawk",
                    event_type="live_exit",
                    data={
                        "order_id": pos.get("order_id", ""),
                        "sell_order_id": sell_id,
                        "condition_id": pos.get("condition_id", ""),
                        "question": pos.get("question", "")[:200],
                        "sell_price": sell_price,
                        "entry_price": entry_price,
                        "shares": round(shares, 2),
                        "pnl_estimate": round(pnl_est, 2),
                        "reason": reason,
                    },
                    summary=f"Hawk LIVE EXIT: {pos.get('question', '')[:80]} | ${pnl_est:+.2f} | {reason}",
                )
            except Exception:
                pass

            return sell_id
        except Exception:
            log.exception("[LIVE] Failed to sell position %s", pos.get("order_id", ""))
            return None

    def add_to_position(self, pos: dict, extra_usd: float, reason: str = "") -> str | None:
        """Buy more tokens to scale up an existing position.

        V9: Live in-play scale-up. Buys additional tokens at current market price.
        """
        token_id = pos.get("token_id", "")
        if not token_id or extra_usd <= 0:
            return None

        log.info("[LIVE] Adding $%.2f to %s | reason: %s | %s",
                 extra_usd, pos.get("condition_id", "")[:12], reason,
                 pos.get("question", "")[:60])

        if self.cfg.dry_run:
            add_id = f"hawk-add-dry-{pos.get('condition_id', '')[:8]}-{int(time.time())}"
            log.info("[LIVE DRY] Simulated add: %s ($%.2f)", add_id, extra_usd)
            return add_id

        if not self.client:
            log.error("[LIVE] No CLOB client for add")
            return None

        try:
            mid = self.client.get_midpoint(token_id)
            buy_price = float(mid) if mid else pos.get("entry_price", 0.5)
            size = extra_usd / buy_price if buy_price > 0 else 0
            if size <= 0:
                return None

            order_args = OrderArgs(
                price=round(buy_price, 2),
                size=round(size, 2),
                side=BUY,
                token_id=token_id,
            )
            signed_order = self.client.create_order(order_args)
            resp = self.client.post_order(signed_order, OrderType.GTC)
            add_id = resp.get("orderID") or resp.get("id", "unknown")
            log.info("[LIVE] Add order placed: %s | $%.2f @ $%.2f | reason: %s",
                     add_id, extra_usd, buy_price, reason)

            _notify_tg(
                f"\U0001f4c8 <b>Hawk LIVE SCALE-UP</b>\n"
                f"{pos.get('question', '')[:100]}\n"
                f"<b>ADDED</b> ${extra_usd:.2f} @ ${buy_price:.2f}\n"
                f"Reason: {reason}"
            )

            try:
                from shared.events import publish as bus_publish
                bus_publish(
                    agent="hawk",
                    event_type="live_scale_up",
                    data={
                        "order_id": pos.get("order_id", ""),
                        "add_order_id": add_id,
                        "condition_id": pos.get("condition_id", ""),
                        "extra_usd": extra_usd,
                        "buy_price": buy_price,
                        "reason": reason,
                    },
                    summary=f"Hawk SCALE-UP: +${extra_usd:.2f} on {pos.get('question', '')[:80]}",
                )
            except Exception:
                pass

            return add_id
        except Exception:
            log.exception("[LIVE] Failed to add to position %s", pos.get("order_id", ""))
            return None

    def check_fills(self) -> None:
        """Poll order status — cancel stale unfilled limit orders, remove dead orders.

        V8: If a limit order has been "live" (unfilled) longer than fill_timeout_minutes,
        cancel it and record a timeout metric. Matched/filled orders stay until resolution.
        """
        if self.cfg.dry_run:
            return

        if not self.client:
            return

        now = time.time()
        timeout_secs = self.cfg.fill_timeout_minutes * 60

        for pos in list(self.tracker.open_positions):
            try:
                order = self.client.get_order(pos["order_id"])
                status = order.get("status", "").lower()
                log.info("Order %s status: %s", pos["order_id"], status)

                if status in ("canceled", "expired"):
                    log.info("Removing dead order %s (status=%s)", pos["order_id"], status)
                    _record_fill_metric(pos, "dead_" + status)
                    self.tracker.remove_position(pos["order_id"])
                elif status == "live":
                    # V8: Stale limit order cancellation
                    placed_at = pos.get("order_placed_at", 0)
                    if placed_at and (now - placed_at) >= timeout_secs:
                        age_min = (now - placed_at) / 60
                        log.info("[FILL] Cancelling stale order %s (age=%.0fm > %dm) | %s",
                                 pos["order_id"], age_min, self.cfg.fill_timeout_minutes,
                                 pos.get("question", "")[:60])
                        try:
                            self.client.cancel(pos["order_id"])
                            _record_fill_metric(pos, "timeout_cancel")
                            # Add cooldown BEFORE removing — prevents re-placing
                            cid = pos.get("condition_id") or pos.get("market_id", "")
                            if cid:
                                self.tracker.add_cooldown(cid)
                            self.tracker.remove_position(pos["order_id"])
                        except Exception:
                            log.warning("Cancel failed for %s — keeping in tracker to prevent ghost", pos["order_id"])
                elif status == "matched":
                    _record_fill_metric(pos, "filled")
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


class KalshiExecutor:
    """Order placement on Kalshi via authenticated API."""

    def __init__(self, cfg: HawkConfig, kalshi_client, tracker: HawkTracker):
        self.cfg = cfg
        self.client = kalshi_client
        self.tracker = tracker

    def place_order(self, opp: TradeOpportunity) -> str | None:
        """Place order on Kalshi.

        Converts HawkMarket price (0.00-1.00) back to cents (0-100) for Kalshi API.
        Uses ticker from condition_id (strip 'kalshi_' prefix).
        """
        # Extract Kalshi ticker from condition_id
        cid = opp.market.condition_id
        if not cid.startswith("kalshi_"):
            log.error("[KALSHI] Not a Kalshi market: %s", cid)
            return None

        ticker = cid.replace("kalshi_", "", 1)
        # Remove _yes/_no suffix if present in token_id
        if ticker.endswith("_yes") or ticker.endswith("_no"):
            ticker = ticker.rsplit("_", 1)[0]

        side = opp.direction.lower()  # "yes" or "no"

        # Price conversion: 0.00-1.00 → cents (1-99)
        if side == "yes":
            price_decimal = float(opp.market.tokens[0].get("price", 0.5))
        else:
            price_decimal = float(opp.market.tokens[1].get("price", 0.5))
        price_cents = max(1, min(99, int(round(price_decimal * 100))))

        # Calculate contract count: position_size_usd / (price_cents / 100)
        price_dollars = price_cents / 100.0
        count = max(1, int(opp.position_size_usd / price_dollars))

        log.info("[KALSHI] Order: %s %s x%d @ %d¢ ($%.2f) | edge=%.1f%% | %s",
                 side.upper(), ticker, count, price_cents, opp.position_size_usd,
                 opp.edge * 100, opp.market.question[:60])

        if self.cfg.dry_run:
            order_id = f"kalshi-dry-{ticker[:12]}-{int(time.time())}"
            log.info("[KALSHI DRY RUN] Simulated order: %s", order_id)
            self.tracker.record_trade(opp, order_id,
                                      order_placed_at=time.time(),
                                      market_price_at_entry=price_decimal)
            _bus_trade_placed(opp, order_id)
            _notify_trade_placed(opp, self.cfg)
            return order_id

        if not self.client:
            log.error("[KALSHI] No authenticated client available")
            return None

        try:
            order_data = self.client.place_order(
                ticker=ticker,
                side=side,
                action="buy",
                count=count,
                type="limit",
                yes_price=price_cents if side == "yes" else None,
                no_price=price_cents if side == "no" else None,
            )
            order_id = order_data.get("order_id", "unknown")
            log.info("[KALSHI] Order placed: %s", order_id)
            self.tracker.record_trade(opp, order_id,
                                      order_placed_at=time.time(),
                                      market_price_at_entry=price_decimal)
            _bus_trade_placed(opp, order_id)
            _notify_trade_placed(opp, self.cfg)
            return order_id
        except Exception:
            log.exception("[KALSHI] Failed to place order on %s", ticker)
            return None

    def check_fills(self) -> None:
        """Poll Kalshi order status, cancel stale orders."""
        if self.cfg.dry_run or not self.client:
            return

        now = time.time()
        timeout_secs = self.cfg.fill_timeout_minutes * 60

        for pos in list(self.tracker.open_positions):
            oid = pos.get("order_id", "")
            if not oid.startswith("kalshi-"):
                continue
            # Strip dry run prefix for real order lookup
            real_oid = oid
            try:
                order = self.client.get_order(real_oid)
                status = order.get("status", "").lower()

                if status in ("canceled", "expired"):
                    log.info("[KALSHI] Removing dead order %s (status=%s)", oid, status)
                    self.tracker.remove_position(oid)
                elif status in ("pending", "resting"):
                    placed_at = pos.get("order_placed_at", 0)
                    if placed_at and (now - placed_at) >= timeout_secs:
                        log.info("[KALSHI] Cancelling stale order %s (age > %dm)",
                                 oid, self.cfg.fill_timeout_minutes)
                        self.client.cancel_order(real_oid)
                        cid = pos.get("condition_id", "")
                        if cid:
                            self.tracker.add_cooldown(cid)
                        self.tracker.remove_position(oid)
            except Exception:
                log.debug("[KALSHI] Could not check order %s", oid)


_YES_OUTCOMES = {"yes", "up", "over"}
_NO_OUTCOMES = {"no", "down", "under"}


def _get_raw_price(opp: TradeOpportunity) -> float:
    """Get the raw market mid-price for the trade direction (no premium/discount)."""
    target = _YES_OUTCOMES if opp.direction == "yes" else _NO_OUTCOMES
    for t in opp.market.tokens:
        tok_outcome = (t.get("outcome") or "").lower()
        if tok_outcome in target:
            try:
                return float(t.get("price", 0.5))
            except (ValueError, TypeError):
                pass
    tokens = opp.market.tokens
    if len(tokens) == 2:
        idx = 0 if opp.direction == "yes" else 1
        try:
            return float(tokens[idx].get("price", 0.5))
        except (ValueError, TypeError):
            pass
    return 0.5


def _get_entry_price(opp: TradeOpportunity, cfg: HawkConfig | None = None) -> float:
    """Get the entry price for the trade direction.

    V8: Two modes controlled by cfg.aggressive_fallback:
      - Limit mode (default): raw_price - discount → rests in book for better fill
      - Aggressive mode: raw_price + 0.02 taker premium → crosses spread immediately
    """
    raw_price = _get_raw_price(opp)

    if cfg and not cfg.aggressive_fallback and cfg.limit_discount > 0:
        # V8 Limit mode: subtract discount to rest in book
        price = raw_price - cfg.limit_discount
    else:
        # Legacy aggressive mode: add taker premium to cross spread
        price = raw_price + 0.02

    return max(0.01, min(0.99, round(price, 2)))


def _record_fill_metric(pos: dict, outcome: str) -> None:
    """Record fill outcome to hawk_fill_metrics.jsonl for monitoring."""
    try:
        DATA_DIR.mkdir(exist_ok=True)
        metric = {
            "order_id": pos.get("order_id", ""),
            "condition_id": pos.get("condition_id", ""),
            "question": pos.get("question", "")[:100],
            "outcome": outcome,
            "entry_price": pos.get("entry_price", 0),
            "market_price_at_entry": pos.get("market_price_at_entry", 0),
            "order_placed_at": pos.get("order_placed_at", 0),
            "recorded_at": time.time(),
            "age_minutes": round((time.time() - pos.get("order_placed_at", time.time())) / 60, 1),
        }
        with open(FILL_METRICS_FILE, "a") as f:
            f.write(json.dumps(metric) + "\n")
    except Exception:
        log.debug("[FILL] Failed to record fill metric")


def _notify_trade_placed(opp: TradeOpportunity, cfg: HawkConfig | None = None) -> None:
    """Send Telegram notification when Hawk places a trade."""
    price = _get_entry_price(opp, cfg)
    mode = "LIMIT" if (cfg and not cfg.aggressive_fallback) else "TAKER"
    _notify_tg(
        f"\U0001f985 <b>Hawk Trade Placed [{mode}]</b>\n"
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
