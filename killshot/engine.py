"""Killshot engine — late-window direction snipe using Chainlink oracle.

Core logic:
1. Throughout kill zone (T-120s to T-5s), monitor price + CLOB book
2. If delta exceeds threshold AND book is 25-95¢ → buy the winning side
3. If book out of range, retry every 5s (DON'T blacklist the window)
"""
from __future__ import annotations

import json
import logging
import time

from killshot.config import KillshotConfig
from killshot.chainlink_ws import ChainlinkWS
from killshot.tracker import PaperTrade, PaperTracker

from bot.price_cache import PriceCache
from bot.snipe.window_tracker import Window
from bot.snipe import clob_book

log = logging.getLogger("killshot.engine")


class KillshotEngine:
    """Evaluates 5m windows in the kill zone and trades (live or paper)."""

    def __init__(self, cfg: KillshotConfig, price_cache: PriceCache,
                 tracker: PaperTracker, clob_client=None,
                 chainlink_ws: ChainlinkWS | None = None,
                 clob_ws=None):
        self._cfg = cfg
        self._cache = price_cache
        self._tracker = tracker
        self._client = clob_client
        self._chainlink = chainlink_ws
        self._clob_ws = clob_ws
        self._dry_run = cfg.dry_run
        # market_id -> timestamp when ACTUALLY traded (permanent blacklist)
        self._traded_windows: dict[str, float] = {}
        # market_id -> timestamp of last skip (5s cooldown before retry)
        self._skip_cooldown: dict[str, float] = {}
        self._daily_loss: float = 0.0
        self._daily_reset_date: str = ""
        self._kill_zone_logged: set[str] = set()

    def tick(self, windows: list[Window]) -> None:
        """Called every tick (default 0.1s) — check all active windows for kill zone entry."""
        now = time.time()
        today = time.strftime("%Y-%m-%d")

        # Daily reset
        if today != self._daily_reset_date:
            self._daily_loss = 0.0
            self._daily_reset_date = today
            self._kill_zone_logged.clear()
            self._skip_cooldown.clear()
            log.info("[KILLSHOT] Daily reset — loss counter cleared")

        # Daily loss cap
        if self._daily_loss >= self._cfg.daily_loss_cap_usd:
            return

        for window in windows:
            # Already traded this window — permanent skip
            if window.market_id in self._traded_windows:
                continue
            if window.asset not in self._cfg.assets:
                continue

            remaining = window.end_ts - now

            # Kill zone: between min_window_seconds and window_seconds before close
            if remaining > self._cfg.window_seconds or remaining < self._cfg.min_window_seconds:
                continue

            # Skip cooldown: retry every 1s (was 5s with REST, reduced since WS is instant)
            last_skip = self._skip_cooldown.get(window.market_id, 0)
            if now - last_skip < 1.0:
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

    def _get_best_price(self, asset: str) -> tuple[float | None, float, str]:
        """Get best available price. Returns (price, age_seconds, source)."""
        # Try RTDS first (sub-second Chainlink via WebSocket)
        if self._chainlink:
            rtds_price = self._chainlink.get_price(asset)
            rtds_age = self._chainlink.get_price_age(asset)
            if rtds_price and rtds_age < 5.0:
                return rtds_price, rtds_age, "RTDS"

        # Try on-chain Chainlink (2s cache)
        chain_price = self._cache.get_resolution_price(asset)
        if chain_price is not None:
            chain_age = self._cache.get_price_age(asset)
            if chain_age < 15.0:
                return chain_price, chain_age, "chainlink"

        # Fall back to Binance spot
        binance_price = self._cache.get_price(asset)
        if binance_price is not None:
            return binance_price, 0.0, "binance"

        return None, float("inf"), ""

    def _evaluate_window(self, window: Window, remaining: float) -> None:
        """Evaluate a single window for trading opportunity.

        Trades when:
        1. Price delta exceeds threshold (direction signal)
        2. Book price is 25-95¢ (not a gamble, not fully priced in)

        If book is out of range → set 5s cooldown and retry.
        Does NOT permanently blacklist skipped windows.
        """
        current_price, price_age, price_source = self._get_best_price(window.asset)

        if current_price is None:
            self._skip_cooldown[window.market_id] = time.time()
            return

        if price_age > 15.0:
            self._skip_cooldown[window.market_id] = time.time()
            return

        # Delta since window opened
        delta = (current_price - window.open_price) / window.open_price

        # Direction must clear threshold
        if abs(delta) < self._cfg.direction_threshold:
            self._skip_cooldown[window.market_id] = time.time()
            return

        direction = "up" if delta > 0 else "down"

        # Fetch CLOB book for the winning side (WS instant, REST fallback)
        winning_token = window.up_token_id if direction == "up" else window.down_token_id
        book = None
        book_src = "none"
        if winning_token and self._clob_ws:
            book = self._clob_ws.get_book(winning_token)
            if book:
                book_src = "WS"
        if book is None:
            book = clob_book.get_orderbook(winning_token) if winning_token else None
            if book:
                book_src = "REST"
                log.debug(
                    "[KILLSHOT] WS miss → REST fallback for %s...",
                    winning_token[:16] if winning_token else "?",
                )
        market_bid = book["best_bid"] if book and book["best_bid"] > 0 else None
        market_ask = book["best_ask"] if book and book["best_ask"] > 0 else None

        book_price = market_ask if market_ask and market_ask > 0 else market_bid

        # Floor only: 25¢ minimum (no gambling on near-zero tokens)
        # NO ceiling — whale pays 90-95¢ and wins 98%. Trust direction at T-20s.
        if not book_price or book_price < 0.25:
            # DON'T blacklist — set cooldown and retry in 5s
            self._skip_cooldown[window.market_id] = time.time()
            log.debug(
                "[KILLSHOT] %s %s T-%.0fs | book=%s — retry in 5s",
                direction.upper(), window.asset.upper(), remaining,
                f"{book_price*100:.0f}¢" if book_price else "none",
            )
            return

        # ── FIRE ──
        size_usd = self._cfg.max_bet_usd

        # Mark window as traded — permanent, no retry
        self._traded_windows[window.market_id] = time.time()

        # Live or paper
        if not self._dry_run and self._client and winning_token:
            entry_price, actual_shares, order_id = self._place_live_order(
                winning_token, market_ask, size_usd,
            )
            if entry_price is None:
                log.warning("[KILLSHOT] Live order FAILED — no fill")
                return
        else:
            entry_price = round(book_price, 2)
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
            "$%.2f (%.1f shares) | T-%.0fs | price=%s book=%s%s",
            mode, direction.upper(), window.asset.upper(), delta * 100,
            entry_price * 100, size_usd, actual_shares, remaining,
            price_source, book_src,
            f" | order={order_id}" if order_id else "",
        )

    def _place_live_order(
        self, token_id: str, market_ask: float | None, size_usd: float,
    ) -> tuple[float | None, float, str]:
        """Place a FOK buy order. Returns (entry_price, shares, order_id) or (None, 0, '').

        FOK (Fill-or-Kill): entire order fills immediately or is cancelled.
        No stale orders sitting on the book — clean binary outcome.
        """
        try:
            from py_clob_client.clob_types import OrderArgs, OrderType
            from py_clob_client.order_builder.constants import BUY

            # Cross the spread: ask + 1¢
            if market_ask and market_ask > 0:
                price = round(market_ask + 0.01, 2)
            else:
                price = 0.90  # fallback

            # No cap — whale pays 90-95¢, so do we
            price = min(price, 0.99)

            shares = round(size_usd / price, 2)
            if shares < 5:
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
            resp = self._client.post_order(signed_order, OrderType.FOK)
            order_id = resp.get("orderID") or resp.get("id", "unknown")
            status = resp.get("status", "")

            log.info("[KILLSHOT] CLOB response: %s", json.dumps(resp)[:500])

            # Only accept "matched" or "filled" — "live" means unfilled (stale)
            if status.lower() in ("matched", "filled"):
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
        self._skip_cooldown = {
            k: v for k, v in self._skip_cooldown.items() if v > cutoff
        }
        removed = before - len(self._traded_windows)
        if removed:
            log.debug("[KILLSHOT] Cleaned %d expired window IDs", removed)
