"""Post-trade analysis — compare expected vs actual on every trade.

Principle: Logs > Opinions. Every closed trade gets 4 quality scores
and an EV capture metric. Appended to trade records in odin_trades.jsonl.
"""
from __future__ import annotations

import json
import logging
import statistics
from pathlib import Path

from odin.exchange.models import TradeResult

log = logging.getLogger("odin.analytics.trade_analyzer")


class TradeAnalyzer:
    """Post-trade analysis engine — 4 quality scores per trade."""

    def __init__(self, data_dir: Path | None = None):
        self._data_dir = data_dir or Path(__file__).parent.parent / "data"
        self._trades_file = self._data_dir / "odin_trades.jsonl"

    def analyze(self, result: TradeResult, signal: dict | None = None) -> dict:
        """Analyze a closed trade. Returns analysis dict with 4 quality scores.

        Scores (0-100):
          entry_quality: price improvement from signal to fill
          exit_quality: R captured vs target R:R
          sl_quality: was SL too tight (wick hit) or appropriate?
          timing_quality: hold duration vs regime-optimal
        Plus: expected_ev, actual_ev, ev_capture_pct
        """
        signal = signal or {}
        entry = result.entry_price
        exit_p = result.exit_price
        sl = result.stop_loss_price
        direction = result.side

        # ── Entry Quality (0-100) ──
        # Measures how well the entry price compares to signal price.
        # Perfect = entered at or better than signal, 0 = entered much worse.
        signal_price = signal.get("signal_price", entry)
        if signal_price > 0 and entry > 0:
            if direction == "LONG":
                # For longs, lower entry is better
                improvement = (signal_price - entry) / signal_price * 100
            else:
                # For shorts, higher entry is better
                improvement = (entry - signal_price) / signal_price * 100
            # Map: +0.5% improvement=100, 0=70, -0.5%=40, -1%=0
            entry_quality = max(0, min(100, 70 + improvement * 60))
        else:
            entry_quality = 50  # No reference price

        # ── Exit Quality (0-100) ──
        # Measures R captured vs expected R:R.
        expected_rr = result.expected_rr
        actual_rr = result.actual_rr
        if expected_rr > 0:
            rr_capture = actual_rr / expected_rr
            if result.is_win:
                # Winning: full RR capture = 100, partial = proportional
                exit_quality = max(0, min(100, rr_capture * 100))
            else:
                # Losing: small loss = 60, full SL hit = 20, worse = 0
                exit_quality = max(0, min(60, 60 - abs(actual_rr) * 40))
        else:
            exit_quality = 50 if result.is_win else 30

        # ── SL Quality (0-100) ──
        # Was the stop-loss appropriately placed?
        if sl > 0 and entry > 0:
            sl_dist_pct = abs(entry - sl) / entry * 100
            if result.exit_reason == "stop_loss":
                # SL was hit — was it too tight?
                if sl_dist_pct < 0.5:
                    sl_quality = 20  # Way too tight
                elif sl_dist_pct < 1.0:
                    sl_quality = 40  # Tight
                else:
                    sl_quality = 60  # Reasonable SL hit
            elif result.is_win:
                # Won without hitting SL — good placement
                sl_quality = 85 if sl_dist_pct >= 1.0 else 70
            else:
                # Lost without SL (time exit, manual, etc.)
                sl_quality = 50
        else:
            sl_quality = 50  # No SL data

        # ── Timing Quality (0-100) ──
        # Optimal hold varies by regime. Trending = longer, choppy = shorter.
        hold_h = result.hold_duration_hours
        regime = result.macro_regime.lower() if result.macro_regime else "neutral"

        # Regime-optimal hold ranges (hours)
        optimal = {"trending": (4, 24), "strong_bull": (4, 24), "strong_bear": (4, 24),
                    "neutral": (2, 12), "choppy": (1, 6)}
        lo, hi = optimal.get(regime, (2, 12))

        if lo <= hold_h <= hi:
            timing_quality = 90  # In optimal range
        elif hold_h < lo:
            # Too short — proportional penalty
            timing_quality = max(30, int(90 * hold_h / lo))
        else:
            # Too long — diminishing returns
            timing_quality = max(40, int(90 - (hold_h - hi) * 5))

        # ── EV Metrics ──
        risk_usd = abs(entry - sl) * result.qty if sl > 0 else 0
        expected_ev = risk_usd * expected_rr if expected_rr > 0 and risk_usd > 0 else 0
        actual_ev = result.pnl_usd
        ev_capture = (actual_ev / expected_ev * 100) if expected_ev > 0 else 0

        analysis = {
            "entry_quality": round(entry_quality),
            "exit_quality": round(exit_quality),
            "sl_quality": round(sl_quality),
            "timing_quality": round(timing_quality),
            "expected_ev": round(expected_ev, 2),
            "actual_ev": round(actual_ev, 2),
            "ev_capture_pct": round(ev_capture, 1),
            "hold_hours": round(hold_h, 2),
            "sl_distance_pct": round(abs(entry - sl) / entry * 100, 3) if sl > 0 and entry > 0 else 0,
            "regime": regime,
        }

        log.info(
            "[ANALYSIS] %s %s: entry=%d/100 exit=%d/100 sl=%d/100 timing=%d/100 | EV capture: %.0f%%",
            result.symbol, result.side,
            analysis["entry_quality"], analysis["exit_quality"],
            analysis["sl_quality"], analysis["timing_quality"],
            analysis["ev_capture_pct"],
        )

        return analysis

    def get_rolling_stats(self, lookback: int = 50) -> dict:
        """Rolling WR, avg RR, Sharpe, max drawdown over last N trades."""
        trades = self._read_trades(lookback)
        if not trades:
            return {"trades": 0, "insufficient_data": True}

        pnls = [t.get("pnl_usd", 0) for t in trades]
        wins = sum(1 for p in pnls if p > 0)
        total = len(pnls)

        # Rolling Sharpe
        if len(pnls) > 1:
            mean_pnl = statistics.mean(pnls)
            std_pnl = statistics.stdev(pnls)
            sharpe = mean_pnl / std_pnl if std_pnl > 0 else 0
        else:
            sharpe = 0

        # Max drawdown
        running = 0.0
        peak = 0.0
        max_dd = 0.0
        for p in pnls:
            running += p
            peak = max(peak, running)
            dd = peak - running
            max_dd = max(max_dd, dd)

        # Avg quality scores (if analysis exists)
        qualities = {"entry": [], "exit": [], "sl": [], "timing": []}
        for t in trades:
            a = t.get("analysis", {})
            if a:
                qualities["entry"].append(a.get("entry_quality", 0))
                qualities["exit"].append(a.get("exit_quality", 0))
                qualities["sl"].append(a.get("sl_quality", 0))
                qualities["timing"].append(a.get("timing_quality", 0))

        avg_qualities = {
            k: round(statistics.mean(v), 1) if v else 0
            for k, v in qualities.items()
        }

        return {
            "trades": total,
            "wins": wins,
            "losses": total - wins,
            "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
            "avg_pnl": round(statistics.mean(pnls), 2) if pnls else 0,
            "total_pnl": round(sum(pnls), 2),
            "sharpe": round(sharpe, 3),
            "max_drawdown": round(max_dd, 2),
            "avg_rr": round(
                statistics.mean([t.get("actual_rr", 0) for t in trades if t.get("actual_rr")]), 2
            ) if any(t.get("actual_rr") for t in trades) else 0,
            "avg_quality": avg_qualities,
        }

    def _read_trades(self, limit: int = 50) -> list[dict]:
        """Read last N trades from JSONL."""
        if not self._trades_file.exists():
            return []
        lines = self._trades_file.read_text().strip().split("\n")
        trades = []
        for line in lines[-limit:]:
            try:
                trades.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return trades
