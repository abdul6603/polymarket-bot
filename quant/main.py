"""Quant — The Strategy Alchemist. Main loop."""
from __future__ import annotations

import asyncio
import json
import logging
import time
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
    write_hawk_review, publish_events, write_live_params,
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
        self._trades_studied_since_opt = 0  # reset after each mini-opt
        self._total_trades_studied = 0
        self._mini_opts_run = 0

    async def run(self):
        """Run backtesting cycles forever, polling event bus between cycles."""
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [QUANT] %(levelname)s %(message)s",
            datefmt="%H:%M:%S",
        )
        log.info("Quant — The Strategy Alchemist starting up")
        log.info("Config: cycle=%dm, max_combos=%d, min_trades=%d, optuna=%s, poll=%ds, mini_opt_after=%d",
                 self.cfg.cycle_minutes, self.cfg.max_combinations,
                 self.cfg.min_trades_for_significance, HAS_OPTUNA,
                 self.cfg.event_poll_interval, self.cfg.mini_opt_threshold)

        while True:
            self.cycle += 1
            try:
                await self._run_cycle()
            except Exception:
                log.exception("Cycle %d failed", self.cycle)

            # Between full cycles, poll the event bus for resolved trades
            log.info("Entering event poll loop for %d minutes (poll every %ds)...",
                     self.cfg.cycle_minutes, self.cfg.event_poll_interval)
            cycle_end = time.time() + self.cfg.cycle_minutes * 60
            while time.time() < cycle_end:
                try:
                    await self._poll_trade_events()
                except Exception:
                    log.exception("Event poll failed")
                await asyncio.sleep(self.cfg.event_poll_interval)

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

        # 6b. Auto-apply validated params to Garves's live config
        if scored and scored[0][1].total_signals >= 20:
            best_result = scored[0][1]
            applied = write_live_params(
                baseline=baseline,
                best=best_result,
                wf_test_wr=wf_result.test_win_rate,
                wf_overfit_drop=wf_result.overfit_drop,
            )
            if applied:
                log.info("AUTO-APPLIED: Quant params pushed to Garves live config")

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

    # ── Per-Trade Learning (event-driven) ──

    async def _poll_trade_events(self):
        """Poll event bus for trade_resolved events and study each one."""
        try:
            import sys
            sys.path.insert(0, str(Path.home() / "shared"))
            from shared.events import get_unread
        except ImportError:
            return

        events = get_unread("quant")
        trade_events = [e for e in events if e.get("type") == "trade_resolved"]

        for evt in trade_events:
            data = evt.get("data", {})
            if not data.get("trade_id"):
                continue
            # Skip unknown outcomes
            if data.get("actual_result") not in ("up", "down"):
                continue
            try:
                self._study_single_trade(data)
            except Exception:
                log.exception("Failed to study trade %s", data.get("trade_id"))

        # Check if mini-opt threshold reached
        if self._trades_studied_since_opt >= self.cfg.mini_opt_threshold:
            try:
                self._run_mini_optimization()
            except Exception:
                log.exception("Mini-optimization failed")

    def _study_single_trade(self, trade_data: dict):
        """Analyze a single resolved trade against current live params."""
        trade_id = trade_data.get("trade_id", "unknown")
        indicator_votes = trade_data.get("indicator_votes", {})
        won = trade_data.get("won", False)
        direction = trade_data.get("direction", "")
        actual = trade_data.get("actual_result", "")

        # Analyze which indicators were right/wrong
        correct_indicators = []
        wrong_indicators = []
        for name, vote in indicator_votes.items():
            if isinstance(vote, dict):
                ind_dir = vote.get("direction", "")
            else:
                ind_dir = str(vote)
            if ind_dir == actual:
                correct_indicators.append(name)
            elif ind_dir in ("up", "down"):
                wrong_indicators.append(name)

        total_voting = len(correct_indicators) + len(wrong_indicators)
        indicator_accuracy = len(correct_indicators) / total_voting if total_voting else 0.0

        # Check if current live params would have passed/blocked this trade
        try:
            live_params = get_live_params()
            from quant.backtester import replay_historical_trades
            # Replay just this one trade with live params
            result = replay_historical_trades([trade_data], live_params)
            live_would_pass = result.total_signals > 0
        except Exception:
            live_would_pass = True  # assume it passed since it was executed

        study = {
            "trade_id": trade_id,
            "studied_at": time.time(),
            "asset": trade_data.get("asset", ""),
            "timeframe": trade_data.get("timeframe", ""),
            "direction": direction,
            "actual_result": actual,
            "won": won,
            "pnl": trade_data.get("pnl", 0.0),
            "edge": trade_data.get("edge", 0.0),
            "confidence": trade_data.get("confidence", 0.0),
            "regime_label": trade_data.get("regime_label", ""),
            "indicator_accuracy": round(indicator_accuracy, 3),
            "correct_indicators": correct_indicators,
            "wrong_indicators": wrong_indicators,
            "live_params_would_pass": live_would_pass,
            "correctly_filtered": (not live_would_pass and not won) or (live_would_pass and won),
        }

        # Write to studies JSONL
        studies_file = DATA_DIR / "quant_trade_studies.jsonl"
        DATA_DIR.mkdir(exist_ok=True)
        try:
            with open(studies_file, "a") as f:
                f.write(json.dumps(study) + "\n")
        except Exception:
            log.exception("Failed to write trade study")

        self._trades_studied_since_opt += 1
        self._total_trades_studied += 1

        log.info("Studied trade %s: %s %s/%s %s | ind_acc=%.0f%% | filter_correct=%s",
                 trade_id, "WIN" if won else "LOSS",
                 study["asset"].upper(), study["timeframe"],
                 direction.upper(), indicator_accuracy * 100,
                 study["correctly_filtered"])

    def _run_mini_optimization(self):
        """Lightweight Optuna run on recent trades after threshold reached."""
        log.info("=== Mini-optimization triggered (%d trades since last) ===",
                 self._trades_studied_since_opt)

        trades = load_all_trades()
        # Use last 50 trades for mini-opt (recent performance focus)
        recent = trades[-50:] if len(trades) > 50 else trades

        if len(recent) < 10:
            log.warning("Mini-opt: only %d recent trades, skipping", len(recent))
            return

        # Quick 50-trial Optuna run
        if HAS_OPTUNA:
            baseline, scored = optuna_full_optimization(
                trades=recent, n_trials=50, min_trades=5,
            )
        else:
            baseline, scored = run_optimization(
                trades=recent, max_combinations=50, min_trades=5,
            )

        self._mini_opts_run += 1
        self._trades_studied_since_opt = 0

        best_wr = scored[0][1].win_rate if scored else baseline.win_rate
        improvement = best_wr - baseline.win_rate

        result = {
            "mini_opt_number": self._mini_opts_run,
            "timestamp": time.time(),
            "trades_used": len(recent),
            "baseline_wr": round(baseline.win_rate, 1),
            "best_wr": round(best_wr, 1),
            "improvement_pp": round(improvement, 1),
            "combos_tested": len(scored),
            "best_params": scored[0][1].params if scored else {},
        }

        # Write mini-opt result
        mini_opt_file = DATA_DIR / "quant_mini_opt.json"
        try:
            mini_opt_file.write_text(json.dumps(result, indent=2))
        except Exception:
            log.exception("Failed to write mini-opt results")

        log.info("Mini-opt #%d complete: baseline=%.1f%%, best=%.1f%% (+%.1fpp), %d combos on %d trades",
                 self._mini_opts_run, baseline.win_rate, best_wr, improvement,
                 len(scored), len(recent))

        # Publish event
        try:
            import sys
            sys.path.insert(0, str(Path.home() / "shared"))
            from shared.events import publish
            publish(
                agent="quant",
                event_type="mini_optimization_complete",
                severity="info",
                summary=f"Mini-opt #{self._mini_opts_run}: {baseline.win_rate:.1f}% → {best_wr:.1f}% ({improvement:+.1f}pp) on {len(recent)} trades",
                data=result,
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

    # Auto-apply validated params
    params_applied = False
    best = scored[0][1] if scored and scored[0][1].total_signals >= 20 else baseline
    if best is not baseline:
        params_applied = write_live_params(
            baseline=baseline,
            best=best,
            wf_test_wr=wf_result.test_win_rate,
            wf_overfit_drop=wf_result.overfit_drop,
        )

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
        "params_auto_applied": params_applied,
    }
