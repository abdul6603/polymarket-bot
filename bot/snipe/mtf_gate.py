"""Multi-Timeframe Gate — confirms 5m signals against 15m/1h structure.

Called when the 5m scorer fires (score >= 75). Checks whether higher
timeframe structure agrees with the detected direction before execution.

Decision matrix:
  Strong confirm: 15m BOS/trend matches direction → execute 1.0x size
  Weak confirm:   15m neutral + 1h matches → execute 0.7x size
  Oppose:         15m opposes direction → SKIP
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from bot.snipe.candle_store import CandleStore
from bot.snipe.market_bridge import ExecutionMarket, find_execution_market

log = logging.getLogger("garves.snipe")


@dataclass
class MTFConfirmation:
    """Result of multi-timeframe gate check."""
    confirmed: bool
    strength: str          # "strong", "weak", "opposed", "no_data"
    exec_market: ExecutionMarket | None
    reason: str
    size_multiplier: float  # 1.0 for strong, 0.7 for weak, 0.0 for skip


def check_mtf(
    asset: str,
    direction: str,
    candle_store: CandleStore,
    preference: str = "15m",
) -> MTFConfirmation:
    """Check multi-timeframe confirmation for a 5m signal.

    Args:
        asset: "bitcoin", "ethereum", etc.
        direction: "up" or "down" from 5m scorer
        candle_store: CandleStore instance with live candle data
        preference: "15m" or "1h" execution preference

    Returns:
        MTFConfirmation with execution decision + market info
    """
    s15 = candle_store.get_structure(asset, "15m")
    s1h = candle_store.get_structure(asset, "1h")

    trend_15m = s15.get("trend", "neutral")
    bos_15m = s15.get("bos")
    trend_1h = s1h.get("trend", "neutral")
    bos_1h = s1h.get("bos")

    # Map direction to expected trend
    expected = "bullish" if direction == "up" else "bearish"
    opposite = "bearish" if direction == "up" else "bullish"

    # Check 15m alignment
    m15_aligned = (trend_15m == expected) or (bos_15m == expected)
    m15_opposed = (trend_15m == opposite) or (bos_15m == opposite)
    m15_neutral = not m15_aligned and not m15_opposed

    # Check 1h alignment
    m1h_aligned = (trend_1h == expected) or (bos_1h == expected)

    # Decision matrix
    if m15_opposed:
        reason = f"5m {direction.upper()} | 15m OPPOSED ({trend_15m}) → SKIP"
        log.info("[MULTI-TF] %s %s", asset.upper(), reason)
        return MTFConfirmation(
            confirmed=False, strength="opposed",
            exec_market=None, reason=reason, size_multiplier=0.0,
        )

    # Find execution market (try regardless — we'll need it if confirmed)
    exec_mkt = find_execution_market(asset, preference)

    if m15_aligned:
        reason = (
            f"5m {direction.upper()} | 15m confirmed {trend_15m.upper()} | "
            f"1h {trend_1h} → Execute on {preference}"
        )
        log.info("[MULTI-TF] %s %s", asset.upper(), reason)
        return MTFConfirmation(
            confirmed=True, strength="strong",
            exec_market=exec_mkt, reason=reason, size_multiplier=1.0,
        )

    if m15_neutral and m1h_aligned:
        reason = (
            f"5m {direction.upper()} | 15m neutral | "
            f"1h confirmed {trend_1h.upper()} → Weak execute on {preference}"
        )
        log.info("[MULTI-TF] %s %s", asset.upper(), reason)
        return MTFConfirmation(
            confirmed=True, strength="weak",
            exec_market=exec_mkt, reason=reason, size_multiplier=0.7,
        )

    # 15m neutral, 1h not aligned — not enough confirmation
    reason = (
        f"5m {direction.upper()} | 15m neutral | "
        f"1h {trend_1h} → No confirmation, SKIP"
    )
    log.info("[MULTI-TF] %s %s", asset.upper(), reason)
    return MTFConfirmation(
        confirmed=False, strength="no_data",
        exec_market=None, reason=reason, size_multiplier=0.0,
    )
