"""Arbiter Executor — place multi-leg arb orders via py_clob_client."""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType, ApiCreds
from py_clob_client.order_builder.constants import BUY, SELL

from arbiter.config import ArbiterConfig
from arbiter.analyzer import ArbOpportunity, ArbLeg

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
ORDERS_FILE = DATA_DIR / "arbiter_orders.jsonl"

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


class ArbiterExecutor:
    """Multi-leg order placement and management."""

    def __init__(self, cfg: ArbiterConfig):
        self.cfg = cfg
        self.client: ClobClient | None = None
        self._init_client()

    def _init_client(self) -> None:
        """Initialize CLOB client with Arbiter-specific credentials."""
        if self.cfg.dry_run:
            log.info("[DRY RUN] Arbiter running in dry-run mode — no CLOB client needed")
            return

        if not self.cfg.private_key:
            log.warning("ARBITER_PRIVATE_KEY not set — running in observation-only mode")
            return

        try:
            self.client = ClobClient(
                self.cfg.clob_host,
                key=self.cfg.private_key,
                chain_id=137,
                funder=self.cfg.funder_address or None,
                signature_type=2,
            )
            if self.cfg.clob_api_key:
                self.client.set_api_creds(ApiCreds(
                    api_key=self.cfg.clob_api_key,
                    api_secret=self.cfg.clob_api_secret,
                    api_passphrase=self.cfg.clob_api_passphrase,
                ))
            resp = self.client.get_ok()
            log.info("Arbiter CLOB connection OK: %s", resp)
        except Exception:
            log.warning("Could not initialize Arbiter CLOB client")
            self.client = None

    def execute_arb(self, opp: ArbOpportunity) -> dict:
        """Execute a multi-leg arb trade.

        Places ALL legs as GTC limit orders. If any leg fails to fill
        within timeout, cancels unfilled and sells filled at market.

        Returns dict with execution result.
        """
        result = {
            "event_slug": opp.event_slug,
            "arb_type": opp.arb_type,
            "legs": len(opp.legs),
            "status": "pending",
            "order_ids": [],
            "filled": [],
            "unfilled": [],
            "timestamp": time.time(),
        }

        # Size each leg: distribute evenly, respect caps
        total_legs = len(opp.legs)
        if total_legs == 0:
            result["status"] = "no_legs"
            return result

        per_leg_usd = min(
            self.cfg.max_per_arb_usd / total_legs,
            self.cfg.max_bet_per_leg_usd,
        )

        # Calculate shares: $USD / price_per_share
        # For BUY: shares = usd / yes_price
        # For SELL: shares = usd / (1 - yes_price)  (selling YES tokens you hold)
        sized_legs = []
        for leg in opp.legs:
            if leg.price <= 0.01 or leg.price >= 0.99:
                log.warning("Skipping leg with extreme price $%.2f", leg.price)
                continue
            shares = per_leg_usd / leg.price
            leg.size_usd = per_leg_usd
            sized_legs.append((leg, shares))

        if not sized_legs:
            result["status"] = "all_legs_skipped"
            return result

        total_cost = sum(leg.price * shares for leg, shares in sized_legs)
        if total_cost > self.cfg.bankroll_usd:
            log.warning("Arb total cost $%.2f exceeds bankroll $%.0f — skipping",
                        total_cost, self.cfg.bankroll_usd)
            result["status"] = "exceeds_bankroll"
            return result

        log.info("Executing %s arb: %d legs, $%.2f total | %s",
                 opp.arb_type, len(sized_legs), total_cost, opp.event_title[:60])

        if self.cfg.dry_run:
            order_ids = []
            for leg, shares in sized_legs:
                oid = f"arb-dry-{leg.condition_id[:8]}-{int(time.time())}"
                order_ids.append(oid)
                log.info("[DRY RUN] %s %s | %.1f shares @ $%.2f ($%.2f) | %s",
                         leg.side, leg.condition_id[:12], shares, leg.price,
                         leg.size_usd, oid)
            result["order_ids"] = order_ids
            result["filled"] = order_ids
            result["status"] = "dry_run_success"
            self._log_orders(opp, result)
            _notify_arb(opp, result, self.cfg)
            return result

        if not self.client:
            result["status"] = "no_client"
            return result

        # Place all legs
        order_ids = []
        for leg, shares in sized_legs:
            try:
                side = BUY if leg.side == "BUY" else SELL
                order_args = OrderArgs(
                    price=round(leg.price, 2),
                    size=round(shares, 2),
                    side=side,
                    token_id=leg.token_id,
                )
                signed_order = self.client.create_order(order_args)
                resp = self.client.post_order(signed_order, OrderType.GTC)
                oid = resp.get("orderID") or resp.get("id", "unknown")
                order_ids.append(oid)
                log.info("Leg placed: %s %s | %.1f shares @ $%.2f | %s",
                         leg.side, leg.condition_id[:12], shares, leg.price, oid)
            except Exception:
                log.exception("Failed to place leg for %s", leg.condition_id[:12])
                order_ids.append(None)

        result["order_ids"] = [o for o in order_ids if o]

        # Monitor fills for timeout period
        filled, unfilled = self._monitor_fills(
            order_ids, self.cfg.fill_timeout_seconds
        )
        result["filled"] = filled
        result["unfilled"] = unfilled

        if unfilled:
            # Partial fill — cancel unfilled, sell filled at market
            log.warning("Partial fill: %d/%d filled — unwinding", len(filled), len(order_ids))
            self._unwind_partial(unfilled, filled, sized_legs)
            result["status"] = "partial_unwind"
        else:
            result["status"] = "success"

        self._log_orders(opp, result)
        _notify_arb(opp, result, self.cfg)
        return result

    def _monitor_fills(self, order_ids: list, timeout_secs: int) -> tuple[list, list]:
        """Poll order status until all filled or timeout."""
        if not self.client:
            return [], order_ids

        deadline = time.time() + timeout_secs
        filled = []
        pending = [oid for oid in order_ids if oid]

        while pending and time.time() < deadline:
            time.sleep(5)
            still_pending = []
            for oid in pending:
                try:
                    order = self.client.get_order(oid)
                    status = order.get("status", "").lower()
                    if status == "matched":
                        filled.append(oid)
                    elif status in ("canceled", "expired"):
                        pass  # Already dead
                    else:
                        still_pending.append(oid)
                except Exception:
                    still_pending.append(oid)
            pending = still_pending

        return filled, pending

    def _unwind_partial(self, unfilled: list, filled: list,
                        sized_legs: list[tuple[ArbLeg, float]]) -> None:
        """Cancel unfilled orders and sell filled positions to scratch."""
        if not self.client:
            return

        # Cancel unfilled
        for oid in unfilled:
            try:
                self.client.cancel(oid)
                log.info("Cancelled unfilled order: %s", oid)
            except Exception:
                log.warning("Failed to cancel %s", oid)

        # Sell filled positions at market to exit
        for oid in filled:
            try:
                order = self.client.get_order(oid)
                token_id = order.get("asset_id", "")
                size_matched = float(order.get("size_matched", 0))
                if token_id and size_matched > 0:
                    sell_args = OrderArgs(
                        price=0.01,  # Market sell
                        size=round(size_matched, 2),
                        side=SELL,
                        token_id=token_id,
                    )
                    signed = self.client.create_order(sell_args)
                    self.client.post_order(signed, OrderType.GTC)
                    log.info("Unwound position: sold %.1f shares of %s", size_matched, oid)
            except Exception:
                log.warning("Failed to unwind filled order %s", oid)

    def check_order_status(self, order_id: str) -> str:
        """Check status of a single order. Returns status string."""
        if self.cfg.dry_run:
            return "matched"
        if not self.client:
            return "unknown"
        try:
            order = self.client.get_order(order_id)
            return order.get("status", "unknown").lower()
        except Exception:
            return "unknown"

    def _log_orders(self, opp: ArbOpportunity, result: dict) -> None:
        """Append order details to JSONL log."""
        try:
            DATA_DIR.mkdir(exist_ok=True)
            record = {
                "event_slug": opp.event_slug,
                "event_title": opp.event_title[:200],
                "arb_type": opp.arb_type,
                "legs": len(opp.legs),
                "total_cost": opp.total_cost,
                "expected_profit_pct": opp.expected_profit_pct,
                "deviation_pct": opp.deviation_pct,
                "status": result["status"],
                "order_ids": result["order_ids"],
                "filled_count": len(result["filled"]),
                "unfilled_count": len(result["unfilled"]),
                "timestamp": time.time(),
            }
            with open(ORDERS_FILE, "a") as f:
                f.write(json.dumps(record) + "\n")
        except Exception:
            log.debug("Failed to log arbiter orders")


def _notify_arb(opp: ArbOpportunity, result: dict, cfg: ArbiterConfig) -> None:
    """Send Telegram notification for arb execution."""
    status = result["status"]
    mode = "DRY RUN" if cfg.dry_run else "LIVE"
    icon = "\u2705" if "success" in status else "\u26a0\ufe0f"

    _notify_tg(
        f"{icon} <b>ARBITER [{mode}]</b>\n"
        f"\n"
        f"\U0001f4cb {opp.event_title[:100]}\n"
        f"\n"
        f"\U0001f4ca Type: <b>{opp.arb_type.upper()}</b>\n"
        f"\U0001f4b0 Legs: {len(opp.legs)} | Dev: {opp.deviation_pct:.1f}%\n"
        f"\U0001f4c8 Expected profit: <b>{opp.expected_profit_pct:.1f}%</b>\n"
        f"\U0001f4dd Status: {status}"
    )
