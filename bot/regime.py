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

    @classmethod
    def momentum_override(cls, base: "RegimeAdjustment") -> "RegimeAdjustment":
        """Override regime parameters during Momentum Capture Mode.

        Loosens fear/greed paralysis while keeping the regime label for logging.
        """
        return cls(
            label=f"momentum_{base.label}",
            fng_value=base.fng_value,
            size_multiplier=1.5,
            edge_multiplier=0.5,
            consensus_offset=0,
            confidence_floor=0.25,
        )


# Default regime when API is unavailable
_DEFAULT_REGIME = RegimeAdjustment(
    label="neutral", fng_value=50,
    size_multiplier=1.0, edge_multiplier=1.0,
    consensus_offset=0, confidence_floor=0.55,
)

REGIME_TABLE = {
    # Confidence floors lowered — edge floors + consensus are the real gates.
    # Let Garves trade and learn. Position sizing controls risk.
    "extreme_fear": lambda fng: RegimeAdjustment(
        label="extreme_fear", fng_value=fng,
        size_multiplier=0.9, edge_multiplier=1.05,
        consensus_offset=0, confidence_floor=0.35,  # Lowered 0.55→0.35: let him trade in fear, size limits risk
    ),
    "fear": lambda fng: RegimeAdjustment(
        label="fear", fng_value=fng,
        size_multiplier=0.95, edge_multiplier=1.05,
        consensus_offset=0, confidence_floor=0.35,  # Lowered 0.55→0.35
    ),
    "neutral": lambda fng: RegimeAdjustment(
        label="neutral", fng_value=fng,
        size_multiplier=1.0, edge_multiplier=1.0,
        consensus_offset=0, confidence_floor=0.35,  # Lowered 0.55→0.35
    ),
    "greed": lambda fng: RegimeAdjustment(
        label="greed", fng_value=fng,
        size_multiplier=0.8, edge_multiplier=1.2,
        consensus_offset=0, confidence_floor=0.40,  # Lowered 0.60→0.40
    ),
    "extreme_greed": lambda fng: RegimeAdjustment(
        label="extreme_greed", fng_value=fng,
        size_multiplier=0.5, edge_multiplier=1.5,
        consensus_offset=0, confidence_floor=0.45,  # Lowered 0.65→0.45
    ),
}


def _classify_fng(value: int) -> str:
    """Classify Fear & Greed index into regime buckets.

    Note: boundaries (20/40/60/80) intentionally differ from the standard
    FnG scale (25/50/75) to give wider neutral and fear bands, which better
    suit crypto prediction market volatility patterns.
    """
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
