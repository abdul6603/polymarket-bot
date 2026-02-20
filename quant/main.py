"""Quant — The Strategy Alchemist. Main loop."""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from quant.config import QuantConfig
from quant.data_loader import load_all_candles, load_all_trades, load_indicator_accuracy
from quant.backtester import replay_historical_trades, backtest_candle_indicators
from quant.optimizer import run_optimization, get_live_params
from quant.walk_forward import (
    walk_forward_validation, bootstrap_confidence_interval,
    optuna_full_optimization, HAS_OPTUNA,
)
from quant.reporter import (
    write_status, write_results, write_recommendations,
    write_hawk_review, publish_events,
)
from quant.analytics import compute_kelly, analyze_indicator_diversity, detect_strategy_decay
from quant.scorer import score_result
from quant.ml_predictor import retrain_model

log = logging.getLogger(__name__)

# Agent Brain — learning memory
_quant_brain = None
try:
    import sys as _sys
    _sys.path.insert(0, str(Path.home() / "shared"))
    from agent_brain import AgentBrain
    _quant_brain = AgentBrain("quant", system_prompt="You are Quant, a backtesting and strategy optimization agent.", task_type="analysis")
except Exception:
    pass

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


class QuantBot:
    """Main Quant agent loop."""

    def __init__(self, cfg: QuantConfig | None = None):
        self.cfg = cfg or QuantConfig()
        self.cycle = 0

    async def run(self):
        """Run backtesting cycles forever."""
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [QUANT] %(levelname)s %(message)s",
            datefmt="%H:%M:%S",
        )
        log.info("Quant — The Strategy Alchemist starting up")
        log.info("Config: cycle=%dm, max_combos=%d, min_trades=%d, optuna=%s",
                 self.cfg.cycle_minutes, self.cfg.max_combinations,
                 self.cfg.min_trades_for_significance, HAS_OPTUNA)

        while True:
            self.cycle += 1
            try:
                await self._run_cycle()
            except Exception:
                log.exception("Cycle %d failed", self.cycle)

            log.info("Sleeping %d minutes until next cycle...", self.cfg.cycle_minutes)
            await asyncio.sleep(self.cfg.cycle_minutes * 60)

    async def _run_cycle(self):
        """Single backtest cycle."""
        log.info("=== Cycle %d starting ===", self.cycle)

        # 1. Load data
        log.info("Loading historical data...")
        trades = load_all_trades()
        candles = load_all_candles()
        accuracy = load_indicator_accuracy()

        candle_counts = {asset: len(c) for asset, c in candles.items()}
        log.info("Data: %d trades, candles: %s", len(trades), candle_counts)

        if len(trades) < self.cfg.min_trades_for_significance:
            log.warning("Only %d trades — need %d for significance. Running baseline only.",
                        len(trades), self.cfg.min_trades_for_significance)

        # 2. Run optimization (Optuna if available, else grid)
        if HAS_OPTUNA:
            baseline, scored = optuna_full_optimization(
                trades=trades,
                n_trials=self.cfg.max_combinations,
                min_trades=self.cfg.min_trades_for_significance,
            )
        else:
            baseline, scored = run_optimization(
                trades=trades,
                max_combinations=self.cfg.max_combinations,
                min_trades=self.cfg.min_trades_for_significance,
            )

        # 3. Bootstrap CI on baseline
        baseline_ci = bootstrap_confidence_interval(baseline.wins, baseline.losses)
        log.info("Baseline CI: %.1f%% [%.1f%%, %.1f%%] ±%.1f%%",
                 baseline_ci.point_estimate, baseline_ci.ci_lower,
                 baseline_ci.ci_upper, baseline_ci.margin_of_error)

        # 4. Walk-forward validation (3 folds for small datasets, 5 for large)
        n_folds = 5 if len(trades) >= 100 else 3
        wf_result = walk_forward_validation(
            trades=trades,
            n_folds=n_folds,
            max_optuna_trials=50,
            min_trades_per_fold=10,
        )

        # 5. Load Hawk trades for calibration review
        hawk_trades = self._load_hawk_trades()

        # 6. Write all reports
        write_status(self.cycle, baseline, len(scored), len(trades), candle_counts)
        write_results(baseline, scored)
        write_recommendations(baseline, scored)
        _write_walk_forward(wf_result, baseline_ci)
        _write_analytics(trades, baseline)
        if self.cfg.hawk_review:
            write_hawk_review(hawk_trades)

        # 7. ML Model retrain (XGBoost on resolved trades)
        try:
            ml_metrics = retrain_model()
            if ml_metrics.get("status") == "trained":
                log.info("ML model retrained: acc=%.3f, f1=%.3f, samples=%d",
                         ml_metrics["accuracy"], ml_metrics["f1"], ml_metrics["num_samples"])
            else:
                log.info("ML model: %s (%d/%d samples)",
                         ml_metrics.get("status"), ml_metrics.get("num_samples", 0),
                         ml_metrics.get("min_required", 30))
        except Exception:
            log.exception("ML model retrain failed (non-fatal)")

        # 8. Publish to event bus
        publish_events(baseline, scored)

        # 8. Log summary
        best_wr = scored[0][1].win_rate if scored and scored[0][1].total_signals >= 20 else 0
        log.info("=== Cycle %d complete ===", self.cycle)
        log.info("Baseline: WR=%.1f%% (%d signals) CI=[%.1f%%, %.1f%%]",
                 baseline.win_rate, baseline.total_signals,
                 baseline_ci.ci_lower, baseline_ci.ci_upper)
        log.info("Best found: WR=%.1f%% | Walk-forward OOS: %.1f%% (overfit=%.1fpp)",
                 best_wr, wf_result.test_win_rate, wf_result.overfit_drop)

        # Brain: record backtest findings + outcome
        if _quant_brain:
            try:
                _did = _quant_brain.remember_decision(
                    context=f"Backtest cycle {self.cycle}: tested {len(scored)} parameter combinations on {len(trades)} trades",
                    decision=f"Baseline WR={baseline.win_rate:.1f}%, best WR={best_wr:.1f}%, walk-forward OOS={wf_result.test_win_rate:.1f}%",
                    confidence=0.5,
                    tags=["backtest"],
                )
                # Record outcome — did we find improvement over baseline?
                _improvement = best_wr - baseline.win_rate
                _score = min(1.0, _improvement / 10.0) if _improvement > 0 else -0.5
                _quant_brain.remember_outcome(
                    _did, f"Improvement={_improvement:+.1f}pp, OOS={wf_result.test_win_rate:.1f}%, overfit={wf_result.overfit_drop:.1f}pp",
                    score=_score,
                )
                if _improvement > 2.0 and wf_result.overfit_drop < 5.0:
                    _quant_brain.learn_pattern(
                        "strong_backtest", f"Found +{_improvement:.1f}pp improvement with low overfit ({wf_result.overfit_drop:.1f}pp)",
                        evidence_count=1, confidence=0.65,
                    )
            except Exception:
                pass

    def _load_hawk_trades(self) -> list[dict]:
        """Load Hawk trades for calibration review."""
        hawk_file = DATA_DIR / "hawk_trades.jsonl"
        if not hawk_file.exists():
            return []
        trades = []
        try:
            with open(hawk_file) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        trades.append(json.loads(line))
        except Exception:
            pass
        return trades


