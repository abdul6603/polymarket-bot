"""Market Matcher — match Viper intel items to active Polymarket markets.

Reads intel items, fuzzy-matches them to markets Hawk is watching,
and writes per-market context to data/viper_market_context.json for Hawk.
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

from viper.intel import IntelItem, load_intel, save_market_context

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
HAWK_OPPS_FILE = DATA_DIR / "hawk_opportunities.json"

# Maximum intel items per market to keep context focused
MAX_CONTEXT_PER_MARKET = 8


def _normalize(text: str) -> set[str]:
    """Extract meaningful words from text for matching."""
    words = set(re.findall(r'\b[a-z]{3,}\b', text.lower()))
    # Remove common stop words
    stop = {"the", "and", "for", "are", "but", "not", "you", "all", "can", "had",
            "her", "was", "one", "our", "out", "has", "have", "will", "this", "that",
            "with", "from", "they", "been", "said", "each", "which", "their", "what",
            "about", "would", "there", "when", "make", "like", "time", "very", "your",
            "just", "than", "them", "some", "could", "other", "into", "more"}
    return words - stop


def match_score(intel_text: str, market_question: str) -> float:
    """Score how well an intel item matches a market question. 0 to 1."""
    intel_words = _normalize(intel_text)
    market_words = _normalize(market_question)

    if not intel_words or not market_words:
        return 0.0

    # Jaccard-like overlap score
    overlap = intel_words & market_words
    if not overlap:
        return 0.0

    # Weight by how much of the market question is covered
    coverage = len(overlap) / len(market_words)
    # Also consider how specific the match is (not too many unrelated words)
    specificity = len(overlap) / max(len(intel_words), 1)

    return min(1.0, (coverage * 0.7 + specificity * 0.3))


def build_market_context(intel_items: list[dict], markets: list[dict] | None = None) -> dict[str, list[dict]]:
    """Match intel items to markets and build per-market context.

    Args:
        intel_items: List of intel dicts from viper_intel.json
        markets: Optional list of market dicts. If None, reads from hawk_opportunities.json

    Returns:
        {condition_id: [matched_intel_items]} dict
    """
    # Load markets if not provided
    if markets is None:
        markets = _load_hawk_markets()

    if not markets:
        log.info("No markets to match intel against")
        return {}

    context: dict[str, list[dict]] = {}
    matches_found = 0

    for market in markets:
        question = market.get("question", "")
        cid = market.get("condition_id", market.get("market_id", ""))
        category = market.get("category", "")

        if not question or not cid:
            continue

        matched: list[tuple[float, dict]] = []

        for intel in intel_items:
            # Skip if intel is already directly matched to this market
            if cid in intel.get("matched_markets", []):
                matched.append((1.0, intel))
                continue

            # Check tag overlap first (fast path)
            intel_tags = set(intel.get("relevance_tags", []))
            if not intel_tags:
                continue

            # Category match bonus
            category_bonus = 0.15 if intel.get("category") == category else 0.0

            # Text matching
            intel_text = intel.get("headline", "") + " " + intel.get("summary", "")
            score = match_score(intel_text, question) + category_bonus

            if score >= 0.15:  # minimum relevance threshold
                matched.append((score, intel))

        # Sort by relevance score and keep top N
        matched.sort(key=lambda x: x[0], reverse=True)
        if matched:
            context[cid] = [
                {
                    "headline": m.get("headline", "")[:200],
                    "summary": m.get("summary", "")[:300],
                    "source": m.get("source", ""),
                    "sentiment": m.get("sentiment", 0),
                    "confidence": m.get("confidence", 0.5),
                    "relevance": round(score, 3),
                    "timestamp": m.get("timestamp", 0),
                }
                for score, m in matched[:MAX_CONTEXT_PER_MARKET]
            ]
            matches_found += len(context[cid])

    log.info("Market context: %d markets matched, %d total intel links", len(context), matches_found)
    return context


def update_market_context() -> int:
    """Load latest intel, match to markets, save context. Returns match count."""
    intel_items = load_intel()
    if not intel_items:
        log.info("No intel items to match")
        return 0

    context = build_market_context(intel_items)
    save_market_context(context)
    return sum(len(v) for v in context.values())


def _load_hawk_markets() -> list[dict]:
    """Load markets from Hawk's opportunities file."""
    if not HAWK_OPPS_FILE.exists():
        # Try loading from hawk_status.json or scanning fresh
        return []
    try:
        data = json.loads(HAWK_OPPS_FILE.read_text())
        return data.get("opportunities", [])
    except Exception:
        return []
