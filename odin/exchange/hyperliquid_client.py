"""Hyperliquid perpetual futures client — drop-in replacement for BitunixClient.

Uses the official hyperliquid-python-sdk for all operations:
  - Info: market data (prices, candles, orderbook, positions, balance)
  - Exchange: authenticated trading (orders, TP/SL, leverage)

Symbols on Hyperliquid use bare names (BTC, ETH, SOL) — no USDT suffix.
We accept both formats and normalize internally.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

import eth_account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants

from odin.config import OdinConfig
from odin.exchange.models import (
    AccountBalance,
    Candle,
    Direction,
    MarginMode,
    Position,
)

log = logging.getLogger(__name__)

# Hyperliquid interval format
INTERVAL_MAP = {
    "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1h", "4h": "4h", "1d": "1d", "1w": "1w",
    "1H": "1h", "4H": "4h", "1D": "1d", "1W": "1w",
    "15M": "15m", "30M": "30m", "5M": "5m", "1M": "1m",
}

# Cache file for tradeable pairs
_PAIRS_CACHE = Path(__file__).parent.parent / "data" / "hl_pairs.json"
_PAIRS_CACHE_TTL = 6 * 3600  # 6 hours


class HyperliquidAPIError(Exception):
    """Raised when Hyperliquid API returns an error."""

    def __init__(self, msg: str, endpoint: str = ""):
        self.msg = msg
        self.endpoint = endpoint
        super().__init__(f"{msg} ({endpoint})")


def _strip_usdt(symbol: str) -> str:
    """Normalize symbol: BTCUSDT → BTC, BTC → BTC."""
    s = symbol.strip().upper()
    if s.endswith("USDT"):
        return s[:-4]
    return s


def _add_usdt(symbol: str) -> str:
    """BTCUSDT format for internal use."""
    s = symbol.strip().upper()
    if not s.endswith("USDT"):
        return f"{s}USDT"
    return s


class HyperliquidClient:
    """Thin client for Hyperliquid perpetual futures."""

    def __init__(self, cfg: OdinConfig):
        self._cfg = cfg
        secret_key = cfg.hl_secret_key
        account_address = cfg.hl_account_address
        testnet = cfg.hl_testnet

        base_url = (
            constants.TESTNET_API_URL if testnet
            else constants.MAINNET_API_URL
        )
        self._base_url = base_url

        # Info client — always available (public data, no auth needed)
        self._info = Info(base_url, skip_ws=True)

        # Exchange client — only if we have credentials (for live trading)
        self._exchange: Optional[Exchange] = None
        if secret_key:
            wallet = eth_account.Account.from_key(secret_key)
            self._exchange = Exchange(
                wallet, base_url,
                account_address=account_address or None,
            )
            self._account_address = account_address or wallet.address
        else:
            self._account_address = account_address or ""

        # Tradeable pairs cache
        self._tradeable_pairs: set[str] = set()
        self._pairs_fetched_at: float = 0.0

        # Size decimals cache (coin → szDecimals)
        self._sz_decimals: dict[str, int] = {}

        log.info("[HL] Client initialized | testnet=%s | auth=%s",
                 testnet, bool(self._exchange))

    # ── Tradeable Pairs ──────────────────────────────────────────

    def get_tradeable_pairs(self) -> set[str]:
        """All tradeable perpetual symbols on Hyperliquid (bare: BTC, ETH, SOL).

        Cached to disk for 6 hours.
        """
        now = time.time()
        if self._tradeable_pairs and (now - self._pairs_fetched_at < _PAIRS_CACHE_TTL):
            return self._tradeable_pairs

        # Try disk cache
        if _PAIRS_CACHE.exists():
            try:
                cached = json.loads(_PAIRS_CACHE.read_text())
                if now - cached.get("ts", 0) < _PAIRS_CACHE_TTL:
                    self._tradeable_pairs = set(cached["pairs"])
                    self._sz_decimals = cached.get("sz_decimals", {})
                    self._pairs_fetched_at = cached["ts"]
                    return self._tradeable_pairs
            except Exception:
                pass

        # Fetch from API
        try:
            meta = self._info.meta()
            pairs = set()
            sz_dec = {}
            for asset in meta.get("universe", []):
                name = asset.get("name", "")
                if name:
                    pairs.add(name)
                    sz_dec[name] = asset.get("szDecimals", 2)

            self._tradeable_pairs = pairs
            self._sz_decimals = sz_dec
            self._pairs_fetched_at = now

            # Save to disk
            _PAIRS_CACHE.parent.mkdir(parents=True, exist_ok=True)
            _PAIRS_CACHE.write_text(json.dumps({
                "ts": now, "pairs": sorted(pairs), "sz_decimals": sz_dec,
            }))

            log.info("[HL] Fetched %d tradeable pairs", len(pairs))
            return pairs

        except Exception as e:
            log.error("[HL] Failed to fetch pairs: %s", str(e)[:200])
            # Return whatever we have
            return self._tradeable_pairs

    def is_tradeable(self, symbol: str) -> bool:
        """Check if a symbol is tradeable on Hyperliquid."""
        pairs = self.get_tradeable_pairs()
        return _strip_usdt(symbol) in pairs

    def get_sz_decimals(self, symbol: str) -> int:
        """Size decimal places for a coin (for rounding order qty)."""
        if not self._sz_decimals:
            self.get_tradeable_pairs()
        return self._sz_decimals.get(_strip_usdt(symbol), 2)

    # ── Market Data (Public) ─────────────────────────────────────

    def get_price(self, symbol: str) -> float:
        """Current mid price for a symbol."""
        coin = _strip_usdt(symbol)
        try:
            all_mids = self._info.all_mids()
            return float(all_mids.get(coin, 0))
        except Exception as e:
            log.debug("[HL] Price fetch error for %s: %s", coin, str(e)[:100])
            return 0.0

    def get_all_prices(self) -> dict[str, float]:
        """All mid prices. Returns {BTC: 97500.0, ETH: 3200.0, ...}."""
        try:
            return {k: float(v) for k, v in self._info.all_mids().items()}
        except Exception as e:
            log.debug("[HL] All prices error: %s", str(e)[:100])
            return {}

    def get_klines(
        self,
        symbol: str,
        interval: str = "4h",
        limit: int = 200,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> list[Candle]:
        """Fetch candlestick data."""
        coin = _strip_usdt(symbol)
        iv = INTERVAL_MAP.get(interval, interval.lower())

        now_ms = int(time.time() * 1000)
        if end_time is None:
            end_time = now_ms
        if start_time is None:
            # Estimate start based on interval and limit
            interval_ms = _interval_to_ms(iv)
            start_time = end_time - (limit * interval_ms)

        try:
            raw = self._info.candles_snapshot(coin, iv, start_time, end_time)
            candles = []
            for c in raw[-limit:]:
                candles.append(Candle(
                    timestamp=float(c.get("t", c.get("T", 0))),
                    open=float(c.get("o", c.get("open", 0))),
                    high=float(c.get("h", c.get("high", 0))),
                    low=float(c.get("l", c.get("low", 0))),
                    close=float(c.get("c", c.get("close", 0))),
                    volume=float(c.get("v", c.get("volume", 0))),
                    symbol=_add_usdt(coin),
                    interval=iv,
                ))
            return candles
        except Exception as e:
            log.debug("[HL] Klines error for %s %s: %s", coin, iv, str(e)[:100])
            return []

    def get_depth(self, symbol: str, limit: int = 5) -> dict:
        """Order book snapshot."""
        coin = _strip_usdt(symbol)
        try:
            l2 = self._info.l2_snapshot(coin)
            levels = l2.get("levels", [[], []])
            return {
                "bids": [{"price": b["px"], "qty": b["sz"]} for b in levels[0][:limit]],
                "asks": [{"price": a["px"], "qty": a["sz"]} for a in levels[1][:limit]],
            }
        except Exception as e:
            log.debug("[HL] Depth error for %s: %s", coin, str(e)[:100])
            return {"bids": [], "asks": []}

    # ── Account (Authenticated) ──────────────────────────────────

    def get_balance(self) -> AccountBalance:
        """Account balance and margin summary (perps + spot for unified accounts)."""
        if not self._account_address:
            return AccountBalance(timestamp=time.time())
        try:
            state = self._info.user_state(self._account_address)
            margin = state.get("marginSummary", {})
            perps_value = float(margin.get("accountValue", 0))
            withdrawable = float(state.get("withdrawable", 0))

            # Unified accounts keep USDC in spot — include it in balance
            spot_usdc = 0.0
            try:
                spot = self._info.spot_user_state(self._account_address)
                for bal in spot.get("balances", []):
                    if bal.get("coin") == "USDC":
                        spot_usdc = float(bal.get("total", 0))
                        break
            except Exception:
                pass

            return AccountBalance(
                total_balance=perps_value + spot_usdc,
                available_balance=withdrawable + spot_usdc,
                margin_used=float(margin.get("totalMarginUsed", 0)),
                unrealized_pnl=float(margin.get("totalNtlPos", 0))
                    - float(margin.get("totalRawUsd", 0)),
                timestamp=time.time(),
            )
        except Exception as e:
            log.error("[HL] Balance error: %s", str(e)[:200])
            return AccountBalance(timestamp=time.time())

    # ── Positions (Authenticated) ────────────────────────────────

    def get_positions(self, symbol: str = "") -> list[Position]:
        """Open positions. Optionally filter by symbol."""
        if not self._account_address:
            return []
        try:
            state = self._info.user_state(self._account_address)
            positions = []
            for pos_data in state.get("assetPositions", []):
                p = pos_data.get("position", {})
                coin = p.get("coin", "")
                sz = float(p.get("szi", 0))
                if sz == 0:
                    continue

                hl_sym = _add_usdt(coin)
                if symbol and hl_sym != _add_usdt(symbol):
                    continue

                positions.append(Position(
                    position_id=f"hl_{coin}",
                    symbol=hl_sym,
                    direction=Direction.LONG if sz > 0 else Direction.SHORT,
                    qty=abs(sz),
                    entry_price=float(p.get("entryPx", 0)),
                    mark_price=float(p.get("markPx", 0)) if "markPx" in p else 0.0,
                    liquidation_price=float(p.get("liquidationPx", 0))
                        if p.get("liquidationPx") else 0.0,
                    leverage=int(float(p.get("leverage", {}).get("value", 1)))
                        if isinstance(p.get("leverage"), dict)
                        else int(float(p.get("leverage", 1))),
                    margin=float(p.get("marginUsed", 0)),
                    unrealized_pnl=float(p.get("unrealizedPnl", 0)),
                    realized_pnl=float(p.get("realizedPnl", 0) if "realizedPnl" in p else 0),
                    margin_mode=MarginMode.CROSS
                        if isinstance(p.get("leverage"), dict)
                        and p["leverage"].get("type") == "cross"
                        else MarginMode.ISOLATED,
                    created_at=0.0,
                ))
            return positions
        except Exception as e:
            log.error("[HL] Positions error: %s", str(e)[:200])
            return []

    # ── Trading (Authenticated) ──────────────────────────────────

    def place_market_order(
        self, symbol: str, is_buy: bool, sz: float, slippage: float = 0.01,
    ) -> dict:
        """Place a market order (IoC with slippage)."""
        if not self._exchange:
            raise HyperliquidAPIError("No exchange client (no credentials)", "market_open")

        coin = _strip_usdt(symbol)
        sz_dec = self.get_sz_decimals(coin)
        rounded_sz = round(sz, sz_dec)

        result = self._exchange.market_open(coin, is_buy, rounded_sz, None, slippage)
        log.info("[HL] Market %s %s sz=%.6f | result=%s",
                 "BUY" if is_buy else "SELL", coin, rounded_sz,
                 result.get("status", "?"))
        return result

    def close_position(self, symbol: str, sz: float = 0, slippage: float = 0.02) -> dict:
        """Close a position (full or partial)."""
        if not self._exchange:
            raise HyperliquidAPIError("No exchange client", "market_close")

        coin = _strip_usdt(symbol)
        if sz > 0:
            sz_dec = self.get_sz_decimals(coin)
            result = self._exchange.market_close(coin, sz=round(sz, sz_dec), slippage=slippage)
        else:
            result = self._exchange.market_close(coin, slippage=slippage)

        log.info("[HL] Close %s | result=%s", coin, result.get("status", "?"))
        return result

    def set_leverage(self, symbol: str, leverage: int, is_cross: bool = True) -> dict:
        """Set leverage for a coin."""
        if not self._exchange:
            raise HyperliquidAPIError("No exchange client", "set_leverage")

        coin = _strip_usdt(symbol)
        result = self._exchange.update_leverage(leverage, coin, is_cross=is_cross)
        log.info("[HL] Leverage %s → %dx (%s)",
                 coin, leverage, "cross" if is_cross else "isolated")
        return result

    def place_tpsl(
        self,
        symbol: str,
        qty: float,
        direction: str,
        tp_price: float = 0,
        sl_price: float = 0,
    ) -> list[dict]:
        """Place TP and/or SL as trigger orders."""
        if not self._exchange:
            raise HyperliquidAPIError("No exchange client", "place_tpsl")

        coin = _strip_usdt(symbol)
        sz_dec = self.get_sz_decimals(coin)
        rounded_qty = round(qty, sz_dec)
        # TP/SL close in opposite direction
        is_long = direction.upper() == "LONG"
        results = []

        if tp_price > 0:
            tp_result = self._exchange.order(
                coin,
                is_buy=not is_long,
                sz=rounded_qty,
                limit_px=tp_price,
                order_type={
                    "trigger": {
                        "triggerPx": tp_price,
                        "isMarket": True,
                        "tpsl": "tp",
                    }
                },
                reduce_only=True,
            )
            results.append(tp_result)
            log.info("[HL] TP set: %s @ $%.2f", coin, tp_price)

        if sl_price > 0:
            sl_result = self._exchange.order(
                coin,
                is_buy=not is_long,
                sz=rounded_qty,
                limit_px=sl_price,
                order_type={
                    "trigger": {
                        "triggerPx": sl_price,
                        "isMarket": True,
                        "tpsl": "sl",
                    }
                },
                reduce_only=True,
            )
            results.append(sl_result)
            log.info("[HL] SL set: %s @ $%.2f", coin, sl_price)

        return results

    def place_limit_order(
        self,
        symbol: str,
        is_buy: bool,
        sz: float,
        price: float,
        tif: str = "Gtc",
        reduce_only: bool = False,
    ) -> dict:
        """Place a GTC/ALO limit order.

        Args:
            tif: "Gtc" (good-til-cancel), "Alo" (post-only/maker), "Ioc" (immediate-or-cancel)
        """
        if not self._exchange:
            raise HyperliquidAPIError("No exchange client", "limit_order")

        coin = _strip_usdt(symbol)
        sz_dec = self.get_sz_decimals(coin)
        rounded_sz = round(sz, sz_dec)

        result = self._exchange.order(
            coin,
            is_buy=is_buy,
            sz=rounded_sz,
            limit_px=price,
            order_type={"limit": {"tif": tif}},
            reduce_only=reduce_only,
        )
        log.info("[HL] Limit %s %s sz=%.6f @ $%.2f tif=%s | result=%s",
                 "BUY" if is_buy else "SELL", coin, rounded_sz, price, tif,
                 result.get("status", "?"))
        return result

    def modify_order(
        self,
        oid: int,
        symbol: str,
        is_buy: bool,
        sz: float,
        price: float,
        tif: str = "Gtc",
        reduce_only: bool = False,
    ) -> dict:
        """Modify an existing order in-place by OID."""
        if not self._exchange:
            raise HyperliquidAPIError("No exchange client", "modify_order")

        coin = _strip_usdt(symbol)
        sz_dec = self.get_sz_decimals(coin)
        rounded_sz = round(sz, sz_dec)

        result = self._exchange.modify_order(
            oid,
            coin,
            is_buy=is_buy,
            sz=rounded_sz,
            limit_px=price,
            order_type={"limit": {"tif": tif}},
            reduce_only=reduce_only,
        )
        log.info("[HL] Modify order %d: %s sz=%.6f @ $%.2f | result=%s",
                 oid, coin, rounded_sz, price, result.get("status", "?"))
        return result

    def bulk_cancel_orders(self, cancels: list[tuple[str, int]]) -> dict:
        """Cancel multiple orders. cancels = [(symbol, oid), ...]."""
        if not self._exchange:
            raise HyperliquidAPIError("No exchange client", "bulk_cancel")

        cancel_requests = []
        for symbol, oid in cancels:
            coin = _strip_usdt(symbol)
            asset_id = self._info.name_to_asset(coin) if hasattr(self._info, "name_to_asset") else 0
            cancel_requests.append({"asset": asset_id, "oid": oid})

        if not cancel_requests:
            return {"status": "ok"}

        result = self._exchange.bulk_cancel(cancel_requests)
        log.info("[HL] Bulk cancel %d orders | result=%s",
                 len(cancel_requests), result.get("status", "?"))
        return result

    def cancel_order(self, symbol: str, order_id: int) -> dict:
        """Cancel an open order by OID."""
        if not self._exchange:
            raise HyperliquidAPIError("No exchange client", "cancel")
        coin = _strip_usdt(symbol)
        return self._exchange.cancel(coin, order_id)

    def get_open_orders(self, symbol: str = "") -> list[dict]:
        """Get open orders, optionally filtered by symbol."""
        if not self._account_address:
            return []
        try:
            orders = self._info.open_orders(self._account_address)
            if symbol:
                coin = _strip_usdt(symbol)
                orders = [o for o in orders if o.get("coin") == coin]
            return orders
        except Exception as e:
            log.debug("[HL] Open orders error: %s", str(e)[:100])
            return []

    # ── Funding Rates ─────────────────────────────────────────────

    def get_funding_rate(self, symbol: str) -> dict[str, float]:
        """Current HL funding rate for a symbol.

        Returns {"rate_8h": X, "annualized": Y} or zeros on error.
        """
        coin = _strip_usdt(symbol)
        try:
            data = self._info.meta_and_asset_ctxs()
            universe = data[0].get("universe", []) if isinstance(data, (list, tuple)) else []
            ctxs = data[1] if isinstance(data, (list, tuple)) and len(data) > 1 else []
            for asset, ctx in zip(universe, ctxs):
                if asset.get("name", "") == coin:
                    rate_8h = float(ctx.get("funding", 0))
                    return {
                        "rate_8h": rate_8h,
                        "annualized": rate_8h * 3 * 365,
                    }
        except Exception as e:
            log.debug("[HL] Funding rate error for %s: %s", coin, str(e)[:100])
        return {"rate_8h": 0.0, "annualized": 0.0}

    def get_funding_history(self, symbol: str, hours: int = 24) -> list[dict]:
        """Recent funding payments for a symbol.

        Returns list of {"rate": X, "time": T} dicts, newest first.
        """
        coin = _strip_usdt(symbol)
        now_ms = int(time.time() * 1000)
        start_ms = now_ms - (hours * 3600 * 1000)
        try:
            raw = self._info.funding_history(coin, start_ms, now_ms)
            result = []
            for entry in raw:
                result.append({
                    "rate": float(entry.get("fundingRate", 0)),
                    "time": entry.get("time", 0),
                })
            result.sort(key=lambda x: x["time"], reverse=True)
            return result
        except Exception as e:
            log.debug("[HL] Funding history error for %s: %s", coin, str(e)[:100])
            return []

    # ── Utility ──────────────────────────────────────────────────

    def ping(self) -> bool:
        """Test connectivity."""
        try:
            mids = self._info.all_mids()
            return isinstance(mids, dict) and len(mids) > 0
        except Exception:
            return False


def _interval_to_ms(interval: str) -> int:
    """Convert interval string to milliseconds."""
    multipliers = {
        "m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000,
    }
    unit = interval[-1]
    value = int(interval[:-1])
    return value * multipliers.get(unit, 60_000)
