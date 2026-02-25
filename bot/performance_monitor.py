"""Garves V2 — Performance Monitor + Kill Switch + Autonomous Debugging.

Auto-STOP when performance degrades. Systematically debug when things break.

Kill switch: 50-trade rolling WR < 52% → STOP
Degradation: 30-trade WR < 55% → WARNING
EV capture: < 40% → WARNING
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
DIAGNOSTICS_FILE = DATA_DIR / "diagnostics.json"
MONITOR_STATE_FILE = DATA_DIR / "performance_monitor_state.json"
ANALYSIS_FILE = DATA_DIR / "post_trade_analysis.jsonl"
INDICATOR_ACCURACY_FILE = DATA_DIR / "indicator_accuracy.json"

# Kill switch thresholds
KILL_SWITCH_WR = 0.52           # 50-trade rolling WR below this → STOP
KILL_SWITCH_WINDOW = 50         # Need 50 trades minimum
DEGRADATION_WR = 0.55           # 30-trade WR below this → WARNING
DEGRADATION_WINDOW = 30
EV_CAPTURE_MIN = 0.40           # Below 40% EV capture → WARNING


@dataclass
class PerformanceState:
    """Current performance snapshot."""
    timestamp: float = field(default_factory=time.time)
    rolling_wr_50: float | None = None
    rolling_wr_30: float | None = None
    ev_capture_pct: float | None = None
    avg_slippage_pct: float = 0.0
    current_drawdown_pct: float = 0.0
    model_drift_score: float = 0.0
    kill_switch_active: bool = False
    kill_switch_reason: str = ""
    diagnostic_mode: bool = False
    warnings: list = field(default_factory=list)
    total_resolved: int = 0


class PerformanceMonitor:
    """Monitors trading performance and triggers kill switch when degraded.

    Usage:
        monitor = PerformanceMonitor()
        state = monitor.check()
        if state.kill_switch_active:
            # STOP TRADING
    """

    def __init__(self):
        self._last_check: float = 0.0
        self._check_interval: float = 30.0  # Check every 30 seconds

    def check(self) -> PerformanceState:
        """Run full performance check.

        Returns:
            PerformanceState with kill switch status and warnings.
        """
        now = time.time()
        if now - self._last_check < self._check_interval:
            # Return cached state
            return self._load_state()

        self._last_check = now
        state = PerformanceState(timestamp=now)

        # Load resolved trades
        resolved = self._load_resolved_trades()
        state.total_resolved = len(resolved)

        if not resolved:
            self._save_state(state)
            return state

        # Calculate rolling win rates
        if len(resolved) >= KILL_SWITCH_WINDOW:
            recent_50 = resolved[-KILL_SWITCH_WINDOW:]
            wins_50 = sum(1 for t in recent_50 if t.get("won"))
            state.rolling_wr_50 = wins_50 / len(recent_50)

        if len(resolved) >= DEGRADATION_WINDOW:
            recent_30 = resolved[-DEGRADATION_WINDOW:]
            wins_30 = sum(1 for t in recent_30 if t.get("won"))
            state.rolling_wr_30 = wins_30 / len(recent_30)

        # EV capture from post-trade analysis
        state.ev_capture_pct = self._calculate_ev_capture()

        # Average slippage
        slippages = [t.get("ob_slippage_pct", 0) for t in resolved[-50:] if t.get("ob_slippage_pct")]
        if slippages:
            state.avg_slippage_pct = sum(slippages) / len(slippages)

        # Model drift
        state.model_drift_score = self._calculate_model_drift(resolved)

        # Drawdown
        state.current_drawdown_pct = self._calculate_drawdown(resolved)

        # Check kill switch
        if state.rolling_wr_50 is not None and state.rolling_wr_50 < KILL_SWITCH_WR:
            state.kill_switch_active = True
            state.kill_switch_reason = (
                f"50-trade WR={state.rolling_wr_50:.1%} < {KILL_SWITCH_WR:.0%} threshold"
            )
            log.warning("[KILL SWITCH] ACTIVATED: %s", state.kill_switch_reason)
            self._trigger_kill_switch(state)

        # Check warnings
        if state.rolling_wr_30 is not None and state.rolling_wr_30 < DEGRADATION_WR:
            state.warnings.append(
                f"30-trade WR={state.rolling_wr_30:.1%} below {DEGRADATION_WR:.0%}"
            )

        if state.ev_capture_pct is not None and state.ev_capture_pct < EV_CAPTURE_MIN:
            state.warnings.append(
                f"EV capture={state.ev_capture_pct:.0%} below {EV_CAPTURE_MIN:.0%}"
            )

        if state.model_drift_score > 0.15:
            state.warnings.append(
                f"Model drift detected: {state.model_drift_score:.2f} (>0.15 threshold)"
            )
            state.diagnostic_mode = True

        if state.current_drawdown_pct > 20:
            state.warnings.append(
                f"Drawdown={state.current_drawdown_pct:.1f}% (>20% warning)"
            )

        if state.warnings:
            for w in state.warnings:
                log.warning("[PERF MONITOR] %s", w)

        self._save_state(state)
        return state

    def run_diagnostics(self) -> dict:
        """Run 6-point diagnostic check when performance degrades.

        Returns:
            Dict with diagnostic results for each check.
        """
        log.info("[DIAGNOSTICS] Running autonomous debugging...")
        diag = {
            "timestamp": time.time(),
            "checks": {},
        }

        # Check 1: Model drift
        resolved = self._load_resolved_trades()
        diag["checks"]["model_drift"] = self._diag_model_drift(resolved)

        # Check 2: Regime shift
        diag["checks"]["regime_shift"] = self._diag_regime_shift(resolved)

        # Check 3: API inconsistencies
        diag["checks"]["api_health"] = self._diag_api_health()

        # Check 4: Liquidity changes
        diag["checks"]["liquidity"] = self._diag_liquidity(resolved)

        # Check 5: Execution latency
        diag["checks"]["execution"] = self._diag_execution()

        # Check 6: Indicator correlation breakdown
        diag["checks"]["indicator_health"] = self._diag_indicator_health()

        # Save diagnostics
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        DIAGNOSTICS_FILE.write_text(json.dumps(diag, indent=2))
        log.info("[DIAGNOSTICS] Complete — saved to %s", DIAGNOSTICS_FILE.name)

        return diag

    def _trigger_kill_switch(self, state: PerformanceState) -> None:
        """Activate kill switch — create emergency stop flag."""
        from bot.v2_tools import emergency_stop
        emergency_stop(reason=f"Kill switch: {state.kill_switch_reason}")

        # Run diagnostics automatically
        diag = self.run_diagnostics()

        # Send Telegram alert
        try:
            import os
            tg_token = os.environ.get("TG_BOT_TOKEN", "")
            tg_chat = os.environ.get("TG_CHAT_ID", "")
            if tg_token and tg_chat:
                import requests
                msg = (
                    f"*GARVES V2 — KILL SWITCH ACTIVATED*\n\n"
                    f"Reason: {state.kill_switch_reason}\n"
                    f"50-trade WR: {state.rolling_wr_50:.1%}\n"
                    f"30-trade WR: {state.rolling_wr_30:.1%}\n"
                    f"Drawdown: {state.current_drawdown_pct:.1f}%\n\n"
                    f"Diagnostics run. Trading halted.\n"
                    f"Clear emergency_stop to resume."
                )
                requests.post(
                    f"https://api.telegram.org/bot{tg_token}/sendMessage",
                    json={"chat_id": tg_chat, "text": msg, "parse_mode": "Markdown"},
                    timeout=10,
                )
        except Exception:
            pass

    @staticmethod
    def _load_resolved_trades() -> list[dict]:
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

    @staticmethod
    def _calculate_ev_capture() -> float | None:
        """Calculate average EV capture % from post-trade analysis."""
        if not ANALYSIS_FILE.exists():
            return None
        analyses = []
        try:
            for line in ANALYSIS_FILE.read_text().splitlines():
                if not line.strip():
                    continue
                a = json.loads(line)
                if a.get("ev_predicted", 0) > 0:
                    analyses.append(a)
        except Exception:
            return None

        if not analyses:
            return None

        recent = analyses[-30:]
        avg = sum(a.get("ev_capture_pct", 0) for a in recent) / len(recent)
        return avg

    @staticmethod
    def _calculate_model_drift(resolved: list[dict]) -> float:
        """Detect model drift by comparing recent vs all-time indicator accuracy.

        Returns 0-1 score where >0.15 = significant drift.
        """
        if len(resolved) < 30:
            return 0.0

        # Compare last 20 trades vs all trades accuracy
        all_correct = sum(1 for t in resolved if t.get("won"))
        all_wr = all_correct / len(resolved) if resolved else 0.5

        recent = resolved[-20:]
        recent_correct = sum(1 for t in recent if t.get("won"))
        recent_wr = recent_correct / len(recent) if recent else 0.5

        # Drift = how much recent WR differs from all-time
        drift = abs(all_wr - recent_wr)
        return drift

    @staticmethod
    def _calculate_drawdown(resolved: list[dict]) -> float:
        """Calculate current drawdown from peak equity."""
        if not resolved:
            return 0.0

        equity = 0.0
        peak = 0.0
        for t in resolved:
            pnl = t.get("pnl", 0.0)
            if pnl == 0:
                # Estimate PnL
                size = t.get("size_usd", 10.0)
                edge = t.get("edge", 0.08)
                pnl = size * edge if t.get("won") else -size * 0.5
            equity += pnl
            peak = max(peak, equity)

        if peak <= 0:
            return 0.0
        drawdown = (peak - equity) / peak * 100
        return max(0.0, drawdown)

    # ── Diagnostic Checks ──

    @staticmethod
    def _diag_model_drift(resolved: list[dict]) -> dict:
        """Check 1: Are indicators losing accuracy?"""
        if len(resolved) < 20:
            return {"status": "insufficient_data", "detail": f"{len(resolved)} trades"}

        recent = resolved[-20:]
        recent_wr = sum(1 for t in recent if t.get("won")) / len(recent)
        all_wr = sum(1 for t in resolved if t.get("won")) / len(resolved)
        drift = abs(all_wr - recent_wr)

        return {
            "status": "drift_detected" if drift > 0.10 else "normal",
            "all_time_wr": round(all_wr, 3),
            "recent_20_wr": round(recent_wr, 3),
            "drift": round(drift, 3),
        }

    @staticmethod
    def _diag_regime_shift(resolved: list[dict]) -> dict:
        """Check 2: Are we trading in unfamiliar regimes?"""
        if len(resolved) < 10:
            return {"status": "insufficient_data"}

        recent = resolved[-10:]
        regimes = [t.get("regime_label", "unknown") for t in recent]
        regime_counts = {}
        for r in regimes:
            regime_counts[r] = regime_counts.get(r, 0) + 1

        dominant = max(regime_counts, key=regime_counts.get) if regime_counts else "unknown"
        dominant_pct = regime_counts.get(dominant, 0) / len(recent) if recent else 0

        return {
            "status": "regime_shift" if dominant_pct < 0.5 else "stable",
            "recent_regimes": regime_counts,
            "dominant": dominant,
            "dominant_pct": round(dominant_pct, 2),
        }

    @staticmethod
    def _diag_api_health() -> dict:
        """Check 3: Are data feeds working?"""
        checks = {}

        # Check signal cycle freshness
        cycle_file = DATA_DIR / "signal_cycle_status.json"
        if cycle_file.exists():
            try:
                data = json.loads(cycle_file.read_text())
                age = time.time() - data.get("last_eval_at", 0)
                checks["signal_cycle_age_s"] = round(age)
                checks["signal_cycle_ok"] = age < 120
            except Exception:
                checks["signal_cycle_ok"] = False
        else:
            checks["signal_cycle_ok"] = False

        # Check binance feed
        binance_file = DATA_DIR / "binance_status.json"
        if binance_file.exists():
            try:
                data = json.loads(binance_file.read_text())
                silence = time.time() - data.get("last_message", 0)
                checks["binance_silence_s"] = round(silence)
                checks["binance_ok"] = silence < 60
            except Exception:
                checks["binance_ok"] = False

        return {
            "status": "healthy" if all(v for k, v in checks.items() if k.endswith("_ok")) else "degraded",
            "checks": checks,
        }

    @staticmethod
    def _diag_liquidity(resolved: list[dict]) -> dict:
        """Check 4: Are spreads widening?"""
        if not resolved:
            return {"status": "no_data"}

        recent = resolved[-20:]
        spreads = [t.get("ob_spread", 0) for t in recent if t.get("ob_spread")]
        if not spreads:
            return {"status": "no_spread_data"}

        avg_spread = sum(spreads) / len(spreads)
        recent_5 = spreads[-5:] if len(spreads) >= 5 else spreads
        recent_avg = sum(recent_5) / len(recent_5)

        return {
            "status": "widening" if recent_avg > avg_spread * 1.3 else "normal",
            "avg_spread_20": round(avg_spread, 4),
            "avg_spread_5": round(recent_avg, 4),
        }

    @staticmethod
    def _diag_execution() -> dict:
        """Check 5: Is execution latency increasing?"""
        exec_file = DATA_DIR / "execution_metrics.jsonl"
        if not exec_file.exists():
            return {"status": "no_data"}

        records = []
        try:
            for line in exec_file.read_text().splitlines()[-20:]:
                if line.strip():
                    records.append(json.loads(line))
        except Exception:
            return {"status": "parse_error"}

        if not records:
            return {"status": "empty"}

        fill_times = [r.get("fill_time_s", 0) for r in records if r.get("fill_time_s")]
        if fill_times:
            avg_fill = sum(fill_times) / len(fill_times)
            return {
                "status": "slow" if avg_fill > 30 else "normal",
                "avg_fill_time_s": round(avg_fill, 1),
                "samples": len(fill_times),
            }
        return {"status": "no_fill_data"}

    @staticmethod
    def _diag_indicator_health() -> dict:
        """Check 6: Are any indicators consistently wrong?"""
        if not INDICATOR_ACCURACY_FILE.exists():
            return {"status": "no_data"}

        try:
            acc_data = json.loads(INDICATOR_ACCURACY_FILE.read_text())
        except Exception:
            return {"status": "parse_error"}

        failing = []
        for name, data in acc_data.items():
            accuracy = data.get("accuracy", 0.5)
            total = data.get("total_votes", 0)
            if total >= 10 and accuracy < 0.45:
                failing.append({
                    "indicator": name,
                    "accuracy": round(accuracy, 3),
                    "total_votes": total,
                })

        return {
            "status": "indicators_failing" if failing else "healthy",
            "failing_indicators": failing,
        }

    def _load_state(self) -> PerformanceState:
        """Load last saved state."""
        if MONITOR_STATE_FILE.exists():
            try:
                data = json.loads(MONITOR_STATE_FILE.read_text())
                state = PerformanceState()
                for k, v in data.items():
                    if hasattr(state, k):
                        setattr(state, k, v)
                return state
            except Exception:
                pass
        return PerformanceState()

    @staticmethod
    def _save_state(state: PerformanceState) -> None:
        """Save state to disk."""
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        MONITOR_STATE_FILE.write_text(json.dumps(asdict(state), indent=2))
