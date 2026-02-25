"""Conviction Calibrator — learn which components predict wins.

Principle: Automate discipline. Measures correlation between each
conviction component and actual trade outcomes, then suggests
weight adjustments. Max single change: +/-3 per cycle.
"""
from __future__ import annotations

import json
import logging
import statistics
from pathlib import Path

log = logging.getLogger("odin.analytics.conviction_calibrator")


class ConvictionCalibrator:
    """Measures which conviction components actually predict outcomes."""

    def __init__(self, data_dir: Path | None = None):
        self._data_dir = data_dir or Path(__file__).parent.parent / "data"
        self._trades_file = self._data_dir / "odin_trades.jsonl"
        self._calibration_file = self._data_dir / "conviction_calibration.json"

    def calibrate(self, trades: list[dict] | None = None) -> dict:
        """For each component: correlation between score and win/loss.

        Returns dict of {component_name: {correlation, avg_win_score, avg_loss_score, predictive}}.
        """
        if trades is None:
            trades = self._load_trades()

        if len(trades) < 10:
            return {"insufficient_data": True, "sample_size": len(trades)}

        # Extract component scores from trades with conviction breakdown
        component_results: dict[str, list[tuple[float, bool]]] = {}

        for t in trades:
            is_win = t.get("pnl_usd", 0) > 0
            breakdown = t.get("conviction_breakdown", {})
            if not breakdown:
                continue

            for comp_name, comp_data in breakdown.items():
                raw = comp_data.get("raw", 0) if isinstance(comp_data, dict) else 0
                component_results.setdefault(comp_name, []).append((raw, is_win))

        results = {}
        for comp, data in component_results.items():
            if len(data) < 5:
                continue

            win_scores = [s for s, w in data if w]
            loss_scores = [s for s, w in data if not w]

            avg_win = statistics.mean(win_scores) if win_scores else 0
            avg_loss = statistics.mean(loss_scores) if loss_scores else 0
            spread = avg_win - avg_loss

            # Predictive = win scores consistently higher than loss scores
            predictive = spread > 0.05 and len(win_scores) >= 3 and len(loss_scores) >= 3

            results[comp] = {
                "avg_win_score": round(avg_win, 3),
                "avg_loss_score": round(avg_loss, 3),
                "spread": round(spread, 3),
                "predictive": predictive,
                "sample_size": len(data),
            }

        log.info(
            "[CALIBRATE] Analyzed %d trades, %d components: %d predictive",
            len(trades), len(results),
            sum(1 for r in results.values() if r["predictive"]),
        )

        return {
            "components": results,
            "sample_size": len(trades),
            "analyzed_components": len(results),
        }

    def suggest_weights(self, current_weights: dict, trades: list[dict] | None = None) -> dict | None:
        """Suggest new weights based on predictiveness.

        Returns None if < 30 trades. Max single change: +/-3 per cycle.
        Weights always sum to 100.
        """
        if trades is None:
            trades = self._load_trades()

        if len(trades) < 30:
            log.info("[CALIBRATE] Need 30+ trades for weight suggestion (have %d)", len(trades))
            return None

        calibration = self.calibrate(trades)
        if calibration.get("insufficient_data"):
            return None

        components = calibration.get("components", {})
        if not components:
            return None

        new_weights = dict(current_weights)
        changes = []

        for comp_name, comp_data in components.items():
            if comp_name not in new_weights:
                continue

            current_w = new_weights[comp_name]
            spread = comp_data["spread"]

            # Highly predictive → increase weight (max +3)
            if comp_data["predictive"] and spread > 0.10:
                delta = min(3, max(1, int(spread * 10)))
                new_weights[comp_name] = current_w + delta
                changes.append(f"{comp_name}: +{delta}")
            # Anti-predictive → decrease weight (max -3)
            elif not comp_data["predictive"] and spread < -0.05:
                delta = min(3, max(1, int(abs(spread) * 10)))
                new_weights[comp_name] = max(2, current_w - delta)  # Floor at 2
                changes.append(f"{comp_name}: -{delta}")

        # Normalize to sum=100
        total = sum(new_weights.values())
        if total != 100 and total > 0:
            scale = 100 / total
            new_weights = {k: round(v * scale, 1) for k, v in new_weights.items()}
            # Fix rounding: adjust largest weight
            diff = 100 - sum(new_weights.values())
            if diff != 0:
                largest = max(new_weights, key=new_weights.get)
                new_weights[largest] = round(new_weights[largest] + diff, 1)

        # Ensure integer weights
        new_weights = {k: int(round(v)) for k, v in new_weights.items()}
        # Final sum fix
        diff = 100 - sum(new_weights.values())
        if diff != 0:
            largest = max(new_weights, key=new_weights.get)
            new_weights[largest] += diff

        if changes:
            log.info("[CALIBRATE] Suggested weight changes: %s", ", ".join(changes))
            self._save_calibration(new_weights, changes)

        return new_weights

    def _load_trades(self, limit: int = 100) -> list[dict]:
        """Load trades from JSONL."""
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

    def _save_calibration(self, weights: dict, changes: list[str]) -> None:
        """Persist calibration results."""
        import time
        data = {
            "weights": weights,
            "changes": changes,
            "calibrated_at": time.time(),
        }
        try:
            self._calibration_file.write_text(json.dumps(data, indent=2))
        except Exception:
            pass
