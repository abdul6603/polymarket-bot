from __future__ import annotations

import base64
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any

from bot.config import Config
from bot.http_session import get_session

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Timeframe:
    name: str
    priority: int  # higher = preferred
    min_remaining_s: int
    max_remaining_s: int


TF_5M = Timeframe(name="5m", priority=1, min_remaining_s=60, max_remaining_s=300)
TF_15M = Timeframe(name="15m", priority=2, min_remaining_s=120, max_remaining_s=900)
TF_1H = Timeframe(name="1h", priority=3, min_remaining_s=300, max_remaining_s=3600)
TF_4H = Timeframe(name="4h", priority=4, min_remaining_s=900, max_remaining_s=14400)

# Assets we trade
ASSETS = {
    "bitcoin": {"keywords": ("bitcoin up or down",), "coingecko_id": "bitcoin"},
    "ethereum": {"keywords": ("ethereum up or down",), "coingecko_id": "ethereum"},
    "solana": {"keywords": ("solana up or down",), "coingecko_id": "solana"},
}


@dataclass
class DiscoveredMarket:
    raw: dict[str, Any]
    timeframe: Timeframe
    remaining_s: float
    market_id: str
    question: str
    asset: str  # "bitcoin" or "ethereum"


# Regex to parse time ranges from market questions
# "Bitcoin Up or Down - February 14, 10:00PM-10:05PM ET"  → 5 min
# "Bitcoin Up or Down - February 14, 10:00PM-10:15PM ET"  → 15 min
# "Bitcoin Up or Down - February 14, 10PM ET"             → 1 hour
_RANGE_RE = re.compile(
    r"(\d{1,2}):?(\d{2})?(AM|PM)-(\d{1,2}):?(\d{2})?(AM|PM)\s+ET",
    re.IGNORECASE,
)
_HOURLY_RE = re.compile(
    r"(\d{1,2})(AM|PM)\s+ET$",
    re.IGNORECASE,
)


def _classify_timeframe(question: str) -> Timeframe | None:
    """Determine the timeframe of a market from its question text."""
    m = _RANGE_RE.search(question)
    if m:
        h1, m1, ap1, h2, m2, ap2 = m.groups()
        start_min = (int(h1) % 12 + (12 if ap1.upper() == "PM" else 0)) * 60 + int(m1 or 0)
        end_min = (int(h2) % 12 + (12 if ap2.upper() == "PM" else 0)) * 60 + int(m2 or 0)
        duration = end_min - start_min
        if duration < 0:
            duration += 24 * 60  # crosses midnight
        if duration <= 5:
            return None  # Skip 5m — too noisy, 25% win rate
        elif duration <= 15:
            return TF_15M
        elif duration <= 60:
            return TF_1H
        elif duration <= 240:
            return TF_4H
        return None

    if _HOURLY_RE.search(question):
        return TF_1H

    return None


def _parse_candle_end_time(question: str, end_date_iso: str) -> float | None:
    """Parse the actual candle end time from the question text.

    For ranged markets like "10:00PM-10:05PM ET", the candle end is 10:05PM ET.
    For hourly like "10PM ET", the candle end is 11PM ET.
    """
    date_match = re.search(
        r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})",
        question,
    )
    if not date_match:
        return None

    month_str, day_str = date_match.groups()
    now = datetime.now(timezone.utc)
    try:
        month_num = datetime.strptime(month_str, "%B").month
    except ValueError:
        return None

    year = now.year
    if month_num < now.month - 1:
        year += 1

    # Get the end time of the candle
    m = _RANGE_RE.search(question)
    if m:
        _, _, _, h2, m2, ap2 = m.groups()
        hour = int(h2) % 12 + (12 if ap2.upper() == "PM" else 0)
        minute = int(m2 or 0)
    else:
        hm = _HOURLY_RE.search(question)
        if hm:
            hour = int(hm.group(1)) % 12 + (12 if hm.group(2).upper() == "PM" else 0)
            hour += 1  # hourly candle ends 1 hour later
            minute = 0
        else:
            return None

    # Build the datetime in ET (UTC-5)
    et_offset = timezone(timedelta(hours=-5))
    try:
        candle_end = datetime(year, month_num, int(day_str), hour % 24, minute, tzinfo=et_offset)
        # Handle hour overflow (e.g. 11PM + 1h = midnight next day)
        if hour >= 24:
            candle_end += timedelta(days=1)
    except ValueError:
        return None

    return candle_end.timestamp()


def _detect_asset(question: str) -> str | None:
    """Detect which asset a market question refers to."""
    q = question.lower()
    for asset_name, info in ASSETS.items():
        if any(kw in q for kw in info["keywords"]):
            return asset_name
    return None


