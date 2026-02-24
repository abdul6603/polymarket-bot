"""Coinglass API client — cross-exchange OI, long/short ratios, funding, ETF flows.

Migrated to V4 API (Feb 2026): V2 endpoints deprecated, returning empty data.
V4 base: https://open-api-v4.coinglass.com/api/
Auth header: CG-API-KEY

Requires Hobbyist plan ($29/mo) for 30 req/min, 70+ endpoints.
API docs: https://docs.coinglass.com/reference/api-overview

Graceful degradation: returns None on failure, never blocks other indicators.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

from bot.http_session import get_session

log = logging.getLogger(__name__)

_API_KEY = os.environ.get("COINGLASS_API_KEY", "")
_BASE_URL = "https://open-api-v4.coinglass.com/api"

# Internal asset name -> Coinglass symbol
SYMBOL_MAP = {
    "bitcoin": "BTC",
    "ethereum": "ETH",
    "solana": "SOL",
    "xrp": "XRP",
}

# Exchange pair suffix for per-exchange endpoints
PAIR_SUFFIX = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
    "XRP": "XRPUSDT",
}

# Cache: asset -> (CoinglassData, timestamp)
_cache: dict[str, tuple["CoinglassData | None", float]] = {}
_CACHE_TTL = 60  # 60s — medium frequency data


@dataclass
class CoinglassData:
    # Open Interest
    oi_usd: float = 0.0
    oi_change_1h_pct: float = 0.0
    oi_change_4h_pct: float = 0.0
    oi_change_24h_pct: float = 0.0

    # Long/Short ratio (global accounts)
    long_short_ratio: float = 1.0  # >1 = more longs, <1 = more shorts

    # Funding rate (aggregated)
    avg_funding_rate: float = 0.0

    # ETF flows (BTC only)
    etf_net_flow_usd: float = 0.0
    etf_available: bool = False

    # Liquidations (24h)
    liq_long_24h_usd: float = 0.0
    liq_short_24h_usd: float = 0.0

    # Liquidation heatmap — price-level clusters
    liq_cluster_above_usd: float = 0.0   # total liq $ in clusters above current price
    liq_cluster_below_usd: float = 0.0   # total liq $ in clusters below current price
    liq_nearest_above_pct: float = 0.0   # distance to nearest heavy cluster above (%)
    liq_nearest_below_pct: float = 0.0   # distance to nearest heavy cluster below (%)
    liq_heatmap_available: bool = False


def _api_get(endpoint: str, params: dict | None = None) -> dict | None:
    """Make authenticated GET request to Coinglass V4 API."""
    if not _API_KEY:
        return None

    headers = {
        "CG-API-KEY": _API_KEY,
        "accept": "application/json",
    }
    url = f"{_BASE_URL}/{endpoint}"

    try:
        resp = get_session().get(url, headers=headers, params=params or {}, timeout=10)
        if resp.status_code != 200:
            log.warning("[CG] HTTP %d for %s (params=%s)", resp.status_code, endpoint, params)
            return None
        data = resp.json()
        # V4 uses "code" field: "0" = success
        code = data.get("code")
        success = data.get("success")
        if code not in ("0", 0) and success is not True:
            log.warning("[CG] API error for %s: code=%s msg=%s", endpoint, code, data.get("msg", "unknown"))
            return None
        return data.get("data")
    except Exception as e:
        log.warning("[CG] Request failed for %s: %s", endpoint, str(e)[:100])
        return None


# Heatmap cache: symbol -> (heatmap_dict, timestamp)
_heatmap_cache: dict[str, tuple[dict | None, float]] = {}
_HEATMAP_CACHE_TTL = 300  # 5 min — slow-changing data, saves API calls


def get_liquidation_heatmap(symbol: str, current_price: float) -> dict | None:
    """Fetch liquidation price-level clusters for a symbol.

    Returns dict with cluster_above_usd, cluster_below_usd, nearest_above_pct,
    nearest_below_pct. Uses the liquidation-map endpoint (aggregated liq levels).

    Separate 5-min cache to avoid burning API quota — heatmap changes slowly.
    """
    if not _API_KEY or current_price <= 0:
        return None

    now = time.time()
    cached = _heatmap_cache.get(symbol)
    if cached and now - cached[1] < _HEATMAP_CACHE_TTL:
        return cached[0]

    try:
        data = _api_get("futures/liquidation/detail/chart", {
            "symbol": symbol,
            "interval": "1h",
        })
        if not data:
            _heatmap_cache[symbol] = (None, now)
            return None

        # V4 returns list of price-level liquidation entries
        # Each entry: {"price": float, "longLiqUsd": float, "shortLiqUsd": float, ...}
        # or nested structure under "y" / "prices" keys
        entries = []
        if isinstance(data, list):
            entries = data
        elif isinstance(data, dict):
            entries = data.get("data", data.get("y", []))
            if not isinstance(entries, list):
                entries = []

        if not entries:
            _heatmap_cache[symbol] = (None, now)
            return None

        above_usd = 0.0
        below_usd = 0.0
        nearest_above_dist = float("inf")
        nearest_below_dist = float("inf")
        min_cluster_usd = 1_000_000  # $1M minimum to count as meaningful cluster

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            price = float(entry.get("price", 0))
            long_liq = float(entry.get("longLiqUsd", 0) or entry.get("liqLong", 0))
            short_liq = float(entry.get("shortLiqUsd", 0) or entry.get("liqShort", 0))
            total_liq = long_liq + short_liq

            if price <= 0 or total_liq < min_cluster_usd:
                continue

            dist_pct = (price - current_price) / current_price * 100

            if price > current_price:
                above_usd += total_liq
                if abs(dist_pct) < nearest_above_dist:
                    nearest_above_dist = abs(dist_pct)
            else:
                below_usd += total_liq
                if abs(dist_pct) < nearest_below_dist:
                    nearest_below_dist = abs(dist_pct)

        result = {
            "cluster_above_usd": above_usd,
            "cluster_below_usd": below_usd,
            "nearest_above_pct": nearest_above_dist if nearest_above_dist != float("inf") else 0.0,
            "nearest_below_pct": nearest_below_dist if nearest_below_dist != float("inf") else 0.0,
        }

        _heatmap_cache[symbol] = (result, now)
        log.debug(
            "[CG] %s heatmap: above=$%.0fM(%.1f%%) below=$%.0fM(%.1f%%)",
            symbol, above_usd / 1e6, result["nearest_above_pct"],
            below_usd / 1e6, result["nearest_below_pct"],
        )
        return result

    except Exception as e:
        log.warning("[CG] %s heatmap failed: %s", symbol, str(e)[:80])
        _heatmap_cache[symbol] = (None, now)
        return None


def get_data(asset: str, current_price: float = 0.0) -> CoinglassData | None:
    """Fetch aggregated derivatives data for an asset.

    V4 API endpoints — ~7 calls per refresh, well within 30 req/min limit.
    Pass current_price to enable liquidation heatmap analysis.
    Returns None if API key is missing or all requests fail.
    """
    if not _API_KEY:
        return None

    symbol = SYMBOL_MAP.get(asset)
    if not symbol:
        return None

    # Check cache — stagger TTL by asset to avoid simultaneous API bursts
    _ASSET_OFFSETS = {"bitcoin": 0, "ethereum": 15, "solana": 30, "xrp": 45}
    now = time.time()
    ttl = _CACHE_TTL + _ASSET_OFFSETS.get(asset, 0)
    cached = _cache.get(asset)
    if cached and now - cached[1] < ttl:
        return cached[0]

    result = CoinglassData()
    any_success = False

    # 1. Open Interest — exchange-list includes OI + change percentages (free tier)
    # The "All" row has aggregated data with 1h/4h/24h change percents built in,
    # eliminating the need for the premium aggregated-history endpoint.
    try:
        oi_data = _api_get("futures/open-interest/exchange-list", {"symbol": symbol})
        if oi_data and isinstance(oi_data, list) and len(oi_data) > 0:
            # Find the "All" aggregate row, or sum individual exchanges
            all_row = None
            for ex in oi_data:
                if ex.get("exchange") == "All":
                    all_row = ex
                    break
            if all_row:
                result.oi_usd = float(all_row.get("open_interest_usd", 0) or all_row.get("openInterest", 0))
                result.oi_change_1h_pct = float(all_row.get("open_interest_change_percent_1h", 0))
                result.oi_change_4h_pct = float(all_row.get("open_interest_change_percent_4h", 0))
                result.oi_change_24h_pct = float(all_row.get("open_interest_change_percent_24h", 0))
            else:
                result.oi_usd = sum(float(ex.get("openInterest", 0) or ex.get("open_interest_usd", 0)) for ex in oi_data)
            any_success = True
            log.debug("[CG] %s OI: $%.0fM chg1h=%.1f%% chg4h=%.1f%% chg24h=%.1f%%",
                      symbol, result.oi_usd / 1e6, result.oi_change_1h_pct,
                      result.oi_change_4h_pct, result.oi_change_24h_pct)
        else:
            log.debug("[CG] %s OI: no data returned (data=%s)", symbol, type(oi_data).__name__)
    except Exception as e:
        log.warning("[CG] %s OI fetch failed: %s", symbol, str(e)[:80])

    # 3. Long/Short ratio — DISABLED: requires Professional plan ($79/mo).
    # Hobbyist ($29/mo) returns 400 "Upgrade plan" on every call, spamming logs.
    # L/S ratio defaults to 1.0 (neutral). Re-enable if plan upgraded.

    # 4. Funding rate — aggregated across exchanges
    try:
        fr_data = _api_get("futures/funding-rate/exchange-list", {"symbol": symbol})
        if fr_data and isinstance(fr_data, list) and len(fr_data) > 0:
            # V4: each entry has "rate" (or "currentFundingRate") per exchange
            rates = []
            for ex in fr_data:
                rate = ex.get("rate") or ex.get("currentFundingRate") or ex.get("fundingRate")
                if rate is not None:
                    try:
                        rates.append(float(rate))
                    except (ValueError, TypeError):
                        pass
            if rates:
                result.avg_funding_rate = sum(rates) / len(rates)
                any_success = True
                log.debug("[CG] %s funding rate: %.4f%% (avg of %d exchanges)",
                          symbol, result.avg_funding_rate * 100, len(rates))
        else:
            log.debug("[CG] %s funding rate: no data returned", symbol)
    except Exception as e:
        log.warning("[CG] %s funding rate failed: %s", symbol, str(e)[:80])

    # 5. ETF flows (BTC only) — V4 endpoint
    if symbol == "BTC":
        try:
            etf_data = _api_get("bitcoin/etf/flow-history", {"limit": 1})
            if etf_data and isinstance(etf_data, list) and len(etf_data) > 0:
                latest = etf_data[-1] if isinstance(etf_data[-1], dict) else {}
                # V4: {"totalNetFlow": ..., "date": ...}
                net_flow = latest.get("totalNetFlow") or latest.get("netflow") or latest.get("value", 0)
                result.etf_net_flow_usd = float(net_flow)
                result.etf_available = True
                any_success = True
                log.debug("[CG] BTC ETF flow: $%.1fM", result.etf_net_flow_usd / 1e6)
            elif etf_data and isinstance(etf_data, dict):
                net_flow = etf_data.get("totalNetFlow") or etf_data.get("netflow", 0)
                result.etf_net_flow_usd = float(net_flow)
                result.etf_available = True
                any_success = True
                log.debug("[CG] BTC ETF flow: $%.1fM", result.etf_net_flow_usd / 1e6)
            else:
                log.debug("[CG] BTC ETF flow: no data returned")
        except Exception as e:
            log.warning("[CG] BTC ETF flow failed: %s", str(e)[:80])

    # 6. Liquidation data — aggregated across exchanges (exchange_list required by V4)
    try:
        liq_data = _api_get("futures/liquidation/aggregated-history", {
            "symbol": symbol,
            "interval": "1d",
            "limit": 1,
            "exchange_list": "Binance,OKX,Bybit",
        })
        if liq_data and isinstance(liq_data, list) and len(liq_data) > 0:
            latest = liq_data[-1] if isinstance(liq_data[-1], dict) else {}
            # V4: {"longLiquidationUsd": ..., "shortLiquidationUsd": ..., ...}
            result.liq_long_24h_usd = float(latest.get("longLiquidationUsd", 0))
            result.liq_short_24h_usd = float(latest.get("shortLiquidationUsd", 0))
            any_success = True
            log.debug("[CG] %s liquidations 24h: long=$%.0fK short=$%.0fK",
                      symbol, result.liq_long_24h_usd / 1e3, result.liq_short_24h_usd / 1e3)
        else:
            log.debug("[CG] %s liquidations: no data returned", symbol)
    except Exception as e:
        log.warning("[CG] %s liquidation data failed: %s", symbol, str(e)[:80])

    # 7. Liquidation heatmap — price-level clusters (separate 5-min cache)
    if current_price > 0:
        heatmap = get_liquidation_heatmap(symbol, current_price)
        if heatmap:
            result.liq_cluster_above_usd = heatmap["cluster_above_usd"]
            result.liq_cluster_below_usd = heatmap["cluster_below_usd"]
            result.liq_nearest_above_pct = heatmap["nearest_above_pct"]
            result.liq_nearest_below_pct = heatmap["nearest_below_pct"]
            result.liq_heatmap_available = True
            any_success = True

    if not any_success:
        log.warning("[CG] %s: ALL endpoints failed — check API key and network", symbol)
        _cache[asset] = (None, now)
        return None

    _cache[asset] = (result, now)
    log.info(
        "[CG] %s: OI=$%.0fM chg1h=%.1f%% chg4h=%.1f%% L/S=%.2f FR=%.4f%% ETF=$%.0fM liq_L=$%.0fK liq_S=$%.0fK",
        symbol, result.oi_usd / 1e6, result.oi_change_1h_pct, result.oi_change_4h_pct,
        result.long_short_ratio, result.avg_funding_rate * 100,
        result.etf_net_flow_usd / 1e6,
        result.liq_long_24h_usd / 1e3, result.liq_short_24h_usd / 1e3,
    )
    return result
