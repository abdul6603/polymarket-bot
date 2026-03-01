"""MakerEngine V2 — Safe, Inventory-Aware Maker for Polymarket crypto markets.

Two-sided maker quoting: BUY YES + BUY NO (never SELL — avoids balance errors).
All positions tracked via on-chain sync (InventoryManager is source of truth).
Safety: per-market exposure caps, imbalance limits, daily loss circuit breaker.

MAKER_DRY_RUN=true by default — only Jordan flips to false.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
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
    asset: str         # e.g. "bitcoin", "ethereum"
    side: str          # "BUY_YES" or "BUY_NO"
    price: float
    size: float        # in tokens
    size_usd: float
    placed_at: float


# ── InventoryManager ─────────────────────────────────────────


class InventoryManager:
    """On-chain-synced inventory tracker. Source of truth for all position data.

    Positions dict: token_id -> {outcome, size, avg_price, market_title, cur_price}
    """

    def __init__(self, wallet_address: str):
        self._wallet = wallet_address
        self._positions: dict[str, dict] = {}
        self._last_sync = 0.0
        self._sync_interval = 30.0
        self._inventory_file = DATA_DIR / "maker_inventory.json"
        self._load_from_disk()
        self.sync()

    def sync(self) -> None:
        """Fetch positions from Polymarket data API. OVERWRITES local state."""
        import urllib.request
        try:
            if not self._wallet:
                log.warning("[INVENTORY] No wallet address — cannot sync")
                return
            url = (
                f"https://data-api.polymarket.com/positions"
                f"?user={self._wallet.lower()}&limit=500&sizeThreshold=0.5"
            )
            req = urllib.request.Request(url, headers={"User-Agent": "MakerV2/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                positions = json.loads(resp.read().decode())

            if not isinstance(positions, list):
                return

            # OVERWRITE — on-chain is the only truth
            new_positions: dict[str, dict] = {}
            for pos in positions:
                size = float(pos.get("size", 0))
                if size < 0.5:
                    continue
                tid = pos.get("asset", "") or pos.get("token_id", "")
                if not tid:
                    continue
                cur_price = float(pos.get("curPrice", 0) or pos.get("cur_price", 0))
                if cur_price <= 0:
                    continue  # dead/resolved market
                avg_price = float(pos.get("avgPrice", 0) or pos.get("avg_price", 0))
                new_positions[tid] = {
                    "outcome": (pos.get("outcome") or "unknown").lower(),
                    "size": size,
                    "avg_price": avg_price if avg_price > 0 else cur_price,
                    "market_title": (pos.get("title") or pos.get("market_title") or "unknown")[:30],
                    "cur_price": cur_price,
                }

            self._positions = new_positions
            self._last_sync = time.time()
            self._save_to_disk()

            if new_positions:
                total = sum(p["size"] * p["avg_price"] for p in new_positions.values())
                log.info("[INVENTORY] Synced %d on-chain positions (~$%.2f)", len(new_positions), total)
            else:
                log.debug("[INVENTORY] No live on-chain positions")
        except Exception as e:
            log.warning("[INVENTORY] Sync failed: %s", str(e)[:150])

    def sync_if_stale(self) -> None:
        """Re-sync if more than 30s since last sync."""
        if time.time() - self._last_sync > self._sync_interval:
            self.sync()

    def update_from_fill(self, token_id: str, size: float, price: float, side: str) -> None:
        """Fast local update on confirmed fill. Next sync() overwrites with on-chain truth."""
        pos = self._positions.get(token_id)
        if side in (BUY, "BUY_YES", "BUY_NO"):
            if pos:
                total_cost = pos["avg_price"] * pos["size"] + price * size
                pos["size"] += size
                pos["avg_price"] = total_cost / pos["size"] if pos["size"] > 0 else price
            else:
                self._positions[token_id] = {
                    "outcome": "unknown",
                    "size": size,
                    "avg_price": price,
                    "market_title": "fill",
                    "cur_price": price,
                }
        elif side == SELL:
            if pos:
                pos["size"] = max(0, pos["size"] - size)
                if pos["size"] < 0.5:
                    self._positions.pop(token_id, None)
        self._save_to_disk()

    def get_position(self, token_id: str) -> dict | None:
        """Returns {outcome, size, avg_price, market_title, cur_price} or None."""
        return self._positions.get(token_id)

    def can_sell(self, token_id: str, qty: float) -> bool:
        """True if we hold >= qty shares of this token on-chain."""
        pos = self._positions.get(token_id)
        return pos is not None and pos["size"] >= qty

    def get_market_exposure(self, up_token: str, down_token: str) -> dict:
        """Returns {up_size, down_size, imbalance_pct, total_usd} for a market pair."""
        up_pos = self._positions.get(up_token)
        dn_pos = self._positions.get(down_token)
        up_usd = up_pos["size"] * up_pos["avg_price"] if up_pos else 0.0
        dn_usd = dn_pos["size"] * dn_pos["avg_price"] if dn_pos else 0.0
        total = up_usd + dn_usd
        imbalance = max(up_usd, dn_usd) / total if total > 0 else 0.0
        return {
            "up_size": up_usd,
            "down_size": dn_usd,
            "imbalance_pct": imbalance,
            "total_usd": total,
        }

    def get_total_exposure(self) -> float:
        """Sum of all live position values in USD."""
        return sum(p["size"] * p["avg_price"] for p in self._positions.values())

    def get_all_positions(self) -> dict[str, dict]:
        """Return copy of all positions."""
        return dict(self._positions)

    def _load_from_disk(self) -> None:
        """Load from maker_inventory.json (fallback if API down on startup)."""
        try:
            if self._inventory_file.exists():
                data = json.loads(self._inventory_file.read_text())
                if isinstance(data, dict) and data:
                    self._positions = data
                    log.info("[INVENTORY] Loaded %d positions from disk (fallback)", len(data))
        except Exception as e:
            log.warning("[INVENTORY] Disk load failed: %s", str(e)[:100])

    def _save_to_disk(self) -> None:
        """Persist current state to maker_inventory.json."""
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            self._inventory_file.write_text(json.dumps(self._positions, indent=2))
        except Exception:
            pass


# ── MakerEngine ──────────────────────────────────────────────


class MakerEngine:
    """Two-sided GTC maker quoting engine for crypto Up/Down markets.

    V2: Inventory-aware, on-chain synced, with per-market exposure caps
    and automatic SELL->BUY conversion to avoid balance errors.
    """

    def __init__(self, cfg: Config, client: ClobClient | None, price_cache: PriceCache):
        self.cfg = cfg
        self.client = client
        self._cache = price_cache

        # Config
        self.enabled = os.getenv("MAKER_ENABLED", "false").lower() in ("true", "1", "yes")
        self.quote_size_usd = float(os.getenv("MAKER_QUOTE_SIZE_USD", "5.0"))
        self.tick_interval_s = float(os.getenv("MAKER_TICK_INTERVAL_S", "5.0"))

        # Spread params (tightened Feb 28 2026 for competitive fills)
        self.min_half_spread = 0.025  # 5c total floor
        self.max_half_spread = 0.08   # 16c total ceiling
        self.base_half_spread = 0.02  # 4c total base (was 6c)

        # Inventory Manager — on-chain synced, source of truth
        wallet = cfg.funder_address or os.getenv("FUNDER_ADDRESS", "")
        self._inv_mgr = InventoryManager(wallet)

        # Active quotes on the book
        self._active_quotes: list[MakerQuote] = []

        # Heartbeat
        self._last_heartbeat = 0.0

        # P&L tracking
        self._fills_today = 0
        self._estimated_rebate_today = 0.0
        self._session_pnl = 0.0
        self._total_spread_captured = 0.0
        self._resolution_losses = 0.0
        self._fills_log: list[dict] = []
        self._kill_reason: str | None = None
        self._token_session_pnl: dict[str, float] = {}  # per-token PnL for circuit breaker

        # Fill rate tracking: quotes placed vs fills received
        self._quotes_placed = 0
        self._quotes_filled = 0
        self._last_fill_rate_log = 0.0

        # HARD BANKROLL CAP: tracks total USD committed by maker (persists across restarts)
        self._committed_file = Path(__file__).parent.parent / "data" / "maker_committed.json"
        self._maker_total_committed = self._load_committed()

        # Early profit-take cooldown (token_id -> last attempt timestamp)
        self._profit_take_cooldown: dict[str, float] = {}

        # Fair value + TTR caches
        self._last_fair: dict[str, float] = {}
        self._market_remaining: dict[str, float] = {}
        self._last_state_write = 0.0

        # Opposite-token mapping: up_token -> down_token
        self._opposite_token: dict[str, str] = {}
        # Token -> asset name mapping (for dashboard: "bitcoin", "ethereum", etc.)
        self._token_asset: dict[str, str] = {}
        # Token -> market category (for category-specific bankroll)
        self._token_category: dict[str, str] = {}

        # Shared balance manager
        self._balance_mgr = None
        try:
            import sys as _bm_sys
            _bm_sys.path.insert(0, str(Path.home() / "shared"))
            from balance_manager import BalanceManager
            self._balance_mgr = BalanceManager("maker")
            self._balance_mgr.register(float(os.getenv("MAKER_ALLOCATION_WEIGHT", "1")))
        except Exception:
            pass

        # Sync open orders from CLOB
        self._sync_open_orders()

        if self.enabled:
            log.info(
                "[MAKER V2] Initialized: bankroll=$%.0f, dry_run=%s, quote=$%.0f, "
                "max_imbalance=%.0f%%, daily_loss=%.0f%%",
                cfg.maker_bankroll_usd, cfg.maker_dry_run, self.quote_size_usd,
                cfg.maker_max_imbalance * 100, cfg.maker_daily_loss_pct * 100,
            )

    def _load_committed(self) -> float:
        """Load total committed USD from disk (persists across restarts)."""
        try:
            if self._committed_file.exists():
                data = json.loads(self._committed_file.read_text())
                val = float(data.get("total_committed", 0))
                log.info("[MAKER] Loaded committed: $%.2f from disk", val)
                return val
        except Exception:
            pass
        return 0.0

    def _save_committed(self) -> None:
        """Persist total committed to disk."""
        try:
            self._committed_file.write_text(json.dumps({
                "total_committed": round(self._maker_total_committed, 2),
                "updated": time.time(),
            }))
        except Exception:
            pass

    def reset_committed(self, amount: float = 0.0) -> None:
        """Reset committed tracker (call when positions resolve/settle)."""
        self._maker_total_committed = amount
        self._save_committed()
        log.info("[MAKER] Committed reset to $%.2f", amount)

    def _reconcile_committed(self) -> None:
        """Auto-release committed capital when positions resolve/get claimed."""
        on_chain = self._inv_mgr.get_total_exposure()
        pending = sum(q.size_usd for q in self._active_quotes)
        actual_used = on_chain + pending
        if self._maker_total_committed > actual_used + 5.0:
            old = self._maker_total_committed
            self._maker_total_committed = max(0, actual_used)
            self._save_committed()
            log.info("[MAKER] Capital recycled: committed $%.0f → $%.0f (freed $%.0f)",
                     old, self._maker_total_committed, old - self._maker_total_committed)

    def _sync_open_orders(self) -> None:
        """Sync open orders from CLOB on startup so we track what's already live."""
        if not self.client or self.cfg.maker_dry_run:
            return
        try:
            resp = self.client.get_orders()
            orders = resp if isinstance(resp, list) else resp.get("data", [])
            loaded = 0
            for order in orders:
                oid = order.get("id") or order.get("orderID", "")
                status = (order.get("status") or "").lower()
                if status not in ("live", "open", "active"):
                    continue
                tid = order.get("asset_id") or order.get("token_id", "")
                side = (order.get("side") or "").upper()
                price = float(order.get("price", 0))
                size = float(order.get("original_size") or order.get("size", 0))
                if not tid or not oid:
                    continue
                q = MakerQuote(
                    order_id=oid, token_id=tid, asset="unknown",
                    side=side, price=price, size=size,
                    size_usd=round(price * size, 2), placed_at=time.time(),
                )
                self._active_quotes.append(q)
                loaded += 1
            if loaded:
                log.info("[MAKER] Synced %d open orders from CLOB", loaded)
        except Exception as e:
            log.warning("[MAKER] Open order sync failed: %s", str(e)[:100])

    # ── Fair Value & Spread ──────────────────────────────────

    def compute_fair_value(self, asset: str, implied_price: float | None, remaining_s: float = 9999) -> float | None:
        """Blend Binance spot momentum with Polymarket implied price.

        Fair value = 60% Polymarket implied + 40% Binance momentum signal.
        When TTR < 5 min, blend in Chainlink oracle price (50% weight) since
        that's what the market actually resolves against.
        """
        if implied_price is None or not (0.05 < implied_price < 0.95):
            return None

        binance_price = self._cache.get_price(asset)
        if binance_price is None or binance_price <= 0:
            return implied_price

        price_1m = self._cache.get_price_ago(asset, 1)
        if price_1m and price_1m > 0:
            momentum = (binance_price - price_1m) / price_1m
            momentum_shift = momentum * 3.0  # amplify (1-min moves are smaller)
            momentum_fair = implied_price + momentum_shift
            momentum_fair = max(0.05, min(0.95, momentum_fair))
        else:
            momentum_fair = implied_price

        fair = 0.6 * implied_price + 0.4 * momentum_fair

        # Near resolution: blend Chainlink oracle (what market actually resolves against)
        if remaining_s < 300:
            chainlink_price = self._cache.get_chainlink_price(asset)
            if chainlink_price and binance_price:
                # Chainlink divergence from Binance as a signal
                divergence = (chainlink_price - binance_price) / binance_price
                # Weight Chainlink more as TTR decreases (50% at 5 min, 80% at 1 min)
                cl_weight = min(0.8, 0.5 + 0.3 * (1.0 - remaining_s / 300))
                chainlink_shift = divergence * cl_weight * 2.0
                fair += chainlink_shift
                fair = max(0.05, min(0.95, fair))

        return max(0.05, min(0.95, round(fair, 4)))

    def compute_spread(self, asset: str, token_id: str, regime_label: str = "neutral", book_spread: float | None = None) -> float:
        """Dynamic half-spread based on volatility regime and inventory skew."""
        half_spread = self.base_half_spread

        # For markets with real book spreads, undercut proportionally
        if book_spread is not None and book_spread > 0.01:
            if book_spread < 0.04:  # tight competitive book
                half_spread = book_spread * 0.50  # more aggressive
            else:
                half_spread = book_spread * 0.35
            half_spread = max(0.005, min(0.04, half_spread))  # 0.5-4 cent range
            return half_spread  # skip ATR-based calculation (no candle data for non-crypto)

        regime_mult = {
            "extreme_fear": 1.8,
            "fear": 1.3,
            "neutral": 1.0,
            "greed": 1.2,
            "extreme_greed": 1.6,
        }.get(regime_label, 1.0)
        half_spread *= regime_mult

        candles = self._cache.get_candles(asset, 30)
        if len(candles) >= 14:
            try:
                from bot.indicators import atr
                atr_val = atr(candles)
                if atr_val and atr_val > 0.003:
                    half_spread *= min(2.0, 1.0 + (atr_val - 0.003) * 50)
            except Exception:
                pass

        return max(self.min_half_spread, min(self.max_half_spread, half_spread))

    def _adverse_selection_guard(self, asset: str) -> tuple[bool, bool]:
        """Returns (skip_buy_yes, skip_buy_no) based on 1-min Binance momentum."""
        price_now = self._cache.get_price(asset)
        price_1m = self._cache.get_price_ago(asset, 1)
        if not price_now or not price_1m or price_1m <= 0:
            return False, False
        pct_move = (price_now - price_1m) / price_1m
        THRESHOLD = 0.0015  # 0.15% in 1 min = fast move
        if pct_move > THRESHOLD:
            return False, True   # skip BUY_NO (price surging up)
        if pct_move < -THRESHOLD:
            return True, False   # skip BUY_YES (price crashing down)
        return False, False

    def _inventory_skew(self, up_token: str, down_token: str) -> float:
        """Inventory imbalance skew. Shifts quotes to reduce one-sided exposure.

        Positive skew = long YES heavy -> lower buy YES, encourage buy NO.
        """
        exposure = self._inv_mgr.get_market_exposure(up_token, down_token)
        if exposure["total_usd"] < 1.0:
            return 0.0

        max_per_market = max(self.quote_size_usd, 20.0)

        # Positive = more YES than NO -> lower BUY YES price, raise BUY NO attractiveness
        imbalance_usd = exposure["up_size"] - exposure["down_size"]
        skew_frac = min(1.0, abs(imbalance_usd) / max_per_market)
        skew = 0.04 * skew_frac  # max 4c shift (half the base spread)

        return skew if imbalance_usd > 0 else -skew

    # ── Order Validation ─────────────────────────────────────

    def validate_order(self, market_id: str, up_token: str, down_token: str,
                       side: str, token_id: str, size: float, price: float) -> dict:
        """Validate an order against all safety rules.

        Returns: {"action": "place"|"convert"|"block", "side": str, "token_id": str,
                  "size": float, "price": float, "reason": str}
        """
        # 1. Sync inventory if stale
        self._inv_mgr.sync_if_stale()

        # 2. If SELL and can't sell -> convert to BUY opposite token
        if side == SELL:
            if not self._inv_mgr.can_sell(token_id, size):
                opp_token = down_token if token_id == up_token else up_token
                converted_price = round(1.0 - price, 4)
                log.info("[MAKER] SELL->BUY converted: no %s tokens, buying opposite", token_id[:12])
                return {
                    "action": "convert", "side": BUY, "token_id": opp_token,
                    "size": size, "price": converted_price,
                    "reason": "auto-convert: no tokens to sell",
                }

        # 3. HARD BANKROLL CAP — committed + pending orders must not exceed bankroll
        bankroll = self.cfg.maker_bankroll_usd
        order_cost = size * price if size > 0 else self.quote_size_usd
        # Count pending GTC orders as reserved (they lock USDC on the CLOB)
        pending_usd = sum(q.size_usd for q in self._active_quotes)
        total_used = self._maker_total_committed + pending_usd
        remaining = bankroll - total_used
        if remaining <= 0 or order_cost > remaining + 1.0:  # $1 tolerance
            return {"action": "block", "side": side, "token_id": token_id,
                    "size": size, "price": price,
                    "reason": f"bankroll exhausted (${self._maker_total_committed:.0f} filled + ${pending_usd:.0f} pending = ${total_used:.0f}/${bankroll:.0f})"}

        # 3b. TOTAL EXPOSURE CAP — on-chain positions must not exceed max_total_exposure
        total_exposure = self._inv_mgr.get_total_exposure()
        max_exposure = self.cfg.maker_max_total_exposure
        if max_exposure > 0 and total_exposure + order_cost > max_exposure + 1.0:
            return {"action": "block", "side": side, "token_id": token_id,
                    "size": size, "price": price,
                    "reason": f"total exposure cap hit (${total_exposure:.0f} on-chain + ${order_cost:.0f} new > ${max_exposure:.0f} max)"}

        # 4. Per-market exposure cap ($50 = room for BTC brackets + both sides)
        exposure = self._inv_mgr.get_market_exposure(up_token, down_token)
        cat = self._token_category.get(token_id, "crypto")
        max_per_market = self.quote_size_usd * 5.0
        if exposure["total_usd"] >= max_per_market:
            return {"action": "block", "side": side, "token_id": token_id,
                    "size": size, "price": price,
                    "reason": f"market cap ${max_per_market:.0f} hit (${exposure['total_usd']:.0f})"}

        # 5. Imbalance check (max 70% one-sided)
        max_imbalance = self.cfg.maker_max_imbalance
        if exposure["total_usd"] > 1.0 and exposure["imbalance_pct"] > max_imbalance:
            dominant = "up" if exposure["up_size"] > exposure["down_size"] else "down"
            if dominant == "up" and token_id == up_token and side == BUY:
                return {"action": "block", "side": side, "token_id": token_id,
                        "size": size, "price": price,
                        "reason": f"imbalance {exposure['imbalance_pct']:.0%}, would worsen (up-heavy)"}
            if dominant == "down" and token_id == down_token and side == BUY:
                return {"action": "block", "side": side, "token_id": token_id,
                        "size": size, "price": price,
                        "reason": f"imbalance {exposure['imbalance_pct']:.0%}, would worsen (down-heavy)"}

        # 6. Per-market daily loss circuit breaker
        market_pnl = (
            self._token_session_pnl.get(up_token, 0)
            + self._token_session_pnl.get(down_token, 0)
        )
        loss_cap = bankroll * self.cfg.maker_daily_loss_pct
        if market_pnl < -loss_cap:
            return {"action": "block", "side": side, "token_id": token_id,
                    "size": size, "price": price,
                    "reason": f"market daily loss ${market_pnl:.2f} > -${loss_cap:.0f}"}

        return {
            "action": "place", "side": side, "token_id": token_id,
            "size": size, "price": price, "reason": "ok",
        }

    # ── Safe Pricing ─────────────────────────────────────────

    def get_safe_buy_price(self, token_id: str, desired_price: float | None = None) -> tuple[float, float, int]:
        """Competitive BUY price near top of book. Returns (price, tick_size, decimals).

        Strategy: Use desired_price capped at best_ask - 1 tick (don't cross book).
        Falls back to best_bid if no desired_price given.
        """
        book = self.client.get_order_book(token_id)
        tick = float(book.tick_size) if book.tick_size else 0.01
        decimals = len(book.tick_size.split(".")[-1]) if book.tick_size and "." in book.tick_size else 2

        best_ask = float(book.asks[-1].price) if book.asks else 0.99  # CLOB sorts desc, best=last
        best_bid = float(book.bids[-1].price) if book.bids else 0.01  # CLOB sorts asc, best=last

        # Safety ceiling: 1 tick below best ask (never cross the book)
        max_safe = round(best_ask - tick, decimals)

        if desired_price is not None:
            safe = min(round(desired_price, decimals), max_safe)
        else:
            safe = min(best_bid, max_safe)

        safe = max(tick, safe)
        return safe, tick, decimals

    # ── Order Placement ──────────────────────────────────────

    def place_buy_order(self, token_id: str, price: float, size: float,
                        asset: str, label: str = "BUY") -> MakerQuote | None:
        """Place a BUY order with 3-attempt retry on 'crosses book'.

        Re-fetches book on each retry and backs off by 1 extra tick.
        """
        tick = 0.01
        decimals = 2

        for attempt in range(3):
            try:
                # Re-fetch safe price on retries
                if attempt > 0:
                    safe, tick, decimals = self.get_safe_buy_price(token_id, price)
                    price = safe
                    if price < tick:
                        log.warning("[MAKER] %s price below minimum tick, aborting", label)
                        return None

                args = OrderArgs(price=price, size=size, side=BUY, token_id=token_id)
                signed = self.client.create_order(args)
                resp = self.client.post_order(signed, OrderType.GTC, post_only=True)
                oid = resp.get("orderID") or resp.get("id", "")
                if oid:
                    q = MakerQuote(
                        order_id=oid, token_id=token_id, asset=asset,
                        side=label, price=price, size=size,
                        size_usd=round(price * size, 2), placed_at=time.time(),
                    )
                    self._active_quotes.append(q)
                    self._quotes_placed += 1
                    log.info("[MAKER] %s %s @ $%.4f (%.1f tokens)", label, asset.upper(), price, size)
                    return q
                break
            except Exception as e:
                err = str(e)
                if "crosses book" in err and attempt < 2:
                    log.debug("[MAKER] %s crosses book (attempt %d), backing off", label, attempt + 1)
                    time.sleep(0.15 * (attempt + 1))
                    continue
                log.warning("[MAKER] %s failed: %s", label, err[:200])
                break
        return None

    # ── Quote Refresh ────────────────────────────────────────

    def refresh_quotes(
        self,
        token_id: str,
        asset: str,
        fair_value: float,
        half_spread: float,
        quote_size_override: float | None = None,
        skip_buy: bool = False,
        skip_sell: bool = False,
        down_token_id: str = "",
        market_id: str = "",
    ) -> list[MakerQuote]:
        """Cancel stale quotes and post new two-sided GTC orders.

        BUY YES + BUY NO (never SELL — avoids "not enough balance" errors).
        All orders go through validate_order() -> get_safe_buy_price() -> place_buy_order().
        """
        if not self.client or self.cfg.maker_dry_run:
            return self._dry_run_quotes(
                token_id, asset, fair_value, half_spread,
                quote_size_override, skip_buy, skip_sell,
                down_token_id=down_token_id, market_id=market_id,
            )

        # Cancel existing quotes for this market
        self._cancel_quotes_for_token(token_id)
        if down_token_id:
            self._cancel_quotes_for_token(down_token_id)

        # Compute prices with skew
        skew = self._inventory_skew(token_id, down_token_id) if down_token_id else 0.0
        buy_price = round(max(0.01, fair_value - half_spread - skew), 4)
        sell_price = round(min(0.99, fair_value + half_spread - skew), 4)

        if buy_price >= sell_price:
            log.debug("[MAKER] Quotes would cross (buy=%.4f sell=%.4f), skipping", buy_price, sell_price)
            return []

        size_usd = quote_size_override or self.quote_size_usd
        new_quotes = []

        # ── BUY YES side ──
        if not skip_buy:
            v = self.validate_order(market_id, token_id, down_token_id, BUY, token_id, 0, buy_price)
            if v["action"] == "block":
                log.info("[MAKER] BUY YES %s blocked: %s", asset.upper(), v["reason"])
            else:
                try:
                    safe_price, tick, decimals = self.get_safe_buy_price(token_id, buy_price)
                    buy_size = max(5.0, size_usd / safe_price)
                    q = self.place_buy_order(token_id, safe_price, buy_size, asset, "BUY_YES")
                    if q:
                        new_quotes.append(q)
                except Exception as e:
                    log.warning("[MAKER] BUY YES %s setup failed: %s", asset.upper(), str(e)[:200])

        # ── BUY NO side (replaces SELL YES) ──
        if not skip_sell and down_token_id:
            no_buy_price = round(1.0 - sell_price, 4)
            v = self.validate_order(market_id, token_id, down_token_id, BUY, down_token_id, 0, no_buy_price)
            if v["action"] == "block":
                log.info("[MAKER] BUY NO %s blocked: %s", asset.upper(), v["reason"])
            else:
                try:
                    safe_no, no_tick, no_dec = self.get_safe_buy_price(down_token_id, no_buy_price)
                    no_size = max(5.0, size_usd / safe_no)
                    q = self.place_buy_order(down_token_id, safe_no, no_size, asset, "BUY_NO")
                    if q:
                        new_quotes.append(q)
                except Exception as e:
                    log.warning("[MAKER] BUY NO %s setup failed: %s", asset.upper(), str(e)[:200])
        elif not skip_sell and not down_token_id:
            log.debug("[MAKER] %s: no DOWN token available, skipping sell side", asset.upper())

        return new_quotes

    def _dry_run_quotes(
        self, token_id: str, asset: str, fair_value: float, half_spread: float,
        quote_size_override: float | None = None,
        skip_buy: bool = False, skip_sell: bool = False,
        down_token_id: str = "", market_id: str = "",
    ) -> list[MakerQuote]:
        """Enhanced dry-run: reads real order books, validates all safety rules, never submits.

        Logs every decision with reason and would-have-filled price from real book.
        """
        size_usd = quote_size_override or self.quote_size_usd

        skew = self._inventory_skew(token_id, down_token_id) if down_token_id else 0.0
        buy_price = round(max(0.01, fair_value - half_spread - skew), 4)
        sell_price = round(min(0.99, fair_value + half_spread - skew), 4)

        if buy_price >= sell_price:
            return []

        now = time.time()
        quotes = []

        # Read real order books for safe pricing (if client available)
        real_buy_price = buy_price
        real_no_price = round(1.0 - sell_price, 4)
        book_info = ""

        if self.client:
            try:
                safe, _, dec = self.get_safe_buy_price(token_id, buy_price)
                real_buy_price = safe
                book = self.client.get_order_book(token_id)
                best_ask = float(book.asks[-1].price) if book.asks else 0.0
                book_info += f"YES:ask=${best_ask:.4f} "
            except Exception:
                pass
            if down_token_id:
                try:
                    safe_no, _, _ = self.get_safe_buy_price(down_token_id, real_no_price)
                    real_no_price = safe_no
                    book = self.client.get_order_book(down_token_id)
                    best_ask = float(book.asks[-1].price) if book.asks else 0.0
                    book_info += f"NO:ask=${best_ask:.4f}"
                except Exception:
                    pass

        # Validate both sides through safety rules
        sides = []
        if not skip_buy:
            v = self.validate_order(market_id, token_id, down_token_id, BUY, token_id, 0, real_buy_price)
            if v["action"] != "block":
                sides.append(("BUY_YES", real_buy_price, token_id))
            else:
                log.info("[MAKER-DRY] BUY YES %s blocked: %s", asset.upper(), v["reason"])

        if not skip_sell and down_token_id:
            v = self.validate_order(market_id, token_id, down_token_id, BUY, down_token_id, 0, real_no_price)
            if v["action"] != "block":
                sides.append(("BUY_NO", real_no_price, down_token_id))
            else:
                log.info("[MAKER-DRY] BUY NO %s blocked: %s", asset.upper(), v["reason"])

        # Preserve competitive existing quotes (avoids resetting placed_at timer)
        keep_tids = {token_id, down_token_id} if down_token_id else {token_id}
        existing = [q for q in self._active_quotes if q.token_id in keep_tids]

        for side, price, tid in sides:
            # Reuse existing quote if price still within 2 cents (competitive)
            old = next(
                (q for q in existing
                 if q.side == side and q.token_id == tid and abs(q.price - price) <= 0.02),
                None,
            )
            if old:
                quotes.append(old)
            else:
                size = max(5.0, size_usd / price) if price > 0 else 5.0
                q = MakerQuote(
                    order_id="dry-v2-%s-%d" % (side.lower(), int(now)),
                    token_id=tid, asset=asset, side=side,
                    price=price, size=size,
                    size_usd=round(price * size, 2), placed_at=now,
                )
                quotes.append(q)

        new_count = sum(1 for q in quotes if q.placed_at == now)
        if new_count > 0 and sides:
            log.info(
                "[MAKER-DRY] %s %s (fair=%.3f spread=%.3f skew=%.4f sz=$%.0f) [%s]",
                asset.upper(),
                " / ".join("%s@%.4f" % (s, p) for s, p, _ in sides),
                fair_value, half_spread * 2, skew, size_usd, book_info.strip(),
            )

        # Replace active quotes for this market
        self._active_quotes = [
            q for q in self._active_quotes if q.token_id not in keep_tids
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

        # Log fill rate every 5 min
        if now - self._last_fill_rate_log > 300 and self._quotes_placed > 0:
            fill_rate = self._quotes_filled / self._quotes_placed * 100
            log.info("[MAKER] Fill rate: %d/%d = %.1f%% | Spread PnL: $%.2f | Rebates: $%.2f",
                     self._quotes_filled, self._quotes_placed, fill_rate,
                     self._total_spread_captured, self._estimated_rebate_today)
            self._last_fill_rate_log = now

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

    # ── Fill Detection ─────────────────────────────────────

    def check_fills(self) -> list[dict]:
        """Poll active quotes for fills. Update inventory on filled orders."""
        fills = []
        still_active = []
        now = time.time()

        for q in self._active_quotes:
            if self.cfg.maker_dry_run:
                # Enhanced dry-run: check real book for would-fill
                would_fill = False
                fill_info = ""

                if self.client:
                    try:
                        book = self.client.get_order_book(q.token_id)
                        if q.side in ("BUY", "BUY_YES", "BUY_NO"):
                            best_bid = float(book.bids[-1].price) if book.bids else 0.0
                            best_ask = float(book.asks[-1].price) if book.asks else 99.0
                            # Fill if bid is competitive (at/above best bid) and rested 20s
                            competitive = q.price >= best_bid
                            would_fill = competitive and now - q.placed_at > 20
                            fill_info = f"our=${q.price:.4f} bid=${best_bid:.4f} ask=${best_ask:.4f}"
                        else:
                            best_bid = float(book.bids[-1].price) if book.bids else 0.0
                            would_fill = best_bid >= q.price and now - q.placed_at > 5
                            fill_info = f"bid=${best_bid:.4f} vs ask=${q.price:.4f}"
                    except Exception:
                        # Fallback: fill after 20s
                        would_fill = now - q.placed_at > 20
                        fill_info = "book-read-failed, time-based"
                else:
                    # No client: time-based simulation
                    would_fill = now - q.placed_at > 20
                    fill_info = "no-client, time-based"

                if would_fill:
                    fair = self._last_fair.get(q.token_id, q.price)
                    self._record_fill(q, q.price, fair)
                    fills.append({
                        "side": q.side, "price": q.price,
                        "size_usd": q.size_usd, "simulated": True,
                    })
                    log.info("[MAKER-DRY] Would-fill: %s %s @ $%.3f ($%.1f) [%s]",
                             q.side, q.asset.upper(), q.price, q.size_usd, fill_info)
                else:
                    # Expire quotes after 30s in dry-run
                    if now - q.placed_at > 30:
                        log.debug("[MAKER-DRY] Expired: %s %s @ $%.4f [%s]",
                                  q.side, q.asset.upper(), q.price, fill_info)
                    else:
                        still_active.append(q)
                continue

            # ── Live mode ──
            # Stale quote check: cancel if older than 10s (2 tick cycles)
            if now - q.placed_at > 10:
                try:
                    self.client.cancel(q.order_id)
                except Exception:
                    pass
                continue

            try:
                order = self.client.get_order(q.order_id)
                status = (order.get("status") or "").lower()
                if status in ("matched", "filled"):
                    fill_price = float(order.get("price", q.price))
                    fair = self._last_fair.get(q.token_id, fill_price)
                    self._record_fill(q, fill_price, fair)
                    fills.append({
                        "side": q.side, "price": fill_price,
                        "size_usd": q.size_usd,
                    })
                    log.info("[MAKER] Fill: %s %s @ $%.3f ($%.1f)",
                             q.side, q.asset.upper(), fill_price, q.size_usd)
                elif status in ("canceled", "expired"):
                    pass  # drop from active
                else:
                    still_active.append(q)
            except Exception:
                still_active.append(q)

        self._active_quotes = still_active
        return fills

    def _record_fill(self, quote: MakerQuote, fill_price: float, fair_value: float) -> None:
        """Update inventory and P&L on a confirmed fill."""
        token_id = quote.token_id

        # Update InventoryManager (fast local, next sync overwrites)
        self._inv_mgr.update_from_fill(token_id, quote.size, fill_price, quote.side)

        # Spread capture calculation
        if quote.side in ("BUY", "BUY_YES"):
            spread_captured = max(0, fair_value - fill_price)
        elif quote.side == "BUY_NO":
            no_fair = 1.0 - fair_value if fair_value < 1.0 else 0.0
            spread_captured = max(0, no_fair - fill_price)
        else:
            spread_captured = max(0, fill_price - fair_value)

        self._fills_today += 1
        self._quotes_filled += 1

        # Track committed against hard bankroll cap
        if not self.cfg.maker_dry_run:
            self._maker_total_committed += quote.size_usd
            self._save_committed()
            log.info("[MAKER] Committed: $%.2f / $%.0f bankroll",
                     self._maker_total_committed, self.cfg.maker_bankroll_usd)

        # Maker rebate estimate: ~20% of taker fee (0.5%) on fill value
        rebate = 0.005 * 0.20 * quote.size_usd
        self._estimated_rebate_today += rebate

        # P&L tracking
        spread_usd = spread_captured * quote.size
        self._total_spread_captured += spread_usd
        self._session_pnl += spread_usd + rebate

        # Per-token PnL tracking (for daily loss circuit breaker)
        self._token_session_pnl[token_id] = (
            self._token_session_pnl.get(token_id, 0) + spread_usd + rebate
        )

        self._fills_log.append({
            "ts": time.time(),
            "side": quote.side,
            "asset": quote.asset,
            "price": fill_price,
            "fair": fair_value,
            "size_usd": quote.size_usd,
            "spread_captured": round(spread_usd, 4),
            "rebate": round(rebate, 4),
            "token_id": token_id[:16],
        })

    # ── Resolution Guard ─────────────────────────────────────

    def _resolution_safe(self, remaining_s: float) -> bool:
        """Don't quote if market resolves within 90 seconds."""
        return remaining_s > 90

    def _ttr_quote_params(self, up_token: str, down_token: str, remaining_s: float) -> dict:
        """Time-to-resolution based quoting adjustments.

        Returns dict with: quote_size_override, skip_buy, skip_sell, force_flat.
        """
        up_pos = self._inv_mgr.get_position(up_token)
        dn_pos = self._inv_mgr.get_position(down_token)
        up_net = up_pos["size"] if up_pos else 0.0
        dn_net = dn_pos["size"] if dn_pos else 0.0
        total_net = up_net + dn_net

        # >60 min: full quoting
        if remaining_s > 3600:
            return {}

        # 30-60 min: reduce quote size to 50%
        if remaining_s > 1800:
            return {"quote_size_override": self.quote_size_usd * 0.5}

        # 10-30 min: only quote the side that REDUCES imbalance
        if remaining_s > 600:
            params: dict = {"quote_size_override": self.quote_size_usd * 0.3}
            if up_net > dn_net + 2:
                params["skip_buy"] = True  # stop buying YES, only buy NO
            elif dn_net > up_net + 2:
                params["skip_sell"] = True  # stop buying NO, only buy YES
            return params

        # <10 min: force flat if large position
        if total_net > 10:
            return {"force_flat": True}

        # <10 min with small positions: tiny quotes, reducing side only
        params = {"quote_size_override": self.quote_size_usd * 0.2}
        if up_net > 0.5:
            params["skip_buy"] = True
        elif dn_net > 0.5:
            params["skip_sell"] = True
        return params

    def _early_profit_take(self) -> None:
        """Sell positions at 95¢+ to free capital instead of waiting for resolution."""
        for token_id, pos in self._inv_mgr.get_all_positions().items():
            cur_price = pos.get("cur_price", 0)
            if cur_price < 0.95:
                continue
            if pos["size"] < 1.0:
                continue
            # Cooldown: don't retry same token within 5 min
            if token_id in self._profit_take_cooldown:
                if time.time() - self._profit_take_cooldown[token_id] < 300:
                    continue
            asset = self._token_asset.get(token_id, pos.get("market_title", "?"))
            log.info("[MAKER] Early exit: %s at %.0f¢ (%.1f shares) — freeing ~$%.0f",
                     asset.upper(), cur_price * 100, pos["size"], pos["size"] * cur_price)
            self._last_fair[token_id] = cur_price
            self._reduce_inventory(token_id, asset, target_shares=0.0)
            self._profit_take_cooldown[token_id] = time.time()

    def _reduce_inventory(self, token_id: str, asset: str, target_shares: float = 0.0) -> None:
        """Reduce inventory toward target. Used for TTR flattening."""
        pos = self._inv_mgr.get_position(token_id)
        if not pos or pos["size"] < 0.5:
            return

        reduce_shares = pos["size"] - target_shares
        if reduce_shares <= 0:
            return

        log.info("[MAKER] Reducing %s inventory: %.1f -> %.1f shares",
                 asset.upper(), pos["size"], target_shares)

        if self.cfg.maker_dry_run:
            fair = self._last_fair.get(token_id, 0.5)
            slippage = 0.02
            exit_price = max(0.01, fair - slippage)
            cost_per = pos["avg_price"]
            pnl = reduce_shares * (exit_price - cost_per)

            if pnl < 0:
                self._resolution_losses += abs(pnl)
            self._session_pnl += pnl
            log.info("[MAKER-DRY] Reduce %s: sold %.1f @ $%.3f, PnL $%.2f",
                     asset.upper(), reduce_shares, exit_price, pnl)
            # Update local inventory
            self._inv_mgr.update_from_fill(token_id, reduce_shares, exit_price, SELL)
        else:
            # Live: SELL the tokens we hold to free USDC
            fair = self._last_fair.get(token_id, 0.5)
            sell_price = round(max(0.01, fair - 0.02), 2)  # 2c below fair for fast fill
            try:
                args = OrderArgs(price=sell_price, size=reduce_shares, side=SELL, token_id=token_id)
                signed = self.client.create_order(args)
                self.client.post_order(signed, OrderType.GTC)
                log.info("[MAKER] Exit: SELL %.1f @ $%.2f for %s (freeing ~$%.0f USDC)",
                         reduce_shares, sell_price, asset.upper(), reduce_shares * sell_price)
                self._inv_mgr.update_from_fill(token_id, reduce_shares, sell_price, SELL)
            except Exception as e:
                log.warning("[MAKER] Reduce/sell failed for %s: %s", asset.upper(), str(e)[:100])

    def _flatten_inventory(self, up_token: str, down_token: str, asset: str) -> None:
        """Emergency flatten: cancel quotes + dump ALL inventory for a market."""
        self._cancel_quotes_for_token(up_token)
        if down_token:
            self._cancel_quotes_for_token(down_token)
        self._reduce_inventory(up_token, asset, target_shares=0.0)
        if down_token:
            self._reduce_inventory(down_token, asset, target_shares=0.0)

    def _persist_daily_pnl(self) -> None:
        """Append daily P&L summary to maker_pnl.jsonl."""
        pnl_file = DATA_DIR / "maker_pnl.jsonl"
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            entry = {
                "date": time.strftime("%Y-%m-%d"),
                "session_pnl": round(self._session_pnl, 4),
                "spread_captured": round(self._total_spread_captured, 4),
                "resolution_losses": round(self._resolution_losses, 4),
                "fills": self._fills_today,
                "rebates": round(self._estimated_rebate_today, 4),
            }
            with open(pnl_file, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass

    # ── Main Tick ────────────────────────────────────────────

    def tick(self, markets: list[dict], regime_label: str = "neutral") -> None:
        """Main maker tick: refresh quotes for all eligible markets."""
        if not self.enabled:
            return

        # Emergency stop check
        if is_emergency_stopped():
            self.cancel_all()
            return

        # On-chain inventory sync (every 30s)
        self._inv_mgr.sync_if_stale()

        # Auto-reconcile committed capital (release resolved positions)
        self._reconcile_committed()

        # Kill switch: disable if session P&L drops below threshold
        bankroll = self.cfg.maker_bankroll_usd
        max_session_loss = float(os.getenv("MAKER_MAX_SESSION_LOSS", "0"))
        if max_session_loss <= 0 and bankroll > 0:
            max_session_loss = bankroll * 0.15  # 15% of bankroll
        if max_session_loss > 0 and self._session_pnl < -max_session_loss:
            log.critical("[MAKER] Session loss $%.2f exceeds -$%.0f — DISABLING",
                         self._session_pnl, max_session_loss)
            self._kill_reason = f"Session loss ${self._session_pnl:.2f}"
            self.cancel_all()
            self.enabled = False
            self._persist_daily_pnl()
            self._write_state()
            return

        # Heartbeat for auto-cancellation safety net
        self.send_heartbeat()

        # Check fills on existing quotes before placing new ones
        fills = self.check_fills()

        # Report exposure to shared balance manager
        if self._balance_mgr and not self.cfg.maker_dry_run:
            try:
                self._balance_mgr.report_exposure(self._inv_mgr.get_total_exposure())
            except Exception:
                pass

        # Early profit-take: sell 95¢+ positions to free capital
        self._early_profit_take()

        quotes_placed = 0
        for mkt in markets:
            tokens = mkt.get("tokens", [])
            asset = mkt.get("asset", "bitcoin")
            remaining_s = mkt.get("remaining_s", 9999)
            market_id = mkt.get("market_id", "")
            up_token = ""
            down_token = ""

            for t in tokens:
                outcome = (t.get("outcome") or "").lower()
                tid = t.get("token_id", "")
                if outcome in ("up", "yes"):
                    up_token = tid
                elif outcome in ("down", "no"):
                    down_token = tid

            if not up_token:
                continue

            # Cache opposite-token mapping + asset name
            if down_token:
                self._opposite_token[up_token] = down_token
                self._opposite_token[down_token] = up_token
            self._token_asset[up_token] = asset
            if down_token:
                self._token_asset[down_token] = asset

            # Track category for bankroll routing
            category = mkt.get("category", "crypto")
            self._token_category[up_token] = category
            if down_token:
                self._token_category[down_token] = category

            # Track TTR for dashboard
            self._market_remaining[up_token] = remaining_s
            if down_token:
                self._market_remaining[down_token] = remaining_s

            # Resolution guard: flatten inventory if <60s remaining
            if remaining_s <= 60:
                self._flatten_inventory(up_token, down_token, asset)
                continue

            # Resolution guard: don't quote if <90s remaining
            if not self._resolution_safe(remaining_s):
                continue

            # Get implied price from market data (prefer book mid_price for non-crypto)
            implied_price = None
            book_mid = mkt.get("mid_price")
            if book_mid and book_mid > 0:
                implied_price = float(book_mid)
            else:
                for t in tokens:
                    if t.get("token_id") == up_token:
                        p = t.get("price")
                        if p:
                            implied_price = float(p)
                        break

            fair = self.compute_fair_value(asset, implied_price, remaining_s=remaining_s)
            if fair is None:
                # Still track high-value positions for profit-taking
                continue

            # Orderbook imbalance adjustment: shift fair value toward heavy side
            # Positive imbalance = more bids = buying pressure = shift fair up
            book_imbalance = mkt.get("book_imbalance", 0.0)
            if abs(book_imbalance) > 0.15:  # only act on meaningful imbalance (>15%)
                imb_shift = book_imbalance * 0.01  # max ±1c shift at full imbalance
                fair = max(0.05, min(0.95, fair + imb_shift))

            # Cache fair value for P&L calculation
            self._last_fair[up_token] = fair
            if down_token:
                self._last_fair[down_token] = 1.0 - fair

            book_spread = mkt.get("book_spread")
            half_spread = self.compute_spread(asset, up_token, regime_label, book_spread=book_spread)

            # Time-to-resolution graduated inventory control
            ttr_params = self._ttr_quote_params(up_token, down_token, remaining_s)

            if ttr_params.get("force_flat"):
                exposure = self._inv_mgr.get_market_exposure(up_token, down_token)
                log.warning("[MAKER] %s TTR<10min with $%.0f exposure — force flattening",
                            asset.upper(), exposure["total_usd"])
                self._flatten_inventory(up_token, down_token, asset)
                continue

            adv_skip_yes, adv_skip_no = self._adverse_selection_guard(asset)
            skip_buy = ttr_params.get("skip_buy", False) or adv_skip_yes
            skip_sell = ttr_params.get("skip_sell", False) or adv_skip_no

            new = self.refresh_quotes(
                up_token, asset, fair, half_spread,
                quote_size_override=ttr_params.get("quote_size_override"),
                skip_buy=skip_buy,
                skip_sell=skip_sell,
                down_token_id=down_token,
                market_id=market_id,
            )
            quotes_placed += len(new)

        if quotes_placed > 0:
            log.info("[MAKER] Refreshed %d quotes across markets", quotes_placed)

        # Write state for dashboard
        self._write_state()

    def _get_warnings(self) -> list[str]:
        """Generate inventory risk warnings for dashboard."""
        warnings = []
        positions = self._inv_mgr.get_all_positions()

        for tid, pos in positions.items():
            size = pos["size"]
            if size < 2:
                continue
            remaining = self._market_remaining.get(tid, 9999)
            title = pos.get("market_title", "unknown")[:15]

            if size >= 15:
                warnings.append(f"{title}: {size:.0f} shares — EXPOSURE HIGH")
            elif size > 10 and remaining < 600:
                warnings.append(f"{title}: {size:.0f} shares <10min TTR — WILL FLAT")
            elif size > 10 and remaining < 1800:
                warnings.append(f"{title}: {size:.0f} shares <30min TTR — reducing")

        # Check per-market imbalances
        checked_pairs: set[tuple[str, str]] = set()
        for tid in positions:
            opp = self._opposite_token.get(tid, "")
            if not opp or (tid, opp) in checked_pairs or (opp, tid) in checked_pairs:
                continue
            checked_pairs.add((tid, opp))
            exposure = self._inv_mgr.get_market_exposure(tid, opp)
            if exposure["total_usd"] > 5 and exposure["imbalance_pct"] > self.cfg.maker_max_imbalance:
                warnings.append(
                    f"Imbalance {exposure['imbalance_pct']:.0%}: "
                    f"UP=${exposure['up_size']:.0f} DN=${exposure['down_size']:.0f}"
                )

        return warnings

    def _write_state(self) -> None:
        """Write current maker state to JSON for dashboard consumption."""
        now = time.time()
        if now - self._last_state_write < 5:
            return
        self._last_state_write = now

        positions = self._inv_mgr.get_all_positions()

        state = {
            "enabled": self.enabled,
            "timestamp": now,
            "version": 2,
            "active_quotes": [
                {
                    "order_id": q.order_id,
                    "token_id": q.token_id[:16],
                    "asset": q.asset,
                    "side": q.side,
                    "price": q.price,
                    "size_usd": round(q.size_usd, 2),
                    "age_s": round(now - q.placed_at),
                }
                for q in self._active_quotes
            ],
            "inventory": {
                tid: {
                    "asset": self._token_asset.get(tid, pos.get("market_title", "unknown")),
                    "outcome": pos.get("outcome", "unknown"),
                    "net_shares": round(pos["size"], 2),
                    "avg_price": round(pos["avg_price"], 4),
                    "value_usd": round(pos["size"] * pos["avg_price"], 2),
                    "fills_today": 0,  # per-token fill count not tracked in V2
                    "estimated_rebate": 0.0,
                    "remaining_s": round(self._market_remaining.get(tid, 9999)),
                }
                for tid, pos in positions.items()
            },
            "warnings": self._get_warnings(),
            "config": {
                "bankroll_usd": self.cfg.maker_bankroll_usd,
                "crypto_bankroll_usd": self.cfg.maker_crypto_bankroll_usd,
                "general_bankroll_usd": self.cfg.maker_general_bankroll_usd,
                "quote_size_usd": self.quote_size_usd,
                "max_imbalance": self.cfg.maker_max_imbalance,
                "daily_loss_pct": self.cfg.maker_daily_loss_pct,
                "tick_interval_s": self.tick_interval_s,
            },
            "stats": {
                "fills_today": self._fills_today,
                "estimated_rebate_today": round(self._estimated_rebate_today, 4),
                "active_quote_count": len(self._active_quotes),
                "total_exposure_usd": round(self._inv_mgr.get_total_exposure(), 2),
            },
            "pnl": {
                "session_pnl": round(self._session_pnl, 4),
                "spread_captured": round(self._total_spread_captured, 4),
                "resolution_losses": round(self._resolution_losses, 4),
                "kill_reason": self._kill_reason,
            },
            "recent_fills": self._fills_log[-20:],
        }

        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            STATE_FILE.write_text(json.dumps(state, indent=2))
        except Exception:
            pass

    def get_status(self) -> dict:
        """Return current maker engine status for API/dashboard."""
        total_exposure = self._inv_mgr.get_total_exposure()

        return {
            "enabled": self.enabled,
            "version": 2,
            "active_quotes": len(self._active_quotes),
            "total_inventory_usd": round(total_exposure, 2),
            "fills_today": self._fills_today,
            "estimated_rebate_today": round(self._estimated_rebate_today, 4),
            "bankroll_usd": self.cfg.maker_bankroll_usd,
            "quote_size_usd": self.quote_size_usd,
            "max_imbalance": self.cfg.maker_max_imbalance,
            "session_pnl": round(self._session_pnl, 4),
            "spread_captured": round(self._total_spread_captured, 4),
            "resolution_losses": round(self._resolution_losses, 4),
            "kill_reason": self._kill_reason,
            "quotes_placed": self._quotes_placed,
            "quotes_filled": self._quotes_filled,
            "fill_rate_pct": round(self._quotes_filled / max(1, self._quotes_placed) * 100, 1),
        }
