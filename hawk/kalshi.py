"""Kalshi cross-platform arbitrage — compare Kalshi vs Polymarket prices.

Free read-only API, no authentication needed.
Cache TTL: 300s (5 min).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from difflib import SequenceMatcher

import requests

log = logging.getLogger(__name__)

_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"

# Cache
_market_cache: tuple[list[dict], float] = ([], 0.0)
_CACHE_TTL = 300  # 5 minutes

MATCH_THRESHOLD = 0.55  # Fuzzy match confidence threshold


@dataclass
class KalshiMatch:
    kalshi_ticker: str
    kalshi_title: str
    kalshi_price: float  # Yes price (0-1)
    polymarket_price: float  # Current Polymarket price
    price_divergence: float  # kalshi - polymarket (positive = Kalshi higher)
    match_confidence: float  # How confident the fuzzy match is (0-1)


def _fetch_kalshi_markets() -> list[dict]:
    """Fetch open markets from Kalshi API."""
    global _market_cache

    now = time.time()
    if _market_cache[0] and now - _market_cache[1] < _CACHE_TTL:
        return _market_cache[0]

    all_markets = []
    cursor = None

    try:
        for _ in range(5):  # Max 5 pages
            params = {"status": "open", "limit": 200}
            if cursor:
                params["cursor"] = cursor

            resp = requests.get(
                f"{_BASE_URL}/markets",
                params=params,
                timeout=15,
                headers={"Accept": "application/json"},
            )
            if resp.status_code != 200:
                log.debug("Kalshi HTTP %d", resp.status_code)
                break

            data = resp.json()
            markets = data.get("markets", [])
            all_markets.extend(markets)

            cursor = data.get("cursor")
            if not cursor or not markets:
                break

    except Exception as e:
        log.debug("Kalshi fetch failed: %s", str(e)[:100])

    _market_cache = (all_markets, now)
    log.debug("Kalshi: fetched %d open markets", len(all_markets))
    return all_markets


def _normalize(text: str) -> str:
    """Normalize text for fuzzy matching."""
    return text.lower().strip().replace("?", "").replace("will ", "").replace("  ", " ")


def _fuzzy_match(query: str, candidates: list[dict]) -> tuple[dict | None, float]:
    """Find best fuzzy match from Kalshi markets."""
    query_norm = _normalize(query)
    best_match = None
    best_score = 0.0

    for market in candidates:
        title = market.get("title", "")
        subtitle = market.get("subtitle", "")

        # Try matching against title and subtitle
        for text in [title, subtitle, f"{title} {subtitle}"]:
            score = SequenceMatcher(None, query_norm, _normalize(text)).ratio()
            if score > best_score:
                best_score = score
                best_match = market

    return best_match, best_score


def get_kalshi_divergence(
    question: str,
    polymarket_price: float,
) -> KalshiMatch | None:
    """Find matching Kalshi market and calculate price divergence.

    Args:
        question: Polymarket market question text
        polymarket_price: Current Polymarket YES price (0-1)

    Returns:
        KalshiMatch if a match is found above threshold, else None.
    """
    markets = _fetch_kalshi_markets()
    if not markets:
        return None

    match, confidence = _fuzzy_match(question, markets)
    if not match or confidence < MATCH_THRESHOLD:
        return None

    # Get Kalshi price (yes_ask or last_price)
    kalshi_price = None
    try:
        kalshi_price = float(match.get("yes_ask", 0)) / 100.0
        if kalshi_price <= 0:
            kalshi_price = float(match.get("last_price", 0)) / 100.0
    except (ValueError, TypeError):
        return None

    if not kalshi_price or kalshi_price <= 0:
        return None

    divergence = kalshi_price - polymarket_price

    result = KalshiMatch(
        kalshi_ticker=match.get("ticker", ""),
        kalshi_title=match.get("title", ""),
        kalshi_price=kalshi_price,
        polymarket_price=polymarket_price,
        price_divergence=divergence,
        match_confidence=confidence,
    )

    if abs(divergence) > 0.03:  # Only log significant divergences
        log.info(
            "[KALSHI] Match (%.0f%%): '%s' | Kalshi=%.2f Poly=%.2f Div=%+.1f%%",
            confidence * 100, match.get("title", "")[:50],
            kalshi_price, polymarket_price, divergence * 100,
        )

    return result
