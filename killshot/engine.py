"""Killshot engine — late-window direction snipe using Chainlink oracle.

Core logic:
1. Throughout kill zone (T-120s to T-5s), monitor price + CLOB book
2. If delta exceeds threshold AND book is 25-95c → buy the winning side
3. If book out of range, retry every 1s (DON'T blacklist the window)

Enhanced with:
- Adaptive time-decaying direction threshold (Phase 1b)
- Kelly Criterion dynamic bet sizing with confidence tiers (Phase 1c)
- Sum-to-one arbitrage detection (Phase 1d)
- Exposure caps (Phase 1e)
- Rust executor integration for sub-20ms orders (Phase 2b)
- Pre-signed order caching (Phase 2c)
- Binance @aggTrade leading indicator (Phase 2d)
- Correlation-aware position limits (Phase 2e)
- Streak-based risk adjustment (Phase 3a)
- Volatility-adaptive threshold (Phase 3b)
- Cross-window conflict avoidance (Phase 3d)
- Circuit breakers: spread, liquidity, volatility (Phase 3e)
- Multi-asset cascade: BTC leads, alts follow (Phase 4a)
- Depth-aware sizing (Phase 4b)
"""
from __future__ import annotations

import json
import logging
import os
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
                 clob_ws=None, binance_agg=None):
        self._cfg = cfg
        self._cache = price_cache
        self._tracker = tracker
        self._client = clob_client
        self._chainlink = chainlink_ws
        self._clob_ws = clob_ws
        self._binance_agg = binance_agg
        self._dry_run = cfg.dry_run

        # market_id -> timestamp when ACTUALLY traded (permanent blacklist)
        self._traded_windows: dict[str, float] = {}
        # market_id -> timestamp of last skip (cooldown before retry)
        self._skip_cooldown: dict[str, float] = {}
        self._daily_loss: float = 0.0
        self._daily_reset_date: str = ""
        self._kill_zone_logged: set[str] = set()

        # Phase 2c: Pre-signed order cache
        self._presigned: dict[str, dict] = {}
        self._presign_ts: dict[str, float] = {}

        # Phase 2e: Per-asset exposure tracking
        self._per_asset_exposure: dict[str, float] = {}

        # Phase 3a: Streak tracking
        self._streak: int = 0  # +N = consecutive wins, -N = consecutive losses
        self._streak_cooldown_trades: int = 0

        # Phase 3d: Last direction per asset (conflict avoidance)
        self._last_direction: dict[str, tuple[str, float]] = {}

        # Phase 4a: Active windows reference for cascade
        self._active_windows: list[Window] = []

        self._daily_trades: int = 0

    # ── Main tick ───────────────────────────────────────────────

    def tick(self, windows: list[Window]) -> None:
        """Called every tick — check all active windows for kill zone entry."""
        now = time.time()
        today = time.strftime("%Y-%m-%d")

        # Daily reset
        if today != self._daily_reset_date:
            self._daily_loss = 0.0
            self._daily_reset_date = today
            self._kill_zone_logged.clear()
            self._skip_cooldown.clear()
            self._streak = 0
            self._streak_cooldown_trades = 0
            self._per_asset_exposure.clear()
            self._presigned.clear()
            self._presign_ts.clear()
            self._last_direction.clear()
            self._daily_trades = 0
            log.info("[KILLSHOT] Daily reset — all counters cleared")

        # Daily loss cap
        if self._daily_loss >= self._cfg.daily_loss_cap_usd:
            return

        # Store for cascade reference
        self._active_windows = windows

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

            # Skip cooldown: retry every 1s
            last_skip = self._skip_cooldown.get(window.market_id, 0)
            if now - last_skip < 1.0:
                continue

            # Log kill zone entry (once per window) + pre-sign orders
            if window.market_id not in self._kill_zone_logged:
                self._kill_zone_logged.add(window.market_id)
                log.info(
                    "[KILLSHOT] Kill zone: %s %s | T-%.0fs | open=$%.2f",
                    window.asset.upper(), window.market_id[:12],
                    remaining, window.open_price,
                )
                self._presign_orders(window)

            self._evaluate_window(window, remaining)

    # ── Price helpers ───────────────────────────────────────────

    def _get_best_price(self, asset: str) -> tuple[float | None, float, str]:
        """Get best available price. Returns (price, age_seconds, source)."""
        if self._chainlink:
            rtds_price = self._chainlink.get_price(asset)
            rtds_age = self._chainlink.get_price_age(asset)
            if rtds_price and rtds_age < 5.0:
                return rtds_price, rtds_age, "RTDS"

        chain_price = self._cache.get_resolution_price(asset)
        if chain_price is not None:
            chain_age = self._cache.get_price_age(asset)
            if chain_age < 15.0:
                return chain_price, chain_age, "chainlink"

        binance_price = self._cache.get_price(asset)
        if binance_price is not None:
            return binance_price, 0.0, "binance"

        return None, float("inf"), ""

    def _get_book_data(self, token_id: str) -> tuple[dict | None, str]:
        """Get orderbook for a token. WS preferred, REST fallback."""
        if not token_id:
            return None, "none"
        if self._clob_ws:
            book = self._clob_ws.get_book(token_id)
            if book:
                return book, "WS"
        book = clob_book.get_orderbook(token_id)
        if book:
            return book, "REST"
        return None, "none"

    # ── Core evaluation ─────────────────────────────────────────

    def _evaluate_window(self, window: Window, remaining: float) -> None:
        """Evaluate a single window for trading opportunity."""
        current_price, price_age, price_source = self._get_best_price(window.asset)

        if current_price is None or price_age > 15.0:
            self._skip_cooldown[window.market_id] = time.time()
            return

        delta = (current_price - window.open_price) / window.open_price
        direction = "up" if delta > 0 else "down"

        # ── Phase 3d: Cross-window conflict avoidance ───────
        if self._cfg.direction_cooldown_s > 0:
            last = self._last_direction.get(window.asset)
            if last and last[0] != direction:
                elapsed = time.time() - last[1]
                if elapsed < self._cfg.direction_cooldown_s:
                    self._skip_cooldown[window.market_id] = time.time()
                    return

        # ── Phase 1d: Sum-to-one arb check (before direction) ──
        if self._cfg.arb_enabled:
            if self._check_arb(window, remaining):
                return

        # ── Phase 1b: Adaptive time-decaying threshold ──────
        if self._cfg.adaptive_threshold:
            ratio = remaining / self._cfg.window_seconds
            threshold = max(self._cfg.direction_threshold * ratio, 0.0001)
        else:
            threshold = self._cfg.direction_threshold

        # ── Phase 3b: Volatility multiplier ─────────────────
        if self._cfg.volatility_adaptive and self._chainlink:
            vol = self._chainlink.get_volatility(window.asset)
            if vol is not None and vol > 0:
                baseline_vol = 0.001
                vol_mult = min(1 + (vol / baseline_vol), 5.0)
                threshold *= vol_mult

        # ── Direction signal check ──────────────────────────
        if abs(delta) < threshold:
            self._skip_cooldown[window.market_id] = time.time()
            log.info(
                "[KILLSHOT] %s T-%.0fs | delta=%.3f%% < %.3f%% threshold — wait",
                window.asset.upper(), remaining, abs(delta) * 100, threshold * 100,
            )
            return

        # ── Fetch CLOB book for the winning side ────────────
        winning_token = window.up_token_id if direction == "up" else window.down_token_id
        book, book_src = self._get_book_data(winning_token)

        market_bid = book["best_bid"] if book and book.get("best_bid", 0) > 0 else None
        market_ask = book["best_ask"] if book and book.get("best_ask", 0) > 0 else None
        book_price = market_ask if market_ask and market_ask > 0 else market_bid

        # Entry price range check
        price_min = self._cfg.entry_price_min
        price_max = self._cfg.entry_price_max
        if not book_price or book_price < price_min or book_price > price_max:
            self._skip_cooldown[window.market_id] = time.time()
            if book_price and book_price > price_max:
                log.info(
                    "[KILLSHOT] %s %s T-%.0fs | book=%.0f¢ > %.0f¢ cap — skip",
                    direction.upper(), window.asset.upper(), remaining,
                    book_price * 100, price_max * 100,
                )
            elif book_price and book_price < price_min:
                log.info(
                    "[KILLSHOT] %s %s T-%.0fs | book=%.0f¢ < %.0f¢ floor — retry",
                    direction.upper(), window.asset.upper(), remaining,
                    book_price * 100, price_min * 100,
                )
            return

        # ── Phase 3e: Circuit breaker — spread ──────────────
        if book:
            spread = book.get("spread", 0)
            if spread > 0.10:
                self._skip_cooldown[window.market_id] = time.time()
                log.info("[KILLSHOT] Circuit breaker: spread %.3f > 0.10 — skip", spread)
                return

        # ── Phase 2d: Binance @aggTrade signal ──────────────
        binance_boost = 0
        if self._cfg.binance_agg_enabled and self._binance_agg:
            sig = self._binance_agg.get_signal(window.asset)
            if sig:
                b_delta, _b_vol, _b_conf = sig
                if abs(b_delta) > 0.001:
                    if (b_delta > 0 and direction == "up") or \
                       (b_delta < 0 and direction == "down"):
                        binance_boost = 1
                    else:
                        binance_boost = -1

        # ── Phase 1c: Confidence tiers ──────────────────────
        tier_mult = 0.5  # Tier 3 default
        if book_price >= 0.94 and abs(delta) >= 0.002 and remaining <= 15:
            tier_mult = 1.0    # Tier 1 (max)
        elif book_price >= 0.90 and abs(delta) >= 0.0015 and remaining <= 20:
            tier_mult = 0.75   # Tier 2 (high)
        elif book_price >= 0.87 and abs(delta) >= 0.001 and remaining <= 25:
            tier_mult = 0.5    # Tier 3 (standard)

        # Binance boost/penalty
        if binance_boost > 0:
            tier_mult = min(tier_mult + 0.25, 1.0)
        elif binance_boost < 0:
            tier_mult = max(tier_mult - 0.25, 0.25)

        # ── Phase 1c: Kelly Criterion sizing ────────────────
        kelly_f = 0.0
        if self._cfg.kelly_enabled:
            stats = self._tracker.get_stats()
            p = stats.get("win_rate", 95) / 100.0
            if stats.get("resolved", 0) < 10:
                p = 0.95

            b = (1.0 - book_price) / book_price if book_price > 0 else 0.1
            kelly_f = max(p - (1 - p) / b, 0) if b > 0 else 0

            size_usd = self._cfg.bankroll_usd * kelly_f * tier_mult * self._cfg.kelly_fraction
        else:
            size_usd = self._cfg.max_bet_usd

        # ── Phase 3a: Streak-based risk adjustment ──────────
        if self._streak <= -3:
            log.warning("[KILLSHOT] 3+ consecutive losses — HALTED for this window")
            self._skip_cooldown[window.market_id] = time.time()
            return

        if self._streak <= -2 and self._streak_cooldown_trades > 0:
            size_usd *= 0.5
            self._streak_cooldown_trades -= 1
            log.info(
                "[KILLSHOT] Streak penalty: 50%% Kelly (%d cooldown trades left)",
                self._streak_cooldown_trades,
            )
        elif self._streak >= 5 and self._cfg.kelly_enabled:
            # Allow three-quarter Kelly on hot streaks
            size_usd = self._cfg.bankroll_usd * kelly_f * 0.75 * tier_mult

        # ── Phase 2e: Correlation-aware reduction ───────────
        if window.asset != "bitcoin":
            btc_exp = self._per_asset_exposure.get("bitcoin", 0)
            if btc_exp > 0:
                corr_factor = max(
                    1.0 - self._cfg.correlation_reduction * self._cfg.avg_correlation,
                    0.2,
                )
                size_usd *= corr_factor

        # Clamp to configured range
        size_usd = max(min(size_usd, self._cfg.max_bet_usd), 5.0)

        # ── Phase 1e: Exposure cap ──────────────────────────
        open_exposure = sum(t.size_usd for t in self._tracker._pending)
        if open_exposure + size_usd > self._cfg.max_exposure_usd:
            log.info(
                "[KILLSHOT] Exposure cap: $%.2f + $%.2f > $%.2f — skip",
                open_exposure, size_usd, self._cfg.max_exposure_usd,
            )
            self._skip_cooldown[window.market_id] = time.time()
            return

        # ── Phase 4b: Depth-aware sizing ────────────────────
        if book:
            ask_depth = book.get("sell_pressure", 0)
            if ask_depth > 0:
                max_by_depth = ask_depth * 0.80
                if size_usd > max_by_depth >= 5.0:
                    log.info(
                        "[KILLSHOT] Depth cap: $%.2f -> $%.2f (80%% of $%.2f depth)",
                        size_usd, max_by_depth, ask_depth,
                    )
                    size_usd = max(max_by_depth, 5.0)

        # ── Phase 3e: Circuit breaker — liquidity ───────────
        if book:
            ask_depth = book.get("sell_pressure", 0)
            if 0 < ask_depth < size_usd * 2:
                log.info(
                    "[KILLSHOT] Liquidity breaker: depth $%.2f < 2x size $%.2f — skip",
                    ask_depth, size_usd,
                )
                self._skip_cooldown[window.market_id] = time.time()
                return

        # ── FIRE ────────────────────────────────────────────
        self._traded_windows[window.market_id] = time.time()
        self._last_direction[window.asset] = (direction, time.time())

        # Live or paper
        if not self._dry_run and self._client and winning_token:
            entry_price, actual_shares, order_id = self._execute_order(
                winning_token, market_ask, size_usd,
            )
            if entry_price is None:
                log.warning("[KILLSHOT] Order FAILED — no fill")
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

        # Update tracking
        self._per_asset_exposure[window.asset] = (
            self._per_asset_exposure.get(window.asset, 0) + size_usd
        )
        self._daily_trades += 1

        log.info(
            "[KILLSHOT] %s FIRE: %s %s | delta=%.3f%% | entry=%.0f¢ | "
            "$%.2f (%.1f shares) | T-%.0fs | tier=%.2f | kelly=%s | "
            "price=%s book=%s%s",
            mode, direction.upper(), window.asset.upper(), delta * 100,
            entry_price * 100, size_usd, actual_shares, remaining,
            tier_mult, "ON" if self._cfg.kelly_enabled else "OFF",
            price_source, book_src,
            f" | order={order_id}" if order_id else "",
        )

        # ── Phase 4a: Multi-asset cascade ───────────────────
        if self._cfg.cascade_enabled and window.asset == "bitcoin":
            self._cascade_check(direction)

    # ── Phase 1d: Arbitrage detection ───────────────────────────

    def _check_arb(self, window: Window, remaining: float) -> bool:
        """Check for sum-to-one arbitrage: buy BOTH sides for guaranteed profit."""
        if not window.up_token_id or not window.down_token_id:
            return False

        up_book, _ = self._get_book_data(window.up_token_id)
        down_book, _ = self._get_book_data(window.down_token_id)

        if not up_book or not down_book:
            return False

        ask_up = up_book.get("best_ask", 0)
        ask_down = down_book.get("best_ask", 0)

        if ask_up <= 0 or ask_down <= 0:
            return False

        total_cost = ask_up + ask_down
        if total_cost >= self._cfg.arb_threshold:
            return False

        # Arb found
        profit_per_pair = 1.0 - total_cost

        # Equal shares on both sides, limited by depth
        up_depth_shares = up_book.get("sell_pressure", 0) / ask_up if ask_up > 0 else 0
        down_depth_shares = down_book.get("sell_pressure", 0) / ask_down if ask_down > 0 else 0
        max_pairs = min(up_depth_shares, down_depth_shares) * 0.80
        max_pairs_by_dollars = self._cfg.max_bet_usd / total_cost
        num_pairs = int(min(max_pairs, max_pairs_by_dollars))

        if num_pairs < 5:
            return False

        total_size = round(num_pairs * total_cost, 2)

        # Exposure check
        open_exposure = sum(t.size_usd for t in self._tracker._pending)
        if open_exposure + total_size > self._cfg.max_exposure_usd:
            return False

        # Execute both sides
        if not self._dry_run and self._client:
            up_cost = round(num_pairs * ask_up, 2)
            ep1, sh1, oid1 = self._execute_order(
                window.up_token_id, ask_up, up_cost,
            )
            if ep1 is None:
                return False
            down_cost = round(num_pairs * ask_down, 2)
            ep2, sh2, oid2 = self._execute_order(
                window.down_token_id, ask_down, down_cost,
            )
            if ep2 is None:
                log.warning("[KILLSHOT] ARB: UP filled but DOWN failed — one-sided risk")
                return False
            entry_price = round(ep1 + ep2, 4)
            actual_pairs = min(sh1, sh2)
        else:
            entry_price = round(total_cost, 4)
            actual_pairs = num_pairs

        self._traded_windows[window.market_id] = time.time()

        trade = PaperTrade(
            timestamp=time.time(),
            asset=window.asset,
            market_id=window.market_id,
            question=window.question,
            direction="arb",
            entry_price=entry_price,
            size_usd=total_size,
            shares=actual_pairs,
            window_end_ts=window.end_ts,
            spot_delta_pct=0,
            open_price=window.open_price,
            market_bid=0,
            market_ask=total_cost,
        )
        self._tracker.record_trade(trade)

        mode = "LIVE" if not self._dry_run else "PAPER"
        log.info(
            "[KILLSHOT] %s ARB FIRE: %s | up=%.0f¢ + down=%.0f¢ = %.0f¢ | "
            "profit=%.1f¢/pair x %d = $%.2f | T-%.0fs",
            mode, window.asset.upper(), ask_up * 100, ask_down * 100,
            total_cost * 100, profit_per_pair * 100, actual_pairs,
            actual_pairs * profit_per_pair, remaining,
        )
        return True

    # ── Phase 2b/2c: Order execution ───────────────────────────

    def _execute_order(
        self, token_id: str, market_ask: float | None, size_usd: float,
    ) -> tuple[float | None, float, str]:
        """Execute via Rust executor (preferred) or Python fallback."""
        if self._cfg.rust_executor_enabled:
            result = self._try_rust_order(token_id, market_ask, size_usd)
            if result[0] is not None:
                return result
            log.warning("[KILLSHOT] Rust executor failed — Python fallback")
        return self._place_live_order(token_id, market_ask, size_usd)

    def _try_rust_order(
        self, token_id: str, market_ask: float | None, size_usd: float,
    ) -> tuple[float | None, float, str]:
        """POST to Rust executor at localhost:9999 for sub-20ms order placement."""
        try:
            import httpx

            price = round(market_ask + 0.01, 2) if market_ask and market_ask > 0 else 0.90
            price = min(price, self._cfg.entry_price_max)
            shares = max(int(size_usd / price), 5)

            resp = httpx.post(
                f"{self._cfg.rust_executor_url}/order",
                json={
                    "token_id": token_id,
                    "price": price,
                    "size": shares,
                    "side": "BUY",
                },
                timeout=5.0,
            )
            data = resp.json()

            if data.get("success"):
                latency = data.get("latency_ms", 0)
                log.info(
                    "[KILLSHOT] Rust order: %.1fms | shares=%d | id=%s",
                    latency, data.get("total_shares", shares),
                    str(data.get("order_id", ""))[:16],
                )
                return (
                    data.get("avg_price", price),
                    data.get("total_shares", shares),
                    data.get("order_id", "rust-unknown"),
                )

            log.warning(
                "[KILLSHOT] Rust order rejected: %s",
                data.get("error", "unknown"),
            )
            return (None, 0, "")

        except Exception as e:
            log.warning("[KILLSHOT] Rust executor error: %s", str(e)[:100])
            return (None, 0, "")

    def _presign_orders(self, window: Window) -> None:
        """Phase 2c: Pre-build and sign orders for both directions on kill zone entry."""
        if self._dry_run or not self._client:
            return

        for token_id in [window.up_token_id, window.down_token_id]:
            if not token_id or token_id in self._presigned:
                continue

            book, _ = self._get_book_data(token_id)
            if not book or book.get("best_ask", 0) <= 0:
                continue

            ask = book["best_ask"]
            price = round(min(ask + 0.01, self._cfg.entry_price_max), 2)
            shares = max(int(self._cfg.max_bet_usd / price), 5)

            try:
                from py_clob_client.clob_types import OrderArgs
                from py_clob_client.order_builder.constants import BUY

                order_args = OrderArgs(
                    price=price,
                    size=shares,
                    side=BUY,
                    token_id=token_id,
                )
                signed = self._client.create_order(order_args)
                self._presigned[token_id] = {
                    "signed": signed, "price": price, "shares": shares,
                }
                self._presign_ts[token_id] = time.time()
                log.debug(
                    "[KILLSHOT] Pre-signed: %s... @ %.0f¢ (%d shares)",
                    token_id[:16], price * 100, shares,
                )
            except Exception as e:
                log.debug("[KILLSHOT] Pre-sign failed: %s", str(e)[:80])

    def _place_live_order(
        self, token_id: str, market_ask: float | None, size_usd: float,
    ) -> tuple[float | None, float, str]:
        """Place a FOK buy order via Python CLOB client.

        Uses pre-signed order if available and price is within 2c.
        """
        presigned = self._presigned.pop(token_id, None)
        presign_age = time.time() - self._presign_ts.pop(token_id, 0)

        try:
            from py_clob_client.clob_types import OrderArgs, OrderType
            from py_clob_client.order_builder.constants import BUY

            if market_ask and market_ask > 0:
                price = round(market_ask + 0.01, 2)
            else:
                price = 0.90
            price = min(price, self._cfg.entry_price_max)

            shares = int(size_usd / price)
            if shares < 5:
                shares = 5
                size_usd = round(shares * price, 2)
                log.info("[KILLSHOT] Bumped to min 5 shares ($%.2f)", size_usd)

            # Use pre-signed if fresh and price is close
            if (presigned and presign_age < 60
                    and abs(presigned["price"] - price) <= 0.02):
                signed_order = presigned["signed"]
                log.info("[KILLSHOT] Using pre-signed order (%.1fs old)", presign_age)
            else:
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

    # ── Phase 4a: Multi-asset cascade ───────────────────────────

    def _cascade_check(self, btc_direction: str) -> None:
        """After BTC fires, check if alts are still mispriced (2-5s lag)."""
        if not self._active_windows:
            return

        time.sleep(self._cfg.cascade_delay_s)

        for window in self._active_windows:
            if window.asset == "bitcoin" or window.market_id in self._traded_windows:
                continue
            if window.asset not in self._cfg.assets:
                continue

            remaining = window.end_ts - time.time()
            if remaining < self._cfg.min_window_seconds or remaining > self._cfg.window_seconds:
                continue

            # Check if alt book is still cheap (hasn't repriced)
            winning_token = (
                window.up_token_id if btc_direction == "up" else window.down_token_id
            )
            book, book_src = self._get_book_data(winning_token)
            if not book:
                continue

            ask = book.get("best_ask", 0)
            if ask <= 0 or ask >= 0.87:
                continue

            # Alt still cheap — direct snipe with conservative sizing
            size_usd = min(self._cfg.max_bet_usd * 0.5, 15.0)

            # Exposure check
            open_exposure = sum(t.size_usd for t in self._tracker._pending)
            if open_exposure + size_usd > self._cfg.max_exposure_usd:
                continue

            self._traded_windows[window.market_id] = time.time()

            if not self._dry_run and self._client and winning_token:
                entry_price, actual_shares, order_id = self._execute_order(
                    winning_token, ask, size_usd,
                )
                if entry_price is None:
                    continue
            else:
                entry_price = round(ask, 2)
                actual_shares = round(size_usd / entry_price, 2)
                order_id = ""

            trade = PaperTrade(
                timestamp=time.time(),
                asset=window.asset,
                market_id=window.market_id,
                question=window.question,
                direction=btc_direction,
                entry_price=entry_price,
                size_usd=size_usd,
                shares=actual_shares,
                window_end_ts=window.end_ts,
                spot_delta_pct=0,
                open_price=window.open_price,
                market_bid=book.get("best_bid", 0),
                market_ask=ask,
            )
            self._tracker.record_trade(trade)

            self._per_asset_exposure[window.asset] = (
                self._per_asset_exposure.get(window.asset, 0) + size_usd
            )

            mode = "LIVE" if not self._dry_run else "PAPER"
            log.info(
                "[KILLSHOT] %s CASCADE: %s %s | BTC led %s | entry=%.0f¢ | "
                "$%.2f | T-%.0fs | book=%s",
                mode, btc_direction.upper(), window.asset.upper(),
                btc_direction.upper(), entry_price * 100, size_usd,
                remaining, book_src,
            )

    # ── Resolution callback ─────────────────────────────────────

    def report_resolved(self, trades: list[PaperTrade]) -> None:
        """Update daily loss, streak, and per-asset exposure from resolved trades."""
        for trade in trades:
            # Arb always wins
            if trade.direction == "arb":
                self._streak = max(self._streak + 1, 1)
                continue

            if trade.outcome == "win":
                self._streak = self._streak + 1 if self._streak >= 0 else 1
            elif trade.outcome == "loss":
                self._daily_loss += abs(trade.pnl)
                self._streak = self._streak - 1 if self._streak <= 0 else -1

                # Phase 3a: streak-based alerts
                if self._streak <= -2:
                    self._streak_cooldown_trades = 5
                    self._notify_tg(
                        f"\u26a0\ufe0f <b>Killshot: {abs(self._streak)} consecutive losses</b>\n"
                        f"Reducing Kelly to 50% for next 5 trades"
                    )
                if self._streak <= -3:
                    self._notify_tg(
                        "\U0001f6a8 <b>Killshot HALTED: 3 consecutive losses</b>\n"
                        "Manual review needed. Skipping all trades until daily reset."
                    )

                log.info(
                    "[KILLSHOT] Daily loss: +$%.2f -> $%.2f / $%.2f | streak=%d",
                    abs(trade.pnl), self._daily_loss, self._cfg.daily_loss_cap_usd,
                    self._streak,
                )

            # Reduce per-asset exposure on resolution
            if trade.asset in self._per_asset_exposure:
                self._per_asset_exposure[trade.asset] = max(
                    0, self._per_asset_exposure[trade.asset] - trade.size_usd,
                )

    # ── Utilities ───────────────────────────────────────────────

    @staticmethod
    def _notify_tg(text: str) -> None:
        """Send Telegram notification."""
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat = os.getenv("TELEGRAM_CHAT_ID", "")
        if not token or not chat:
            return
        try:
            import requests
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat, "text": text, "parse_mode": "HTML"},
                timeout=5,
            )
        except Exception:
            pass

    def cleanup_expired(self) -> None:
        """Remove old entries to prevent memory growth."""
        cutoff = time.time() - 3600
        before = len(self._traded_windows)
        self._traded_windows = {
            k: v for k, v in self._traded_windows.items() if v > cutoff
        }
        self._skip_cooldown = {
            k: v for k, v in self._skip_cooldown.items() if v > cutoff
        }
        self._presigned = {
            k: v for k, v in self._presigned.items()
            if self._presign_ts.get(k, 0) > cutoff
        }
        self._presign_ts = {
            k: v for k, v in self._presign_ts.items() if v > cutoff
        }
        self._last_direction = {
            k: v for k, v in self._last_direction.items() if v[1] > cutoff
        }
        removed = before - len(self._traded_windows)
        if removed:
            log.debug("[KILLSHOT] Cleaned %d expired entries", removed)
