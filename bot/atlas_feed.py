"""Atlas Knowledge Feed — reads Atlas KB learnings for agent decision loops.

Atlas researches continuously and stores learnings in:
    ~/atlas/data/knowledge_base.json

This module provides a lightweight, safe-to-call-every-tick reader
that extracts per-agent actionable insights from the KB.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)

KB_FILE = Path.home() / "atlas" / "data" / "knowledge_base.json"

# Cache: reload at most once per 5 minutes
_cache: dict = {}
_cache_ts: float = 0.0
_CACHE_TTL = 300  # seconds


def _load_kb() -> dict:
    """Load KB with in-memory cache (5 min TTL)."""
    global _cache, _cache_ts
    now = time.time()
    if _cache and now - _cache_ts < _CACHE_TTL:
        return _cache
    if not KB_FILE.exists():
        return {}
    try:
        _cache = json.loads(KB_FILE.read_text())
        _cache_ts = now
        return _cache
    except Exception:
        log.debug("Failed to read Atlas KB, using cached data")
        return _cache or {}


def get_learnings(agent: str, min_confidence: float = 0.7,
                  unapplied_only: bool = False,
                  include_general: bool = True) -> list[dict]:
    """Get Atlas KB learnings for an agent.

    Args:
        agent: Agent name (garves, hawk, soren, etc.)
        min_confidence: Minimum confidence threshold (default 0.7)
        unapplied_only: If True, only return learnings not yet marked applied
        include_general: If True, also include "general" learnings (default True)

    Returns list of learning dicts with keys: insight, confidence, agent, applied, timestamp
    """
    kb = _load_kb()
    learnings = kb.get("learnings", [])
    targets = {agent, "general"} if include_general else {agent}
    results = []
    for l in learnings:
        if l.get("agent", "") not in targets:
            continue
        if l.get("confidence", 0) < min_confidence:
            continue
        if unapplied_only and l.get("applied", False):
            continue
        results.append(l)
    return results


def get_actionable_insights(agent: str) -> list[str]:
    """Get concise actionable insight strings for an agent.

    Returns a list of insight text strings (high confidence, unapplied).
    Safe to call every tick — cached and fast.
    """
    learnings = get_learnings(agent, min_confidence=0.7, unapplied_only=True)
    return [l.get("insight", "")[:200] for l in learnings if l.get("insight")]


def get_agent_summary(agent: str) -> str:
    """Get a single-paragraph summary of Atlas knowledge about an agent.

    Useful for injecting into GPT system prompts (Hawk analyst, etc.)
    """
    learnings = get_learnings(agent, min_confidence=0.7)
    if not learnings:
        return ""
    lines = []
    for l in learnings:
        insight = l.get("insight", "")[:150]
        conf = l.get("confidence", 0)
        if insight:
            lines.append(f"- [{conf:.0%}] {insight}")
    if not lines:
        return ""
    return "ATLAS INTELLIGENCE:\n" + "\n".join(lines[-5:])  # Last 5 most recent


# Improvements cache (separate from KB cache)
_imp_cache: dict = {}
_imp_cache_ts: float = 0.0
_IMP_CACHE_TTL = 300  # 5 minutes


def get_improvements(agent: str) -> list[dict]:
    """Get Atlas improvement suggestions for an agent.

    Reads from ~/atlas/data/improvements.json (cached 5 min).
    """
    global _imp_cache, _imp_cache_ts
    now = time.time()
    if _imp_cache and now - _imp_cache_ts < _IMP_CACHE_TTL:
        return _imp_cache.get(agent, [])

    imp_file = Path.home() / "atlas" / "data" / "improvements.json"
    if not imp_file.exists():
        return []
    try:
        _imp_cache = json.loads(imp_file.read_text())
        _imp_cache_ts = now
        return _imp_cache.get(agent, [])
    except Exception:
        log.debug("Failed to read Atlas improvements file")
        return []
