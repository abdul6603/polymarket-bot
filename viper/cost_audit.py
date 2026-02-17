"""API Cost Tracker — estimate spend per agent/service."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class AgentCost:
    agent: str
    service: str
    period: str
    cost_usd: float
    usage_count: int
    trend: str  # "up", "down", "stable"
    waste: bool = False


# Known API costs per token/call (estimates)
_OPENAI_COSTS = {
    "gpt-4o": {"input": 2.50 / 1_000_000, "output": 10.00 / 1_000_000},
    "gpt-4o-mini": {"input": 0.15 / 1_000_000, "output": 0.60 / 1_000_000},
    "dall-e-3": {"per_call": 0.04},
}

_ELEVENLABS_COST_PER_CHAR = 0.30 / 1000  # ~$0.30 per 1k chars

# Agent -> services mapping
_AGENT_SERVICES = {
    "garves": [("OpenAI", "gpt-4o-mini", 50, 0.05)],  # ~50 calls/day for brain interpreter
    "soren": [("OpenAI", "gpt-4o", 20, 0.30), ("OpenAI", "dall-e-3", 10, 0.40), ("ElevenLabs", "tts", 10, 0.15)],
    "atlas": [("Tavily", "search", 100, 0.01)],
    "hawk": [("OpenAI", "gpt-4o", 40, 0.60)],  # 20 analyses per cycle, 2 cycles/day
    "lisa": [("OpenAI", "gpt-4o-mini", 30, 0.02)],
    "thor": [("Anthropic", "claude", 5, 0.50)],
}


def audit_all() -> dict:
    """Full breakdown: total_monthly, by_agent, by_service, waste_flags."""
    costs: list[AgentCost] = []
    total_monthly = 0.0

    for agent, services in _AGENT_SERVICES.items():
        for service_name, model, daily_calls, cost_per_call in services:
            monthly_cost = daily_calls * cost_per_call * 30
            total_monthly += monthly_cost

            costs.append(AgentCost(
                agent=agent,
                service=f"{service_name} ({model})",
                period="monthly",
                cost_usd=round(monthly_cost, 2),
                usage_count=daily_calls * 30,
                trend="stable",
                waste=monthly_cost > 20 and daily_calls > 100,
            ))

    # Sort by cost descending
    costs.sort(key=lambda c: c.cost_usd, reverse=True)

    return {
        "total_monthly": round(total_monthly, 2),
        "costs": [
            {
                "agent": c.agent,
                "service": c.service,
                "cost_usd": c.cost_usd,
                "usage_count": c.usage_count,
                "trend": c.trend,
                "waste": c.waste,
            }
            for c in costs
        ],
        "waste_flags": [c for c in costs if c.waste],
    }


def find_waste() -> list[dict]:
    """Identify wasteful patterns."""
    audit = audit_all()
    return [
        {"agent": c["agent"], "service": c["service"], "monthly": c["cost_usd"], "reason": "High volume + cost"}
        for c in audit["costs"]
        if c["waste"]
    ]
