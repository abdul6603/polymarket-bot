"""Killshot engine — late-window direction snipe using Binance spot price.

Core logic:
1. At T-60s to T-10s before each 5m window close, check Binance spot price
2. If spot delta since window open exceeds threshold → direction is "locked"
3. Simulate posting a maker limit order on the winning side at 60-75¢
4. Track outcome at resolution for paper P&L
"""
from __future__ import annotations

import logging
import time

from killshot.config import KillshotConfig
from killshot.tracker import PaperTrade, PaperTracker

from bot.price_cache import PriceCache
from bot.snipe.window_tracker import Window
from bot.snipe import clob_book

log = logging.getLogger("killshot.engine")


class KillshotEngine:
    """Evaluates 5m windows in the kill zone and simulates paper trades."""

    def __init__(self, cfg: KillshotConfig, price_cache: PriceCache,
                 tracker: PaperTracker):
        self._cfg = cfg
        self._cache = price_cache
        self._tracker = tracker
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
        """Evaluate a single window — determine direction and simulate trade."""
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

        # Fetch CLOB book for the winning side to see real market prices
        winning_token = window.up_token_id if direction == "up" else window.down_token_id
        book = clob_book.get_orderbook(winning_token) if winning_token else None
        market_bid = book["best_bid"] if book and book["best_bid"] > 0 else None
        market_ask = book["best_ask"] if book and book["best_ask"] > 0 else None

        # Entry price: stronger delta → higher confidence → willing to pay more
        delta_strength = min(abs(delta) / (self._cfg.direction_threshold * 5), 1.0)
        entry_price = self._cfg.entry_price_min + delta_strength * (
            self._cfg.entry_price_max - self._cfg.entry_price_min
        )
        entry_price = round(entry_price, 2)

        # Position sizing (capped at max_bet and 10% of bankroll)
        size_usd = min(self._cfg.max_bet_usd, self._cfg.bankroll_usd * 0.10)
        shares = round(size_usd / entry_price, 2)

        # Log CLOB book vs our simulated entry
        if market_bid is not None and market_ask is not None:
            fillable = "YES" if entry_price >= market_ask else "NO"
            log.info(
                "[KILLSHOT] BOOK %s: bid=%.0f¢ ask=%.0f¢ spread=%.1f¢ | "
                "our_entry=%.0f¢ | fillable=%s",
                direction.upper(), market_bid * 100, market_ask * 100,
                (market_ask - market_bid) * 100,
                entry_price * 100, fillable,
            )
        elif market_bid is not None:
            log.info(
                "[KILLSHOT] BOOK %s: bid=%.0f¢ ask=NONE | our_entry=%.0f¢",
                direction.upper(), market_bid * 100, entry_price * 100,
            )
        else:
            log.warning("[KILLSHOT] BOOK unavailable for %s", direction)

        # Mark window as traded (one shot per window per asset)
        self._traded_windows[window.market_id] = time.time()

        # Record paper trade
        trade = PaperTrade(
            timestamp=time.time(),
            asset=window.asset,
            market_id=window.market_id,
            question=window.question,
            direction=direction,
            entry_price=entry_price,
            size_usd=size_usd,
            shares=shares,
            window_end_ts=window.end_ts,
            spot_delta_pct=round(delta, 6),
            open_price=window.open_price,
            market_bid=market_bid or 0.0,
            market_ask=market_ask or 0.0,
        )
        self._tracker.record_trade(trade)

        # Track daily loss cap using actual losses only (updated on resolution)
        # Don't count here — tracker handles it via report_loss()

        log.info(
            "[KILLSHOT] FIRE: %s %s | delta=%.3f%% | entry=%.0f¢ | "
            "$%.2f (%.1f shares) | T-%.0fs",
            direction.upper(), window.asset.upper(), delta * 100,
            entry_price * 100, size_usd, shares, remaining,
        )

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
