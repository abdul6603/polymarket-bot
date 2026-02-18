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
TF_WEEKLY = Timeframe(name="weekly", priority=5, min_remaining_s=3600, max_remaining_s=604800)

# Assets we trade
ASSETS = {
    "bitcoin": {"keywords": ("bitcoin up or down",), "coingecko_id": "bitcoin"},
    "ethereum": {"keywords": ("ethereum up or down",), "coingecko_id": "ethereum"},
    "solana": {"keywords": ("solana up or down",), "coingecko_id": "solana"},
    "xrp": {"keywords": ("xrp up or down",), "coingecko_id": "ripple"},
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
        elif duration <= 1440:
            return TF_WEEKLY  # >4h up to 24h
        return None

    if _HOURLY_RE.search(question):
        return TF_1H

    # Weekly markets: check for "weekly" keyword or very long durations
    q_lower = question.lower()
    if "weekly" in q_lower:
        return TF_WEEKLY

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
    # If the market month is far behind the current month, it's likely next year
    # Handle January wrap: month_num=12 when now.month=1 should NOT advance year
    months_behind = now.month - month_num
    if months_behind > 6:
        year += 1
    elif months_behind < -6:
        year -= 1

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

    # Build the datetime in ET (DST-aware)
    from zoneinfo import ZoneInfo
    et_tz = ZoneInfo("America/New_York")
    try:
        candle_end = datetime(year, month_num, int(day_str), hour % 24, minute, tzinfo=et_tz)
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

# Gamma API cache for hourly markets (not in CLOB paginated list)
_gamma_cache: list[dict[str, Any]] = []
_gamma_cached_at: float = 0
_GAMMA_CACHE_TTL = 120  # refresh every 2 minutes


def _fetch_hourly_from_gamma() -> list[dict[str, Any]]:
    """Fetch hourly crypto Up/Down markets from the Gamma events API.

    These markets use the 'XPM ET' format (e.g. "Bitcoin Up or Down - February 17, 8PM ET")
    and are NOT returned by the CLOB paginated /markets endpoint.
    We query Gamma, then fetch full market data from CLOB by condition_id.
    """
    global _gamma_cache, _gamma_cached_at

    now = time.time()
    if _gamma_cache and (now - _gamma_cached_at) < _GAMMA_CACHE_TTL:
        return _gamma_cache

    results: list[dict[str, Any]] = []
    seen_cids: set[str] = set()

    # Build slug patterns for current + next day hourly markets
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo
    et_now = _dt.now(ZoneInfo("America/New_York"))

    # Generate slugs for current and nearby hours across all assets
    slug_prefixes = {
        "bitcoin": "bitcoin-up-or-down",
        "ethereum": "ethereum-up-or-down",
        "solana": "solana-up-or-down",
        "xrp": "xrp-up-or-down",
    }

    for asset_name, prefix in slug_prefixes.items():
        # Check current hour and next few hours
        for hour_offset in range(0, 4):
            target = et_now + timedelta(hours=hour_offset)
            month_name = target.strftime("%B").lower()
            day = target.day
            hour = target.hour
            ampm = "am" if hour < 12 else "pm"
            display_hour = hour % 12
            if display_hour == 0:
                display_hour = 12
            slug = f"{prefix}-{month_name}-{day}-{display_hour}{ampm}-et"

            try:
                resp = get_session().get(
                    f"https://gamma-api.polymarket.com/events?slug={slug}",
                    timeout=5,
                )
                if resp.status_code != 200:
                    continue
                events = resp.json()
                if not events:
                    continue

                ev = events[0]
                for m in ev.get("markets", []):
                    cid = m.get("conditionId", "")
                    if not cid or cid in seen_cids:
                        continue
                    if not m.get("active") or m.get("closed"):
                        continue

                    # Fetch full CLOB data for this market
                    clob_resp = get_session().get(
                        f"https://clob.polymarket.com/markets/{cid}",
                        timeout=5,
                    )
                    if clob_resp.status_code == 200:
                        clob_market = clob_resp.json()
                        if clob_market.get("accepting_orders") and clob_market.get("active"):
                            seen_cids.add(cid)
                            results.append(clob_market)
            except Exception:
                continue

    if results:
        log.info("Gamma hourly discovery: found %d markets", len(results))

    _gamma_cache = results
    _gamma_cached_at = now
    return results


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

    # Fetch hourly markets from Gamma API (not in CLOB paginated list)
    try:
        gamma_markets = _fetch_hourly_from_gamma()
        for m in gamma_markets:
            cid = m.get("condition_id", "")
            if cid not in seen_ids and _detect_asset(m.get("question", "")) is not None:
                seen_ids.add(cid)
                all_active.append(m)
    except Exception:
        log.debug("Gamma hourly fetch failed, continuing with CLOB-only markets")

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
