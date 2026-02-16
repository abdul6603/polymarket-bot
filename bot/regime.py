"""Garves — Market Regime Detection via Fear & Greed Index.

Adjusts trading parameters dynamically based on market sentiment:
- Extreme Fear: aggressive (buy the fear)
- Neutral: default parameters
- Extreme Greed: conservative (reduce exposure)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from bot.http_session import get_session

log = logging.getLogger(__name__)

# Cache FnG value for 5 minutes
_regime_cache: dict = {"regime": None, "timestamp": 0.0}
_REGIME_CACHE_TTL = 300


@dataclass
class RegimeAdjustment:
    label: str              # "extreme_fear", "fear", "neutral", "greed", "extreme_greed"
    fng_value: int          # Current Fear & Greed value (0-100)
    size_multiplier: float  # Position size multiplier
    edge_multiplier: float  # Min-edge multiplier (lower = easier entry)
    consensus_offset: int   # Consensus requirement adjustment
    confidence_floor: float # Minimum confidence override


# Default regime when API is unavailable
_DEFAULT_REGIME = RegimeAdjustment(
    label="neutral", fng_value=50,
    size_multiplier=1.0, edge_multiplier=1.0,
    consensus_offset=0, confidence_floor=0.25,
)

REGIME_TABLE = {
    # FIXED: extreme_fear was making trading EASIER (lower edge, lower consensus)
    # but data shows 35.7% WR in extreme_fear — indicators are unreliable in panics.
    # Now: trade LESS and require STRONGER signals during extreme fear.
    "extreme_fear": lambda fng: RegimeAdjustment(
        label="extreme_fear", fng_value=fng,
        size_multiplier=0.7, edge_multiplier=1.3,
        consensus_offset=1, confidence_floor=0.30,
    ),
    "fear": lambda fng: RegimeAdjustment(
        label="fear", fng_value=fng,
        size_multiplier=0.9, edge_multiplier=1.1,
        consensus_offset=0, confidence_floor=0.25,
    ),
    "neutral": lambda fng: RegimeAdjustment(
        label="neutral", fng_value=fng,
        size_multiplier=1.0, edge_multiplier=1.0,
        consensus_offset=0, confidence_floor=0.25,
    ),
    "greed": lambda fng: RegimeAdjustment(
        label="greed", fng_value=fng,
        size_multiplier=0.8, edge_multiplier=1.2,
        consensus_offset=1, confidence_floor=0.30,
    ),
    "extreme_greed": lambda fng: RegimeAdjustment(
        label="extreme_greed", fng_value=fng,
        size_multiplier=0.5, edge_multiplier=1.5,
        consensus_offset=2, confidence_floor=0.35,
    ),
}


def _classify_fng(value: int) -> str:
    if value < 20:
        return "extreme_fear"
    elif value < 40:
        return "fear"
    elif value < 60:
        return "neutral"
    elif value < 80:
        return "greed"
    else:
        return "extreme_greed"


def detect_regime() -> RegimeAdjustment:
    """Fetch Fear & Greed Index and return regime-appropriate parameter adjustments.

    Uses a 5-minute cache to avoid hammering the API.
    Falls back to neutral if the API is unavailable.
    """
    global _regime_cache
    now = time.time()

    if _regime_cache["regime"] is not None and now - _regime_cache["timestamp"] < _REGIME_CACHE_TTL:
        return _regime_cache["regime"]

    try:
        resp = get_session().get("https://api.alternative.me/fng/?limit=1", timeout=5)
        if resp.status_code != 200:
            log.warning("[REGIME] FnG API returned %d, using cached/default", resp.status_code)
            return _regime_cache["regime"] or _DEFAULT_REGIME

        data = resp.json()
        fng_value = int(data["data"][0]["value"])
        label = _classify_fng(fng_value)
        regime = REGIME_TABLE[label](fng_value)

        _regime_cache = {"regime": regime, "timestamp": now}
        log.info(
            "[REGIME] %s (FnG=%d) | size=%.1fx edge=%.2fx consensus=%+d conf_floor=%.2f",
            regime.label.upper(), regime.fng_value,
            regime.size_multiplier, regime.edge_multiplier,
            regime.consensus_offset, regime.confidence_floor,
        )
        return regime

    except Exception as e:
        log.warning("[REGIME] FnG fetch failed: %s, using default", str(e)[:80])
        return _regime_cache["regime"] or _DEFAULT_REGIME
