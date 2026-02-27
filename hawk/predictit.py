"""PredictIt cross-reference — political markets only.

Free API, no key needed. Single endpoint for all markets.
Cache TTL: 120s (rate limited to ~1 req/min).
Only activated when market.category == "politics".

V2: Question-type classification + keyword overlap to prevent
matching different question types (e.g., "matchup" vs "winner").
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from difflib import SequenceMatcher

import requests

log = logging.getLogger(__name__)

_API_URL = "https://www.predictit.org/api/marketdata/all/"

# Cache
_market_cache: tuple[list[dict], float] = ([], 0.0)
_CACHE_TTL = 120  # 2 minutes

MATCH_THRESHOLD = 0.60  # Raised from 0.50
MIN_KEYWORD_OVERLAP = 2  # Need at least 2 key entities in common


@dataclass
class PredictItMatch:
    pi_market_id: int
    pi_market_name: str
    pi_contract_name: str
    pi_price: float  # Last trade price (0-1)
    polymarket_price: float
    price_divergence: float
    match_confidence: float


# ── Question type classification ──

_MATCHUP_PATTERNS = [
    r"\bmatchup\b", r"\bvs?\.?\b", r"\bversus\b",
    r"\bnominee\b", r"\bcandidate[s]?\sfor\b",
    r"\bwhat will the .* be\b",
    r"\bwho will be the .* nominee\b",
    r"\bwill .* and .* be the candidates\b",
]
_WINNER_PATTERNS = [
    r"\bwin\b", r"\bwinner\b", r"\bwon\b", r"\belected\b",
    r"\bwill .* win\b", r"\bwill .* be elected\b",
    r"\bwill .* become\b",
]
_QUANTITY_PATTERNS = [
    r"\bhow many\b", r"\bhow much\b",
    r"\babove\b", r"\bbelow\b", r"\bover\b", r"\bunder\b",
    r"\bbetween\b.*\band\b", r"\bmore than\b", r"\bless than\b",
    r"\bclose above\b", r"\bclose below\b",
]


def _classify_question(text: str) -> str:
    """Classify question type: matchup, winner, quantity, or generic."""
    lower = text.lower()
    for pat in _MATCHUP_PATTERNS:
        if re.search(pat, lower):
            return "matchup"
    for pat in _WINNER_PATTERNS:
        if re.search(pat, lower):
            return "winner"
    for pat in _QUANTITY_PATTERNS:
        if re.search(pat, lower):
            return "quantity"
    return "generic"


def _types_compatible(type_a: str, type_b: str) -> bool:
    """Check if two question types can be meaningfully compared."""
    if type_a == type_b:
        return True
    # Generic can match anything (we don't know enough to block)
    if type_a == "generic" or type_b == "generic":
        return True
    return False


# ── Keyword extraction ──

_STOPWORDS = {
    "will", "the", "be", "to", "in", "a", "an", "of", "on", "at", "for",
    "and", "or", "is", "this", "that", "by", "from", "with", "has", "have",
    "not", "do", "does", "before", "after", "during", "between", "more",
    "than", "less", "above", "below", "over", "under", "what", "who",
    "how", "many", "much",
}


def _extract_keywords(text: str) -> set[str]:
    """Extract meaningful keywords (names, dates, entities) from question."""
    words = re.findall(r"[a-zA-Z0-9]+", text.lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 2}


def _keyword_overlap(kw_a: set[str], kw_b: set[str]) -> int:
    """Count overlapping keywords between two question keyword sets."""
    return len(kw_a & kw_b)


# ── Core matching ──

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

    V2: Uses question-type classification + keyword overlap + fuzzy matching.
    Only matches same-type questions (winner↔winner, matchup↔matchup).
    """
    markets = _fetch_all_markets()
    if not markets:
        return None

    query_norm = _normalize(question)
    query_type = _classify_question(question)
    query_kw = _extract_keywords(question)

    best_match = None
    best_contract = None
    best_score = 0.0

    for market in markets:
        market_name = market.get("name", "")

        for contract in market.get("contracts", []):
            contract_name = contract.get("name", "")
            combined = f"{market_name} {contract_name}"

            # Gate 1: Question type must be compatible
            pi_type = _classify_question(combined)
            if not _types_compatible(query_type, pi_type):
                continue

            # Gate 2: Keyword overlap — need shared entities
            pi_kw = _extract_keywords(combined)
            overlap = _keyword_overlap(query_kw, pi_kw)
            if overlap < MIN_KEYWORD_OVERLAP:
                continue

            # Score: weighted blend of fuzzy + keyword Jaccard
            fuzzy_score = SequenceMatcher(None, query_norm, _normalize(combined)).ratio()
            union = len(query_kw | pi_kw)
            jaccard = overlap / union if union > 0 else 0

            # 60% fuzzy + 40% keyword overlap
            score = fuzzy_score * 0.6 + jaccard * 0.4

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
