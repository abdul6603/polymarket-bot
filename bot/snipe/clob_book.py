"""CLOB orderbook bridge — fetches live bid/ask from Polymarket REST API.

The Polymarket WebSocket "market" channel only sends price_change events,
NOT full orderbook (book) events. So we fetch orderbook snapshots via the
REST /book endpoint with a 5-second cache to avoid hammering the API.

The snipe engine calls get_orderbook() only when scoring candidates that
already passed the delta pre-filter, so requests are infrequent (~5-10/window).

Usage:
    # In Garves main (once during init):
    from bot.snipe import clob_book
    clob_book.init("https://clob.polymarket.com")

    # In snipe engine (every tick, during scoring):
    book = clob_book.get_orderbook(token_id)
"""
from __future__ import annotations

import logging
import time

log = logging.getLogger("garves.snipe")

_clob_host: str = ""
_cache: dict[str, tuple[float, dict]] = {}  # token_id -> (timestamp, result)
CACHE_TTL = 2  # 2 seconds — fast enough for flow detection at 2s tick rate


def init(clob_host: str) -> None:
    """Set the CLOB host URL. Called once from Garves main."""
    global _clob_host
    _clob_host = clob_host


def set_feed(feed) -> None:
    """Legacy compat — init from feed's config instead."""
    if feed and hasattr(feed, "cfg") and hasattr(feed.cfg, "clob_host"):
        init(feed.cfg.clob_host)


def _fetch_book(token_id: str) -> dict | None:
    """Fetch raw orderbook from Polymarket REST API."""
    if not _clob_host:
        return None
    try:
        from bot.http_session import get_session
        resp = get_session().get(
            f"{_clob_host}/book",
            params={"token_id": token_id},
            timeout=3,
        )
        if resp.status_code == 200:
            return resp.json()
        log.debug("[CLOB_BOOK] REST %d for %s...", resp.status_code, token_id[:16])
    except Exception as e:
        log.debug("[CLOB_BOOK] REST error for %s...: %s", token_id[:16], str(e)[:80])
    return None


def _parse_book(data: dict) -> dict:
    """Parse raw orderbook into pressure/spread metrics."""
    bids = data.get("bids", [])
    asks = data.get("asks", [])

    def _sum_levels(levels: list, n: int = 5) -> tuple[float, float]:
        """Sum price*size for top N levels. Returns (pressure, best_price)."""
        pressure = 0.0
        best = 0.0
        for lvl in levels[:n]:
            if isinstance(lvl, dict):
                p = float(lvl.get("price", 0))
                s = float(lvl.get("size", 0))
            elif isinstance(lvl, (list, tuple)) and len(lvl) >= 2:
                p, s = float(lvl[0]), float(lvl[1])
            else:
                continue
            pressure += p * s
            if best == 0.0:
                best = p
        return pressure, best

    buy_pressure, best_bid = _sum_levels(bids)
    sell_pressure, best_ask = _sum_levels(asks)
    spread = best_ask - best_bid if best_bid > 0 and best_ask > 0 else 0.0

    return {
        "buy_pressure": buy_pressure,
        "sell_pressure": sell_pressure,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": spread,
    }


def get_orderbook(token_id: str) -> dict | None:
    """Get latest orderbook snapshot for a token.

    Returns dict with: buy_pressure, sell_pressure, best_bid, best_ask, spread.
    Uses 5s REST cache. Returns None if unavailable.
    """
    now = time.time()

    # Check cache
    if token_id in _cache:
        cached_at, cached_result = _cache[token_id]
        if now - cached_at < CACHE_TTL:
            return cached_result

    # Fetch fresh
    raw = _fetch_book(token_id)
    if raw is None:
        return _cache.get(token_id, (0, None))[1]  # Return stale if available

    result = _parse_book(raw)
    _cache[token_id] = (now, result)

    log.info(
        "[CLOB_BOOK] %s... bid=%.3f ask=%.3f spread=%.4f buy_p=%.1f sell_p=%.1f",
        token_id[:16], result["best_bid"], result["best_ask"],
        result["spread"], result["buy_pressure"], result["sell_pressure"],
    )
    return result


def get_spread(token_id: str) -> float | None:
    """Get current spread for a token. Returns None if no data."""
    book = get_orderbook(token_id)
    return book["spread"] if book else None


def get_mid_price(token_id: str) -> float | None:
    """Get mid price (avg of best bid + best ask). Returns None if no data."""
    book = get_orderbook(token_id)
    if not book or book["best_bid"] <= 0 or book["best_ask"] <= 0:
        return None
    return (book["best_bid"] + book["best_ask"]) / 2
