"""Kalshi V2 — Deep cross-platform integration for Hawk V7.

Uses Kalshi's event + market API for structured matching instead of pure fuzzy search.
Strategies:
  1. Event-level category browse → find related events → match markets
  2. Multi-keyword extraction from question → score against Kalshi titles
  3. Entity-based matching (names, dates, numbers) for high-confidence pairs

Free read-only API, no auth needed. Cache: 5 min per category.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from difflib import SequenceMatcher

from bot.http_session import get_session

log = logging.getLogger(__name__)

_BASE_URL = "https://trading-api.kalshi.com/trade-api/v2"

# Category-level cache: category → (markets, timestamp)
_cat_cache: dict[str, tuple[list[dict], float]] = {}
_all_cache: tuple[list[dict], float] = ([], 0.0)
_CACHE_TTL = 300  # 5 min

# Polymarket → Kalshi category mapping
_CATEGORY_MAP = {
    "politics": ["Politics"],
    "culture": ["Culture", "Entertainment"],
    "crypto_event": ["Crypto", "Financial"],
    "weather": ["Climate", "Weather"],
    "science": ["Science", "Tech"],
    "other": [],  # search all
}

MATCH_THRESHOLD = 0.45  # Lowered from 0.55 — multi-strategy compensates
KEYWORD_BOOST = 0.15    # Bonus for shared key entities (names, numbers, dates)


@dataclass
class KalshiMatch:
    kalshi_ticker: str
    kalshi_title: str
    kalshi_price: float       # Yes price (0-1)
    polymarket_price: float
    price_divergence: float   # kalshi - polymarket
    match_confidence: float   # 0-1
    kalshi_volume: int = 0
    event_ticker: str = ""


def _fetch_all_markets() -> list[dict]:
    """Fetch all open markets from Kalshi (cached)."""
    global _all_cache
    now = time.time()
    if _all_cache[0] and now - _all_cache[1] < _CACHE_TTL:
        return _all_cache[0]

    session = get_session()
    all_markets = []
    cursor = None

    try:
        for _ in range(10):  # Up to 2000 markets
            params: dict = {"status": "open", "limit": 200}
            if cursor:
                params["cursor"] = cursor

            resp = session.get(
                f"{_BASE_URL}/markets",
                params=params,
                timeout=15,
                headers={"Accept": "application/json"},
            )
            if resp.status_code != 200:
                log.debug("[KALSHI] HTTP %d fetching markets", resp.status_code)
                break

            data = resp.json()
            markets = data.get("markets", [])
            all_markets.extend(markets)

            cursor = data.get("cursor")
            if not cursor or not markets:
                break

    except Exception as e:
        log.debug("[KALSHI] Fetch failed: %s", str(e)[:100])

    if all_markets:
        _all_cache = (all_markets, now)
        log.info("[KALSHI] Cached %d open markets", len(all_markets))
    return all_markets


def _fetch_events(category: str = "") -> list[dict]:
    """Fetch events from Kalshi, optionally filtered by category."""
    session = get_session()
    events = []
    cursor = None

    try:
        for _ in range(3):
            params: dict = {"status": "open", "limit": 100}
            if cursor:
                params["cursor"] = cursor

            resp = session.get(
                f"{_BASE_URL}/events",
                params=params,
                timeout=15,
                headers={"Accept": "application/json"},
            )
            if resp.status_code != 200:
                break

            data = resp.json()
            batch = data.get("events", [])
            events.extend(batch)
            cursor = data.get("cursor")
            if not cursor or not batch:
                break
    except Exception:
        pass

    return events


# ── Matching Strategies ──

_ENTITY_RE = re.compile(
    r'\b(?:'
    r'[A-Z][a-z]+(?:\s[A-Z][a-z]+)+|'   # Proper nouns (Donald Trump, Elon Musk)
    r'\d{1,2}/\d{1,2}/\d{2,4}|'          # Dates
    r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+\d{1,2}|'  # Month Day
    r'\$[\d,.]+[KMBkmb]?|'               # Dollar amounts
    r'\d+(?:\.\d+)?%'                     # Percentages
    r')\b'
)


def _extract_entities(text: str) -> set[str]:
    """Extract key entities (names, dates, numbers) from text."""
    return {m.group().lower().strip() for m in _ENTITY_RE.finditer(text)}


def _extract_keywords(text: str) -> set[str]:
    """Extract meaningful keywords (skip common words)."""
    _STOP = {
        "will", "the", "a", "an", "in", "on", "at", "to", "of", "by", "for",
        "be", "is", "are", "was", "were", "has", "have", "had", "do", "does",
        "this", "that", "it", "or", "and", "not", "from", "with", "as",
        "before", "after", "during", "between", "more", "less", "than",
        "least", "most", "any", "all", "each", "every", "some",
    }
    words = re.findall(r'[a-z]+', text.lower())
    return {w for w in words if len(w) > 2 and w not in _STOP}


def _multi_match_score(query: str, kalshi_title: str) -> float:
    """Multi-strategy match scoring.

    Combines:
      1. Fuzzy sequence match (base)
      2. Keyword overlap (Jaccard)
      3. Entity match bonus
    """
    # Strategy 1: Fuzzy
    q_norm = query.lower().replace("?", "").strip()
    k_norm = kalshi_title.lower().replace("?", "").strip()
    fuzzy = SequenceMatcher(None, q_norm, k_norm).ratio()

    # Strategy 2: Keyword overlap (Jaccard similarity)
    q_kw = _extract_keywords(query)
    k_kw = _extract_keywords(kalshi_title)
    if q_kw and k_kw:
        jaccard = len(q_kw & k_kw) / len(q_kw | k_kw)
    else:
        jaccard = 0.0

    # Strategy 3: Entity match bonus
    q_ent = _extract_entities(query)
    k_ent = _extract_entities(kalshi_title)
    entity_bonus = 0.0
    if q_ent and k_ent:
        shared = q_ent & k_ent
        if shared:
            entity_bonus = min(KEYWORD_BOOST * len(shared), 0.30)

    # Weighted combination
    score = fuzzy * 0.4 + jaccard * 0.4 + entity_bonus * 0.2

    # Bonus: if >50% of query keywords found in Kalshi title
    if q_kw and len(q_kw & k_kw) / len(q_kw) > 0.5:
        score = min(1.0, score + 0.10)

    return score


def _get_kalshi_price(market: dict) -> float | None:
    """Extract YES probability from Kalshi market data."""
    # Kalshi prices are in cents (0-100)
    for field in ("yes_ask", "last_price", "yes_bid"):
        try:
            val = float(market.get(field, 0))
            if val > 0:
                # Kalshi returns cents for some endpoints, dollars for others
                price = val / 100.0 if val > 1.0 else val
                if 0.01 <= price <= 0.99:
                    return price
        except (ValueError, TypeError):
            continue
    return None


def get_kalshi_divergence(
    question: str,
    polymarket_price: float,
    category: str = "",
) -> KalshiMatch | None:
    """Find matching Kalshi market and calculate price divergence.

    V7: Uses multi-strategy matching for much better hit rate.
    """
    markets = _fetch_all_markets()
    if not markets:
        return None

    # Score all markets
    best_match = None
    best_score = 0.0

    for m in markets:
        title = m.get("title", "")
        subtitle = m.get("subtitle", "")

        # Score against title, subtitle, and combined
        for text in [title, subtitle, f"{title} {subtitle}"]:
            if not text.strip():
                continue
            score = _multi_match_score(question, text)
            if score > best_score:
                best_score = score
                best_match = m

    if not best_match or best_score < MATCH_THRESHOLD:
        return None

    kalshi_price = _get_kalshi_price(best_match)
    if kalshi_price is None:
        return None

    divergence = kalshi_price - polymarket_price

    result = KalshiMatch(
        kalshi_ticker=best_match.get("ticker", ""),
        kalshi_title=best_match.get("title", ""),
        kalshi_price=kalshi_price,
        polymarket_price=polymarket_price,
        price_divergence=divergence,
        match_confidence=best_score,
        kalshi_volume=int(best_match.get("volume", 0)),
        event_ticker=best_match.get("event_ticker", ""),
    )

    if best_score >= MATCH_THRESHOLD:
        log.info(
            "[KALSHI] Match (%.0f%%): '%s' ↔ '%s' | K=%.2f P=%.2f Div=%+.1f%%",
            best_score * 100, question[:40], best_match.get("title", "")[:40],
            kalshi_price, polymarket_price, divergence * 100,
        )

    return result


def get_kalshi_price_for_market(question: str, category: str = "") -> tuple[float | None, float]:
    """Simplified interface — returns (kalshi_probability, match_confidence) or (None, 0).

    Used by analyst.py for cross-platform edge detection.
    """
    markets = _fetch_all_markets()
    if not markets:
        return None, 0.0

    best_match = None
    best_score = 0.0

    for m in markets:
        title = m.get("title", "")
        subtitle = m.get("subtitle", "")
        for text in [title, subtitle]:
            if not text.strip():
                continue
            score = _multi_match_score(question, text)
            if score > best_score:
                best_score = score
                best_match = m

    if not best_match or best_score < MATCH_THRESHOLD:
        return None, 0.0

    price = _get_kalshi_price(best_match)
    if price is None:
        return None, 0.0

    return price, best_score
