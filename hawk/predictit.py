"""PredictIt cross-reference — political markets only.

Free API, no key needed. Single endpoint for all markets.
Cache TTL: 120s (rate limited to ~1 req/min).
Only activated when market.category == "politics".
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from difflib import SequenceMatcher

import requests

log = logging.getLogger(__name__)

_API_URL = "https://www.predictit.org/api/marketdata/all/"

# Cache
_market_cache: tuple[list[dict], float] = ([], 0.0)
_CACHE_TTL = 120  # 2 minutes

MATCH_THRESHOLD = 0.50


@dataclass
class PredictItMatch:
    pi_market_id: int
    pi_market_name: str
    pi_contract_name: str
    pi_price: float  # Last trade price (0-1)
    polymarket_price: float
    price_divergence: float
    match_confidence: float


def _fetch_all_markets() -> list[dict]:
    """Fetch all PredictIt markets (single endpoint)."""
    global _market_cache

    now = time.time()
    if _market_cache[0] and now - _market_cache[1] < _CACHE_TTL:
        return _market_cache[0]

    try:
        resp = requests.get(_API_URL, timeout=15)
        if resp.status_code != 200:
            log.debug("PredictIt HTTP %d", resp.status_code)
            return []

        data = resp.json()
        markets = data.get("markets", [])
        _market_cache = (markets, now)
        log.debug("PredictIt: fetched %d markets", len(markets))
        return markets

    except Exception as e:
        log.debug("PredictIt fetch failed: %s", str(e)[:100])
        return []


def _normalize(text: str) -> str:
    return text.lower().strip().replace("?", "").replace("will ", "").replace("  ", " ")


def match_political_market(
    question: str,
    polymarket_price: float,
) -> PredictItMatch | None:
    """Find matching PredictIt market for a political Polymarket question.

    Only call this for political markets (category == "politics").
    """
    markets = _fetch_all_markets()
    if not markets:
        return None

    query_norm = _normalize(question)
    best_match = None
    best_contract = None
    best_score = 0.0

    for market in markets:
        market_name = market.get("name", "")
        market_score = SequenceMatcher(None, query_norm, _normalize(market_name)).ratio()

        # Also check individual contracts within the market
        for contract in market.get("contracts", []):
            contract_name = contract.get("name", "")
            combined = f"{market_name} {contract_name}"
            contract_score = SequenceMatcher(None, query_norm, _normalize(combined)).ratio()

            score = max(market_score, contract_score)
            if score > best_score:
                best_score = score
                best_match = market
                best_contract = contract

    if not best_match or not best_contract or best_score < MATCH_THRESHOLD:
        return None

    # Get price from contract
    try:
        pi_price = float(best_contract.get("lastTradePrice", 0))
    except (ValueError, TypeError):
        return None

    if pi_price <= 0:
        return None

    divergence = pi_price - polymarket_price

    result = PredictItMatch(
        pi_market_id=best_match.get("id", 0),
        pi_market_name=best_match.get("name", ""),
        pi_contract_name=best_contract.get("name", ""),
        pi_price=pi_price,
        polymarket_price=polymarket_price,
        price_divergence=divergence,
        match_confidence=best_score,
    )

    if abs(divergence) > 0.03:
        log.info(
            "[PREDICTIT] Match (%.0f%%): '%s/%s' | PI=%.2f Poly=%.2f Div=%+.1f%%",
            best_score * 100,
            best_match.get("name", "")[:30],
            best_contract.get("name", "")[:20],
            pi_price, polymarket_price, divergence * 100,
        )

    return result
