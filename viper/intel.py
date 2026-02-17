"""Intelligence data structures and storage for Viper's market intelligence feed."""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
INTEL_FILE = DATA_DIR / "viper_intel.json"
MARKET_CONTEXT_FILE = DATA_DIR / "viper_market_context.json"

# Keep last N intel items to prevent unbounded growth
MAX_INTEL_ITEMS = 500
# Intel expires after 24 hours
INTEL_TTL_SECONDS = 86400


@dataclass
class IntelItem:
    id: str = ""
    source: str = ""           # tavily, reddit, polymarket_activity
    headline: str = ""
    summary: str = ""
    url: str = ""
    relevance_tags: list[str] = field(default_factory=list)
    sentiment: float = 0.0     # -1 (bearish/negative) to 1 (bullish/positive)
    confidence: float = 0.5    # 0 to 1
    timestamp: float = 0.0
    matched_markets: list[str] = field(default_factory=list)  # condition_ids
    category: str = ""         # politics, sports, crypto, culture, other
    raw_data: dict = field(default_factory=dict)


def make_intel_id(source: str, headline: str) -> str:
    raw = f"{source}:{headline}".lower().strip()
    return hashlib.md5(raw.encode()).hexdigest()[:16]


def load_intel() -> list[dict]:
    """Load current intel feed from disk."""
    if not INTEL_FILE.exists():
        return []
    try:
        data = json.loads(INTEL_FILE.read_text())
        items = data.get("items", [])
        # Prune expired
        now = time.time()
        items = [i for i in items if now - i.get("timestamp", 0) < INTEL_TTL_SECONDS]
        return items
    except Exception:
        log.exception("Failed to load intel feed")
        return []


def save_intel(items: list[dict]) -> None:
    """Save intel feed to disk (with pruning)."""
    DATA_DIR.mkdir(exist_ok=True)
    now = time.time()
    # Prune expired and limit size
    items = [i for i in items if now - i.get("timestamp", 0) < INTEL_TTL_SECONDS]
    items = items[-MAX_INTEL_ITEMS:]
    try:
        INTEL_FILE.write_text(json.dumps({
            "items": items,
            "count": len(items),
            "updated": now,
        }, indent=2))
    except Exception:
        log.exception("Failed to save intel feed")


def append_intel(new_items: list[IntelItem]) -> int:
    """Append new intel items, deduplicate, save. Returns count of new items added."""
    existing = load_intel()
    seen_ids = {i.get("id") for i in existing}
    added = 0
    for item in new_items:
        if item.id not in seen_ids:
            existing.append(asdict(item))
            seen_ids.add(item.id)
            added += 1
    save_intel(existing)
    return added


def load_market_context() -> dict[str, list[dict]]:
    """Load per-market context file. Returns {condition_id: [intel_items]}."""
    if not MARKET_CONTEXT_FILE.exists():
        return {}
    try:
        return json.loads(MARKET_CONTEXT_FILE.read_text())
    except Exception:
        return {}


def save_market_context(context: dict[str, list[dict]]) -> None:
    """Save per-market context for Hawk consumption."""
    DATA_DIR.mkdir(exist_ok=True)
    try:
        MARKET_CONTEXT_FILE.write_text(json.dumps(context, indent=2))
    except Exception:
        log.exception("Failed to save market context")


def get_context_for_market(condition_id: str) -> list[dict]:
    """Get Viper intel relevant to a specific market. Used by Hawk."""
    ctx = load_market_context()
    return ctx.get(condition_id, [])