def _write_analytics(trades: list[dict], baseline: BacktestResult):
    """Write Kelly, diversity, and decay analysis to JSON."""
    from quant.reporter import _now_et

    kelly = compute_kelly(baseline.wins, baseline.losses, baseline.avg_edge)
    diversity = analyze_indicator_diversity(trades)
    decay = detect_strategy_decay(trades)

    output = {
        "kelly": {
            "win_rate": kelly.win_rate,
            "avg_win_return": kelly.avg_win_return,
            "full_kelly_pct": kelly.full_kelly,
            "half_kelly_pct": kelly.half_kelly,
            "quarter_kelly_pct": kelly.quarter_kelly,
            "recommended_usd": kelly.recommended_usd,
            "current_size_usd": kelly.current_size_usd,
            "bankroll": kelly.bankroll,
        },
        "diversity": {
            "n_indicators": diversity.n_indicators,
            "avg_agreement": diversity.avg_pairwise_agreement,
            "diversity_score": diversity.diversity_score,
            "redundant_pairs": diversity.redundant_pairs[:10],
            "independent_indicators": diversity.independent_indicators,
        },
        "decay": {
            "is_decaying": decay.is_decaying,
            "trend_direction": decay.trend_direction,
            "current_rolling_wr": decay.current_rolling_wr,
            "peak_rolling_wr": decay.peak_rolling_wr,
            "decay_amount": decay.decay_amount,
            "rolling_window": decay.rolling_window,
            "alert_message": decay.alert_message,
            "rolling_history": decay.rolling_history[-30:],  # last 30 points
        },
        "updated": _now_et(),
    }
    DATA_DIR.mkdir(exist_ok=True)
    (DATA_DIR / "quant_analytics.json").write_text(json.dumps(output, indent=2))
    log.info("Wrote quant_analytics.json (Kelly=$%.2f, diversity=%.0f, decay=%s)",
             kelly.recommended_usd, diversity.diversity_score, decay.trend_direction)


