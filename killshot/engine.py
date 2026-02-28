"""Killshot engine — late-window direction snipe using Binance spot price.

Core logic:
1. At T-60s to T-10s before each 5m window close, check Binance spot price
2. If spot delta since window open exceeds threshold → direction is "locked"
3. Buy the winning side token on the CLOB (or simulate in paper mode)
4. Track outcome at resolution for P&L
"""
from __future__ import annotations

import json
import logging
import time

from killshot.config import KillshotConfig
from killshot.tracker import PaperTrade, PaperTracker

from bot.price_cache import PriceCache
from bot.snipe.window_tracker import Window
from bot.snipe import clob_book

log = logging.getLogger("killshot.engine")


class KillshotEngine:
    """Evaluates 5m windows in the kill zone and trades (live or paper)."""

    def __init__(self, cfg: KillshotConfig, price_cache: PriceCache,
                 tracker: PaperTracker, clob_client=None):
        self._cfg = cfg
        self._cache = price_cache
        self._tracker = tracker
        self._client = clob_client
        self._dry_run = cfg.dry_run
        # market_id -> timestamp when traded (for cleanup)
        self._traded_windows: dict[str, float] = {}
        self._daily_loss: float = 0.0
        self._daily_reset_date: str = ""
        self._kill_zone_logged: set[str] = set()

    def tick(self, windows: list[Window]) -> None:
        """Called every ~1s — check all active windows for kill zone entry."""
        now = time.time()
        today = time.strftime("%Y-%m-%d")

        # Daily reset
        if today != self._daily_reset_date:
            self._daily_loss = 0.0
            self._daily_reset_date = today
            self._kill_zone_logged.clear()
            log.info("[KILLSHOT] Daily reset — loss counter cleared")

        # Daily loss cap
        if self._daily_loss >= self._cfg.daily_loss_cap_usd:
            return

        for window in windows:
            if window.market_id in self._traded_windows:
                continue
            if window.asset not in self._cfg.assets:
                continue

            remaining = window.end_ts - now

            # Kill zone: between min_window_seconds and window_seconds before close
            if remaining > self._cfg.window_seconds or remaining < self._cfg.min_window_seconds:
                continue

            # Log kill zone entry (once per window)
            if window.market_id not in self._kill_zone_logged:
                self._kill_zone_logged.add(window.market_id)
                log.info(
                    "[KILLSHOT] Kill zone: %s %s | T-%.0fs | open=$%.2f",
                    window.asset.upper(), window.market_id[:12],
                    remaining, window.open_price,
                )

            self._evaluate_window(window, remaining)

    def _evaluate_window(self, window: Window, remaining: float) -> None:
        """Evaluate a single window — determine direction and trade."""
        # Get current spot price
        current_price = self._cache.get_price(window.asset)
        if current_price is None:
            return

        # Price freshness — stale data = bad decision
        age = self._cache.get_price_age(window.asset)
        if age > 5.0:
            log.warning(
                "[KILLSHOT] Stale price (%.1fs) for %s, skipping",
                age, window.asset,
            )
            return

        # Spot price delta since window opened
        delta = (current_price - window.open_price) / window.open_price

        # Direction must clear threshold
        if abs(delta) < self._cfg.direction_threshold:
            return

        direction = "up" if delta > 0 else "down"

        # Fetch CLOB book for the winning side
        winning_token = window.up_token_id if direction == "up" else window.down_token_id
        book = clob_book.get_orderbook(winning_token) if winning_token else None
        market_bid = book["best_bid"] if book and book["best_bid"] > 0 else None
        market_ask = book["best_ask"] if book and book["best_ask"] > 0 else None

        # Entry price logic
        delta_strength = min(abs(delta) / (self._cfg.direction_threshold * 5), 1.0)
        paper_entry = self._cfg.entry_price_min + delta_strength * (
            self._cfg.entry_price_max - self._cfg.entry_price_min
        )
        paper_entry = round(paper_entry, 2)

        # Position sizing
        size_usd = self._cfg.max_bet_usd

        # Log CLOB book
        if market_bid is not None and market_ask is not None:
            log.info(
                "[KILLSHOT] BOOK %s: bid=%.0f¢ ask=%.0f¢ spread=%.1f¢",
                direction.upper(), market_bid * 100, market_ask * 100,
                (market_ask - market_bid) * 100,
            )
        elif market_bid is not None:
            log.info(
                "[KILLSHOT] BOOK %s: bid=%.0f¢ ask=NONE",
                direction.upper(), market_bid * 100,
            )

        # Skip if winning side already at extreme price (no edge left)
        if not self._dry_run and market_ask is None and market_bid and market_bid > 0.90:
            log.info(
                "[KILLSHOT] Skip: %s already at %.0f¢ (no asks), no edge",
                direction.upper(), market_bid * 100,
            )
            self._traded_windows[window.market_id] = time.time()
            return

        # Mark window as traded
        self._traded_windows[window.market_id] = time.time()

        # ── LIVE MODE: place real order ──
        if not self._dry_run and self._client and winning_token:
            entry_price, actual_shares, order_id = self._place_live_order(
                winning_token, market_ask, size_usd,
            )
            if entry_price is None:
                log.warning("[KILLSHOT] Live order FAILED — no fill")
                return
        else:
            # Paper mode
            entry_price = paper_entry
            actual_shares = round(size_usd / entry_price, 2)
            order_id = ""

        mode = "LIVE" if not self._dry_run else "PAPER"

        trade = PaperTrade(
            timestamp=time.time(),
            asset=window.asset,
            market_id=window.market_id,
            question=window.question,
            direction=direction,
            entry_price=entry_price,
            size_usd=size_usd,
            shares=actual_shares,
            window_end_ts=window.end_ts,
            spot_delta_pct=round(delta, 6),
            open_price=window.open_price,
            market_bid=market_bid or 0.0,
            market_ask=market_ask or 0.0,
        )
        self._tracker.record_trade(trade)

        log.info(
            "[KILLSHOT] %s FIRE: %s %s | delta=%.3f%% | entry=%.0f¢ | "
            "$%.2f (%.1f shares) | T-%.0fs%s",
            mode, direction.upper(), window.asset.upper(), delta * 100,
            entry_price * 100, size_usd, actual_shares, remaining,
            f" | order={order_id}" if order_id else "",
        )

    def _place_live_order(
        self, token_id: str, market_ask: float | None, size_usd: float,
    ) -> tuple[float | None, float, str]:
        """Place a real GTC buy order. Returns (entry_price, shares, order_id) or (None, 0, '')."""
        try:
            from py_clob_client.clob_types import OrderArgs, OrderType
            from py_clob_client.order_builder.constants import BUY

            # Use market ask + 1¢ to cross the spread (taker)
            # If no ask data, use entry_price_max as ceiling
            if market_ask and market_ask > 0:
                price = round(min(market_ask + 0.01, 0.95), 2)
            else:
                price = self._cfg.entry_price_max

            shares = round(size_usd / price, 2)
            if shares < 5:
                # Polymarket minimum is 5 shares — bump up
                shares = 5.0
                size_usd = round(shares * price, 2)
                log.info("[KILLSHOT] Bumped to min 5 shares ($%.2f)", size_usd)

            order_args = OrderArgs(
                price=price,
                size=shares,
                side=BUY,
                token_id=token_id,
            )
            signed_order = self._client.create_order(order_args)
            resp = self._client.post_order(signed_order, OrderType.GTC)
            order_id = resp.get("orderID") or resp.get("id", "unknown")
            status = resp.get("status", "")

            log.info("[KILLSHOT] CLOB response: %s", json.dumps(resp)[:500])

            if status.lower() in ("matched", "filled", "live"):
                # Parse actual fill price if available
                matches = resp.get("matchedOrders", []) or resp.get("matched_orders", [])
                if matches:
                    total_cost = 0.0
                    total_shares = 0.0
                    for m in matches:
                        mp = float(m.get("price", price))
                        ms = float(m.get("matchSize") or m.get("size", 0))
                        total_cost += mp * ms
                        total_shares += ms
                    if total_shares > 0:
                        actual_price = round(total_cost / total_shares, 4)
                        return actual_price, round(total_shares, 2), order_id

                return price, shares, order_id
            else:
                log.warning("[KILLSHOT] Order not filled: status=%s", status)
                return None, 0, ""

        except Exception as e:
            log.error("[KILLSHOT] Live order error: %s", str(e)[:200])
            return None, 0, ""

    def report_resolved(self, trades: list[PaperTrade]) -> None:
        """Update daily loss counter from actually resolved trades."""
        for trade in trades:
            if trade.outcome == "loss":
                self._daily_loss += abs(trade.pnl)
                log.info(
                    "[KILLSHOT] Daily loss updated: +$%.2f → $%.2f / $%.2f cap",
                    abs(trade.pnl), self._daily_loss, self._cfg.daily_loss_cap_usd,
                )

    def cleanup_expired(self) -> None:
        """Remove old window IDs to prevent memory growth."""
        cutoff = time.time() - 3600
        before = len(self._traded_windows)
        self._traded_windows = {
            k: v for k, v in self._traded_windows.items() if v > cutoff
        }
        removed = before - len(self._traded_windows)
        if removed:
            log.debug("[KILLSHOT] Cleaned %d expired window IDs", removed)
