"""Hawk Briefing Generator — tells Viper exactly what to research.

Hawk scans markets, identifies opportunities, then generates a briefing file
(`data/hawk_briefing.json`) with entities and targeted search queries.
Viper reads this briefing and runs focused Tavily searches instead of generic ones.
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
BRIEFING_FILE = DATA_DIR / "hawk_briefing.json"

# Stale threshold — briefing older than 2 hours is considered stale
STALE_SECONDS = 7200

# Common words to exclude from entity extraction
_STOP_WORDS = frozenset({
    "will", "the", "and", "for", "are", "but", "not", "you", "all", "can",
    "had", "her", "was", "one", "our", "out", "has", "have", "this", "that",
    "with", "from", "they", "been", "said", "each", "which", "their", "what",
    "about", "would", "there", "when", "make", "like", "time", "very", "your",
    "just", "than", "them", "some", "could", "other", "into", "more", "before",
    "after", "does", "who", "how", "much", "many", "most", "next", "first",
    "over", "under", "between", "during", "win", "reach", "become", "remain",
    "stay", "get", "end", "yes", "pass",
})

# Abbreviation patterns (2-5 uppercase letters, possibly with numbers)
_ABBREV_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,5}\b")

# Year pattern
_YEAR_RE = re.compile(r"\b(20[2-3]\d)\b")

# Proper noun pattern — capitalized words not at sentence start
_PROPER_NOUN_RE = re.compile(r"(?<=[a-z,;:] )([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)")

# Category-specific query templates
_QUERY_TEMPLATES = {
    "sports": [
        "{entities} latest news {year}",
        "{entities} odds predictions {year}",
    ],
    "politics": [
        "{entities} latest polling news {year}",
        "{entities} political analysis {year}",
    ],
    "crypto_event": [
        "{entities} crypto news today {year}",
        "{entities} market analysis {year}",
    ],
    "culture": [
        "{entities} latest news {year}",
        "{entities} predictions odds {year}",
    ],
    "other": [
        "{entities} latest news {year}",
        "{entities} analysis predictions {year}",
    ],
}


def _extract_entities(question: str) -> list[str]:
    """Extract proper nouns, abbreviations, and years from a market question."""
    entities: list[str] = []
    seen_lower: set[str] = set()

    # 1. Abbreviations (NBA, NFL, MVP, SEC, ETF, etc.)
    for match in _ABBREV_RE.finditer(question):
        abbr = match.group()
        if abbr.lower() not in seen_lower and abbr.lower() not in _STOP_WORDS:
            entities.append(abbr)
            seen_lower.add(abbr.lower())

    # 2. Multi-word proper nouns (Shai Gilgeous-Alexander, Donald Trump, etc.)
    # Look for sequences of capitalized words
    words = question.split()
    i = 0
    while i < len(words):
        # Check if word starts with uppercase
        word = words[i].strip("?,.'\"!()[]")
        if word and word[0].isupper() and word.lower() not in _STOP_WORDS:
            # Collect consecutive capitalized words
            phrase_parts = [word]
            j = i + 1
            while j < len(words):
                next_word = words[j].strip("?,.'\"!()[]")
                # Allow hyphenated names like Gilgeous-Alexander
                if next_word and (next_word[0].isupper() or "-" in next_word) and next_word.lower() not in _STOP_WORDS:
                    phrase_parts.append(next_word)
                    j += 1
                else:
                    break
            phrase = " ".join(phrase_parts)
            if len(phrase) > 2 and phrase.lower() not in seen_lower:
                entities.append(phrase)
                seen_lower.add(phrase.lower())
            i = j
        else:
            i += 1

    # 3. Years
    for match in _YEAR_RE.finditer(question):
        year = match.group()
        if year not in entities:
            entities.append(year)

    return entities


def _generate_queries(question: str, category: str, entities: list[str]) -> list[str]:
    """Generate 2-3 targeted Tavily search queries for a market."""
    # Get year from question or use current
    year_match = _YEAR_RE.search(question)
    year = year_match.group() if year_match else "2026"

    # Build entity string (top 3 entities for query brevity)
    entity_str = " ".join(entities[:3]) if entities else question[:60]

    templates = _QUERY_TEMPLATES.get(category, _QUERY_TEMPLATES["other"])
    queries = []
    for tmpl in templates:
        q = tmpl.format(entities=entity_str, year=year)
        queries.append(q)

    # Add a direct question-based query (most specific)
    # Trim "Will " prefix and "?" suffix for better search
    direct = question.strip()
    if direct.lower().startswith("will "):
        direct = direct[5:]
    direct = direct.rstrip("?").strip()
    if len(direct) > 20:
        queries.append(direct[:80] + " latest news")

    return queries[:3]  # Max 3 queries per market


def generate_briefing(opportunities: list[dict], cycle: int = 0) -> dict:
    """Generate hawk_briefing.json from Hawk's latest opportunities.

    Args:
        opportunities: List of opp dicts (must have question, category, edge, condition_id)
        cycle: Current Hawk cycle number

    Returns:
        The briefing dict that was saved
    """
    DATA_DIR.mkdir(exist_ok=True)

    # Sort by edge descending, take top 10
    sorted_opps = sorted(opportunities, key=lambda o: abs(o.get("edge", 0)), reverse=True)
    top_markets = sorted_opps[:10]

    markets = []
    for priority, opp in enumerate(top_markets, 1):
        question = opp.get("question", "")
        category = opp.get("category", "other")
        condition_id = opp.get("condition_id", "")

        if not question or not condition_id:
            continue

        entities = _extract_entities(question)
        queries = _generate_queries(question, category, entities)

        markets.append({
            "condition_id": condition_id,
            "question": question[:300],
            "category": category,
            "edge": round(opp.get("edge", 0), 4),
            "search_queries": queries,
            "entities": entities,
            "priority": priority,
        })

    briefing = {
        "generated_at": time.time(),
        "cycle": cycle,
        "markets": markets,
        "briefed_markets": len(markets),
    }

    try:
        BRIEFING_FILE.write_text(json.dumps(briefing, indent=2))
        log.info("Hawk briefing: %d markets briefed with %d total queries",
                 len(markets), sum(len(m["search_queries"]) for m in markets))
    except Exception:
        log.exception("Failed to save hawk briefing")

    return briefing


def load_briefing() -> dict | None:
    """Load hawk_briefing.json. Returns None if missing or stale (>2hr)."""
    if not BRIEFING_FILE.exists():
        log.debug("No hawk briefing file found")
        return None

    try:
        briefing = json.loads(BRIEFING_FILE.read_text())
    except Exception:
        log.warning("Failed to parse hawk briefing")
        return None

    age = time.time() - briefing.get("generated_at", 0)
    if age > STALE_SECONDS:
        log.warning("Hawk briefing is stale (%.0f min old)", age / 60)
        return None

    return briefing