def _write_walk_forward(wf: walk_forward_validation.__class__, ci: bootstrap_confidence_interval.__class__):
    """Write walk-forward + CI results to JSON."""
    from quant.walk_forward import WalkForwardResult, BootstrapCI
    from quant.reporter import _now_et

    output = {
        "walk_forward": {
            "train_win_rate": wf.train_win_rate,
            "test_win_rate": wf.test_win_rate,
            "overfit_drop": wf.overfit_drop,
            "n_folds": wf.n_folds,
            "fold_results": wf.fold_results,
            "elapsed_seconds": round(wf.elapsed_seconds, 2),
        },
        "confidence_interval": {
            "point_estimate": ci.point_estimate,
            "ci_lower": ci.ci_lower,
            "ci_upper": ci.ci_upper,
            "margin_of_error": ci.margin_of_error,
            "ci_level": ci.ci_level,
            "n_trades": ci.n_trades,
        },
        "updated": _now_et(),
    }
    DATA_DIR.mkdir(exist_ok=True)
    (DATA_DIR / "quant_walk_forward.json").write_text(json.dumps(output, indent=2))
    log.info("Wrote quant_walk_forward.json")


def run_single_backtest(progress_callback=None) -> dict:
    """Run a single backtest cycle (called from dashboard API).

    Returns summary dict for the API response.
    """
    trades = load_all_trades()
    candles = load_all_candles()

    # Use Optuna if available, else grid
    if HAS_OPTUNA:
        baseline, scored = optuna_full_optimization(
            trades=trades,
            n_trials=200,
            min_trades=20,
            progress_callback=progress_callback,
        )
    else:
        baseline, scored = run_optimization(
            trades=trades,
            max_combinations=500,
            min_trades=20,
            progress_callback=progress_callback,
        )

    # Bootstrap CI
    baseline_ci = bootstrap_confidence_interval(baseline.wins, baseline.losses)

    # Walk-forward (quick: 3 folds, 50 trials)
    n_folds = 5 if len(trades) >= 100 else 3
    wf_result = walk_forward_validation(
        trades=trades,
        n_folds=n_folds,
        max_optuna_trials=50,
        min_trades_per_fold=10,
    )

    # Write reports
    candle_counts = {asset: len(c) for asset, c in candles.items()}
    write_status(0, baseline, len(scored), len(trades), candle_counts)
    write_results(baseline, scored)
    write_recommendations(baseline, scored)
    _write_walk_forward(wf_result, baseline_ci)
    _write_analytics(trades, baseline)

    # Hawk review
    hawk_file = DATA_DIR / "hawk_trades.jsonl"
    hawk_trades = []
    if hawk_file.exists():
        try:
            with open(hawk_file) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        hawk_trades.append(json.loads(line))
        except Exception:
            pass
    write_hawk_review(hawk_trades)

    publish_events(baseline, scored)

    best = scored[0][1] if scored and scored[0][1].total_signals >= 20 else baseline
    return {
        "baseline_wr": round(baseline.win_rate, 1),
        "best_wr": round(best.win_rate, 1),
        "combos_tested": len(scored),
        "trades_used": len(trades),
        "baseline_ci": {
            "lower": baseline_ci.ci_lower,
            "upper": baseline_ci.ci_upper,
            "margin": baseline_ci.margin_of_error,
        },
        "walk_forward": {
            "train_wr": wf_result.train_win_rate,
            "test_wr": wf_result.test_win_rate,
            "overfit_drop": wf_result.overfit_drop,
        },
        "optimizer": "optuna" if HAS_OPTUNA else "grid",
    }
