"""Market Regime Detection — know when NOT to trade.

Checks market-wide conditions and determines if Hawk should be active,
cautious, or paused.

Signals:
  - High volatility: many markets moving = systematic uncertainty
  - Thin liquidity hours: 2-5 AM ET = wider spreads, worse fills
  - Loss streak: 3+ consecutive losses = cool-down period
  - API health: if sportsbook/CLOB APIs degraded = reduce activity
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
TRADES_FILE = DATA_DIR / "hawk_trades.jsonl"

ET = ZoneInfo("America/New_York")


@dataclass
class RegimeState:
    regime: str              # "normal", "cautious", "paused"
    reasons: list[str]       # why this regime
    size_multiplier: float   # 1.0 normal, 0.5 cautious, 0.0 paused
    should_skip_cycle: bool  # True = don't trade at all


def check_regime(
    market_prices: list[float] | None = None,
    prev_prices: list[float] | None = None,
    consecutive_losses: int = 0,
    category: str = "",
) -> RegimeState:
    """Evaluate market regime from multiple signals.

    Args:
        market_prices: Current YES prices for all scanned markets (0-1)
        prev_prices: Previous cycle's prices for same markets
        consecutive_losses: Number of consecutive losing trades

    Returns:
        RegimeState with trading recommendation.
    """
    reasons = []
    multiplier = 1.0
    skip = False

    # ── Check 1: Time of day (thin liquidity) ──
    now_et = datetime.now(ET)
    hour = now_et.hour
    if 2 <= hour < 5:
        reasons.append(f"thin_liquidity_{hour}am_ET")
        multiplier *= 0.5
        log.info("[REGIME] Thin liquidity window: %d:00 AM ET — reducing size 50%%", hour)

    # ── Check 2: Loss streak ──
    if consecutive_losses >= 5:
        reasons.append(f"loss_streak_{consecutive_losses}")
        skip = True
        multiplier = 0.0
        log.warning("[REGIME] PAUSED: %d consecutive losses — skipping cycle", consecutive_losses)
    elif consecutive_losses >= 3:
        reasons.append(f"loss_streak_{consecutive_losses}")
        multiplier *= 0.5
        log.info("[REGIME] Cautious: %d consecutive losses — reducing size 50%%", consecutive_losses)

    # ── Check 3: Market volatility ──
    if market_prices and prev_prices and len(market_prices) == len(prev_prices):
        moves = [abs(c - p) for c, p in zip(market_prices, prev_prices) if p > 0]
        if moves:
            big_moves = sum(1 for m in moves if m > 0.05)
            volatility_pct = big_moves / len(moves) * 100
            if volatility_pct > 30:
                reasons.append(f"high_volatility_{volatility_pct:.0f}%")
                multiplier *= 0.5
                log.info("[REGIME] High volatility: %.0f%% of markets moved >5%% — reducing size", volatility_pct)
            elif volatility_pct > 15:
                reasons.append(f"elevated_volatility_{volatility_pct:.0f}%")
                multiplier *= 0.7

    # ── Check 4: Recent loss rate from trades file ──
    recent_wr = _recent_win_rate(hours=6)
    if recent_wr is not None and recent_wr < 0.20:
        reasons.append(f"poor_recent_wr_{recent_wr:.0%}")
        multiplier *= 0.5
        log.info("[REGIME] Poor 6h win rate: %.0f%% — reducing size", recent_wr * 100)

    # ── Check 5: V8 Category-specific regime ──
    if category:
        cat_mult, cat_reason = _category_regime(category)
        if cat_mult < 1.0:
            reasons.append(cat_reason)
            multiplier *= cat_mult
            if cat_mult == 0.0:
                skip = True
                log.warning("[REGIME] Category '%s' BLOCKED: %s", category, cat_reason)
            else:
                log.info("[REGIME] Category '%s' cold (%.0fx): %s", category, cat_mult, cat_reason)

    # Determine regime
    if skip or multiplier <= 0.0:
        regime = "paused"
        multiplier = 0.0
        skip = True
    elif multiplier < 0.7:
        regime = "cautious"
    else:
        regime = "normal"

    if reasons:
        log.info("[REGIME] State=%s mult=%.2f reasons=%s", regime, multiplier, ", ".join(reasons))

    return RegimeState(
        regime=regime,
        reasons=reasons,
        size_multiplier=round(multiplier, 2),
        should_skip_cycle=skip,
    )


def _recent_win_rate(hours: int = 6) -> float | None:
    """Calculate win rate from trades in the last N hours."""
    if not TRADES_FILE.exists():
        return None

    cutoff = time.time() - hours * 3600
    wins = 0
    total = 0

    try:
        with open(TRADES_FILE) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                t = json.loads(line)
                if not t.get("resolved"):
                    continue
                resolve_time = t.get("resolve_time", 0)
                if resolve_time < cutoff:
                    continue
                total += 1
                if t.get("won"):
                    wins += 1
    except Exception:
        return None

    if total < 3:
        return None  # Not enough data

    return wins / total


def _category_regime(category: str, hours: int = 12) -> tuple[float, str]:
    """V8: Per-category regime filter based on recent resolved trades.

    Returns (multiplier, reason):
      1.0 = normal, 0.5 = cold (reduce 50%), 0.0 = dead (block entirely)
    """
    if not TRADES_FILE.exists():
        return 1.0, ""

    cutoff = time.time() - hours * 3600
    wins = 0
    total = 0

    try:
        with open(TRADES_FILE) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                t = json.loads(line)
                if not t.get("resolved"):
                    continue
                if t.get("category", "") != category:
                    continue
                resolve_time = t.get("resolve_time", 0)
                if resolve_time < cutoff:
                    continue
                total += 1
                if t.get("won"):
                    wins += 1
    except Exception:
        return 1.0, ""

    if total < 3:
        return 1.0, ""  # Not enough data to judge

    wr = wins / total
    if wr < 0.20 and total >= 5:
        return 0.0, f"category_{category}_dead_{wr:.0%}_wr_{total}trades"
    if wr < 0.35:
        return 0.5, f"category_{category}_cold_{wr:.0%}_wr_{total}trades"

    return 1.0, ""
