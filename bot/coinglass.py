"""Coinglass API client — cross-exchange OI, long/short ratios, funding, ETF flows.

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
_BASE_URL = "https://open-api.coinglass.com/public/v2"

# Internal asset name -> Coinglass symbol
SYMBOL_MAP = {
    "bitcoin": "BTC",
    "ethereum": "ETH",
    "solana": "SOL",
    "xrp": "XRP",
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


def _api_get(endpoint: str, params: dict | None = None) -> dict | None:
    """Make authenticated GET request to Coinglass API."""
    if not _API_KEY:
        return None

    headers = {"coinglassSecret": _API_KEY}
    url = f"{_BASE_URL}/{endpoint}"

    try:
        resp = get_session().get(url, headers=headers, params=params or {}, timeout=10)
        if resp.status_code != 200:
            log.debug("Coinglass HTTP %d for %s", resp.status_code, endpoint)
            return None
        data = resp.json()
        if data.get("code") != "0" and data.get("success") is not True:
            log.debug("Coinglass error: %s", data.get("msg", "unknown"))
            return None
        return data.get("data")
    except Exception as e:
        log.debug("Coinglass request failed: %s", str(e)[:100])
        return None


def get_data(asset: str) -> CoinglassData | None:
    """Fetch aggregated derivatives data for an asset.

    Uses ~13 API calls per cache refresh, well within 30 req/min limit.
    Returns None if API key is missing or all requests fail.
    """
    if not _API_KEY:
        return None

    symbol = SYMBOL_MAP.get(asset)
    if not symbol:
        return None

    # Check cache
    now = time.time()
    cached = _cache.get(asset)
    if cached and now - cached[1] < _CACHE_TTL:
        return cached[0]

    result = CoinglassData()
    any_success = False

    # 1. Open Interest
    try:
        oi_data = _api_get("open_interest", {"symbol": symbol, "time_type": 0})
        if oi_data and isinstance(oi_data, list) and len(oi_data) > 0:
            # Aggregate across exchanges
            total_oi = sum(float(ex.get("openInterest", 0)) for ex in oi_data)
            result.oi_usd = total_oi
            any_success = True
    except Exception:
        pass

    # 2. OI change over time (1h candles: -2 = 1h ago, -5 = 4h ago)
    try:
        ohlc = _api_get("open_interest_his", {"symbol": symbol, "time_type": 1, "currency": "USD"})
        if ohlc and isinstance(ohlc, dict):
            prices = ohlc.get("priceList", []) or ohlc.get("dataMap", {})
            if isinstance(prices, list) and len(prices) >= 2:
                current = float(prices[-1][-1]) if prices[-1] else 0
                h1_ago = float(prices[-2][-1]) if len(prices) > 1 and prices[-2] else current
                h4_ago = float(prices[-5][-1]) if len(prices) > 4 and prices[-5] else current
                if h1_ago > 0:
                    result.oi_change_1h_pct = (current - h1_ago) / h1_ago * 100
                if h4_ago > 0:
                    result.oi_change_4h_pct = (current - h4_ago) / h4_ago * 100
                any_success = True
    except Exception:
        pass

    # 3. Long/Short ratio
    try:
        ls_data = _api_get("long_short", {"symbol": symbol, "time_type": 1})
        if ls_data and isinstance(ls_data, list) and len(ls_data) > 0:
            latest = ls_data[-1] if isinstance(ls_data[-1], dict) else {}
            ratio = float(latest.get("longRate", 50)) / max(float(latest.get("shortRate", 50)), 0.01)
            result.long_short_ratio = ratio
            any_success = True
    except Exception:
        pass

    # 4. Aggregated funding rate
    try:
        fr_data = _api_get("funding", {"symbol": symbol})
        if fr_data and isinstance(fr_data, list) and len(fr_data) > 0:
            rates = [float(ex.get("rate", 0)) for ex in fr_data if ex.get("rate")]
            if rates:
                result.avg_funding_rate = sum(rates) / len(rates)
                any_success = True
    except Exception:
        pass

    # 5. ETF flows (BTC only)
    if symbol == "BTC":
        try:
            etf_data = _api_get("bitcoin_etf_netflow_total")
            if etf_data and isinstance(etf_data, dict):
                result.etf_net_flow_usd = float(etf_data.get("netflow", 0))
                result.etf_available = True
                any_success = True
            elif etf_data and isinstance(etf_data, list) and len(etf_data) > 0:
                latest = etf_data[-1]
                result.etf_net_flow_usd = float(latest.get("netflow", latest.get("value", 0)))
                result.etf_available = True
                any_success = True
        except Exception:
            pass

    # 6. Liquidation data (24h)
    try:
        liq_data = _api_get("liquidation_history", {"symbol": symbol, "time_type": 2})
        if liq_data and isinstance(liq_data, list) and len(liq_data) > 0:
            latest = liq_data[-1] if isinstance(liq_data[-1], dict) else {}
            result.liq_long_24h_usd = float(latest.get("longLiquidationUsd", 0))
            result.liq_short_24h_usd = float(latest.get("shortLiquidationUsd", 0))
            any_success = True
    except Exception:
        pass

    if not any_success:
        _cache[asset] = (None, now)
        return None

    _cache[asset] = (result, now)
    log.info(
        "[CG] %s: OI=$%.0fM chg1h=%.1f%% L/S=%.2f FR=%.4f%% ETF=$%.0fM",
        symbol, result.oi_usd / 1e6, result.oi_change_1h_pct,
        result.long_short_ratio, result.avg_funding_rate * 100,
        result.etf_net_flow_usd / 1e6,
    )
    return result
