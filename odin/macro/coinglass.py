"""CoinGlass API V4 client — on-chain + derivatives data for regime detection.

Hobbyist plan ($29/mo): 70+ endpoints, 30 req/min, updates <= 1 min.
Base URL: https://open-api-v4.coinglass.com
Auth: CG-API-KEY header.

V4 PATH RULES (critical):
  - Kebab-case: funding-rate NOT fundingRate, open-interest NOT openInterest
  - Coin-level endpoints: symbol=BTC
  - Pair-level endpoints: symbol=BTCUSDT + exchange=Binance
  - Interval format: h1, h4, h8, h12, h24
  - coins-markets needs Startup plan — use liquidation/coin-list + OI/FR per symbol

Key data feeds:
  - Funding rates per exchange (crowding signal)
  - Open Interest + change % (leverage buildup)
  - Liquidations per coin (cascade detection)
  - Long/Short ratios (positioning)
  - Exchange flows (whale moves)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://open-api-v4.coinglass.com"
_TIMEOUT = 12


@dataclass
class CoinMetrics:
    """Aggregated metrics for a single coin from CoinGlass."""
    symbol: str
    price: float = 0.0
    oi_usd: float = 0.0
    oi_change_1h: float = 0.0
    oi_change_4h: float = 0.0
    oi_change_24h: float = 0.0
    funding_rate: float = 0.0
    funding_rate_avg: float = 0.0
    long_ratio: float = 0.5
    short_ratio: float = 0.5
    top_trader_long_ratio: float = 0.5
    liq_long_24h: float = 0.0
    liq_short_24h: float = 0.0
    liq_long_4h: float = 0.0
    liq_short_4h: float = 0.0
    taker_buy_ratio: float = 0.5
    price_change_1h: float = 0.0
    price_change_4h: float = 0.0
    price_change_24h: float = 0.0
    volume_24h: float = 0.0
    timestamp: float = 0.0


@dataclass
class MarketSnapshot:
    """Full market snapshot from CoinGlass scan."""
    coins: dict[str, CoinMetrics] = field(default_factory=dict)
    top_symbols: list[str] = field(default_factory=list)
    scan_time: float = 0.0
    api_calls_used: int = 0


class CoinGlassClient:
    """CoinGlass API V4 REST client.

    Rate-limit aware: tracks calls per minute, backs off when near 30/min.
    Caches responses for dedup within scan cycles.
    """

    def __init__(self, api_key: str, top_n: int = 100):
        self._api_key = api_key
        self._top_n = top_n
        self._session = requests.Session()
        self._session.headers.update({
            "accept": "application/json",
            "CG-API-KEY": api_key,
        })
        self._call_count = 0
        self._minute_start = time.time()
        self._cache: dict[str, tuple[float, Any]] = {}
        self._cache_ttl = 170  # ~3 min cache (matches coinglass poll interval)

    def _rate_check(self) -> bool:
        """Check if we're within rate limit (30 req/min)."""
        now = time.time()
        if now - self._minute_start > 60:
            self._call_count = 0
            self._minute_start = now
        if self._call_count >= 28:  # Leave 2 buffer
            log.warning("[CG] Rate limit approaching (%d/30), backing off",
                        self._call_count)
            return False
        return True

    def _get(self, path: str, params: Optional[dict] = None,
             cache_key: str = "") -> Optional[Any]:
        """GET with rate limiting and caching."""
        if cache_key:
            cached = self._cache.get(cache_key)
            if cached and time.time() - cached[0] < self._cache_ttl:
                return cached[1]

        if not self._rate_check():
            return None

        try:
            url = f"{BASE_URL}{path}"
            resp = self._session.get(url, params=params, timeout=_TIMEOUT)
            self._call_count += 1

            if resp.status_code != 200:
                log.warning("[CG] HTTP %d on %s", resp.status_code, path)
                return None

            data = resp.json()
            if data.get("code") != "0":
                log.warning("[CG] API error on %s: %s", path, data.get("msg", ""))
                return None

            result = data.get("data")
            if cache_key and result is not None:
                self._cache[cache_key] = (time.time(), result)
            return result

        except Exception as e:
            log.warning("[CG] Request error %s: %s", path, str(e)[:100])
            return None

    # ── Supported Coins ──

    def get_supported_symbols(self) -> list[str]:
        """Get list of supported coin symbols."""
        data = self._get("/api/futures/supported-coins", cache_key="supported")
        if isinstance(data, list):
            return data
        return []

    # ── Funding Rates (coin-level) ──

    def get_funding_rates(self, symbol: str) -> list:
        """Get current funding rates across exchanges for a coin.

        Returns list with one item containing stablecoin_margin_list.
        Each entry: {exchange, funding_rate, funding_rate_interval, next_funding_time}
        """
        data = self._get(
            "/api/futures/funding-rate/exchange-list",
            params={"symbol": symbol},
            cache_key=f"fr_{symbol}",
        )
        return data if isinstance(data, list) else []

    def get_funding_rate_history(self, symbol: str, interval: str = "h4",
                                 limit: int = 24) -> list:
        """OI-weighted funding rate history (coin-level)."""
        data = self._get(
            "/api/futures/funding-rate/oi-weight-history",
            params={"symbol": symbol, "interval": interval, "limit": limit},
            cache_key=f"fr_hist_{symbol}_{interval}",
        )
        return data if isinstance(data, list) else []

    # ── Open Interest (coin-level) ──

    def get_oi_exchange_list(self, symbol: str) -> list:
        """Get OI by exchange for a coin. First item is "All" (aggregate).

        Fields: open_interest_usd, open_interest_change_percent_1h/4h/24h, etc.
        """
        data = self._get(
            "/api/futures/open-interest/exchange-list",
            params={"symbol": symbol},
            cache_key=f"oi_{symbol}",
        )
        return data if isinstance(data, list) else []

    def get_oi_history(self, symbol: str, interval: str = "h4",
                       limit: int = 24) -> list:
        """Aggregated OI history (coin-level)."""
        data = self._get(
            "/api/futures/open-interest/aggregated-history",
            params={"symbol": symbol, "interval": interval, "limit": limit},
            cache_key=f"oi_hist_{symbol}_{interval}",
        )
        return data if isinstance(data, list) else []

    # ── Liquidations ──

    def get_liquidation_coins(self) -> list:
        """All coins liquidation summary — 24h/12h/4h/1h.

        Single API call for ALL coins. Fields per coin:
        symbol, long_liquidation_usd_24h, short_liquidation_usd_24h,
        long_liquidation_usd_4h, short_liquidation_usd_4h, etc.
        """
        data = self._get(
            "/api/futures/liquidation/coin-list",
            cache_key="liq_coins",
        )
        return data if isinstance(data, list) else []

    def get_liquidation_exchange(self, symbol: str, time_range: str = "4h") -> list:
        """Liquidations by exchange for a coin."""
        data = self._get(
            "/api/futures/liquidation/exchange-list",
            params={"symbol": symbol, "range": time_range},
            cache_key=f"liq_ex_{symbol}_{time_range}",
        )
        return data if isinstance(data, list) else []

    # ── Long/Short Ratios (pair-level: needs exchange + BTCUSDT format) ──

    def get_global_ls_ratio(self, pair: str, exchange: str = "Binance",
                            interval: str = "h4", limit: int = 1) -> list:
        """Global account long/short ratio history.

        pair must be instrument format: BTCUSDT, ETHUSDT, etc.
        Returns: [{time, global_account_long_percent, short_percent, ratio}]
        """
        data = self._get(
            "/api/futures/global-long-short-account-ratio/history",
            params={
                "exchange": exchange,
                "symbol": pair,
                "interval": interval,
                "limit": limit,
            },
            cache_key=f"gls_{pair}_{exchange}_{interval}",
        )
        return data if isinstance(data, list) else []

    def get_top_trader_ls_ratio(self, pair: str, exchange: str = "Binance",
                                interval: str = "h4", limit: int = 1) -> list:
        """Top trader account long/short ratio history (pair-level)."""
        data = self._get(
            "/api/futures/top-long-short-account-ratio/history",
            params={
                "exchange": exchange,
                "symbol": pair,
                "interval": interval,
                "limit": limit,
            },
            cache_key=f"tls_{pair}_{exchange}_{interval}",
        )
        return data if isinstance(data, list) else []

    # ── Taker Buy/Sell ──

    def get_taker_volume(self, symbol: str, time_range: str = "4h") -> list:
        """Taker buy vs sell volume by exchange (coin-level)."""
        data = self._get(
            "/api/futures/taker-buy-sell-volume/exchange-list",
            params={"symbol": symbol, "range": time_range},
            cache_key=f"taker_{symbol}_{time_range}",
        )
        return data if isinstance(data, list) else []

    # ── Exchange Flows ──

    def get_exchange_flows(self, symbol: str = "BTC") -> list:
        """Exchange balance list — inflows/outflows."""
        data = self._get(
            "/api/exchange/balance/list",
            params={"symbol": symbol},
            cache_key=f"flow_{symbol}",
        )
        return data if isinstance(data, list) else []

    # ── Fear & Greed ──

    def get_fear_greed(self) -> list:
        """Fear & Greed index history."""
        data = self._get(
            "/api/index/fear-greed-history",
            cache_key="fear_greed",
        )
        return data if isinstance(data, list) else []

    # ── Full Market Scan ──

    def scan_market(self, priority_symbols: list[str] | None = None) -> MarketSnapshot:
        """Full market scan using Hobbyist-available endpoints.

        Strategy (fits within 30 req/min):
          1. liquidation/coin-list (1 call) — ALL 935 coins liq data
          2. Per priority symbol: funding + OI + L/S = 3 calls each
          Total: 1 + (4 × 3) = 13 calls for 4 priority symbols.
        """
        snapshot = MarketSnapshot(scan_time=time.time())
        priority = priority_symbols or ["BTC", "ETH", "XRP", "SOL"]

        # Step 1: Get ALL coins liquidation data (1 API call for 935 coins)
        liq_coins = self.get_liquidation_coins()
        snapshot.api_calls_used += 1

        for liq in liq_coins:
            sym = liq.get("symbol", "")
            if not sym:
                continue

            snapshot.top_symbols.append(sym)
            snapshot.coins[sym] = CoinMetrics(
                symbol=sym,
                liq_long_24h=float(liq.get("long_liquidation_usd_24h", 0) or 0),
                liq_short_24h=float(liq.get("short_liquidation_usd_24h", 0) or 0),
                liq_long_4h=float(liq.get("long_liquidation_usd_4h", 0) or 0),
                liq_short_4h=float(liq.get("short_liquidation_usd_4h", 0) or 0),
                timestamp=time.time(),
            )

        # Sort by total 24h liquidation volume (most active first)
        snapshot.top_symbols.sort(
            key=lambda s: (
                snapshot.coins[s].liq_long_24h + snapshot.coins[s].liq_short_24h
            ),
            reverse=True,
        )
        # Keep top N
        snapshot.top_symbols = snapshot.top_symbols[:self._top_n]

        # Step 2: Detailed data for priority symbols
        for sym in priority:
            if sym not in snapshot.coins:
                snapshot.coins[sym] = CoinMetrics(symbol=sym, timestamp=time.time())

            coin = snapshot.coins[sym]

            # Funding rates (coin-level: symbol=BTC)
            fr_data = self.get_funding_rates(sym)
            snapshot.api_calls_used += 1
            if fr_data:
                item = fr_data[0] if isinstance(fr_data, list) and fr_data else {}
                rates = item.get("stablecoin_margin_list") or []
                if rates:
                    fr_vals = [
                        float(r.get("funding_rate", 0) or 0)
                        for r in rates if r.get("funding_rate") is not None
                    ]
                    if fr_vals:
                        coin.funding_rate = fr_vals[0]
                        coin.funding_rate_avg = sum(fr_vals) / len(fr_vals)

            # OI exchange list (coin-level: symbol=BTC)
            oi_data = self.get_oi_exchange_list(sym)
            snapshot.api_calls_used += 1
            if oi_data:
                # First item is "All" (aggregate)
                agg = oi_data[0] if oi_data else {}
                coin.oi_usd = float(agg.get("open_interest_usd", 0) or 0)
                coin.oi_change_1h = float(
                    agg.get("open_interest_change_percent_1h", 0) or 0
                )
                coin.oi_change_4h = float(
                    agg.get("open_interest_change_percent_4h", 0) or 0
                )
                coin.oi_change_24h = float(
                    agg.get("open_interest_change_percent_24h", 0) or 0
                )

            # L/S ratio (pair-level: exchange=Binance, symbol=BTCUSDT)
            pair = f"{sym}USDT"
            ls_data = self.get_global_ls_ratio(pair, exchange="Binance",
                                               interval="h4", limit=1)
            snapshot.api_calls_used += 1
            if ls_data:
                latest = ls_data[-1] if ls_data else {}
                long_pct = float(
                    latest.get("global_account_long_percent", 50) or 50
                )
                short_pct = float(
                    latest.get("global_account_short_percent", 50) or 50
                )
                # Convert from percentage (0-100) to ratio (0-1)
                coin.long_ratio = long_pct / 100.0
                coin.short_ratio = short_pct / 100.0

        log.info("[CG] Market scan complete: %d coins, %d priority, %d API calls",
                 len(snapshot.top_symbols), len(priority), snapshot.api_calls_used)

        return snapshot

    def get_top_priority(
        self, n: int, tradeable_set: set[str], last_snapshot: Optional[MarketSnapshot] = None,
    ) -> list[str]:
        """Top N coins by liquidation volume, filtered to tradeable exchange pairs.

        Uses last snapshot if available, otherwise returns known majors.
        """
        if last_snapshot and last_snapshot.top_symbols:
            filtered = [
                s for s in last_snapshot.top_symbols
                if s in tradeable_set
            ]
            return filtered[:n]

        # Fallback: known high-volume coins that are likely tradeable
        fallback = ["BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "AVAX", "LINK"]
        return [s for s in fallback if s in tradeable_set][:n]

    @property
    def calls_remaining(self) -> int:
        now = time.time()
        if now - self._minute_start > 60:
            return 30
        return max(0, 30 - self._call_count)
