"""Intel Scoring — 0-100 score for intelligence items based on trading potential."""
from __future__ import annotations

import sys
from pathlib import Path

from viper.intel import IntelItem

# ── Shared Intelligence Layer (MLX routing) ──
_USE_SHARED_LLM = False
_shared_llm_call = None
try:
    sys.path.insert(0, str(Path.home() / "shared"))
    from llm_client import llm_call as _llm_call
    _shared_llm_call = _llm_call
    _USE_SHARED_LLM = True
except ImportError:
    pass


def _llm_actionability_bonus(item: IntelItem) -> int:
    """LLM-assessed actionability bonus (0-15 points) for high-potential items.
    Only runs on pre-filtered items (score >= 50 from base scoring) to save LLM calls."""
    if not (_USE_SHARED_LLM and _shared_llm_call):
        return 0
    try:
        text = f"{item.title}: {item.summary[:200]}"
        result = _shared_llm_call(
            system=(
                "You assess whether news/intel is actionable for prediction market trading. "
                "Reply with ONLY a number 0-15 representing actionability: "
                "0=not tradable, 5=maybe tradable, 10=clearly tradable, 15=high-confidence trade signal."
            ),
            user=f"Can Hawk trade on this? Categories: {', '.join(item.relevance_tags[:5])}. Intel: {text}",
            agent="viper",
            task_type="analysis",
            max_tokens=10,
            temperature=0.1,
        )
        if result:
            bonus = int(float(result.strip()))
            return max(0, min(15, bonus))
    except (ValueError, TypeError, Exception):
        pass
    return 0


def score_intel(item: IntelItem) -> int:
    """Score 0-100: relevance(35%) + confidence(25%) + recency(20%) + sentiment(10%) + LLM actionability(10%)."""
    import time

    # Relevance score: more tags = more relevant to prediction markets
    tag_count = len(item.relevance_tags)
    if tag_count >= 5:
        relevance_score = 100
    elif tag_count >= 3:
        relevance_score = 75
    elif tag_count >= 1:
        relevance_score = 50
    else:
        relevance_score = 10

    # Already matched to a market = very relevant
    if item.matched_markets:
        relevance_score = min(100, relevance_score + 30)

    # Confidence score
    confidence_score = int(item.confidence * 100)

    # Recency score: newer = better
    age_minutes = (time.time() - item.timestamp) / 60
    if age_minutes < 30:
        recency_score = 100
    elif age_minutes < 120:
        recency_score = 80
    elif age_minutes < 360:
        recency_score = 50
    elif age_minutes < 1440:
        recency_score = 30
    else:
        recency_score = 10

    # Sentiment strength: strong sentiment (positive or negative) is more actionable
    sentiment_strength = abs(item.sentiment)
    if sentiment_strength >= 0.5:
        sentiment_score = 100
    elif sentiment_strength >= 0.3:
        sentiment_score = 70
    elif sentiment_strength >= 0.1:
        sentiment_score = 40
    else:
        sentiment_score = 20

    # Base score (without LLM)
    base_total = (
        relevance_score * 0.35 +
        confidence_score * 0.25 +
        recency_score * 0.20 +
        sentiment_score * 0.10
    )

    # LLM actionability bonus — only for items scoring >= 50 base (pre-filter)
    llm_bonus = 0
    if base_total >= 50:
        llm_bonus = _llm_actionability_bonus(item)

    total = base_total + llm_bonus
    return max(0, min(100, int(total)))