def _find_market_offset(cfg: Config) -> int:
    """Find the offset where active Up/Down markets live.

    Binary-searches for the total market count, then scans backwards.
    """
    lo, hi = 0, 600000
    while hi - lo > 1000:
        mid = (lo + hi) // 2
        cursor = base64.b64encode(str(mid).encode()).decode()
        try:
            resp = get_session().get(
                f"{cfg.clob_host}/markets",
                params={"limit": 10, "next_cursor": cursor},
                timeout=10,
            )
            data = resp.json()
            has_data = bool(data.get("data"))
        except Exception:
            has_data = False

        if has_data:
            lo = mid
        else:
            hi = mid

    total_approx = lo
    log.info("Approximate total markets: %d", total_approx)

    # Scan backwards from the end to find active Up/Down markets
    for probe in range(total_approx, max(total_approx - 60000, 0), -5000):
        cursor = base64.b64encode(str(probe).encode()).decode()
        try:
            resp = get_session().get(
                f"{cfg.clob_host}/markets",
                params={"limit": 1000, "next_cursor": cursor},
                timeout=10,
            )
            data = resp.json()
            markets = data.get("data", [])
        except Exception:
            continue

        if not markets:
            continue

        has_active = any(
            "up or down" in (m.get("question") or "").lower()
            and m.get("accepting_orders")
            for m in markets
        )
        if has_active:
            result = max(probe - 5000, 0)
            log.info("Found active Up/Down markets near offset %d", probe)
            return result

    return max(total_approx - 30000, 0)


# Module-level cache so we don't re-search every tick
_cached_offset: int | None = None
_offset_cached_at: float = 0
_OFFSET_CACHE_TTL = 600  # re-search every 10 minutes


def fetch_markets(cfg: Config) -> list[DiscoveredMarket]:
    """Fetch active Bitcoin & Ethereum Up/Down markets from the CLOB API."""
    global _cached_offset, _offset_cached_at

    now = time.time()

    if _cached_offset is None or (now - _offset_cached_at) > _OFFSET_CACHE_TTL:
        log.info("Searching for market offset range...")
        _cached_offset = _find_market_offset(cfg)
        _offset_cached_at = now
        log.info("Market offset range starts at ~%d", _cached_offset)

    # Scan a window around the cached offset
    all_active: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for page_start in range(_cached_offset, _cached_offset + 30000, 1000):
        cursor = base64.b64encode(str(page_start).encode()).decode()
        try:
            resp = get_session().get(
                f"{cfg.clob_host}/markets",
                params={"limit": 1000, "next_cursor": cursor},
                timeout=10,
            )
            data = resp.json()
            markets = data.get("data", [])
        except Exception:
            log.exception("Failed to fetch CLOB markets at offset %d", page_start)
            continue

        if not markets:
            break

        for m in markets:
            q = (m.get("question") or "").lower()
            cid = m.get("condition_id", "")
            if (
                "up or down" in q
                and m.get("accepting_orders")
                and m.get("active")
                and not m.get("closed")
                and cid not in seen_ids
                and _detect_asset(m.get("question", "")) is not None
            ):
                seen_ids.add(cid)
                all_active.append(m)

    # Classify into timeframes and compute remaining time
    results: list[DiscoveredMarket] = []
    for m in all_active:
        question = m.get("question", "")
        asset = _detect_asset(question)
        if not asset:
            continue

        tf = _classify_timeframe(question)
        if not tf:
            continue

        candle_end = _parse_candle_end_time(question, m.get("end_date_iso", ""))
        if candle_end is None:
            continue

        remaining = candle_end - now
        if remaining < tf.min_remaining_s or remaining > tf.max_remaining_s:
            continue

        results.append(DiscoveredMarket(
            raw=m,
            timeframe=tf,
            remaining_s=remaining,
            market_id=m.get("condition_id", ""),
            question=question[:100],
            asset=asset,
        ))

    # Log summary
    by_key: dict[str, int] = {}
    for dm in results:
        key = f"{dm.asset}/{dm.timeframe.name}"
        by_key[key] = by_key.get(key, 0) + 1
    summary = ", ".join(f"{k}: {v}" for k, v in sorted(by_key.items()))
    log.info("Found markets — %s (total: %d)", summary or "none", len(results))
    return results


def rank_markets(markets: list[DiscoveredMarket]) -> list[DiscoveredMarket]:
    """Return all markets sorted by priority (higher timeframe first, then soonest expiry)."""
    if not markets:
        return []

    # Sort by: highest priority first, then soonest expiry
    markets.sort(key=lambda dm: (-dm.timeframe.priority, dm.remaining_s))
    return markets
