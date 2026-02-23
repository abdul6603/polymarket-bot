"""Market Bridge — maps 5m scanner signals to 15m/1h execution markets.

5m CLOB books are structurally dead (no mid-price liquidity).
This module finds the real-liquidity 15m or 1h market for the same asset
so the engine can execute where fills actually happen.

Slug patterns mirror market_discovery.py:
  15m: {short}-updown-15m-{unix_15m_boundary}
  1h:  {asset}-up-or-down-{month}-{day}-{hour}{ampm}-et
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

log = logging.getLogger("garves.snipe")

ET = ZoneInfo("America/New_York")
CACHE_TTL = 60  # seconds

ASSET_SHORT = {"bitcoin": "btc", "ethereum": "eth", "solana": "sol", "xrp": "xrp"}
ASSET_LONG = {
    "bitcoin": "bitcoin-up-or-down",
    "ethereum": "ethereum-up-or-down",
    "solana": "solana-up-or-down",
    "xrp": "xrp-up-or-down",
}


@dataclass
class ExecutionMarket:
    """Resolved execution venue for a 5m signal."""
    market_id: str
    up_token_id: str
    down_token_id: str
    timeframe: str       # "15m" or "1h"
    end_ts: float        # Unix timestamp when market resolves
    slug: str = ""


# slug -> (ExecutionMarket | None, fetched_at)
_cache: dict[str, tuple[ExecutionMarket | None, float]] = {}


def find_execution_market(
    asset: str, preference: str = "15m",
) -> ExecutionMarket | None:
    """Find the nearest 15m or 1h execution market for an asset.

    Args:
        asset: "bitcoin", "ethereum", "solana", "xrp"
        preference: "15m" (default) or "1h"

    Returns:
        ExecutionMarket with token IDs, or None if no market found.
        Falls through preference -> alternate timeframe.
    """
    if preference == "15m":
        order = ["15m", "1h"]
    else:
        order = ["1h", "15m"]

    for tf in order:
        result = _find_for_timeframe(asset, tf)
        if result:
            return result
    return None


def _find_for_timeframe(asset: str, tf: str) -> ExecutionMarket | None:
    """Try to find an active market for a specific timeframe."""
    now = datetime.now(ET)
    slugs = _build_slugs(asset, tf, now)

    for slug, end_ts in slugs:
        # Check cache
        cached = _cache.get(slug)
        if cached:
            mkt, fetched_at = cached
            if time.time() - fetched_at < CACHE_TTL:
                return mkt

        # Fetch from Gamma -> CLOB
        mkt = _fetch_market(slug, tf, end_ts)
        _cache[slug] = (mkt, time.time())
        if mkt:
            log.info(
                "[BRIDGE] Found %s market for %s: %s (ends %.0fs)",
                tf, asset, mkt.market_id[:16], mkt.end_ts - time.time(),
            )
            return mkt

    return None


def _build_slugs(
    asset: str, tf: str, now: datetime,
) -> list[tuple[str, float]]:
    """Build candidate slugs for the given timeframe.

    Returns list of (slug, estimated_end_ts) tuples.
    """
    results: list[tuple[str, float]] = []

    if tf == "15m":
        short = ASSET_SHORT.get(asset)
        if not short:
            return results
        min_15 = (now.minute // 15) * 15
        start = now.replace(minute=min_15, second=0, microsecond=0)
        for slot in range(0, 3):  # current + next 2
            target = start + timedelta(minutes=15 * slot)
            ts = int(target.timestamp())
            end_ts = ts + 900  # 15 minutes
            # Skip if already expired or about to expire (< 60s)
            if end_ts - time.time() < 60:
                continue
            results.append((f"{short}-updown-15m-{ts}", float(end_ts)))

    elif tf == "1h":
        prefix = ASSET_LONG.get(asset)
        if not prefix:
            return results
        for hour_offset in range(0, 2):  # current + next
            target = now + timedelta(hours=hour_offset)
            target = target.replace(minute=0, second=0, microsecond=0)
            month_name = target.strftime("%B").lower()
            day = target.day
            hour = target.hour
            ampm = "am" if hour < 12 else "pm"
            dh = hour % 12 or 12
            slug = f"{prefix}-{month_name}-{day}-{dh}{ampm}-et"
            end_ts = float(int(target.timestamp()) + 3600)
            if end_ts - time.time() < 60:
                continue
            results.append((slug, end_ts))

    return results


def _fetch_market(
    slug: str, tf: str, end_ts: float,
) -> ExecutionMarket | None:
    """Fetch a market from Gamma API by slug, then resolve token IDs."""
    try:
        from bot.http_session import get_session
        sess = get_session()

        resp = sess.get(
            f"https://gamma-api.polymarket.com/events?slug={slug}",
            timeout=5,
        )
        if resp.status_code != 200 or not resp.json():
            return None

        ev = resp.json()[0]
        for m in ev.get("markets", []):
            if not m.get("active") or m.get("closed"):
                continue
            cid = m.get("conditionId", "")
            if not cid:
                continue

            # Resolve token IDs from CLOB
            from bot.config import Config
            cfg = Config()
            clob_resp = sess.get(
                f"{cfg.clob_host}/markets/{cid}",
                timeout=5,
            )
            if clob_resp.status_code != 200:
                continue
            clob_data = clob_resp.json()
            tokens = clob_data.get("tokens", [])
            if len(tokens) < 2:
                continue

            up_id = ""
            down_id = ""
            for t in tokens:
                outcome = (t.get("outcome") or "").lower()
                if outcome in ("up", "yes"):
                    up_id = t.get("token_id", "")
                elif outcome in ("down", "no"):
                    down_id = t.get("token_id", "")

            if up_id and down_id:
                # Use gameStartTime/endDate for accurate end_ts
                gst = m.get("gameStartTime") or m.get("endDate") or ""
                if gst:
                    try:
                        from datetime import datetime as _dt
                        clean = gst.strip().replace(" ", "T")
                        if clean.endswith("+00"):
                            clean = clean[:-3] + "+00:00"
                        elif not clean.endswith("Z") and "+" not in clean[10:]:
                            clean += "+00:00"
                        parsed = _dt.fromisoformat(clean.replace("Z", "+00:00"))
                        end_ts = parsed.timestamp()
                    except Exception:
                        pass

                return ExecutionMarket(
                    market_id=cid,
                    up_token_id=up_id,
                    down_token_id=down_id,
                    timeframe=tf,
                    end_ts=end_ts,
                    slug=slug,
                )
    except Exception as e:
        log.warning("[BRIDGE] Fetch failed for %s: %s", slug, str(e)[:100])
    return None
