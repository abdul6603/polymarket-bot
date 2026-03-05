"""Killshot engine — late-window heavy-side snipe using Chainlink oracle.

Core logic (whale strategy):
1. Throughout kill zone, monitor spot price delta + BOTH tokens' CLOB books
2. Pick heavy side (whichever token ≥ 60¢) — confirmed by direction signal
3. If heavy side and direction disagree → skip (market confused)
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import httpx

from killshot.config import KillshotConfig
from killshot.chainlink_ws import ChainlinkWS
from killshot.tracker import PaperTrade, PaperTracker

from bot.price_cache import PriceCache
from bot.snipe.window_tracker import Window
from bot.snipe import clob_book

log = logging.getLogger("killshot.engine")

# Persist pending GTC orders so restarts don't orphan them
_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PENDING_ORDERS_FILE = _DATA_DIR / "killshot_pending_orders.json"


class _WindowSnapshot:
    """Minimal window data for restored pending orders (fill recording)."""
    __slots__ = ("market_id", "question", "asset", "end_ts", "open_price", "up_token_id", "down_token_id")

    def __init__(self, d: dict):
        self.market_id = d.get("market_id", "")
        self.question = d.get("question", "")
        self.asset = d.get("asset", "")
        self.end_ts = float(d.get("end_ts", 0))
        self.open_price = float(d.get("open_price", 0))
        self.up_token_id = d.get("up_token_id") or ""
        self.down_token_id = d.get("down_token_id") or ""


class KillshotEngine:
    """Evaluates 5m windows in the kill zone and trades (live or paper)."""

    def __init__(self, cfg: KillshotConfig, price_cache: PriceCache,
                 tracker: PaperTracker, clob_client=None,
                 dir_client=None,
                 chainlink_ws: ChainlinkWS | None = None,
                 clob_ws=None, binance_agg=None, binance_feed=None):
        self._cfg = cfg
        self._cache = price_cache
        self._tracker = tracker
        self._client = clob_client
        self._dir_client = dir_client
        self._chainlink = chainlink_ws
        self._clob_ws = clob_ws
        self._binance_feed = binance_agg or binance_feed
        self._dry_run = cfg.dry_run
        self._rust_url = cfg.rust_executor_url.rstrip("/") if cfg.rust_executor_url else ""
        self._rust_http = httpx.Client(timeout=5.0) if self._rust_url else None
        # market_id -> timestamp when ACTUALLY traded (permanent blacklist)
        self._traded_windows: dict[str, float] = {}
        # market_id -> timestamp of last skip (5s cooldown before retry)
        self._skip_cooldown: dict[str, float] = {}
        self._daily_loss: float = 0.0
        self._daily_reset_date: str = ""
        self._kill_zone_logged: set[str] = set()
        # Momentum state
        self._momentum_logged: set[str] = set()
        self._pending_limit_orders: dict[str, dict] = {}  # market_id -> order info
        # Spread state
        self._spread_logged: set[str] = set()

        # Restore pending GTC orders from disk (survives restarts)
        self._load_pending_orders()
        # Cancel any CLOB orders we don't track (orphans from crash between legs or lost state)
        if not self._dry_run and self._client:
            self._reconcile_open_orders()

    def _window_to_snapshot(self, window) -> dict:
        """Serialize Window for persistence."""
        return {
            "market_id": getattr(window, "market_id", ""),
            "question": getattr(window, "question", ""),
            "asset": getattr(window, "asset", ""),
            "end_ts": getattr(window, "end_ts", 0),
            "open_price": getattr(window, "open_price", 0),
            "up_token_id": getattr(window, "up_token_id", "") or "",
            "down_token_id": getattr(window, "down_token_id", "") or "",
        }

    def _save_pending_orders(self) -> None:
        """Persist _pending_limit_orders to disk so restarts can restore and avoid orphans."""
        if not self._pending_limit_orders:
            if PENDING_ORDERS_FILE.exists():
                try:
                    PENDING_ORDERS_FILE.unlink()
                except OSError:
                    pass
            return
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        out = {}
        for market_id, info in self._pending_limit_orders.items():
            snap = {"placed_at": info["placed_at"]}
            if info.get("type") == "spread":
                snap["type"] = "spread"
                snap["up_order_id"] = info["up_order_id"]
                snap["down_order_id"] = info["down_order_id"]
                snap["up_token_id"] = info.get("up_token_id") or ""
                snap["down_token_id"] = info.get("down_token_id") or ""
                snap["up_price"] = info["up_price"]
                snap["down_price"] = info["down_price"]
                snap["up_shares"] = info["up_shares"]
                snap["down_shares"] = info["down_shares"]
                snap["size_usd"] = info["size_usd"]
            else:
                snap["type"] = "momentum"
                snap["order_id"] = info["order_id"]
                snap["token_id"] = info.get("token_id") or ""
                snap["price"] = info["price"]
                snap["shares"] = info["shares"]
                snap["size_usd"] = info["size_usd"]
            snap["window_snapshot"] = self._window_to_snapshot(info["window"])
            out[market_id] = snap
        try:
            with open(PENDING_ORDERS_FILE, "w") as f:
                json.dump(out, f, indent=0)
        except OSError as e:
            log.warning("[KILLSHOT] Failed to save pending orders: %s", str(e)[:80])

    def _load_pending_orders(self) -> None:
        """Restore _pending_limit_orders from disk after restart."""
        if not PENDING_ORDERS_FILE.exists():
            return
        try:
            with open(PENDING_ORDERS_FILE) as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            log.warning("[KILLSHOT] Failed to load pending orders: %s", str(e)[:80])
            return
        for market_id, snap in data.items():
            ws = _WindowSnapshot(snap.get("window_snapshot") or {})
            placed_at = float(snap.get("placed_at", 0))
            if placed_at <= 0:
                continue
            if snap.get("type") == "spread":
                self._pending_limit_orders[market_id] = {
                    "type": "spread",
                    "up_order_id": snap.get("up_order_id", ""),
                    "down_order_id": snap.get("down_order_id", ""),
                    "up_token_id": snap.get("up_token_id") or "",
                    "down_token_id": snap.get("down_token_id") or "",
                    "up_price": float(snap.get("up_price", 0)),
                    "down_price": float(snap.get("down_price", 0)),
                    "up_shares": float(snap.get("up_shares", 0)),
                    "down_shares": float(snap.get("down_shares", 0)),
                    "size_usd": float(snap.get("size_usd", 0)),
                    "placed_at": placed_at,
                    "window": ws,
                }
            else:
                self._pending_limit_orders[market_id] = {
                    "order_id": snap.get("order_id", ""),
                    "token_id": snap.get("token_id") or "",
                    "price": float(snap.get("price", 0)),
                    "shares": float(snap.get("shares", 0)),
                    "size_usd": float(snap.get("size_usd", 0)),
                    "placed_at": placed_at,
                    "window": ws,
                }
        if self._pending_limit_orders:
            log.info("[KILLSHOT] Restored %d pending GTC orders from disk", len(self._pending_limit_orders))

    def _reconcile_open_orders(self) -> None:
        """Cancel any CLOB open order we don't track (orphans from crash or lost state)."""
        known_ids = set()
        for info in self._pending_limit_orders.values():
            if info.get("type") == "spread":
                known_ids.add(info.get("up_order_id"))
                known_ids.add(info.get("down_order_id"))
            else:
                known_ids.add(info.get("order_id"))
        known_ids.discard("")
        known_ids.discard("unknown")
        try:
            resp = self._client.get_orders()
            orders = resp if isinstance(resp, list) else resp.get("data", []) or []
        except Exception as e:
            log.warning("[KILLSHOT] Reconcile: get_orders failed: %s", str(e)[:80])
            return
        cancelled = 0
        for order in orders:
            oid = order.get("id") or order.get("orderID", "")
            status = (order.get("status") or "").lower()
            if status not in ("live", "open", "active"):
                continue
            if oid and oid not in known_ids:
                if self._cancel_order(oid):
                    cancelled += 1
                    log.info("[KILLSHOT] Reconcile: cancelled orphan order %s", oid[:12])
        if cancelled:
            log.info("[KILLSHOT] Reconcile: cancelled %d orphan order(s)", cancelled)

    # ── Active windows cache (for event-driven spread from WS callback) ──
    _active_windows: list = []

    def on_book_update(self, token_id: str, book: dict) -> None:
        """Event-driven spread check — called from CLOB WS on every book update.

        Maps token_id back to windows and fires _evaluate_spread immediately,
        bypassing the tick loop for lower latency.
        """
        cfg = self._cfg
        if not (cfg.spread_enabled and cfg.spread_only_mode):
            return
        if self._daily_loss >= cfg.daily_loss_cap_usd:
            return

        now = time.time()
        for window in self._active_windows:
            if window.market_id in self._traded_windows:
                continue
            if window.asset not in cfg.assets:
                continue
            # Check if this token belongs to this window
            if token_id not in (getattr(window, "up_token_id", ""), getattr(window, "down_token_id", "")):
                continue
            elapsed = now - window.start_ts
            if cfg.spread_entry_start_s <= elapsed <= cfg.spread_entry_end_s:
                self._evaluate_spread(window, elapsed)

    def tick(self, windows: list[Window]) -> None:
        """Called every tick (default 0.1s) — check all active windows."""
        now = time.time()
        today = time.strftime("%Y-%m-%d")

        # Cache windows for event-driven WS callback
        self._active_windows = windows

        # Daily reset
        if today != self._daily_reset_date:
            self._daily_loss = 0.0
            self._daily_reset_date = today
            self._kill_zone_logged.clear()
            self._momentum_logged.clear()
            self._spread_logged.clear()
            self._skip_cooldown.clear()
            log.info("[KILLSHOT] Daily reset — loss counter cleared")

        # Daily loss cap
        if self._daily_loss >= self._cfg.daily_loss_cap_usd:
            return

        # Check/cancel expired GTC limit orders
        self._check_pending_limit_orders(now)

        cfg = self._cfg

        # ── SPREAD-ONLY MODE: only run spread, skip kill zone and momentum ──
        if cfg.spread_enabled and cfg.spread_only_mode:
            # Require WS connection for spread-only
            if not (self._clob_ws and self._clob_ws.is_connected):
                return
            for window in windows:
                if window.market_id in self._traded_windows:
                    continue
                if window.asset not in cfg.assets:
                    continue
                elapsed = now - window.start_ts
                if cfg.spread_entry_start_s <= elapsed <= cfg.spread_entry_end_s:
                    self._evaluate_spread(window, elapsed)
            return  # Don't run kill zone or momentum

        # ── Mixed mode (legacy) ──
        for window in windows:
            if window.market_id in self._traded_windows:
                continue
            if window.asset not in cfg.assets:
                continue

            elapsed = now - window.start_ts
            remaining = window.end_ts - now

            # SPREAD ZONE
            if (cfg.spread_enabled
                    and cfg.spread_entry_start_s <= elapsed <= cfg.spread_entry_end_s
                    and window.market_id not in self._pending_limit_orders):
                fired = self._evaluate_spread(window, elapsed)
                if fired:
                    continue

            # MOMENTUM ZONE
            if (cfg.momentum_enabled
                    and cfg.momentum_entry_start_s <= elapsed <= cfg.momentum_entry_end_s
                    and window.market_id not in self._pending_limit_orders):
                self._evaluate_momentum(window, elapsed)
                continue

            # KILL ZONE
            if remaining > cfg.window_seconds or remaining < cfg.min_window_seconds:
                continue
            last_skip = self._skip_cooldown.get(window.market_id, 0)
            if now - last_skip < 1.0:
                continue
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

    def _get_book(self, token_id: str | None) -> tuple[dict | None, str]:
        """Fetch orderbook for a token. Returns (book, source)."""
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

    def _evaluate_window(self, window: Window, remaining: float) -> None:
        """Evaluate a single window — whale strategy: buy the heavy side.

        1. Check spot delta for direction signal
        2. Fetch BOTH tokens' books
        3. Pick heavy side (highest book price, must be ≥ 60¢)
        4. Confirm: direction must agree with heavy side
        5. Fire if everything lines up
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

        # Direction must clear threshold (low bar — whale trades on tiny moves)
        if abs(delta) < self._cfg.direction_threshold:
            self._skip_cooldown[window.market_id] = time.time()
            return

        direction = "up" if delta > 0 else "down"

        # ── Fetch BOTH tokens' books ──
        up_book, up_src = self._get_book(window.up_token_id)
        down_book, down_src = self._get_book(window.down_token_id)

        up_ask = up_book["best_ask"] if up_book and up_book.get("best_ask", 0) > 0 else None
        up_bid = up_book["best_bid"] if up_book and up_book.get("best_bid", 0) > 0 else None
        down_ask = down_book["best_ask"] if down_book and down_book.get("best_ask", 0) > 0 else None
        down_bid = down_book["best_bid"] if down_book and down_book.get("best_bid", 0) > 0 else None

        up_price = up_ask or up_bid or 0.0
        down_price = down_ask or down_bid or 0.0

        # ── Pick heavy side ──
        if up_price >= down_price:
            heavy_side = "up"
            heavy_price = up_price
            heavy_token = window.up_token_id
            heavy_ask = up_ask
            book_src = up_src
        else:
            heavy_side = "down"
            heavy_price = down_price
            heavy_token = window.down_token_id
            heavy_ask = down_ask
            book_src = down_src

        # Heavy side must be ≥ 90¢ (high confidence only — zero losses above 85¢)
        if heavy_price < 0.90:
            self._skip_cooldown[window.market_id] = time.time()
            return

        # ── Direction must CONFIRM heavy side ──
        # If BTC went up, heavy side should be UP token. If they disagree, market is confused → skip.
        if direction != heavy_side:
            self._skip_cooldown[window.market_id] = time.time()
            log.info(
                "[KILLSHOT] %s T-%.0fs | direction=%s but heavy=%s (%.0f¢) — conflict, skip",
                window.asset.upper(), remaining, direction.upper(),
                heavy_side.upper(), heavy_price * 100,
            )
            return

        # ── FIRE ──
        market_bid = up_bid if heavy_side == "up" else down_bid
        market_ask = heavy_ask
        size_usd = self._cfg.max_bet_usd

        # Mark window as traded — permanent, no retry
        self._traded_windows[window.market_id] = time.time()

        # Live or paper
        if not self._dry_run and self._client and heavy_token:
            entry_price, actual_shares, order_id = self._place_live_order(
                heavy_token, market_ask, size_usd,
            )
            if entry_price is None:
                log.warning("[KILLSHOT] Live order FAILED — no fill")
                return
        else:
            entry_price = round(heavy_price, 2)
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
            token_id=heavy_token or "",
        )
        self._tracker.record_trade(trade)

        log.info(
            "[KILLSHOT] %s FIRE: %s %s | heavy=%s %.0f¢ | delta=%.3f%% | entry=%.0f¢ | "
            "$%.2f (%.1f shares) | T-%.0fs | price=%s book=%s%s",
            mode, direction.upper(), window.asset.upper(),
            heavy_side.upper(), heavy_price * 100, delta * 100,
            entry_price * 100, size_usd, actual_shares, remaining,
            price_source, book_src,
            f" | order={order_id}" if order_id else "",
        )

    # ── Spread capture (both-sides) methods ─────────────────────

    def _evaluate_spread(self, window: Window, elapsed: float) -> bool:
        """Evaluate a window for spread capture (buy BOTH sides for guaranteed profit).

        Returns True if spread orders were placed/simulated, False if skipped.
        """
        cfg = self._cfg

        # Log spread zone entry (once per window)
        if window.market_id not in self._spread_logged:
            self._spread_logged.add(window.market_id)
            log.info(
                "[KILLSHOT] Spread zone: %s %s | T+%.0fs | open=$%.2f",
                window.asset.upper(), window.market_id[:12], elapsed, window.open_price,
            )

        # Fetch BOTH books
        up_book, up_src = self._get_book(window.up_token_id)
        down_book, down_src = self._get_book(window.down_token_id)

        up_ask = up_book["best_ask"] if up_book and up_book.get("best_ask", 0) > 0 else None
        down_ask = down_book["best_ask"] if down_book and down_book.get("best_ask", 0) > 0 else None

        if up_ask is None or down_ask is None:
            return False

        combined = up_ask + down_ask
        if combined >= cfg.spread_max_combined_cost:
            return False  # Spread too tight — no guaranteed profit margin

        # Fee-aware check: taker fee = 2% * min(price, 1-price) per share
        up_fee = 0.02 * min(up_ask, 1.0 - up_ask)
        down_fee = 0.02 * min(down_ask, 1.0 - down_ask)
        net_edge = 1.0 - combined - up_fee - down_fee
        if net_edge < cfg.spread_min_net_edge:
            return False  # Not profitable after fees

        guaranteed_profit_pct = net_edge * 100

        half_usd = cfg.spread_max_bet_usd / 2

        # Depth check: best ask must have enough size (× depth multiplier)
        up_ask_size = up_book.get("best_ask_size", 0) if up_book else 0
        down_ask_size = down_book.get("best_ask_size", 0) if down_book else 0
        up_shares_needed = half_usd / up_ask
        down_shares_needed = half_usd / down_ask
        depth_mult = cfg.spread_min_depth_multiplier
        if up_ask_size > 0 and up_ask_size < up_shares_needed * depth_mult:
            return False  # Not enough UP depth
        if down_ask_size > 0 and down_ask_size < down_shares_needed * depth_mult:
            return False  # Not enough DOWN depth

        log.info(
            "[KILLSHOT] Spread found: %s UP@%.0f¢ + DOWN@%.0f¢ = %.0f¢ | net=%.1f¢/$ (fee=%.1f¢)",
            window.asset.upper(), up_ask * 100, down_ask * 100,
            combined * 100, net_edge * 100, (up_fee + down_fee) * 100,
        )

        up_shares = round(up_shares_needed, 2)
        down_shares = round(down_shares_needed, 2)

        # Mark window as traded
        self._traded_windows[window.market_id] = time.time()

        if self._dry_run:
            # Paper mode: simulate both fills immediately
            up_trade = PaperTrade(
                timestamp=time.time(),
                asset=window.asset,
                market_id=window.market_id,
                question=window.question,
                direction="up",
                entry_price=round(up_ask, 2),
                size_usd=round(half_usd, 2),
                shares=up_shares,
                window_end_ts=window.end_ts,
                spot_delta_pct=0.0,
                open_price=window.open_price,
                market_ask=up_ask,
                token_id=window.up_token_id or "",
            )
            down_trade = PaperTrade(
                timestamp=time.time() + 0.001,  # offset to avoid JSONL collision
                asset=window.asset,
                market_id=window.market_id,
                question=window.question,
                direction="down",
                entry_price=round(down_ask, 2),
                size_usd=round(half_usd, 2),
                shares=down_shares,
                window_end_ts=window.end_ts,
                spot_delta_pct=0.0,
                open_price=window.open_price,
                market_ask=down_ask,
                token_id=window.down_token_id or "",
            )
            self._tracker.record_trade(up_trade, strategy="spread")
            self._tracker.record_trade(down_trade, strategy="spread")
            self._tracker.notify_spread_entry(
                window.asset, up_ask, down_ask, cfg.spread_max_bet_usd, guaranteed_profit_pct,
            )

            log.info(
                "[KILLSHOT] PAPER SPREAD: %s | UP %.0f¢ (%.1f sh) + DOWN %.0f¢ (%.1f sh) | "
                "$%.2f total | net %.1f¢/$",
                window.asset.upper(), up_ask * 100, up_shares,
                down_ask * 100, down_shares,
                cfg.spread_max_bet_usd, guaranteed_profit_pct,
            )
            return True

        # Live mode: batch both legs via Rust executor
        return self._place_spread_orders_batch(window, up_ask, down_ask, half_usd,
                                               up_shares, down_shares)

    def _place_spread_orders_batch(
        self, window: Window, up_ask: float, down_ask: float,
        half_usd: float, up_shares: float, down_shares: float,
    ) -> bool:
        """FOK both spread legs via Rust batch endpoint (POST /orders).

        If batch succeeds partially (one leg fills, other doesn't),
        unwind the filled leg via smart FOK sell at best_bid.
        """
        up_price = min(round(up_ask + 0.01, 2), 0.99)
        down_price = min(round(down_ask + 0.01, 2), 0.99)
        up_sh = max(round(half_usd / up_price, 2), 5.0)
        down_sh = max(round(half_usd / down_price, 2), 5.0)

        # ── Try Rust batch endpoint ──
        if self._rust_http and self._rust_url:
            try:
                resp = self._rust_http.post(
                    f"{self._rust_url}/orders",
                    json=[
                        {"token_id": window.up_token_id, "price": up_price,
                         "size": up_sh, "side": "BUY", "order_type": "FOK", "neg_risk": False},
                        {"token_id": window.down_token_id, "price": down_price,
                         "size": down_sh, "side": "BUY", "order_type": "FOK", "neg_risk": False},
                    ],
                )
                data = resp.json()
                results = data.get("results", [])
                latency = data.get("latency_ms", 0)

                if len(results) == 2:
                    up_ok = results[0].get("success", False)
                    down_ok = results[1].get("success", False)

                    if up_ok and down_ok:
                        up_entry = results[0].get("avg_price", up_price)
                        down_entry = results[1].get("avg_price", down_price)
                        up_actual = results[0].get("total_shares", up_sh)
                        down_actual = results[1].get("total_shares", down_sh)
                        up_oid = results[0].get("order_id", "")
                        down_oid = results[1].get("order_id", "")
                        return self._record_spread_fill(
                            window, up_entry, down_entry, up_actual, down_actual,
                            up_oid, down_oid, half_usd, latency,
                        )

                    # Partial fill — unwind
                    if up_ok and not down_ok:
                        up_actual = results[0].get("total_shares", up_sh)
                        log.warning("[KILLSHOT] Batch: UP filled, DOWN failed — unwinding UP (%.1f sh)", up_actual)
                        self._unwind_orphan_leg(window.up_token_id, up_actual, "UP-batch")
                    elif down_ok and not up_ok:
                        down_actual = results[1].get("total_shares", down_sh)
                        log.warning("[KILLSHOT] Batch: DOWN filled, UP failed — unwinding DOWN (%.1f sh)", down_actual)
                        self._unwind_orphan_leg(window.down_token_id, down_actual, "DOWN-batch")

                    self._traded_windows.pop(window.market_id, None)
                    return False

            except Exception as e:
                log.warning("[KILLSHOT] Rust batch failed — falling back to sequential: %s", str(e)[:100])

        # ── Python fallback: sequential FOK ──
        up_entry, up_actual, up_oid = self._place_live_order(
            window.up_token_id, up_ask, half_usd,
        )
        if up_entry is None:
            log.warning("[KILLSHOT] Spread UP FOK failed — no fill")
            self._traded_windows.pop(window.market_id, None)
            return False

        down_entry, down_actual, down_oid = self._place_live_order(
            window.down_token_id, down_ask, half_usd,
        )
        if down_entry is None:
            log.warning("[KILLSHOT] Spread DOWN FOK failed — unwinding UP orphan (%.1f sh)", up_actual)
            self._unwind_orphan_leg(window.up_token_id, up_actual, "UP-spread")
            self._traded_windows.pop(window.market_id, None)
            return False

        return self._record_spread_fill(
            window, up_entry, down_entry, up_actual, down_actual,
            up_oid, down_oid, half_usd, 0,
        )

    def _record_spread_fill(
        self, window, up_entry, down_entry, up_actual, down_actual,
        up_oid, down_oid, half_usd, latency,
    ) -> bool:
        """Record both spread legs after successful fill."""
        up_trade = PaperTrade(
            timestamp=time.time(),
            asset=window.asset,
            market_id=window.market_id,
            question=window.question,
            direction="up",
            entry_price=up_entry,
            size_usd=round(half_usd, 2),
            shares=up_actual,
            window_end_ts=window.end_ts,
            spot_delta_pct=0.0,
            open_price=window.open_price,
            market_ask=up_entry,
            token_id=window.up_token_id or "",
        )
        down_trade = PaperTrade(
            timestamp=time.time() + 0.001,
            asset=window.asset,
            market_id=window.market_id,
            question=window.question,
            direction="down",
            entry_price=down_entry,
            size_usd=round(half_usd, 2),
            shares=down_actual,
            window_end_ts=window.end_ts,
            spot_delta_pct=0.0,
            open_price=window.open_price,
            market_ask=down_entry,
            token_id=window.down_token_id or "",
        )
        self._tracker.record_trade(up_trade, strategy="spread")
        self._tracker.record_trade(down_trade, strategy="spread")
        net_pct = (1.0 - (up_entry + down_entry)) * 100
        self._tracker.notify_spread_entry(
            window.asset, up_entry, down_entry,
            self._cfg.spread_max_bet_usd, net_pct,
        )

        log.info(
            "[KILLSHOT] LIVE SPREAD FILLED: %s UP@%.0f¢ (%s) + DOWN@%.0f¢ (%s) | $%.2f | net=%.1f¢/$ | %dms",
            window.asset.upper(), up_entry * 100, (up_oid or "?")[:12],
            down_entry * 100, (down_oid or "?")[:12],
            self._cfg.spread_max_bet_usd, net_pct, latency,
        )
        return True

    def _place_gtc_leg(self, token_id: str | None, price: float, shares: float, label: str) -> str | None:
        """Place a single GTC leg. Returns order_id or None on failure."""
        if not token_id:
            return None
        if shares < 5:
            shares = 5.0

        # Try Rust executor
        if self._rust_http and self._rust_url:
            try:
                resp = self._rust_http.post(
                    f"{self._rust_url}/order",
                    json={
                        "token_id": token_id,
                        "price": price,
                        "size": shares,
                        "side": "BUY",
                        "order_type": "GTC",
                        "neg_risk": False,
                    },
                )
                data = resp.json()
                if data.get("success"):
                    return data.get("order_id", "unknown")
            except Exception as e:
                log.warning("[KILLSHOT] Spread %s Rust GTC failed: %s", label, str(e)[:80])

        # Python fallback
        if not self._client:
            return None
        try:
            from py_clob_client.clob_types import OrderArgs, OrderType
            from py_clob_client.order_builder.constants import BUY

            order_args = OrderArgs(price=price, size=shares, side=BUY, token_id=token_id)
            signed_order = self._client.create_order(order_args)
            resp = self._client.post_order(signed_order, OrderType.GTC)
            return resp.get("orderID") or resp.get("id")
        except Exception as e:
            log.error("[KILLSHOT] Spread %s GTC error: %s", label, str(e)[:150])
            return None

    # ── Momentum (early-entry) methods ─────────────────────────

    def _evaluate_momentum(self, window: Window, elapsed: float) -> None:
        """Evaluate a window for early momentum entry (T+30s to T+60s).

        3-signal consensus: previous candle direction, current delta, Binance flow.
        Need >= min_signals aligned in same direction to fire.
        """
        cfg = self._cfg
        asset = window.asset

        # Log momentum zone entry (once per window)
        if window.market_id not in self._momentum_logged:
            self._momentum_logged.add(window.market_id)
            log.info(
                "[KILLSHOT] Momentum zone: %s %s | T+%.0fs | open=$%.2f",
                asset.upper(), window.market_id[:12], elapsed, window.open_price,
            )

        # ── Signal 1: Previous candle direction ──
        prev_price = self._cache.get_price_ago(asset, 5)
        sig1_dir = None
        sig1_delta = 0.0
        if prev_price and window.open_price > 0:
            sig1_delta = (window.open_price - prev_price) / prev_price
            if abs(sig1_delta) >= cfg.momentum_prev_candle_threshold:
                sig1_dir = "up" if sig1_delta > 0 else "down"

        # ── Signal 2: Current candle early delta ──
        current_price, price_age, price_source = self._get_best_price(asset)
        sig2_dir = None
        sig2_delta = 0.0
        if current_price and window.open_price > 0:
            sig2_delta = (current_price - window.open_price) / window.open_price
            if abs(sig2_delta) >= cfg.momentum_confirm_threshold:
                sig2_dir = "up" if sig2_delta > 0 else "down"

        # ── Signal 3: Binance depth flow (bid/ask imbalance as flow proxy) ──
        sig3_dir = None
        if self._binance_feed:
            depth = self._binance_feed.get_depth(asset)
            if depth:
                bids = depth.get("bids", [])
                asks = depth.get("asks", [])
                bid_vol = sum(float(b[0]) * float(b[1]) for b in bids) if bids else 0
                ask_vol = sum(float(a[0]) * float(a[1]) for a in asks) if asks else 0
                total = bid_vol + ask_vol
                if total > 0:
                    imbalance = (bid_vol - ask_vol) / total
                    if abs(imbalance) >= cfg.momentum_flow_min_strength:
                        sig3_dir = "up" if imbalance > 0 else "down"

        # ── Consensus ──
        votes = {"up": 0, "down": 0}
        for sig in (sig1_dir, sig2_dir, sig3_dir):
            if sig:
                votes[sig] += 1

        # Need >= min_signals aligned
        if votes["up"] >= cfg.momentum_min_signals:
            direction = "up"
            vote_count = votes["up"]
        elif votes["down"] >= cfg.momentum_min_signals:
            direction = "down"
            vote_count = votes["down"]
        else:
            return  # No consensus

        # ── Pick target token ──
        if direction == "up":
            target_token = window.up_token_id
        else:
            target_token = window.down_token_id

        if not target_token:
            return

        # ── Check CLOB book for entry price ──
        book, book_src = self._get_book(target_token)
        if not book:
            return

        best_ask = book.get("best_ask", 0) or 0
        if best_ask <= 0:
            return

        # Entry price must be in [entry_price_min, entry_price_max]
        if best_ask < cfg.momentum_entry_price_min or best_ask > cfg.momentum_entry_price_max:
            return

        entry_price = round(best_ask, 2)
        size_usd = cfg.momentum_max_bet_usd
        shares = round(size_usd / entry_price, 2)
        if shares < 1:
            return

        # Mark window as traded
        self._traded_windows[window.market_id] = time.time()

        # ── Execute ──
        if not self._dry_run and self._client and target_token:
            result = self._place_momentum_order(
                target_token, entry_price, size_usd, window,
            )
            if result is None:
                # GTC resting — don't record trade yet, wait for fill
                log.info(
                    "[KILLSHOT] MOMENTUM GTC resting: %s %s | %.0f¢ | $%.2f | "
                    "signals=%d (%s/%s/%s) | T+%.0fs",
                    direction.upper(), asset.upper(), entry_price * 100,
                    size_usd, vote_count,
                    sig1_dir or "-", sig2_dir or "-", sig3_dir or "-",
                    elapsed,
                )
                return
            entry_price, shares, order_id = result
        else:
            order_id = ""

        mode = "LIVE" if not self._dry_run else "PAPER"
        delta = sig2_delta  # Use current delta for the trade record

        trade = PaperTrade(
            timestamp=time.time(),
            asset=asset,
            market_id=window.market_id,
            question=window.question,
            direction=direction,
            entry_price=entry_price,
            size_usd=size_usd,
            shares=shares,
            window_end_ts=window.end_ts,
            spot_delta_pct=round(delta, 6),
            open_price=window.open_price,
            market_bid=book.get("best_bid", 0) or 0,
            market_ask=best_ask,
            token_id=target_token,
        )
        self._tracker.record_trade(trade, strategy="momentum")

        log.info(
            "[KILLSHOT] %s MOMENTUM FIRE: %s %s | entry=%.0f¢ | $%.2f (%.1f shares) | "
            "signals=%d (%s/%s/%s) | T+%.0fs | book=%s",
            mode, direction.upper(), asset.upper(),
            entry_price * 100, size_usd, shares, vote_count,
            sig1_dir or "-", sig2_dir or "-", sig3_dir or "-",
            elapsed, book_src,
        )

    def _place_momentum_order(
        self, token_id: str, price: float, size_usd: float, window: Window,
    ) -> tuple[float, float, str] | None:
        """Place a GTC limit buy order for momentum entry.

        Returns (entry_price, shares, order_id) if instantly filled.
        Returns None if order is resting (stored in _pending_limit_orders).
        """
        shares = round(size_usd / price, 2)
        if shares < 5:
            shares = 5.0

        # ── Try Rust executor (GTC) ──
        if self._rust_http and self._rust_url:
            try:
                resp = self._rust_http.post(
                    f"{self._rust_url}/order",
                    json={
                        "token_id": token_id,
                        "price": price,
                        "size": shares,
                        "side": "BUY",
                        "order_type": "GTC",
                        "neg_risk": False,
                    },
                )
                data = resp.json()
                if data.get("success"):
                    status = data.get("status", "").lower()
                    if status in ("matched", "filled"):
                        return (
                            data.get("avg_price", price),
                            data.get("total_shares", shares),
                            data.get("order_id", "unknown"),
                        )
                    # Resting order
                    order_id = data.get("order_id", "unknown")
                    self._pending_limit_orders[window.market_id] = {
                        "order_id": order_id,
                        "token_id": token_id,
                        "price": price,
                        "shares": shares,
                        "size_usd": size_usd,
                        "placed_at": time.time(),
                        "window": window,
                    }
                    self._save_pending_orders()
                    return None
            except Exception as e:
                log.warning("[KILLSHOT] Rust GTC failed — falling back to Python: %s", str(e)[:100])

        # ── Python fallback (GTC) ──
        try:
            from py_clob_client.clob_types import OrderArgs, OrderType
            from py_clob_client.order_builder.constants import BUY

            order_args = OrderArgs(
                price=price,
                size=shares,
                side=BUY,
                token_id=token_id,
            )
            signed_order = self._client.create_order(order_args)
            resp = self._client.post_order(signed_order, OrderType.GTC)
            order_id = resp.get("orderID") or resp.get("id", "unknown")
            status = (resp.get("status") or "").lower()

            log.info("[KILLSHOT] Momentum GTC response: %s", json.dumps(resp)[:500])

            if status in ("matched", "filled"):
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
                        return (round(total_cost / total_shares, 4), round(total_shares, 2), order_id)
                return (price, shares, order_id)

            # Resting — store for monitoring
            self._pending_limit_orders[window.market_id] = {
                "order_id": order_id,
                "token_id": token_id,
                "price": price,
                "shares": shares,
                "size_usd": size_usd,
                "placed_at": time.time(),
                "window": window,
            }
            self._save_pending_orders()
            return None

        except Exception as e:
            log.error("[KILLSHOT] Momentum GTC order error: %s", str(e)[:200])
            # Remove from traded so kill zone can still try
            self._traded_windows.pop(window.market_id, None)
            return None

    def _check_pending_limit_orders(self, now: float) -> None:
        """Monitor pending GTC limit orders — cancel on timeout, record on fill.

        Handles both momentum (single-leg) and spread (dual-leg) orders.
        """
        if not self._pending_limit_orders:
            return

        to_remove = []

        for market_id, info in self._pending_limit_orders.items():
            age = now - info["placed_at"]

            # Per-type timeout and check interval
            if info.get("type") == "spread":
                timeout_s = self._cfg.spread_fill_timeout_s   # 10s
                check_interval = 2.0
            else:
                timeout_s = self._cfg.momentum_fill_timeout_s  # 120s
                check_interval = 5.0

            last_check = info.get("last_check", 0)
            if now - last_check < check_interval:
                continue
            info["last_check"] = now

            if info.get("type") == "spread":
                self._check_spread_orders(market_id, info, age, timeout_s, to_remove)
            else:
                self._check_momentum_order(market_id, info, age, timeout_s, to_remove)

        for mid in to_remove:
            self._pending_limit_orders.pop(mid, None)
        if to_remove:
            self._save_pending_orders()

    def _check_momentum_order(self, market_id: str, info: dict,
                              age: float, timeout_s: int, to_remove: list) -> None:
        """Check a single-leg momentum GTC order."""
        order_id = info["order_id"]

        if age > timeout_s:
            self._cancel_order(order_id)
            self._traded_windows.pop(market_id, None)
            to_remove.append(market_id)
            log.info(
                "[KILLSHOT] Momentum timeout: cancelling %s after %.0fs",
                order_id[:12], age,
            )
            return

        if self._dry_run:
            return

        filled = self._check_order_fill(order_id)
        if filled:
            window = info["window"]
            trade = PaperTrade(
                timestamp=time.time(),
                asset=window.asset,
                market_id=market_id,
                question=window.question,
                direction="up" if info["token_id"] == window.up_token_id else "down",
                entry_price=info["price"],
                size_usd=info["size_usd"],
                shares=info["shares"],
                window_end_ts=window.end_ts,
                spot_delta_pct=0.0,
                open_price=window.open_price,
                token_id=info["token_id"],
            )
            self._tracker.record_trade(trade, strategy="momentum")
            to_remove.append(market_id)
            log.info(
                "[KILLSHOT] Momentum FILLED: %s %s @ %.0f¢ | $%.2f",
                trade.direction.upper(), window.asset.upper(),
                info["price"] * 100, info["size_usd"],
            )

    def _check_spread_orders(self, market_id: str, info: dict,
                             age: float, timeout_s: int, to_remove: list) -> None:
        """Check a dual-leg spread GTC order pair."""
        up_oid = info["up_order_id"]
        down_oid = info["down_order_id"]

        # Paper mode: just check timeout
        if self._dry_run:
            if age > timeout_s:
                self._traded_windows.pop(market_id, None)
                to_remove.append(market_id)
                log.info("[KILLSHOT] Spread timeout (paper): %.0fs", age)
            return

        up_filled = info.get("up_filled") or self._check_order_fill(up_oid)
        down_filled = info.get("down_filled") or self._check_order_fill(down_oid)

        # Cache fill state so we don't re-check filled legs
        if up_filled:
            info["up_filled"] = True
        if down_filled:
            info["down_filled"] = True

        # ── Both filled — success ──
        if up_filled and down_filled:
            window = info["window"]
            up_trade = PaperTrade(
                timestamp=time.time(),
                asset=window.asset,
                market_id=market_id,
                question=window.question,
                direction="up",
                entry_price=info["up_price"],
                size_usd=round(info["size_usd"] / 2, 2),
                shares=info["up_shares"],
                window_end_ts=window.end_ts,
                spot_delta_pct=0.0,
                open_price=window.open_price,
                market_ask=info["up_price"],
                token_id=info["up_token_id"] or "",
            )
            down_trade = PaperTrade(
                timestamp=time.time() + 0.001,
                asset=window.asset,
                market_id=market_id,
                question=window.question,
                direction="down",
                entry_price=info["down_price"],
                size_usd=round(info["size_usd"] / 2, 2),
                shares=info["down_shares"],
                window_end_ts=window.end_ts,
                spot_delta_pct=0.0,
                open_price=window.open_price,
                market_ask=info["down_price"],
                token_id=info["down_token_id"] or "",
            )
            self._tracker.record_trade(up_trade, strategy="spread")
            self._tracker.record_trade(down_trade, strategy="spread")
            guaranteed_pct = (1.0 - (info["up_price"] + info["down_price"])) * 100
            self._tracker.notify_spread_entry(
                window.asset,
                info["up_price"],
                info["down_price"],
                info["size_usd"],
                guaranteed_pct,
            )
            to_remove.append(market_id)
            log.info(
                "[KILLSHOT] Spread FILLED: %s UP@%.0f¢ + DOWN@%.0f¢ | $%.2f",
                window.asset.upper(), info["up_price"] * 100,
                info["down_price"] * 100, info["size_usd"],
            )
            return

        # ── One filled, other pending — orphan detection ──
        if up_filled or down_filled:
            if "first_fill_at" not in info:
                info["first_fill_at"] = time.time()
            orphan_age = time.time() - info["first_fill_at"]

            if orphan_age < self._cfg.spread_orphan_window_s and age <= timeout_s:
                return  # Still in orphan window — give other leg time

            # Orphan window expired OR full timeout — cancel unfilled, SELL filled
            if up_filled and not down_filled:
                self._cancel_order(down_oid)
                self._unwind_orphan_leg(info["up_token_id"], info["up_shares"], "UP")
                log.warning(
                    "[KILLSHOT] Spread orphan: UP filled, DOWN cancelled — SOLD UP via FOK (%.1fs)",
                    orphan_age,
                )
            else:
                self._cancel_order(up_oid)
                self._unwind_orphan_leg(info["down_token_id"], info["down_shares"], "DOWN")
                log.warning(
                    "[KILLSHOT] Spread orphan: DOWN filled, UP cancelled — SOLD DOWN via FOK (%.1fs)",
                    orphan_age,
                )
            self._traded_windows.pop(market_id, None)
            to_remove.append(market_id)
            return

        # ── Neither filled — check full timeout ──
        if age > timeout_s:
            self._cancel_order(up_oid)
            self._cancel_order(down_oid)
            self._traded_windows.pop(market_id, None)
            to_remove.append(market_id)
            log.info("[KILLSHOT] Spread timeout: cancelling both legs after %.0fs", age)

    def _cancel_order(self, order_id: str) -> bool:
        """Cancel a GTC order via py_clob_client. Retries up to 3 times to avoid orphan on transient failure."""
        if not self._client:
            return False
        last_err = None
        for attempt in range(3):
            try:
                self._client.cancel(order_id)
                return True
            except Exception as e:
                last_err = e
                if attempt < 2:
                    time.sleep(1.0)
        log.warning("[KILLSHOT] Cancel failed for %s after 3 attempts: %s", order_id[:12], str(last_err)[:100])
        return False

    def _unwind_orphan_leg(self, token_id: str, shares: float, label: str) -> bool:
        """Sell orphaned shares via FOK at best_bid (or best_bid - 0.01).

        Uses book to get a fair exit price. Falls back to 1¢ only when no usable bid.
        """
        if not self._cfg.spread_use_fok:
            log.info("[KILLSHOT] Orphan unwind skipped (%s): spread_use_fok=false", label)
            return False
        if not token_id or shares <= 0:
            return False

        # Get best bid from WS book for a fair exit
        price = 0.01  # fallback
        book, _ = self._get_book(token_id)
        if book:
            best_bid = book.get("best_bid", 0) or 0
            if best_bid >= 0.02:
                price = round(best_bid - 0.01, 2)
                log.info("[KILLSHOT] Orphan %s: using best_bid %.0f¢ → sell at %.0f¢",
                         label, best_bid * 100, price * 100)
        if shares < 5:
            shares = 5.0

        # Try Rust executor
        if self._rust_http and self._rust_url:
            try:
                resp = self._rust_http.post(
                    f"{self._rust_url}/order",
                    json={
                        "token_id": token_id,
                        "price": price,
                        "size": shares,
                        "side": "SELL",
                        "order_type": "FOK",
                        "neg_risk": False,
                    },
                )
                data = resp.json()
                if data.get("success"):
                    log.info("[KILLSHOT] Orphan SOLD (%s): %.1f shares via Rust FOK", label, shares)
                    return True
            except Exception as e:
                log.warning("[KILLSHOT] Orphan Rust SELL failed (%s): %s", label, str(e)[:80])

        # Python fallback
        if not self._client:
            return False
        try:
            from py_clob_client.clob_types import OrderArgs, OrderType
            from py_clob_client.order_builder.constants import SELL

            order_args = OrderArgs(price=price, size=shares, side=SELL, token_id=token_id)
            signed_order = self._client.create_order(order_args)
            resp = self._client.post_order(signed_order, OrderType.FOK)
            status = (resp.get("status") or "").lower()
            if status in ("matched", "filled"):
                log.info("[KILLSHOT] Orphan SOLD (%s): %.1f shares via Python FOK", label, shares)
                return True
            else:
                log.warning("[KILLSHOT] Orphan SELL not filled (%s): status=%s", label, status)
                return False
        except Exception as e:
            log.error("[KILLSHOT] Orphan SELL error (%s): %s", label, str(e)[:150])
            return False

    def _check_order_fill(self, order_id: str) -> bool:
        """Check if a GTC order has been filled."""
        if not self._client:
            return False
        try:
            order = self._client.get_order(order_id)
            status = (order.get("status") or "").lower()
            return status in ("matched", "filled")
        except Exception as e:
            log.debug("[KILLSHOT] Order check failed for %s: %s", order_id[:12], str(e)[:80])
            return False

    def _try_rust_order(
        self, token_id: str, price: float, shares: float,
    ) -> tuple[float | None, float, str] | None:
        """Try the Rust executor. Returns (entry_price, shares, order_id) or None on failure."""
        if not self._rust_http or not self._rust_url:
            return None
        try:
            resp = self._rust_http.post(
                f"{self._rust_url}/order",
                json={
                    "token_id": token_id,
                    "price": price,
                    "size": shares,
                    "side": "BUY",
                    "order_type": "FOK",
                    "neg_risk": False,
                },
            )
            data = resp.json()
            latency = data.get("latency_ms", 0)
            if data.get("success"):
                log.info(
                    "[KILLSHOT] Rust executor: %s | %.1fms",
                    data.get("status", "matched"), latency,
                )
                return (
                    data.get("avg_price", price),
                    data.get("total_shares", shares),
                    data.get("order_id", "unknown"),
                )
            else:
                log.warning(
                    "[KILLSHOT] Rust executor rejected: %s | %.1fms",
                    data.get("error", "unknown"), latency,
                )
                return None
        except Exception as e:
            log.warning("[KILLSHOT] Rust executor down — falling back to Python: %s", str(e)[:100])
            return None

    def _place_live_order(
        self, token_id: str, market_ask: float | None, size_usd: float,
    ) -> tuple[float | None, float, str]:
        """Place a FOK buy order. Returns (entry_price, shares, order_id) or (None, 0, '').

        Tries Rust executor first (fast path), falls back to Python py_clob_client.
        FOK (Fill-or-Kill): entire order fills immediately or is cancelled.
        """
        # Cross the spread: ask + 1¢
        if market_ask and market_ask > 0:
            price = round(market_ask + 0.01, 2)
        else:
            price = 0.90  # fallback
        price = min(price, 0.99)

        shares = round(size_usd / price, 2)
        if shares < 5:
            shares = 5.0
            size_usd = round(shares * price, 2)
            log.info("[KILLSHOT] Bumped to min 5 shares ($%.2f)", size_usd)

        # ── Try Rust executor (fast path) ──
        rust_result = self._try_rust_order(token_id, price, shares)
        if rust_result is not None:
            return rust_result

        # ── Python fallback ──
        try:
            from py_clob_client.clob_types import OrderArgs, OrderType
            from py_clob_client.order_builder.constants import BUY

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

            log.info("[KILLSHOT] CLOB response (Python): %s", json.dumps(resp)[:500])

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
        # Clean stale pending limit orders (shouldn't survive timeout, but safety net)
        self._pending_limit_orders = {
            k: v for k, v in self._pending_limit_orders.items()
            if v.get("placed_at", 0) > cutoff
        }
        self._save_pending_orders()
        removed = before - len(self._traded_windows)
        if removed:
            log.debug("[KILLSHOT] Cleaned %d expired window IDs", removed)
