from __future__ import annotations

import base64
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from bot.config import Config
from bot.http_session import get_session

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Timeframe:
    name: str
    priority: int  # higher = preferred
    min_remaining_s: int
    max_remaining_s: int


TF_5M = Timeframe(name="5m", priority=1, min_remaining_s=10, max_remaining_s=300)
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
            return TF_5M
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

# Gamma API cache for active crypto up/down markets
_gamma_cache: list[dict[str, Any]] = []
_gamma_cached_at: float = 0
_GAMMA_CACHE_TTL = 90  # refresh every 90 seconds

# 5m rolling market cache (slug-based lookup, separate from broad Gamma search)
_5m_cache: list[dict[str, Any]] = []
_5m_cached_at: float = 0
_5M_CACHE_TTL = 30  # refresh every 30 seconds

# Slug prefixes for 5m rolling markets → asset name
_5M_SLUG_PREFIXES = ("btc", "eth", "sol", "xrp")


def _fetch_active_from_gamma() -> list[dict[str, Any]]:
    """Fetch active crypto Up/Down markets via Gamma slug-based lookups.

    Uses predictable slug patterns for each timeframe:
      Hourly: {asset}-up-or-down-{month}-{day}-{hour}{ampm}-et
      15m:    {short}-updown-15m-{unix_start}
      4h:     {short}-updown-4h-{unix_start}
      5m:     handled by snipe engine separately

    Fetches full CLOB data only for markets found via slug.
    """
    global _gamma_cache, _gamma_cached_at

    now = time.time()
    if _gamma_cache and (now - _gamma_cached_at) < _GAMMA_CACHE_TTL:
        return _gamma_cache

    results: list[dict[str, Any]] = []
    seen_cids: set[str] = set()
    sess = get_session()

    from datetime import datetime as _dt
    et_now = _dt.now(ZoneInfo("America/New_York"))

    asset_short = {"bitcoin": "btc", "ethereum": "eth", "solana": "sol", "xrp": "xrp"}
    asset_long = {
        "bitcoin": "bitcoin-up-or-down",
        "ethereum": "ethereum-up-or-down",
        "solana": "solana-up-or-down",
        "xrp": "xrp-up-or-down",
    }

    slugs: list[str] = []

    # ── Hourly: slug = {asset}-up-or-down-{month}-{day}-{hour}{ampm}-et
    for _asset, prefix in asset_long.items():
        for hour_offset in range(0, 4):
            target = et_now + timedelta(hours=hour_offset)
            month_name = target.strftime("%B").lower()
            day = target.day
            hour = target.hour
            ampm = "am" if hour < 12 else "pm"
            dh = hour % 12 or 12
            slugs.append(f"{prefix}-{month_name}-{day}-{dh}{ampm}-et")

    # ── 15m: slug = {short}-updown-15m-{unix_start}
    min_15 = (et_now.minute // 15) * 15
    start_15m = et_now.replace(minute=min_15, second=0, microsecond=0)
    for _asset, short in asset_short.items():
        for slot in range(0, 4):  # current + next 3 windows
            target = start_15m + timedelta(minutes=15 * slot)
            ts = int(target.timestamp())
            slugs.append(f"{short}-updown-15m-{ts}")

    # ── 4h: slug = {short}-updown-4h-{unix_start}
    start_4h = et_now.replace(hour=(et_now.hour // 4) * 4, minute=0, second=0, microsecond=0)
    for _asset, short in asset_short.items():
        for slot in range(0, 2):  # current + next window
            target = start_4h + timedelta(hours=4 * slot)
            ts = int(target.timestamp())
            slugs.append(f"{short}-updown-4h-{ts}")

    # Fetch each slug from Gamma, then CLOB
    for slug in slugs:
        try:
            resp = sess.get(
                f"https://gamma-api.polymarket.com/events?slug={slug}",
                timeout=5,
            )
            if resp.status_code != 200 or not resp.json():
                continue
            ev = resp.json()[0]
            for m in ev.get("markets", []):
                cid = m.get("conditionId", "")
                if not cid or cid in seen_cids:
                    continue
                if not m.get("active") or m.get("closed"):
                    continue
                seen_cids.add(cid)

                # Fetch full CLOB market data
                clob_resp = sess.get(
                    f"https://clob.polymarket.com/markets/{cid}",
                    timeout=5,
                )
                if clob_resp.status_code == 200:
                    clob_market = clob_resp.json()
                    if clob_market.get("accepting_orders") and clob_market.get("active"):
                        results.append(clob_market)
        except Exception:
            continue

    if results:
        log.info("Gamma discovery: found %d markets (1h+15m+4h)", len(results))

    _gamma_cache = results
    _gamma_cached_at = now
    return results


def _fetch_5m_rolling() -> list[dict[str, Any]]:
    """Fetch 5m rolling crypto Up/Down markets via Gamma slug lookup.

    These markets use timestamp-based slugs like 'btc-updown-5m-{unix_ts}'
    where ts aligns to the 5-minute boundary. They are NOT returned by the
    CLOB paginated endpoint or the broad Gamma events search.
    """
    global _5m_cache, _5m_cached_at

    now = time.time()
    if _5m_cache and (now - _5m_cached_at) < _5M_CACHE_TTL:
        return _5m_cache

    results: list[dict[str, Any]] = []
    seen_cids: set[str] = set()

    # Check current 5m interval and the next one
    current_ts = int(now // 300) * 300
    intervals = [current_ts, current_ts + 300]

    for ts in intervals:
        for coin in _5M_SLUG_PREFIXES:
            slug = f"{coin}-updown-5m-{ts}"
            try:
                resp = get_session().get(
                    "https://gamma-api.polymarket.com/markets",
                    params={"slug": slug},
                    timeout=5,
                )
                if resp.status_code != 200:
                    continue
                markets = resp.json()
                if not markets:
                    continue

                for m in markets:
                    cid = m.get("conditionId") or m.get("condition_id", "")
                    if not cid or cid in seen_cids:
                        continue
                    if m.get("closed"):
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
        log.info("5m rolling discovery: found %d markets", len(results))

    _5m_cache = results
    _5m_cached_at = now
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

    # Fetch active markets from Gamma API (catches 15m, 1h, 4h that CLOB scan misses)
    try:
        gamma_markets = _fetch_active_from_gamma()
        for m in gamma_markets:
            cid = m.get("condition_id", "")
            if cid not in seen_ids and _detect_asset(m.get("question", "")) is not None:
                seen_ids.add(cid)
                all_active.append(m)
    except Exception:
        log.debug("Gamma active fetch failed, continuing with CLOB-only markets")

    # Fetch 5m rolling markets (slug-based, invisible to both CLOB scan and broad Gamma)
    try:
        rolling_5m = _fetch_5m_rolling()
        for m in rolling_5m:
            cid = m.get("condition_id", "")
            if cid not in seen_ids and _detect_asset(m.get("question", "")) is not None:
                seen_ids.add(cid)
                all_active.append(m)
    except Exception:
        log.debug("5m rolling fetch failed, continuing without 5m markets")

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

    # Sort by: highest priority first, then soonest expiry (return new list, don't mutate input)
    return sorted(markets, key=lambda dm: (-dm.timeframe.priority, dm.remaining_s))
