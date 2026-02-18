"""Metaculus crowd wisdom — non-sports prediction markets.

Free API, no key needed. 10-min cache.
Adds 0.5s delay between searches (be respectful, no documented rate limit).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import requests

log = logging.getLogger(__name__)

_BASE_URL = "https://www.metaculus.com/api2/questions/"

# Cache: query_hash -> (MetaculusMatch, timestamp)
_cache: dict[str, tuple["MetaculusMatch | None", float]] = {}
_CACHE_TTL = 600  # 10 minutes

# Throttle
_last_request_time = 0.0
_REQUEST_DELAY = 0.5  # 500ms between requests


@dataclass
class MetaculusMatch:
    metaculus_id: int
    metaculus_title: str
    community_prob: float  # Community median prediction (0-1)
    num_predictions: int
    polymarket_price: float
    price_divergence: float  # metaculus - polymarket
    match_confidence: float


def _extract_keywords(question: str, max_words: int = 6) -> str:
    """Extract most important keywords from a market question for search."""
    # Remove common filler words
    stop_words = {
        "will", "the", "a", "an", "be", "is", "are", "was", "were", "has",
        "have", "had", "do", "does", "did", "by", "on", "in", "at", "to",
        "for", "of", "with", "from", "this", "that", "it", "its", "or",
        "and", "but", "if", "then", "than", "so", "as", "before", "after",
    }

    words = question.lower().replace("?", "").split()
    keywords = [w for w in words if w not in stop_words and len(w) > 2]
    return " ".join(keywords[:max_words])


def get_crowd_probability(
    question: str,
    polymarket_price: float,
) -> MetaculusMatch | None:
    """Search Metaculus for a matching question and get community probability.

    Args:
        question: Polymarket market question
        polymarket_price: Current Polymarket YES price

    Returns:
        MetaculusMatch if found, else None.
    """
    global _last_request_time

    # Check cache
    cache_key = question[:100]
    cached = _cache.get(cache_key)
    if cached and time.time() - cached[1] < _CACHE_TTL:
        return cached[0]

    # Extract search keywords
    keywords = _extract_keywords(question)
    if not keywords:
        return None

    # Throttle
    now = time.time()
    elapsed = now - _last_request_time
    if elapsed < _REQUEST_DELAY:
        time.sleep(_REQUEST_DELAY - elapsed)
    _last_request_time = time.time()

    try:
        resp = requests.get(
            _BASE_URL,
            params={
                "search": keywords,
                "status": "open",
                "type": "forecast",
                "limit": 5,
                "order_by": "-activity",
            },
            timeout=10,
            headers={"Accept": "application/json"},
        )
        if resp.status_code != 200:
            log.debug("Metaculus HTTP %d", resp.status_code)
            _cache[cache_key] = (None, time.time())
            return None

        data = resp.json()
        results = data.get("results", [])
        if not results:
            _cache[cache_key] = (None, time.time())
            return None

    except Exception as e:
        log.debug("Metaculus search failed: %s", str(e)[:100])
        _cache[cache_key] = (None, time.time())
        return None

    # Find best match with community prediction
    from difflib import SequenceMatcher
    query_norm = question.lower().strip().replace("?", "")

    best_match = None
    best_score = 0.0
    best_prob = None

    for q in results:
        title = q.get("title", "")
        score = SequenceMatcher(
            None, query_norm, title.lower().strip().replace("?", ""),
        ).ratio()

        # Extract community prediction
        cp = q.get("community_prediction")
        if cp is None:
            continue

        # community_prediction can be a dict with 'full' key
        prob = None
        if isinstance(cp, dict):
            full = cp.get("full")
            if isinstance(full, dict):
                prob = full.get("q2")  # Median
            elif isinstance(full, (int, float)):
                prob = float(full)
        elif isinstance(cp, (int, float)):
            prob = float(cp)

        if prob is not None and score > best_score:
            best_score = score
            best_match = q
            best_prob = prob

    if not best_match or best_prob is None or best_score < 0.40:
        _cache[cache_key] = (None, time.time())
        return None

    divergence = best_prob - polymarket_price

    result = MetaculusMatch(
        metaculus_id=best_match.get("id", 0),
        metaculus_title=best_match.get("title", ""),
        community_prob=best_prob,
        num_predictions=best_match.get("number_of_predictions", 0),
        polymarket_price=polymarket_price,
        price_divergence=divergence,
        match_confidence=best_score,
    )

    _cache[cache_key] = (result, time.time())

    if abs(divergence) > 0.05:
        log.info(
            "[METACULUS] Match (%.0f%%): '%s' | Community=%.2f Poly=%.2f Div=%+.1f%% (%d predictions)",
            best_score * 100, best_match.get("title", "")[:50],
            best_prob, polymarket_price, divergence * 100,
            best_match.get("number_of_predictions", 0),
        )

    return result
