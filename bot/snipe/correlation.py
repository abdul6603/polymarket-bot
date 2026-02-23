"""Cross-Asset Correlation Scanner — detects aligned moves across BTC/ETH/SOL/XRP.

Called after all asset slots are scored each tick. When 3/4 or 4/4 assets
move in the same direction, applies a score bonus to each aligned signal.

Also manages position sizing for concurrent positions:
  1st position = 1.0x, 2nd = 0.8x, 3rd = 0.6x
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger("garves.snipe")


@dataclass
class CorrelationResult:
    """Result of cross-asset correlation evaluation."""
    dominant_direction: str  # "up", "down", or "mixed"
    aligned_count: int       # How many assets agree (0-4)
    total_scored: int        # How many assets had scores this tick
    score_bonus: float       # Bonus to add to each aligned score
    size_multiplier: float   # Position sizing: 1st=1.0, 2nd=0.8, 3rd=0.6


# Position sizing ladder based on active concurrent positions
POSITION_SIZE_LADDER = {0: 1.0, 1: 0.8, 2: 0.6}


def evaluate_correlation(
    asset_scores: dict[str, tuple[str, float]],
    active_positions: int = 0,
) -> CorrelationResult:
    """Evaluate cross-asset correlation from this tick's scored signals.

    Args:
        asset_scores: {asset: (direction, score)} for each asset that
                      scored above minimum threshold this tick
        active_positions: number of currently open positions (for sizing)

    Returns:
        CorrelationResult with bonus and sizing info
    """
    if not asset_scores:
        return CorrelationResult(
            dominant_direction="mixed", aligned_count=0,
            total_scored=0, score_bonus=0.0,
            size_multiplier=POSITION_SIZE_LADDER.get(active_positions, 0.6),
        )

    # Count directions
    up_count = sum(1 for d, _ in asset_scores.values() if d == "up")
    down_count = sum(1 for d, _ in asset_scores.values() if d == "down")
    total = len(asset_scores)

    # Determine dominant direction
    if up_count > down_count:
        dominant = "up"
        aligned = up_count
    elif down_count > up_count:
        dominant = "down"
        aligned = down_count
    else:
        dominant = "mixed"
        aligned = max(up_count, down_count)

    # Score bonus based on alignment
    if aligned >= 4:
        bonus = 8.0
    elif aligned >= 3:
        bonus = 5.0
    else:
        bonus = 0.0

    # Position size multiplier
    size_mult = POSITION_SIZE_LADDER.get(active_positions, 0.6)

    if bonus > 0:
        assets_str = ", ".join(
            f"{a[:3].upper()}={d.upper()}"
            for a, (d, s) in asset_scores.items()
        )
        log.info(
            "[CORRELATION] %d/%d %s → +%.0f bonus | sizing=%.1fx | %s",
            aligned, total, dominant.upper(), bonus, size_mult, assets_str,
        )

    return CorrelationResult(
        dominant_direction=dominant,
        aligned_count=aligned,
        total_scored=total,
        score_bonus=bonus,
        size_multiplier=size_mult,
    )
