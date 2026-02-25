"""Self-diagnostics — detect data quality issues, conviction drift, execution degradation.

Principle: Automate discipline. Runs every 30 min, reports GREEN/YELLOW/RED.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

log = logging.getLogger("odin.debug.health_monitor")


class HealthMonitor:
    """Self-diagnostics — runs periodically to detect issues."""

    def __init__(self, data_dir: Path | None = None):
        self._data_dir = data_dir or Path(__file__).parent.parent / "data"
        self._status_file = self._data_dir / "odin_status.json"
        self._trades_file = self._data_dir / "odin_trades.jsonl"
        self._health_file = self._data_dir / "health_report.json"

    def check_data_quality(self) -> list[str]:
        """Check for stale candles, missing CoinGlass, API errors."""
        issues = []

        # Check status freshness
        if self._status_file.exists():
            try:
                status = json.loads(self._status_file.read_text())
                age = time.time() - status.get("last_updated", 0)
                if age > 600:  # >10 min stale
                    issues.append(f"Status file stale ({age:.0f}s old)")

                # CoinGlass data freshness
                cg = status.get("regime", {})
                cg_age = time.time() - cg.get("last_scan", 0) if cg.get("last_scan") else 0
                if cg_age > 600:
                    issues.append(f"CoinGlass data stale ({cg_age:.0f}s)")

                # WebSocket status
                ws = status.get("ws_status", {})
                if ws.get("connected") is False:
                    issues.append("WebSocket disconnected")

            except (json.JSONDecodeError, Exception) as e:
                issues.append(f"Status file corrupt: {str(e)[:50]}")
        else:
            issues.append("No status file found")

        return issues

    def check_conviction_drift(self, recent_trades: list[dict] | None = None) -> dict:
        """Is HIGH tier winning more than LOW? If not → drift alert."""
        trades = recent_trades or self._load_recent_trades(30)
        if len(trades) < 10:
            return {"status": "insufficient_data", "trades": len(trades)}

        # Group by conviction tier
        tiers: dict[str, list[bool]] = {}
        for t in trades:
            tier = t.get("conviction_tier", "")
            if not tier:
                score = t.get("conviction_score", 0)
                if score >= 70:
                    tier = "HIGH"
                elif score >= 40:
                    tier = "MODERATE"
                else:
                    tier = "LOW"
            tiers.setdefault(tier, []).append(t.get("pnl_usd", 0) > 0)

        tier_wr = {}
        for tier, outcomes in tiers.items():
            wins = sum(outcomes)
            total = len(outcomes)
            tier_wr[tier] = round(wins / total * 100, 1) if total > 0 else 0

        # Drift detection: HIGH should outperform LOW
        high_wr = tier_wr.get("HIGH", tier_wr.get("FULL", 0))
        low_wr = tier_wr.get("LOW", tier_wr.get("MODERATE", 0))

        if high_wr > 0 and low_wr > 0 and low_wr >= high_wr:
            drift = True
            log.warning(
                "[HEALTH] Conviction drift detected: HIGH WR=%.0f%% <= LOW WR=%.0f%%",
                high_wr, low_wr,
            )
        else:
            drift = False

        return {
            "drift_detected": drift,
            "tier_win_rates": tier_wr,
            "sample_size": len(trades),
        }

    def check_execution_quality(self, slippage_stats: dict | None = None) -> dict:
        """Check slippage trends and fill rates."""
        if not slippage_stats:
            return {"status": "no_data"}

        issues = []
        for symbol, stats in slippage_stats.items():
            avg_slip = abs(stats.get("avg_slippage_pct", 0))
            if avg_slip > 0.1:
                issues.append(f"{symbol}: high avg slippage {avg_slip:.3f}%")
            if stats.get("avg_latency_ms", 0) > 5000:
                issues.append(f"{symbol}: high latency {stats['avg_latency_ms']:.0f}ms")

        return {
            "issues": issues,
            "symbols_tracked": len(slippage_stats),
            "status": "degraded" if issues else "healthy",
        }

    def run_diagnostic(
        self,
        recent_trades: list[dict] | None = None,
        slippage_stats: dict | None = None,
    ) -> dict:
        """Full diagnostic report: GREEN/YELLOW/RED."""
        data_issues = self.check_data_quality()
        conviction = self.check_conviction_drift(recent_trades)
        execution = self.check_execution_quality(slippage_stats)

        # Determine overall health
        red_flags = len(data_issues)
        if conviction.get("drift_detected"):
            red_flags += 1
        if execution.get("status") == "degraded":
            red_flags += len(execution.get("issues", []))

        if red_flags >= 3:
            overall = "RED"
        elif red_flags >= 1:
            overall = "YELLOW"
        else:
            overall = "GREEN"

        recommendations = []
        if data_issues:
            recommendations.append("Check data feeds — stale or missing data detected")
        if conviction.get("drift_detected"):
            recommendations.append("Review conviction weights — high-conviction trades underperforming")
        if execution.get("status") == "degraded":
            recommendations.append("Check execution — slippage or latency elevated")

        report = {
            "overall": overall,
            "timestamp": time.time(),
            "data_quality": {"issues": data_issues, "ok": not data_issues},
            "conviction_drift": conviction,
            "execution_quality": execution,
            "recommendations": recommendations,
            "red_flags": red_flags,
        }

        # Persist report
        try:
            self._health_file.write_text(json.dumps(report, indent=2))
        except Exception:
            pass

        level = {"GREEN": "info", "YELLOW": "warning", "RED": "error"}.get(overall, "info")
        getattr(log, level)(
            "[HEALTH] %s — %d flags | data=%s conviction=%s execution=%s",
            overall, red_flags,
            "OK" if not data_issues else f"{len(data_issues)} issues",
            "drift" if conviction.get("drift_detected") else "OK",
            execution.get("status", "unknown"),
        )

        return report

    def _load_recent_trades(self, limit: int = 30) -> list[dict]:
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
