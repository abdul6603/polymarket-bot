"""Mempool.space BTC network congestion indicator.

BTC-specific: uses mempool fee data as a whale activity proxy.
Fee spikes indicate network stress / large tx volume.

Free API, no key needed.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from bot.http_session import get_session

log = logging.getLogger(__name__)

_BASE_URL = "https://mempool.space/api"

# Cache
_cache: dict[str, tuple["MempoolData | None", float]] = {}
_CACHE_TTL = 30  # 30s — fast-changing data


@dataclass
class MempoolData:
    # Current recommended fees (sat/vB)
    fastest_fee: float = 0.0
    half_hour_fee: float = 0.0
    hour_fee: float = 0.0
    economy_fee: float = 0.0

    # Mempool stats
    tx_count: int = 0
    total_vsize: float = 0.0  # total virtual size in mempool (MB)

    # Derived
    fee_ratio_vs_baseline: float = 1.0  # current / baseline (>3 = spike)
    congestion_level: str = "normal"  # "normal", "elevated", "high", "extreme"

    timestamp: float = 0.0


# Rolling baseline for fee comparison
_fee_history: list[float] = []
_FEE_HISTORY_MAX = 360  # ~3 hours at 30s intervals


def get_data() -> MempoolData | None:
    """Fetch BTC mempool data.

    Returns None if API fails.
    """
    now = time.time()
    cached = _cache.get("mempool")
    if cached and now - cached[1] < _CACHE_TTL:
        return cached[0]

    result = MempoolData(timestamp=now)
    any_success = False

    # 1. Recommended fees
    try:
        resp = get_session().get(f"{_BASE_URL}/v1/fees/recommended", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            result.fastest_fee = float(data.get("fastestFee", 0))
            result.half_hour_fee = float(data.get("halfHourFee", 0))
            result.hour_fee = float(data.get("hourFee", 0))
            result.economy_fee = float(data.get("economyFee", 0))
            any_success = True
    except Exception as e:
        log.debug("Mempool fees fetch failed: %s", str(e)[:100])

    # 2. Mempool stats
    try:
        resp = get_session().get(f"{_BASE_URL}/mempool", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            result.tx_count = int(data.get("count", 0))
            result.total_vsize = float(data.get("vsize", 0)) / 1e6  # bytes -> MB
            any_success = True
    except Exception as e:
        log.debug("Mempool stats fetch failed: %s", str(e)[:100])

    if not any_success:
        _cache["mempool"] = (None, now)
        return None

    # Calculate fee ratio vs rolling baseline
    current_fee = result.fastest_fee
    _fee_history.append(current_fee)
    if len(_fee_history) > _FEE_HISTORY_MAX:
        _fee_history.pop(0)

    if len(_fee_history) >= 10:
        baseline = sum(_fee_history) / len(_fee_history)
        if baseline > 0:
            result.fee_ratio_vs_baseline = current_fee / baseline
    else:
        result.fee_ratio_vs_baseline = 1.0

    # Congestion level
    ratio = result.fee_ratio_vs_baseline
    if ratio >= 5.0:
        result.congestion_level = "extreme"
    elif ratio >= 3.0:
        result.congestion_level = "high"
    elif ratio >= 1.5:
        result.congestion_level = "elevated"
    else:
        result.congestion_level = "normal"

    _cache["mempool"] = (result, now)
    log.debug(
        "[MEMPOOL] Fee: %d sat/vB (ratio: %.1fx) | TX: %d | vSize: %.1f MB | %s",
        current_fee, ratio, result.tx_count, result.total_vsize,
        result.congestion_level.upper(),
    )
    return result
