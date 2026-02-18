"""Pre-execution orderbook depth analysis for Garves V3.

Fetches live orderbook via REST API before every trade to check:
1. Minimum liquidity (total book depth in $)
2. Maximum spread (best_ask - best_bid)
3. Slippage estimation for our order size

Blocks trades on illiquid markets where we'd get bad fills.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from bot.http_session import get_session

log = logging.getLogger(__name__)

# Orderbook quality thresholds
MIN_BOOK_LIQUIDITY_USD = 150.0   # Skip if total book depth < $150
MAX_SPREAD = 0.06                # Skip if spread > 6 cents ($0.06)
MAX_SLIPPAGE_PCT = 0.05          # Warn (but allow) if slippage > 5%
ORDERBOOK_TIMEOUT = 3            # REST timeout in seconds

# Cache to avoid hammering the API (token_id -> (timestamp, result))
_ob_cache: dict[str, tuple[float, OrderbookAnalysis]] = {}
CACHE_TTL = 15  # 15 second cache per token


@dataclass
class OrderbookAnalysis:
    """Results of orderbook depth analysis."""
    total_liquidity_usd: float
    bid_liquidity_usd: float
    ask_liquidity_usd: float
    best_bid: float
    best_ask: float
    spread: float
    estimated_slippage_pct: float
    depth_at_price: float  # liquidity within 2 cents of our price
    ok: bool
    reason: str


def _fetch_orderbook_rest(clob_host: str, token_id: str) -> dict | None:
    """Fetch orderbook from Polymarket REST API (no auth needed)."""
    try:
        resp = get_session().get(
            f"{clob_host}/book",
            params={"token_id": token_id},
            timeout=ORDERBOOK_TIMEOUT,
        )
        if resp.status_code == 200:
            return resp.json()
        log.debug("Orderbook REST returned %d for %s", resp.status_code, token_id[:16])
        return None
    except Exception as e:
        log.debug("Orderbook REST failed for %s: %s", token_id[:16], str(e)[:80])
        return None


def _estimate_slippage(levels: list[dict], order_size_usd: float, side: str) -> float:
    """Estimate price slippage for a given order size.

    For BUY orders, we walk up the ask side.
    Returns slippage as a fraction (0.02 = 2%).
    """
    if not levels:
        return 1.0  # No liquidity = 100% slippage (will block)

    remaining = order_size_usd
    weighted_price = 0.0
    total_filled = 0.0

    for level in levels:
        price = float(level.get("price", 0))
        size = float(level.get("size", 0))
        if price <= 0 or size <= 0:
            continue

        level_usd = price * size
        fill_usd = min(remaining, level_usd)
        fill_tokens = fill_usd / price

        weighted_price += price * fill_tokens
        total_filled += fill_tokens
        remaining -= fill_usd

        if remaining <= 0:
            break

    if total_filled <= 0:
        return 1.0

    avg_fill_price = weighted_price / total_filled
    best_price = float(levels[0].get("price", 0)) if levels else 0
    if best_price <= 0:
        return 1.0

    slippage = abs(avg_fill_price - best_price) / best_price
    return slippage


def check_orderbook_depth(
    clob_host: str,
    token_id: str,
    order_size_usd: float,
    target_price: float,
) -> tuple[bool, str, OrderbookAnalysis | None]:
    """Check orderbook depth before placing a trade.

    Returns:
        (allowed, reason, analysis)
        - allowed: True if trade should proceed
        - reason: Human-readable explanation if blocked
        - analysis: Full depth analysis data (for logging)
    """
    now = time.time()

    # Check cache first
    if token_id in _ob_cache:
        cached_time, cached_result = _ob_cache[token_id]
        if now - cached_time < CACHE_TTL:
            return cached_result.ok, cached_result.reason, cached_result

    # Fetch fresh orderbook
    data = _fetch_orderbook_rest(clob_host, token_id)

    if data is None:
        # Can't fetch orderbook — allow trade but log warning
        log.warning("Orderbook unavailable for %s — allowing trade (no data)", token_id[:16])
        return True, "orderbook_unavailable", None

    bids = data.get("bids", [])
    asks = data.get("asks", [])

    # Sort: bids descending by price, asks ascending by price
    bids = sorted(bids, key=lambda x: float(x.get("price", 0)), reverse=True)
    asks = sorted(asks, key=lambda x: float(x.get("price", 0)))

    # Calculate liquidity
    bid_liquidity = sum(float(b.get("price", 0)) * float(b.get("size", 0)) for b in bids)
    ask_liquidity = sum(float(a.get("price", 0)) * float(a.get("size", 0)) for a in asks)
    total_liquidity = bid_liquidity + ask_liquidity

    # Best bid/ask and spread
    best_bid = float(bids[0]["price"]) if bids else 0.0
    best_ask = float(asks[0]["price"]) if asks else 1.0
    spread = best_ask - best_bid if (bids and asks) else 1.0

    # Depth near our target price (within 2 cents)
    depth_at_price = 0.0
    for level in bids + asks:
        p = float(level.get("price", 0))
        s = float(level.get("size", 0))
        if abs(p - target_price) <= 0.02:
            depth_at_price += p * s

    # Estimate slippage for our order size (we're buying, walk the asks)
    slippage = _estimate_slippage(asks, order_size_usd, "buy")

    # Build analysis
    ok = True
    reason = "pass"

    if total_liquidity < MIN_BOOK_LIQUIDITY_USD:
        ok = False
        reason = f"thin_book: ${total_liquidity:.0f} < ${MIN_BOOK_LIQUIDITY_USD:.0f} min"
    elif spread > MAX_SPREAD:
        ok = False
        reason = f"wide_spread: ${spread:.3f} > ${MAX_SPREAD:.3f} max"
    elif slippage > MAX_SLIPPAGE_PCT:
        # Warn but still allow — slippage is estimated, not guaranteed
        reason = f"high_slippage: {slippage*100:.1f}% estimated (warning only)"
        log.warning("High slippage estimated: %.1f%% for $%.0f order on %s",
                     slippage * 100, order_size_usd, token_id[:16])

    analysis = OrderbookAnalysis(
        total_liquidity_usd=total_liquidity,
        bid_liquidity_usd=bid_liquidity,
        ask_liquidity_usd=ask_liquidity,
        best_bid=best_bid,
        best_ask=best_ask,
        spread=spread,
        estimated_slippage_pct=slippage,
        depth_at_price=depth_at_price,
        ok=ok,
        reason=reason,
    )

    # Cache result
    _ob_cache[token_id] = (now, analysis)

    return ok, reason, analysis
