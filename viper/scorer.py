"""Opportunity Scoring — 0-100 score for each opportunity."""
from __future__ import annotations

from viper.scanner import Opportunity


def score_opportunity(opp: Opportunity) -> int:
    """Score 0-100: value(40%) + effort(20%) + urgency(20%) + confidence(20%)."""

    # Value score (0-100): higher value = higher score
    if opp.estimated_value_usd >= 1000:
        value_score = 100
    elif opp.estimated_value_usd >= 500:
        value_score = 80
    elif opp.estimated_value_usd >= 200:
        value_score = 60
    elif opp.estimated_value_usd >= 100:
        value_score = 40
    else:
        value_score = 20

    # Effort score (0-100): lower effort = higher score (inverse)
    if opp.effort_hours <= 4:
        effort_score = 100
    elif opp.effort_hours <= 8:
        effort_score = 80
    elif opp.effort_hours <= 20:
        effort_score = 50
    elif opp.effort_hours <= 40:
        effort_score = 30
    else:
        effort_score = 10

    # Urgency score
    urgency_map = {"urgent": 100, "high": 80, "normal": 50, "low": 20}
    urgency_score = urgency_map.get(opp.urgency, 50)

    # Confidence score
    confidence_score = int(opp.confidence * 100)

    # Weighted average
    total = (
        value_score * 0.40 +
        effort_score * 0.20 +
        urgency_score * 0.20 +
        confidence_score * 0.20
    )
    return max(0, min(100, int(total)))
