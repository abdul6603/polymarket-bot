"""Market Matcher — entity-based matching of Viper intel to Hawk markets.

Uses Hawk briefing entities for strict matching:
  Tier 1: Pre-linked items (from targeted Tavily queries) -> score 1.0
  Tier 2: Entity match — intel text must contain >=35% of market entities

Replaces the old word-overlap approach that matched garbage.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from viper.intel import load_intel, save_market_context

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
HAWK_OPPS_FILE = DATA_DIR / "hawk_opportunities.json"
BRIEFING_FILE = DATA_DIR / "hawk_briefing.json"

MAX_CONTEXT_PER_MARKET = 8
ENTITY_MATCH_THRESHOLD = 0.35  # Must match >=35% of entities


def _load_briefing_entities() -> dict[str, list[str]]:
    """Load entities per condition_id from hawk_briefing.json.

    Returns {condition_id: [entity1, entity2, ...]}
    """
    if not BRIEFING_FILE.exists():
        return {}
    try:
        briefing = json.loads(BRIEFING_FILE.read_text())
        age = time.time() - briefing.get("generated_at", 0)
        if age > 7200:
            return {}
        result = {}
        for m in briefing.get("markets", []):
            cid = m.get("condition_id", "")
            entities = m.get("entities", [])
            if cid and entities:
                result[cid] = entities
        return result
    except Exception:
        return {}


def _entity_match_score(intel_text: str, entities: list[str]) -> float:
    """Score how many entities appear in the intel text. Returns 0.0 to 1.0."""
    if not entities:
        return 0.0
    text_lower = intel_text.lower()
    matches = sum(1 for e in entities if e.lower() in text_lower)
    return matches / len(entities)


def build_market_context(intel_items: list[dict], markets: list[dict] | None = None) -> dict[str, list[dict]]:
    """Match intel items to markets using entity-based strict matching.

    Tier 1: Pre-linked items (from targeted Tavily) -> auto match, score 1.0
    Tier 2: Entity match — intel must contain >=35% of market entities

    Returns {condition_id: [matched_intel_items]}
    """
    if markets is None:
        markets = _load_hawk_markets()

    if not markets:
        log.info("No markets to match intel against")
        return {}

    # Load entities from briefing for tier 2 matching
    briefing_entities = _load_briefing_entities()

    context: dict[str, list[dict]] = {}
    matches_found = 0
    pre_linked = 0
    entity_matched = 0

    for market in markets:
        question = market.get("question", "")
        cid = market.get("condition_id", market.get("market_id", ""))

        if not question or not cid:
            continue

        matched: list[tuple[float, dict, str]] = []  # (score, intel, match_type)
        entities = briefing_entities.get(cid, [])

        for intel in intel_items:
            # Tier 1: Pre-linked (from targeted Tavily query)
            if cid in intel.get("matched_markets", []):
                matched.append((1.0, intel, "pre_linked"))
                pre_linked += 1
                continue

            # Tier 2: Entity match — check if intel mentions enough entities
            if entities:
                intel_text = intel.get("headline", "") + " " + intel.get("summary", "")
                score = _entity_match_score(intel_text, entities)
                if score >= ENTITY_MATCH_THRESHOLD:
                    matched.append((score, intel, "entity"))
                    entity_matched += 1

        # Sort by score and keep top N
        matched.sort(key=lambda x: x[0], reverse=True)
        if matched:
            context[cid] = [
                {
                    "headline": m.get("headline", "")[:200],
                    "summary": m.get("summary", "")[:300],
                    "source": m.get("source", ""),
                    "url": m.get("url", ""),
                    "sentiment": m.get("sentiment", 0),
                    "confidence": m.get("confidence", 0.5),
                    "relevance": round(score, 3),
                    "match_type": match_type,
                    "timestamp": m.get("timestamp", 0),
                }
                for score, m, match_type in matched[:MAX_CONTEXT_PER_MARKET]
            ]
            matches_found += len(context[cid])

    log.info("Market context: %d markets, %d links (%d pre-linked, %d entity-matched)",
             len(context), matches_found, pre_linked, entity_matched)
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
        return []
    try:
        data = json.loads(HAWK_OPPS_FILE.read_text())
        return data.get("opportunities", [])
    except Exception:
        return []
