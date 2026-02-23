"""VPIN (Volume-Synchronized Probability of Informed Trading) for Polymarket.

Detects informed flow by measuring buy/sell volume imbalance from CLOB trade
history. High VPIN = insiders are trading = we should NOT trade this market.

Thresholds:
  VPIN > 0.70 → HIGH toxicity → BLOCK trade
  VPIN 0.50-0.70 → MEDIUM toxicity → reduce size by 50%
  VPIN < 0.50 → LOW toxicity → proceed normally
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from bot.http_session import get_session

log = logging.getLogger(__name__)

# Cache: condition_id → (timestamp, VPINResult)
_cache: dict[str, tuple[float, "VPINResult"]] = {}
_CACHE_TTL = 300  # 5 minutes


@dataclass
class VPINResult:
    vpin: float              # 0.0 to 1.0 — higher = more informed flow
    toxicity: str            # "low", "medium", "high"
    buy_volume: float        # total buy-side volume (USD)
    sell_volume: float       # total sell-side volume (USD)
    total_volume: float      # buy + sell
    trade_count: int         # number of trades analyzed
    recommendation: str      # "proceed", "reduce_size", "block"
    size_multiplier: float   # 1.0, 0.5, or 0.0


# Thresholds
VPIN_HIGH = 0.70    # Block
VPIN_MEDIUM = 0.50  # Reduce size


def compute_vpin(condition_id: str, token_id: str = "") -> VPINResult:
    """Compute VPIN for a market by analyzing recent CLOB trade history.

    Args:
        condition_id: Market condition ID
        token_id: Specific token to analyze (optional — will check both)

    Returns:
        VPINResult with toxicity assessment and size recommendation.
    """
    cache_key = f"{condition_id}:{token_id}"
    now = time.time()

    # Check cache
    if cache_key in _cache:
        ts, cached = _cache[cache_key]
        if now - ts < _CACHE_TTL:
            return cached

    result = _fetch_and_compute(condition_id, token_id)
    _cache[cache_key] = (now, result)
    return result


def _fetch_and_compute(condition_id: str, token_id: str) -> VPINResult:
    """Fetch recent trades from CLOB and compute VPIN."""
    session = get_session()

    # Fetch recent trades from CLOB API
    try:
        # CLOB timeseries endpoint gives recent trades
        url = f"https://clob.polymarket.com/trades"
        params = {"market": condition_id, "limit": 200}
        if token_id:
            params["asset_id"] = token_id

        resp = session.get(url, params=params, timeout=10)
        if resp.status_code != 200:
            log.debug("[VPIN] CLOB trades API returned %d for %s", resp.status_code, condition_id[:12])
            return _default_result()

        trades = resp.json()
        if not trades or not isinstance(trades, list):
            return _default_result()

    except Exception:
        log.debug("[VPIN] Failed to fetch trades for %s", condition_id[:12])
        return _default_result()

    # Classify trades as buy/sell by taker side
    buy_vol = 0.0
    sell_vol = 0.0
    count = 0

    for trade in trades:
        try:
            size = float(trade.get("size", 0))
            price = float(trade.get("price", 0))
            side = trade.get("side", "").upper()
            usd_value = size * price

            if side == "BUY":
                buy_vol += usd_value
            elif side == "SELL":
                sell_vol += usd_value
            else:
                # If no explicit side, use price movement heuristic
                # Trades above mid-price are likely buys
                buy_vol += usd_value * 0.5
                sell_vol += usd_value * 0.5

            count += 1
        except (ValueError, TypeError):
            continue

    total = buy_vol + sell_vol
    if total < 10 or count < 5:
        # Not enough data — assume clean
        return _default_result(trade_count=count, total_volume=total)

    # VPIN = |buy_volume - sell_volume| / total_volume
    vpin = abs(buy_vol - sell_vol) / total

    # Classify toxicity
    if vpin >= VPIN_HIGH:
        toxicity = "high"
        recommendation = "block"
        multiplier = 0.0
    elif vpin >= VPIN_MEDIUM:
        toxicity = "medium"
        recommendation = "reduce_size"
        multiplier = 0.5
    else:
        toxicity = "low"
        recommendation = "proceed"
        multiplier = 1.0

    result = VPINResult(
        vpin=round(vpin, 4),
        toxicity=toxicity,
        buy_volume=round(buy_vol, 2),
        sell_volume=round(sell_vol, 2),
        total_volume=round(total, 2),
        trade_count=count,
        recommendation=recommendation,
        size_multiplier=multiplier,
    )

    if toxicity != "low":
        log.info("[VPIN] %s toxicity: VPIN=%.4f buy=$%.0f sell=$%.0f (%d trades) | %s",
                 toxicity.upper(), vpin, buy_vol, sell_vol, count, condition_id[:12])

    return result


def _default_result(trade_count: int = 0, total_volume: float = 0.0) -> VPINResult:
    """Return a safe default when VPIN cannot be computed."""
    return VPINResult(
        vpin=0.0,
        toxicity="unknown",
        buy_volume=0.0,
        sell_volume=0.0,
        total_volume=total_volume,
        trade_count=trade_count,
        recommendation="proceed",
        size_multiplier=1.0,
    )
