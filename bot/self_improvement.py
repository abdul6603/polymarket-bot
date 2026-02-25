"""Garves V2 — Self-Improvement Metrics Engine.

Calculates core performance metrics, logs improvements, and
suggests parameter adjustments based on metric thresholds.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
TRADES_FILE = DATA_DIR / "trades.jsonl"
IMPROVEMENT_LOG_FILE = DATA_DIR / "improvement_log.jsonl"
METRICS_FILE = DATA_DIR / "performance_metrics.json"


@dataclass
class CoreMetrics:
    """Snapshot of core trading performance metrics."""
    wr_20: float | None = None      # 20-trade rolling win rate
    wr_50: float | None = None      # 50-trade rolling win rate
    wr_100: float | None = None     # 100-trade rolling win rate
    ev_capture_pct: float = 0.0     # Actual PnL / Expected PnL
    avg_slippage_pct: float = 0.0   # Average execution slippage
    total_slippage_cost: float = 0.0
    current_drawdown_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    variance_vs_expected: float = 0.0  # How much actual results differ from expected
    total_resolved: int = 0
    total_pnl: float = 0.0
    timestamp: float = field(default_factory=time.time)


class SelfImprovementEngine:
    """Calculates metrics, logs improvements, suggests parameter changes.

    Usage:
        engine = SelfImprovementEngine()
        metrics = engine.calculate_metrics()
        suggestions = engine.suggest_improvements(metrics)
    """

    def calculate_metrics(self) -> CoreMetrics:
        """Load resolved trades and compute all core metrics."""
        resolved = self._load_resolved()
        metrics = CoreMetrics(total_resolved=len(resolved))

        if not resolved:
            return metrics

        # Rolling win rates
        if len(resolved) >= 20:
            recent_20 = resolved[-20:]
            metrics.wr_20 = sum(1 for t in recent_20 if t.get("won")) / 20

        if len(resolved) >= 50:
            recent_50 = resolved[-50:]
            metrics.wr_50 = sum(1 for t in recent_50 if t.get("won")) / 50

        if len(resolved) >= 100:
            recent_100 = resolved[-100:]
            metrics.wr_100 = sum(1 for t in recent_100 if t.get("won")) / 100

        # EV capture: actual PnL vs expected PnL
        total_ev_predicted = 0.0
        total_pnl = 0.0
        for t in resolved:
            total_pnl += t.get("pnl", 0.0)
            total_ev_predicted += t.get("ev_predicted", 0.0)

        metrics.total_pnl = round(total_pnl, 2)
        if total_ev_predicted > 0:
            metrics.ev_capture_pct = total_pnl / total_ev_predicted
        elif total_pnl > 0:
            metrics.ev_capture_pct = 1.0

        # Slippage
        slippages = [t.get("ob_slippage_pct", 0) for t in resolved if t.get("ob_slippage_pct")]
        if slippages:
            metrics.avg_slippage_pct = sum(slippages) / len(slippages)
        slip_costs = [t.get("ob_slippage_pct", 0) * t.get("size_usd", 0) for t in resolved]
        metrics.total_slippage_cost = round(sum(slip_costs), 2)

        # Drawdown
        equity = 0.0
        peak = 0.0
        max_dd = 0.0
        for t in resolved:
            pnl = t.get("pnl", 0.0)
            equity += pnl
            peak = max(peak, equity)
            if peak > 0:
                dd = (peak - equity) / peak * 100
                max_dd = max(max_dd, dd)

        metrics.current_drawdown_pct = round((peak - equity) / peak * 100, 1) if peak > 0 else 0.0
        metrics.max_drawdown_pct = round(max_dd, 1)

        # Variance vs expected
        if len(resolved) >= 20:
            expected_pnls = []
            actual_pnls = []
            for t in resolved[-50:]:
                edge = t.get("edge", 0.08)
                size = t.get("size_usd", 10.0)
                expected_pnls.append(edge * size)
                actual_pnls.append(t.get("pnl", 0.0))
            if expected_pnls:
                avg_expected = sum(expected_pnls) / len(expected_pnls)
                avg_actual = sum(actual_pnls) / len(actual_pnls)
                metrics.variance_vs_expected = round(abs(avg_actual - avg_expected), 4)

        return metrics

    @staticmethod
    def log_improvement(trigger: str, component: str, old_value: float, new_value: float, reason: str) -> None:
        """Append an improvement event to the log."""
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": time.time(),
            "trigger": trigger,
            "component": component,
            "old_value": old_value,
            "new_value": new_value,
            "reason": reason,
        }
        try:
            with open(IMPROVEMENT_LOG_FILE, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            log.debug("Failed to write improvement log")

    @staticmethod
    def suggest_improvements(metrics: CoreMetrics) -> list[dict]:
        """Generate rule-based improvement suggestions from metric thresholds."""
        suggestions = []

        # Win rate degradation
        if metrics.wr_20 is not None and metrics.wr_20 < 0.50:
            suggestions.append({
                "priority": "high",
                "component": "signal_engine",
                "suggestion": f"20-trade WR is {metrics.wr_20:.0%} — consider raising edge floor or confidence floor",
                "metric": "wr_20",
                "value": metrics.wr_20,
            })

        if metrics.wr_50 is not None and metrics.wr_50 < 0.55:
            suggestions.append({
                "priority": "medium",
                "component": "signal_engine",
                "suggestion": f"50-trade WR is {metrics.wr_50:.0%} — review indicator weights",
                "metric": "wr_50",
                "value": metrics.wr_50,
            })

        # EV capture
        if metrics.ev_capture_pct < 0.30 and metrics.total_resolved >= 20:
            suggestions.append({
                "priority": "high",
                "component": "execution",
                "suggestion": f"EV capture is {metrics.ev_capture_pct:.0%} — slippage or sizing issue",
                "metric": "ev_capture_pct",
                "value": metrics.ev_capture_pct,
            })

        # Slippage
        if metrics.avg_slippage_pct > 0.03:
            suggestions.append({
                "priority": "medium",
                "component": "execution",
                "suggestion": f"Avg slippage {metrics.avg_slippage_pct:.1%} — consider tighter spread limits",
                "metric": "avg_slippage_pct",
                "value": metrics.avg_slippage_pct,
            })

        # Drawdown
        if metrics.current_drawdown_pct > 25:
            suggestions.append({
                "priority": "high",
                "component": "risk",
                "suggestion": f"Drawdown at {metrics.current_drawdown_pct:.0f}% — reduce position sizes",
                "metric": "current_drawdown_pct",
                "value": metrics.current_drawdown_pct,
            })

        # Variance
        if metrics.variance_vs_expected > 0.50:
            suggestions.append({
                "priority": "low",
                "component": "model",
                "suggestion": f"High variance vs expected ({metrics.variance_vs_expected:.2f}) — model may be miscalibrated",
                "metric": "variance_vs_expected",
                "value": metrics.variance_vs_expected,
            })

        return suggestions

    @staticmethod
    def get_improvement_log(limit: int = 20) -> list[dict]:
        """Return recent improvement log entries."""
        if not IMPROVEMENT_LOG_FILE.exists():
            return []
        entries = []
        try:
            for line in IMPROVEMENT_LOG_FILE.read_text().splitlines():
                if line.strip():
                    entries.append(json.loads(line))
        except Exception:
            pass
        return entries[-limit:]

    @staticmethod
    def _load_resolved() -> list[dict]:
        """Load resolved trades from trades.jsonl."""
        if not TRADES_FILE.exists():
            return []
        resolved = []
        try:
            for line in TRADES_FILE.read_text().splitlines():
                if not line.strip():
                    continue
                t = json.loads(line)
                if t.get("resolved") and t.get("outcome") in ("up", "down"):
                    resolved.append(t)
        except Exception:
            pass
        return resolved
