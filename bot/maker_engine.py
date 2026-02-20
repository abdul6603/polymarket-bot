"""MakerEngine — Two-sided maker liquidity for Polymarket crypto markets.

Posts GTC limit orders with post_only=True on both sides of the spread,
capturing maker rebates (zero taker fee). Quotes are refreshed every tick
and inventory is managed to prevent one-sided exposure.

Disabled by default — enable via MAKER_ENABLED=true env var.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY, SELL

from bot.config import Config
from bot.price_cache import PriceCache
from bot.v2_tools import is_emergency_stopped

log = logging.getLogger("maker")

DATA_DIR = Path(__file__).parent.parent / "data"
STATE_FILE = DATA_DIR / "maker_state.json"


@dataclass
class MakerQuote:
    """A live maker order on one side of the book."""
    order_id: str
    token_id: str
    side: str          # "BUY" or "SELL"
    price: float
    size: float        # in tokens
    size_usd: float
    placed_at: float


@dataclass
class InventoryPosition:
    """Net inventory for a single token (asset side)."""
    token_id: str
    asset: str
    net_shares: float = 0.0     # positive = long, negative = short
    cost_basis: float = 0.0     # total USD cost
    fills_today: int = 0
    estimated_rebate: float = 0.0


class MakerEngine:
    """Two-sided GTC maker quoting engine for crypto Up/Down markets."""

    def __init__(self, cfg: Config, client: ClobClient | None, price_cache: PriceCache):
        self.cfg = cfg
        self.client = client
        self._cache = price_cache

        # Config (all from env vars with safe defaults)
        import os
        self.enabled = os.getenv("MAKER_ENABLED", "false").lower() in ("true", "1", "yes")
        self.quote_size_usd = float(os.getenv("MAKER_QUOTE_SIZE_USD", "8.0"))
        self.max_inventory_usd = float(os.getenv("MAKER_MAX_INVENTORY_USD", "30.0"))
        self.max_total_exposure = float(os.getenv("MAKER_MAX_TOTAL_EXPOSURE", "60.0"))
        self.tick_interval_s = float(os.getenv("MAKER_TICK_INTERVAL_S", "5.0"))

        # Spread params
        self.min_half_spread = 0.005    # 0.5 cent minimum half-spread
        self.max_half_spread = 0.03     # 3 cent max half-spread (extreme vol)
        self.base_half_spread = 0.01    # 1 cent default half-spread

        # State
        self._active_quotes: list[MakerQuote] = []
        self._inventory: dict[str, InventoryPosition] = {}  # token_id -> position
        self._last_heartbeat = 0.0
        self._fills_today = 0
        self._estimated_rebate_today = 0.0
        self._last_state_write = 0.0

    def compute_fair_value(self, asset: str, implied_price: float | None) -> float | None:
        """Blend Binance spot momentum with Polymarket implied price.

        Fair value = 60% Polymarket implied + 40% Binance momentum signal.
        This prevents quoting at stale prices when spot moves fast.
        """
        if implied_price is None or not (0.05 < implied_price < 0.95):
            return None

        binance_price = self._cache.get_price(asset)
        if binance_price is None or binance_price <= 0:
            return implied_price

        # Get 3-min momentum from Binance
        price_3m = self._cache.get_price_ago(asset, 3)
        if price_3m and price_3m > 0:
            momentum = (binance_price - price_3m) / price_3m
            # Convert momentum to probability shift: +1% move ≈ +0.02 prob shift
            momentum_shift = momentum * 2.0
            momentum_fair = implied_price + momentum_shift
            momentum_fair = max(0.05, min(0.95, momentum_fair))
        else:
            momentum_fair = implied_price

        # Blend: 60% market, 40% momentum
        fair = 0.6 * implied_price + 0.4 * momentum_fair
        return max(0.05, min(0.95, round(fair, 4)))

    def compute_spread(self, asset: str, token_id: str, regime_label: str = "neutral") -> float:
        """Dynamic half-spread based on volatility regime and inventory skew.

        - Calm market: tighter spread (more fills)
        - High vol / extreme regime: wider spread (protect against adverse selection)
        - Inventory skew: shift quotes to encourage fills that reduce inventory
        """
        half_spread = self.base_half_spread

        # Regime-based widening
        regime_mult = {
            "extreme_fear": 1.8,
            "fear": 1.3,
            "neutral": 1.0,
            "greed": 1.2,
            "extreme_greed": 1.6,
        }.get(regime_label, 1.0)
        half_spread *= regime_mult

        # ATR-based widening (if available)
        candles = self._cache.get_candles(asset, 30)
        if len(candles) >= 14:
            try:
                from bot.indicators import atr
                atr_val = atr(candles)
                if atr_val and atr_val > 0.003:
                    # Scale spread with volatility
                    half_spread *= min(2.0, 1.0 + (atr_val - 0.003) * 50)
            except Exception:
                pass

        return max(self.min_half_spread, min(self.max_half_spread, half_spread))

    def _inventory_skew(self, token_id: str) -> float:
        """Inventory skew: shift quotes to reduce one-sided exposure.

        Returns a price offset:
        - Positive: we're long → lower buy price, raise sell price (encourage sells to us)
        - Negative: we're short → raise buy price, lower sell price
        """
        inv = self._inventory.get(token_id)
        if not inv or inv.net_shares == 0:
            return 0.0

        # Scale skew: max shift of 1 cent per $15 of inventory
        inv_usd = abs(inv.net_shares * inv.cost_basis / max(abs(inv.net_shares), 1))
        skew_frac = min(1.0, inv_usd / self.max_inventory_usd)
        skew = 0.01 * skew_frac
        return skew if inv.net_shares > 0 else -skew

    def refresh_quotes(
        self,
        token_id: str,
        asset: str,
        fair_value: float,
        half_spread: float,
    ) -> list[MakerQuote]:
        """Cancel stale quotes and post new two-sided GTC limit orders.

        Returns list of newly placed quotes.
        """
        if not self.client or self.cfg.dry_run:
            return self._dry_run_quotes(token_id, asset, fair_value, half_spread)

        # Cancel existing quotes for this token
        self._cancel_quotes_for_token(token_id)

        skew = self._inventory_skew(token_id)
        buy_price = round(max(0.01, fair_value - half_spread - skew), 2)
        sell_price = round(min(0.99, fair_value + half_spread - skew), 2)

        # Don't quote if spread is too tight (would cross)
        if buy_price >= sell_price:
            log.debug("[MAKER] Quotes would cross (buy=%.2f sell=%.2f), skipping", buy_price, sell_price)
            return []

        # Check total exposure
        total_exposure = sum(
            abs(inv.net_shares * inv.cost_basis / max(abs(inv.net_shares), 1))
            for inv in self._inventory.values()
            if inv.net_shares != 0
        )
        if total_exposure >= self.max_total_exposure:
            log.info("[MAKER] Total exposure $%.2f >= $%.2f cap, skipping new quotes",
                     total_exposure, self.max_total_exposure)
            return []

        # Per-asset inventory check
        inv = self._inventory.get(token_id)
        if inv:
            inv_usd = abs(inv.net_shares * inv.cost_basis / max(abs(inv.net_shares), 1))
            if inv_usd >= self.max_inventory_usd:
                log.info("[MAKER] %s inventory $%.2f >= $%.2f cap, skipping",
                         asset.upper(), inv_usd, self.max_inventory_usd)
                return []

        new_quotes = []

        # BUY side
        buy_size = self.quote_size_usd / buy_price
        try:
            buy_args = OrderArgs(
                price=buy_price,
                size=buy_size,
                side=BUY,
                token_id=token_id,
            )
            signed = self.client.create_order(buy_args)
            resp = self.client.post_order(signed, OrderType.GTC, post_only=True)
            oid = resp.get("orderID") or resp.get("id", "")
            if oid:
                q = MakerQuote(
                    order_id=oid, token_id=token_id, side="BUY",
                    price=buy_price, size=buy_size,
                    size_usd=self.quote_size_usd, placed_at=time.time(),
                )
                self._active_quotes.append(q)
                new_quotes.append(q)
                log.info("[MAKER] BUY  %s @ $%.3f  (%.1f tokens, $%.1f)",
                         asset.upper(), buy_price, buy_size, self.quote_size_usd)
        except Exception as e:
            log.debug("[MAKER] BUY order failed: %s", str(e)[:100])

        # SELL side
        sell_size = self.quote_size_usd / sell_price
        try:
            sell_args = OrderArgs(
                price=sell_price,
                size=sell_size,
                side=SELL,
                token_id=token_id,
            )
            signed = self.client.create_order(sell_args)
            resp = self.client.post_order(signed, OrderType.GTC, post_only=True)
            oid = resp.get("orderID") or resp.get("id", "")
            if oid:
                q = MakerQuote(
                    order_id=oid, token_id=token_id, side="SELL",
                    price=sell_price, size=sell_size,
                    size_usd=self.quote_size_usd, placed_at=time.time(),
                )
                self._active_quotes.append(q)
                new_quotes.append(q)
                log.info("[MAKER] SELL %s @ $%.3f  (%.1f tokens, $%.1f)",
                         asset.upper(), sell_price, sell_size, self.quote_size_usd)
        except Exception as e:
            log.debug("[MAKER] SELL order failed: %s", str(e)[:100])

        return new_quotes

    def _dry_run_quotes(
        self, token_id: str, asset: str, fair_value: float, half_spread: float
    ) -> list[MakerQuote]:
        """Simulated quote placement for DRY_RUN mode."""
        skew = self._inventory_skew(token_id)
        buy_price = round(max(0.01, fair_value - half_spread - skew), 2)
        sell_price = round(min(0.99, fair_value + half_spread - skew), 2)

        if buy_price >= sell_price:
            return []

        now = time.time()
        quotes = []
        for side, price in [("BUY", buy_price), ("SELL", sell_price)]:
            size = self.quote_size_usd / price
            q = MakerQuote(
                order_id=f"dry-maker-{side.lower()}-{int(now)}",
                token_id=token_id, side=side,
                price=price, size=size,
                size_usd=self.quote_size_usd, placed_at=now,
            )
            quotes.append(q)

        log.info("[MAKER-DRY] %s BUY@%.3f / SELL@%.3f (fair=%.3f spread=%.3f skew=%.4f)",
                 asset.upper(), buy_price, sell_price, fair_value, half_spread * 2, skew)
        # Replace active quotes for this token
        self._active_quotes = [
            q for q in self._active_quotes if q.token_id != token_id
        ] + quotes
        return quotes

    def _cancel_quotes_for_token(self, token_id: str) -> None:
        """Cancel all active quotes for a specific token."""
        to_cancel = [q for q in self._active_quotes if q.token_id == token_id]
        if not to_cancel or not self.client:
            self._active_quotes = [q for q in self._active_quotes if q.token_id != token_id]
            return

        order_ids = [q.order_id for q in to_cancel]
        try:
            self.client.cancel_orders(order_ids)
        except Exception as e:
            log.debug("[MAKER] Cancel failed: %s", str(e)[:100])

        self._active_quotes = [q for q in self._active_quotes if q.token_id != token_id]

    def send_heartbeat(self) -> None:
        """Send heartbeat via SDK — auto-cancels all orders if bot dies."""
        now = time.time()
        if now - self._last_heartbeat < 30:
            return
        self._last_heartbeat = now

        if not self.client:
            return
        try:
            self.client.post_heartbeat(heartbeat_id=None)
            log.debug("[MAKER] Heartbeat sent")
        except Exception as e:
            log.warning("[MAKER] Heartbeat failed: %s", str(e)[:100])

    def cancel_all(self) -> None:
        """Cancel all active maker quotes (shutdown / emergency)."""
        if self.client and self._active_quotes:
            try:
                order_ids = [q.order_id for q in self._active_quotes]
                self.client.cancel_orders(order_ids)
                log.info("[MAKER] Cancelled %d quotes on shutdown", len(order_ids))
            except Exception as e:
                log.warning("[MAKER] Bulk cancel failed: %s", str(e)[:100])
        self._active_quotes.clear()

    def tick(self, markets: list[dict], regime_label: str = "neutral") -> None:
        """Main maker tick: refresh quotes for all eligible markets.

        Args:
            markets: List of market dicts with tokens, asset, timeframe info.
            regime_label: Current market regime for spread computation.
        """
        if not self.enabled:
            return

        # Emergency stop check
        if is_emergency_stopped():
            self.cancel_all()
            return

        # Heartbeat for auto-cancellation safety net
        self.send_heartbeat()

        quotes_placed = 0
        for mkt in markets:
            tokens = mkt.get("tokens", [])
            asset = mkt.get("asset", "bitcoin")
            up_token = ""

            for t in tokens:
                outcome = (t.get("outcome") or "").lower()
                if outcome in ("up", "yes"):
                    up_token = t.get("token_id", "")
                    break

            if not up_token:
                continue

            # Get implied price from market data
            implied_price = None
            for t in tokens:
                if t.get("token_id") == up_token:
                    p = t.get("price")
                    if p:
                        implied_price = float(p)
                    break

            fair = self.compute_fair_value(asset, implied_price)
            if fair is None:
                continue

            half_spread = self.compute_spread(asset, up_token, regime_label)

            new = self.refresh_quotes(up_token, asset, fair, half_spread)
            quotes_placed += len(new)

        if quotes_placed > 0:
            log.info("[MAKER] Refreshed %d quotes across markets", quotes_placed)

        # Write state for dashboard
        self._write_state()

    def _write_state(self) -> None:
        """Write current maker state to JSON for dashboard consumption."""
        now = time.time()
        if now - self._last_state_write < 5:
            return
        self._last_state_write = now

        state = {
            "enabled": self.enabled,
            "timestamp": now,
            "active_quotes": [
                {
                    "order_id": q.order_id,
                    "token_id": q.token_id[:16],
                    "side": q.side,
                    "price": q.price,
                    "size_usd": round(q.size_usd, 2),
                    "age_s": round(now - q.placed_at),
                }
                for q in self._active_quotes
            ],
            "inventory": {
                tid: {
                    "asset": inv.asset,
                    "net_shares": round(inv.net_shares, 2),
                    "fills_today": inv.fills_today,
                    "estimated_rebate": round(inv.estimated_rebate, 4),
                }
                for tid, inv in self._inventory.items()
            },
            "config": {
                "quote_size_usd": self.quote_size_usd,
                "max_inventory_usd": self.max_inventory_usd,
                "max_total_exposure": self.max_total_exposure,
                "tick_interval_s": self.tick_interval_s,
            },
            "stats": {
                "fills_today": self._fills_today,
                "estimated_rebate_today": round(self._estimated_rebate_today, 4),
                "active_quote_count": len(self._active_quotes),
            },
        }

        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            STATE_FILE.write_text(json.dumps(state, indent=2))
        except Exception:
            pass

    def get_status(self) -> dict:
        """Return current maker engine status for API/dashboard."""
        now = time.time()
        total_inv_usd = 0.0
        for inv in self._inventory.values():
            if inv.net_shares != 0:
                total_inv_usd += abs(inv.net_shares * inv.cost_basis / max(abs(inv.net_shares), 1))

        return {
            "enabled": self.enabled,
            "active_quotes": len(self._active_quotes),
            "total_inventory_usd": round(total_inv_usd, 2),
            "fills_today": self._fills_today,
            "estimated_rebate_today": round(self._estimated_rebate_today, 4),
            "quote_size_usd": self.quote_size_usd,
            "max_inventory_usd": self.max_inventory_usd,
            "max_total_exposure": self.max_total_exposure,
        }
