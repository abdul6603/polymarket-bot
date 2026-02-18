"""DeFiLlama Stablecoin + TVL data — leading indicators for crypto markets.

Free API, no key needed:
- stablecoins.llama.fi/stablecoins — stablecoin market cap changes
- api.llama.fi/v2/historicalChainTvl — DeFi TVL shifts
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from bot.http_session import get_session

log = logging.getLogger(__name__)

# Cache
_cache: dict[str, tuple["DefiFlowData | None", float]] = {}
_CACHE_TTL = 3600  # 1 hour — slow-changing data


@dataclass
class DefiFlowData:
    # Stablecoin market cap
    stablecoin_mcap_usd: float = 0.0
    stablecoin_change_7d_usd: float = 0.0
    stablecoin_change_7d_pct: float = 0.0

    # DeFi TVL
    tvl_usd: float = 0.0
    tvl_change_24h_pct: float = 0.0
    tvl_change_7d_pct: float = 0.0

    timestamp: float = 0.0


def get_data() -> DefiFlowData | None:
    """Fetch stablecoin flows + TVL data from DeFiLlama.

    Returns None if both requests fail.
    """
    now = time.time()
    cached = _cache.get("defi")
    if cached and now - cached[1] < _CACHE_TTL:
        return cached[0]

    result = DefiFlowData(timestamp=now)
    any_success = False

    # 1. Stablecoin market cap
    try:
        resp = get_session().get(
            "https://stablecoins.llama.fi/stablecoins?includePrices=false",
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            peggedAssets = data.get("peggedAssets", [])

            total_mcap = 0.0
            total_mcap_7d_ago = 0.0
            for asset in peggedAssets:
                chains = asset.get("chainCirculating", {})
                current = 0.0
                for chain_data in chains.values():
                    current += float(chain_data.get("current", {}).get("peggedUSD", 0))
                total_mcap += current

                # 7d change from peggedAsset level
                mcap_prev = float(asset.get("circulating", {}).get("peggedUSD", 0))
                total_mcap_7d_ago += mcap_prev

            if total_mcap > 0:
                result.stablecoin_mcap_usd = total_mcap
                # Use the aggregate difference
                if total_mcap_7d_ago > 0:
                    result.stablecoin_change_7d_usd = total_mcap - total_mcap_7d_ago
                    result.stablecoin_change_7d_pct = (
                        (total_mcap - total_mcap_7d_ago) / total_mcap_7d_ago * 100
                    )
                any_success = True
    except Exception as e:
        log.debug("DeFiLlama stablecoin fetch failed: %s", str(e)[:100])

    # 2. Total DeFi TVL
    try:
        resp = get_session().get(
            "https://api.llama.fi/v2/historicalChainTvl",
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list) and len(data) >= 8:
                # Data is daily TVL entries: [{date, tvl}, ...]
                current_tvl = float(data[-1].get("tvl", 0))
                day_ago_tvl = float(data[-2].get("tvl", 0)) if len(data) >= 2 else current_tvl
                week_ago_tvl = float(data[-8].get("tvl", 0)) if len(data) >= 8 else current_tvl

                result.tvl_usd = current_tvl
                if day_ago_tvl > 0:
                    result.tvl_change_24h_pct = (current_tvl - day_ago_tvl) / day_ago_tvl * 100
                if week_ago_tvl > 0:
                    result.tvl_change_7d_pct = (current_tvl - week_ago_tvl) / week_ago_tvl * 100
                any_success = True
    except Exception as e:
        log.debug("DeFiLlama TVL fetch failed: %s", str(e)[:100])

    if not any_success:
        _cache["defi"] = (None, now)
        return None

    _cache["defi"] = (result, now)
    log.info(
        "[DEFI] Stablecoin MCap=$%.0fB (7d: $%+.0fB, %+.1f%%) | TVL=$%.0fB (24h: %+.1f%%, 7d: %+.1f%%)",
        result.stablecoin_mcap_usd / 1e9,
        result.stablecoin_change_7d_usd / 1e9,
        result.stablecoin_change_7d_pct,
        result.tvl_usd / 1e9,
        result.tvl_change_24h_pct,
        result.tvl_change_7d_pct,
    )
    return result
