"""Edge decay detection — rolling performance tracking + weekly review.

Principle: Edge must be measurable. Detect when it's eroding and auto-reduce exposure.
Uses z-test: recent 20 WR vs all-time WR to detect statistically significant decay.
"""
from __future__ import annotations

import json
import logging
import math
import time
from pathlib import Path

log = logging.getLogger("odin.analytics.edge_tracker")

# Decay thresholds
MIN_TRADES_FOR_DETECTION = 20
DECAY_Z_THRESHOLD = -1.5       # z < -1.5 → WEAKENING
DECAY_Z_CRITICAL = -2.0        # z < -2.0 → DECAYED


class EdgeTracker:
    """Weekly edge review + rolling performance tracking."""

    def __init__(self, data_dir: Path | None = None):
        self._data_dir = data_dir or Path(__file__).parent.parent / "data"
        self._trades_file = self._data_dir / "odin_trades.jsonl"
        self._edge_file = self._data_dir / "edge_report.json"

    def _load_trades(self, limit: int = 0) -> list[dict]:
        if not self._trades_file.exists():
            return []
        lines = self._trades_file.read_text().strip().split("\n")
        trades = []
        for line in (lines[-limit:] if limit else lines):
            try:
                trades.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return trades

    def rolling_sharpe(self, lookback: int = 50) -> float:
        """Annualized Sharpe ratio over last N trades."""
        trades = self._load_trades(lookback)
        if len(trades) < 10:
            return 0.0

        pnls = [t.get("pnl_usd", 0) for t in trades]
        mean_pnl = sum(pnls) / len(pnls)
        if len(pnls) < 2:
            return 0.0

        variance = sum((p - mean_pnl) ** 2 for p in pnls) / (len(pnls) - 1)
        std = math.sqrt(variance) if variance > 0 else 0.001

        # Annualize assuming ~250 trading days, ~2 trades/day avg
        trades_per_year = 500
        sharpe = (mean_pnl / std) * math.sqrt(trades_per_year)
        return round(sharpe, 2)

    def detect_decay(self) -> str:
        """Detect edge erosion: STRONG / WEAKENING / DECAYED / INSUFFICIENT_DATA.

        Uses z-test: compare recent 20 WR vs all-time WR.
        """
        all_trades = self._load_trades()
        if len(all_trades) < MIN_TRADES_FOR_DETECTION + 10:
            return "INSUFFICIENT_DATA"

        # All-time win rate
        all_wins = sum(1 for t in all_trades if t.get("pnl_usd", 0) > 0)
        all_wr = all_wins / len(all_trades)

        # Recent window
        recent = all_trades[-MIN_TRADES_FOR_DETECTION:]
        recent_wins = sum(1 for t in recent if t.get("pnl_usd", 0) > 0)
        recent_wr = recent_wins / len(recent)

        # Z-test for proportions
        p = all_wr
        n = len(recent)
        if p <= 0 or p >= 1:
            return "INSUFFICIENT_DATA"

        se = math.sqrt(p * (1 - p) / n)
        z = (recent_wr - p) / se if se > 0 else 0

        if z < DECAY_Z_CRITICAL:
            status = "DECAYED"
            log.warning(
                "[EDGE] DECAYED — recent WR=%.0f%% vs all-time=%.0f%% (z=%.2f)",
                recent_wr * 100, all_wr * 100, z,
            )
        elif z < DECAY_Z_THRESHOLD:
            status = "WEAKENING"
            log.warning(
                "[EDGE] WEAKENING — recent WR=%.0f%% vs all-time=%.0f%% (z=%.2f)",
                recent_wr * 100, all_wr * 100, z,
            )
        else:
            status = "STRONG"

        return status

    def get_risk_scalar(self) -> float:
        """Risk multiplier based on edge status. Only reduces, never increases."""
        status = self.detect_decay()
        scalars = {
            "STRONG": 1.0,
            "WEAKENING": 0.7,
            "DECAYED": 0.3,
            "INSUFFICIENT_DATA": 1.0,
        }
        scalar = scalars.get(status, 1.0)
        if scalar < 1.0:
            log.info("[EDGE] Risk scalar: %.1f (status=%s)", scalar, status)
        return scalar

    def weekly_report(self) -> dict:
        """Comprehensive weekly edge report."""
        all_trades = self._load_trades()

        if not all_trades:
            return {"status": "no_data", "trades": 0}

        # Overall metrics
        total = len(all_trades)
        wins = sum(1 for t in all_trades if t.get("pnl_usd", 0) > 0)
        wr = wins / total * 100 if total > 0 else 0

        pnls = [t.get("pnl_usd", 0) for t in all_trades]
        total_pnl = sum(pnls)
        avg_pnl = total_pnl / total if total > 0 else 0

        # Max drawdown
        peak = 0.0
        max_dd = 0.0
        running = 0.0
        for p in pnls:
            running += p
            if running > peak:
                peak = running
            dd = peak - running
            if dd > max_dd:
                max_dd = dd

        # Per-symbol breakdown
        by_symbol: dict[str, list[float]] = {}
        for t in all_trades:
            sym = t.get("symbol", "UNKNOWN")
            by_symbol.setdefault(sym, []).append(t.get("pnl_usd", 0))

        symbol_stats = {}
        for sym, sym_pnls in by_symbol.items():
            sym_wins = sum(1 for p in sym_pnls if p > 0)
            symbol_stats[sym] = {
                "trades": len(sym_pnls),
                "win_rate": round(sym_wins / len(sym_pnls) * 100, 1),
                "total_pnl": round(sum(sym_pnls), 2),
            }

        # Per-regime breakdown
        by_regime: dict[str, list[float]] = {}
        for t in all_trades:
            regime = t.get("regime", "unknown")
            by_regime.setdefault(regime, []).append(t.get("pnl_usd", 0))

        regime_stats = {}
        for regime, reg_pnls in by_regime.items():
            reg_wins = sum(1 for p in reg_pnls if p > 0)
            regime_stats[regime] = {
                "trades": len(reg_pnls),
                "win_rate": round(reg_wins / len(reg_pnls) * 100, 1),
                "total_pnl": round(sum(reg_pnls), 2),
            }

        # Conviction accuracy
        by_tier: dict[str, list[bool]] = {}
        for t in all_trades:
            tier = t.get("conviction_tier", "")
            if not tier:
                score = t.get("conviction_score", 0)
                if score >= 70:
                    tier = "HIGH"
                elif score >= 40:
                    tier = "MODERATE"
                else:
                    tier = "LOW"
            by_tier.setdefault(tier, []).append(t.get("pnl_usd", 0) > 0)

        conviction_accuracy = {}
        for tier, outcomes in by_tier.items():
            conviction_accuracy[tier] = {
                "trades": len(outcomes),
                "win_rate": round(sum(outcomes) / len(outcomes) * 100, 1),
            }

        # Edge status
        edge_status = self.detect_decay()
        sharpe = self.rolling_sharpe()

        # Recommendation
        if edge_status == "DECAYED":
            recommendation = "PAUSE"
        elif edge_status == "WEAKENING":
            recommendation = "REDUCE"
        elif wr < 45 and total >= 20:
            recommendation = "REDUCE"
        else:
            recommendation = "MAINTAIN"

        report = {
            "timestamp": time.time(),
            "edge_status": edge_status,
            "recommendation": recommendation,
            "overall": {
                "trades": total,
                "win_rate": round(wr, 1),
                "total_pnl": round(total_pnl, 2),
                "avg_pnl": round(avg_pnl, 2),
                "sharpe": sharpe,
                "max_drawdown": round(max_dd, 2),
            },
            "by_symbol": symbol_stats,
            "by_regime": regime_stats,
            "conviction_accuracy": conviction_accuracy,
            "risk_scalar": self.get_risk_scalar(),
        }

        # Persist
        try:
            self._edge_file.write_text(json.dumps(report, indent=2))
        except Exception:
            pass

        log.info(
            "[EDGE] Weekly: %d trades, %.0f%% WR, $%.2f PnL, Sharpe=%.2f, "
            "status=%s → %s",
            total, wr, total_pnl, sharpe, edge_status, recommendation,
        )
        return report
