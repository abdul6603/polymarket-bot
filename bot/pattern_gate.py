"""Garves — Historical Pattern Gate.

Loads resolved trades and computes rolling WR per (asset, timeframe, direction, regime) combo.
Blocks or raises the bar for historically losing combinations.

Rules:
- <35% WR over 20+ trades → BLOCK entirely
- <45% WR over 15+ trades → require edge >= 12%
- >65% WR over 15+ trades → lower edge requirement by 1pp

Refreshes every 10 minutes from trades.jsonl.
"""
from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
TRADES_FILE = DATA_DIR / "trades.jsonl"

# Refresh interval
_REFRESH_INTERVAL = 600  # 10 minutes

# Gate thresholds
BLOCK_WR = 0.35          # Block if WR < 35%
BLOCK_MIN_TRADES = 20    # Need 20+ trades to trigger block
RAISE_WR = 0.45          # Raise edge requirement if WR < 45%
RAISE_MIN_TRADES = 15    # Need 15+ trades to trigger raise
RAISE_EDGE = 0.12        # Required edge when WR < 45%
BOOST_WR = 0.65          # Lower edge requirement if WR > 65%
BOOST_MIN_TRADES = 15    # Need 15+ trades to trigger boost
BOOST_EDGE_REDUCTION = 0.01  # Reduce edge requirement by 1pp


@dataclass
class PatternStats:
    """Rolling stats for a (asset, timeframe, direction) combo."""
    wins: int = 0
    losses: int = 0

    @property
    def total(self) -> int:
        return self.wins + self.losses

    @property
    def win_rate(self) -> float:
        return self.wins / self.total if self.total > 0 else 0.5


@dataclass
class GateDecision:
    """Result of pattern gate evaluation."""
    allowed: bool
    reason: str
    edge_adjustment: float = 0.0  # Positive = raise requirement, negative = lower
    win_rate: float = 0.5
    sample_size: int = 0


class PatternGate:
    """Historical pattern gate — blocks losing combos, boosts winning ones."""

    def __init__(self):
        self._stats: dict[str, PatternStats] = defaultdict(PatternStats)
        self._last_refresh: float = 0.0
        self._loaded = False

    def _make_key(self, asset: str, timeframe: str, direction: str) -> str:
        """Create lookup key for a combo."""
        return f"{asset}:{timeframe}:{direction}"

    def _refresh(self) -> None:
        """Reload stats from trades.jsonl if stale."""
        now = time.time()
        if self._loaded and (now - self._last_refresh) < _REFRESH_INTERVAL:
            return

        new_stats: dict[str, PatternStats] = defaultdict(PatternStats)

        if not TRADES_FILE.exists():
            self._stats = new_stats
            self._last_refresh = now
            self._loaded = True
            return

        try:
            with open(TRADES_FILE) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    if not rec.get("resolved") or rec.get("outcome") not in ("up", "down"):
                        continue

                    asset = rec.get("asset", "")
                    tf = rec.get("timeframe", "")
                    direction = rec.get("direction", "")
                    won = rec.get("won", False)

                    if not asset or not tf or not direction:
                        continue

                    key = self._make_key(asset, tf, direction)
                    if won:
                        new_stats[key].wins += 1
                    else:
                        new_stats[key].losses += 1

            self._stats = new_stats
            self._last_refresh = now
            self._loaded = True

            # Log notable patterns
            for key, stats in new_stats.items():
                if stats.total >= 15:
                    wr = stats.win_rate
                    if wr < BLOCK_WR and stats.total >= BLOCK_MIN_TRADES:
                        log.info("[PATTERN_GATE] BLOCKED combo: %s (%.0f%% WR, %d trades)",
                                 key, wr * 100, stats.total)
                    elif wr < RAISE_WR:
                        log.info("[PATTERN_GATE] Raised bar for: %s (%.0f%% WR, %d trades)",
                                 key, wr * 100, stats.total)

        except Exception as e:
            log.warning("[PATTERN_GATE] Failed to load trades: %s", str(e)[:100])

    def evaluate(self, asset: str, timeframe: str, direction: str) -> GateDecision:
        """Evaluate whether a trade combo should be allowed.

        Returns GateDecision with allowed/blocked status and edge adjustments.
        """
        self._refresh()

        key = self._make_key(asset, timeframe, direction)
        stats = self._stats.get(key)

        if stats is None or stats.total < 10:
            # Not enough data — allow with no adjustment
            return GateDecision(
                allowed=True,
                reason="insufficient_data",
                win_rate=0.5,
                sample_size=stats.total if stats else 0,
            )

        wr = stats.win_rate
        total = stats.total

        # BLOCK: <35% WR over 20+ trades
        if wr < BLOCK_WR and total >= BLOCK_MIN_TRADES:
            log.info("[PATTERN_GATE] BLOCKED %s: %.0f%% WR over %d trades",
                     key, wr * 100, total)
            try:
                from bot.self_improvement import SelfImprovementEngine
                SelfImprovementEngine.log_improvement("pattern_gate", key, wr, 0.0, f"blocked: {wr:.0%} WR over {total} trades")
            except Exception:
                pass
            return GateDecision(
                allowed=False,
                reason=f"blocked_low_wr_{wr:.0%}_over_{total}",
                win_rate=wr,
                sample_size=total,
            )

        # RAISE: <45% WR over 15+ trades → require 12% edge
        if wr < RAISE_WR and total >= RAISE_MIN_TRADES:
            edge_adj = RAISE_EDGE  # Will be compared against actual edge in caller
            log.info("[PATTERN_GATE] Raised bar for %s: %.0f%% WR, requiring %.0f%% edge",
                     key, wr * 100, edge_adj * 100)
            return GateDecision(
                allowed=True,
                reason=f"raised_bar_{wr:.0%}_wr",
                edge_adjustment=edge_adj,
                win_rate=wr,
                sample_size=total,
            )

        # BOOST: >65% WR over 15+ trades → lower edge by 1pp
        if wr > BOOST_WR and total >= BOOST_MIN_TRADES:
            log.debug("[PATTERN_GATE] Boosted %s: %.0f%% WR, edge reduced by %.0f%%",
                      key, wr * 100, BOOST_EDGE_REDUCTION * 100)
            return GateDecision(
                allowed=True,
                reason=f"boosted_{wr:.0%}_wr",
                edge_adjustment=-BOOST_EDGE_REDUCTION,
                win_rate=wr,
                sample_size=total,
            )

        # Normal — allow with no adjustment
        return GateDecision(
            allowed=True,
            reason="normal",
            win_rate=wr,
            sample_size=total,
        )

    def get_all_stats(self) -> dict[str, dict]:
        """Return all combo stats for dashboard/monitoring."""
        self._refresh()
        result = {}
        for key, stats in sorted(self._stats.items()):
            if stats.total >= 5:
                result[key] = {
                    "wins": stats.wins,
                    "losses": stats.losses,
                    "total": stats.total,
                    "win_rate": f"{stats.win_rate:.0%}",
                }
        return result


# Module singleton
_gate: PatternGate | None = None


def get_pattern_gate() -> PatternGate:
    """Get or create the global pattern gate instance."""
    global _gate
    if _gate is None:
        _gate = PatternGate()
    return _gate
