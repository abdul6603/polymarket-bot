"""Intel Scoring — 0-100 score for intelligence items based on trading potential."""
from __future__ import annotations

from viper.intel import IntelItem


def score_intel(item: IntelItem) -> int:
    """Score 0-100: relevance(40%) + confidence(25%) + recency(20%) + sentiment_strength(15%)."""
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

    total = (
        relevance_score * 0.40 +
        confidence_score * 0.25 +
        recency_score * 0.20 +
        sentiment_score * 0.15
    )
    return max(0, min(100, int(total)))
