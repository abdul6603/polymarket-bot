"""Walk-Forward Optimization + Bootstrap Confidence Intervals + Optuna Bayesian Search.

Prevents overfitting by training on a rolling window and testing on held-out data.
Bootstrap CI gives statistical confidence in results.
Optuna replaces grid search for 3-10x more efficient parameter search.
"""
from __future__ import annotations

import logging
import math
import random
import time
from dataclasses import dataclass, field

import numpy as np

try:
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    HAS_OPTUNA = True
except ImportError:
    HAS_OPTUNA = False

from bot.signals import WEIGHTS, TF_WEIGHT_SCALE, PROB_CLAMP
from bot.signals import MIN_CONSENSUS, MIN_CONFIDENCE, UP_CONFIDENCE_PREMIUM
from bot.signals import MIN_EDGE_ABSOLUTE, MIN_EDGE_BY_TF, ASSET_EDGE_PREMIUM

from quant.backtester import BacktestParams, BacktestResult, replay_historical_trades
from quant.scorer import score_result

log = logging.getLogger(__name__)


@dataclass
class WalkForwardResult:
    """Result from walk-forward validation."""
    # In-sample (training) performance
    train_win_rate: float = 0.0
    train_signals: int = 0
    train_score: float = 0.0
    # Out-of-sample (test) performance
    test_win_rate: float = 0.0
    test_signals: int = 0
    test_score: float = 0.0
    # Overfitting metric: how much does WR drop from train → test
    overfit_drop: float = 0.0
    # Number of folds
    n_folds: int = 0
    # Per-fold results
    fold_results: list[dict] = field(default_factory=list)
    # Best params found across folds
    best_params: dict = field(default_factory=dict)
    best_label: str = ""
    elapsed_seconds: float = 0.0


@dataclass
class BootstrapCI:
    """Bootstrap confidence interval for a win rate."""
    point_estimate: float = 0.0
    ci_lower: float = 0.0
    ci_upper: float = 0.0
    ci_level: float = 0.95
    n_bootstrap: int = 1000
    n_trades: int = 0
    margin_of_error: float = 0.0


def bootstrap_confidence_interval(
    wins: int,
    losses: int,
    n_bootstrap: int = 1000,
    ci_level: float = 0.95,
    seed: int = 42,
) -> BootstrapCI:
    """Compute bootstrap confidence interval for win rate.

    Resamples the trade outcomes with replacement, computes WR each time,
    then takes percentiles for CI bounds.
    """
    total = wins + losses
    if total == 0:
        return BootstrapCI()

    rng = np.random.RandomState(seed)
    outcomes = np.array([1] * wins + [0] * losses)

    bootstrap_wrs = []
    for _ in range(n_bootstrap):
        sample = rng.choice(outcomes, size=total, replace=True)
        bootstrap_wrs.append(sample.mean() * 100)

    bootstrap_wrs = np.array(bootstrap_wrs)
    alpha = (1 - ci_level) / 2
    lo = float(np.percentile(bootstrap_wrs, alpha * 100))
    hi = float(np.percentile(bootstrap_wrs, (1 - alpha) * 100))
    point = wins / total * 100

    return BootstrapCI(
        point_estimate=round(point, 1),
        ci_lower=round(lo, 1),
        ci_upper=round(hi, 1),
        ci_level=ci_level,
        n_bootstrap=n_bootstrap,
        n_trades=total,
        margin_of_error=round((hi - lo) / 2, 1),
    )


def walk_forward_validation(
    trades: list[dict],
    n_folds: int = 5,
    max_optuna_trials: int = 100,
    min_trades_per_fold: int = 10,
    progress_callback=None,
) -> WalkForwardResult:
    """Walk-forward optimization with time-ordered splits.

    Sorts trades by timestamp, splits into n_folds.
    For each fold k:
      - Train on folds 0..k-1 (optimize params with Optuna)
      - Test on fold k (evaluate held-out performance)
    Reports average OOS win rate.
    """
    t0 = time.time()
    result = WalkForwardResult(n_folds=n_folds)

    # Sort trades by timestamp
    sorted_trades = sorted(trades, key=lambda t: t.get("timestamp", 0))

    # Split into folds
    fold_size = len(sorted_trades) // n_folds
    if fold_size < min_trades_per_fold:
        log.warning("Not enough trades for %d folds (%d per fold, need %d). Reducing folds.",
                     n_folds, fold_size, min_trades_per_fold)
        n_folds = max(2, len(sorted_trades) // min_trades_per_fold)
        fold_size = len(sorted_trades) // n_folds
        result.n_folds = n_folds

    folds = []
    for i in range(n_folds):
        start = i * fold_size
        end = start + fold_size if i < n_folds - 1 else len(sorted_trades)
        folds.append(sorted_trades[start:end])

    log.info("Walk-forward: %d folds, %d trades each (±)", n_folds, fold_size)

    train_wrs = []
    test_wrs = []
    train_scores = []
    test_scores = []
    all_fold_results = []

    for k in range(1, n_folds):
        # Train set: folds 0..k-1
        train_data = []
        for j in range(k):
            train_data.extend(folds[j])
        test_data = folds[k]

        if len(train_data) < min_trades_per_fold or len(test_data) < min_trades_per_fold:
            continue

        if progress_callback:
            pct = int(10 + 80 * (k / (n_folds - 1)))
            progress_callback("Walk-forward", f"Fold {k}/{n_folds-1}", pct)

        # Optimize on training data
        best_params = _optimize_fold(train_data, max_trials=max_optuna_trials)

        # Evaluate on training data (in-sample)
        train_result = replay_historical_trades(train_data, best_params)
        train_score = score_result(train_result, min_trades=5)

        # Evaluate on test data (out-of-sample)
        test_result = replay_historical_trades(test_data, best_params)
        test_score = score_result(test_result, min_trades=5)

        train_wr = train_result.win_rate if train_result.total_signals > 0 else 0
        test_wr = test_result.win_rate if test_result.total_signals > 0 else 0

        train_wrs.append(train_wr)
        test_wrs.append(test_wr)
        train_scores.append(train_score)
        test_scores.append(test_score)

        fold_info = {
            "fold": k,
            "train_size": len(train_data),
            "test_size": len(test_data),
            "train_wr": round(train_wr, 1),
            "test_wr": round(test_wr, 1),
            "train_signals": train_result.total_signals,
            "test_signals": test_result.total_signals,
            "train_score": round(train_score, 1),
            "test_score": round(test_score, 1),
            "best_params_label": best_params.label,
        }
        all_fold_results.append(fold_info)
        log.info("Fold %d: train WR=%.1f%% (%d sig) | test WR=%.1f%% (%d sig)",
                 k, train_wr, train_result.total_signals, test_wr, test_result.total_signals)

    # Aggregate
    if train_wrs:
        result.train_win_rate = round(sum(train_wrs) / len(train_wrs), 1)
        result.test_win_rate = round(sum(test_wrs) / len(test_wrs), 1)
        result.train_score = round(sum(train_scores) / len(train_scores), 1)
        result.test_score = round(sum(test_scores) / len(test_scores), 1)
        result.train_signals = sum(1 for _ in train_wrs)
        result.test_signals = sum(1 for _ in test_wrs)
        result.overfit_drop = round(result.train_win_rate - result.test_win_rate, 1)

    result.fold_results = all_fold_results
    result.elapsed_seconds = time.time() - t0

    log.info("Walk-forward complete: train avg WR=%.1f%%, test avg WR=%.1f%%, overfit=%.1fpp",
             result.train_win_rate, result.test_win_rate, result.overfit_drop)

    return result


def _optimize_fold(
    train_data: list[dict],
    max_trials: int = 100,
) -> BacktestParams:
    """Optimize parameters on a training set using Optuna (or grid fallback)."""
    if HAS_OPTUNA:
        return _optuna_optimize(train_data, max_trials)
    else:
        return _grid_optimize(train_data, max_trials)


def _optuna_optimize(
    train_data: list[dict],
    max_trials: int = 100,
) -> BacktestParams:
    """Use Optuna Bayesian search to find best params on training data."""
    live_weights = dict(WEIGHTS)
    live_tf_scale = {tf: dict(s) for tf, s in TF_WEIGHT_SCALE.items()}
    live_prob_clamp = dict(PROB_CLAMP)
    live_asset_premiums = dict(ASSET_EDGE_PREMIUM)

    # Uncertain indicators to optimize weights for
    uncertain = ["heikin_ashi", "order_flow", "momentum", "ema", "macd",
                 "spot_depth", "news", "volume_spike"]

    best_score = -1.0
    best_params = None

    def objective(trial: optuna.Trial) -> float:
        nonlocal best_score, best_params

        # Sample weights
        weights = dict(live_weights)
        for ind in uncertain:
            base = live_weights.get(ind, 1.0)
            mult = trial.suggest_float(f"w_{ind}", 0.0, 3.0, step=0.25)
            weights[ind] = base * mult

        # Sample thresholds
        min_consensus = trial.suggest_int("min_consensus", 7, 10)
        min_edge = trial.suggest_float("min_edge", 0.08, 0.16, step=0.02)
        min_conf = trial.suggest_float("min_confidence", 0.15, 0.40, step=0.05)
        up_premium = trial.suggest_float("up_premium", 0.0, 0.15, step=0.03)

        params = BacktestParams(
            weights=weights,
            tf_weight_scale=live_tf_scale,
            min_consensus=min_consensus,
            min_confidence=min_conf,
            up_confidence_premium=up_premium,
            min_edge_absolute=min_edge,
            min_edge_by_tf={tf: max(min_edge, v) for tf, v in MIN_EDGE_BY_TF.items()},
            asset_edge_premiums=live_asset_premiums,
            prob_clamp=live_prob_clamp,
            label=f"optuna_t{trial.number}",
        )

        result = replay_historical_trades(train_data, params)
        s = score_result(result, min_trades=5)

        if s > best_score:
            best_score = s
            best_params = params

        return s

    study = optuna.create_study(direction="maximize")
    study.optimize(objective, n_trials=max_trials, show_progress_bar=False)

    if best_params is None:
        # Fallback to live params
        from quant.optimizer import get_live_params
        best_params = get_live_params()

    return best_params


def _grid_optimize(
    train_data: list[dict],
    max_combos: int = 100,
) -> BacktestParams:
    """Fallback grid search when Optuna is not available."""
    from quant.optimizer import run_optimization
    _, scored = run_optimization(train_data, max_combinations=max_combos, min_trades=5)
    if scored and scored[0][0] > 0:
        # Reconstruct params from best result
        best = scored[0][1]
        return BacktestParams(
            weights=best.params.get("weights", dict(WEIGHTS)),
            tf_weight_scale={tf: dict(s) for tf, s in TF_WEIGHT_SCALE.items()},
            min_consensus=best.params.get("min_consensus", MIN_CONSENSUS),
            min_confidence=best.params.get("min_confidence", MIN_CONFIDENCE),
            up_confidence_premium=best.params.get("up_confidence_premium", UP_CONFIDENCE_PREMIUM),
            min_edge_absolute=best.params.get("min_edge_absolute", MIN_EDGE_ABSOLUTE),
            min_edge_by_tf=dict(MIN_EDGE_BY_TF),
            asset_edge_premiums=dict(ASSET_EDGE_PREMIUM),
            prob_clamp=dict(PROB_CLAMP),
            label="grid_best",
        )
    from quant.optimizer import get_live_params
    return get_live_params()


def optuna_full_optimization(
    trades: list[dict],
    n_trials: int = 200,
    min_trades: int = 20,
    progress_callback=None,
) -> tuple[BacktestResult, list[tuple[float, BacktestResult]]]:
    """Run Optuna Bayesian optimization on all trades (no walk-forward split).

    Returns (baseline, sorted_results) same interface as grid optimizer.
    Use walk_forward_validation() to get unbiased OOS estimates.
    """
    if not HAS_OPTUNA:
        log.warning("Optuna not installed, falling back to grid search")
        from quant.optimizer import run_optimization
        return run_optimization(trades, max_combinations=n_trials, min_trades=min_trades,
                                progress_callback=progress_callback)

    t0 = time.time()
    from quant.optimizer import get_live_params

    live_params = get_live_params()
    baseline = replay_historical_trades(trades, live_params)
    baseline_score = score_result(baseline, min_trades)
    log.info("Optuna baseline: WR=%.1f%%, score=%.1f", baseline.win_rate, baseline_score)

    if progress_callback:
        progress_callback("Optuna baseline", f"WR={baseline.win_rate:.1f}%", 10)

    live_weights = dict(WEIGHTS)
    live_tf_scale = {tf: dict(s) for tf, s in TF_WEIGHT_SCALE.items()}
    live_prob_clamp = dict(PROB_CLAMP)
    live_asset_premiums = dict(ASSET_EDGE_PREMIUM)

    uncertain = ["heikin_ashi", "order_flow", "momentum", "ema", "macd",
                 "spot_depth", "news", "volume_spike"]

    all_results: list[tuple[float, BacktestResult]] = []

    def objective(trial: optuna.Trial) -> float:
        weights = dict(live_weights)
        for ind in uncertain:
            base = live_weights.get(ind, 1.0)
            mult = trial.suggest_float(f"w_{ind}", 0.0, 3.0, step=0.25)
            weights[ind] = base * mult

        min_consensus = trial.suggest_int("min_consensus", 7, 10)
        min_edge = trial.suggest_float("min_edge", 0.08, 0.16, step=0.02)
        min_conf = trial.suggest_float("min_confidence", 0.15, 0.40, step=0.05)
        up_premium = trial.suggest_float("up_premium", 0.0, 0.15, step=0.03)

        params = BacktestParams(
            weights=weights,
            tf_weight_scale=live_tf_scale,
            min_consensus=min_consensus,
            min_confidence=min_conf,
            up_confidence_premium=up_premium,
            min_edge_absolute=min_edge,
            min_edge_by_tf={tf: max(min_edge, v) for tf, v in MIN_EDGE_BY_TF.items()},
            asset_edge_premiums=live_asset_premiums,
            prob_clamp=live_prob_clamp,
            label=f"optuna_t{trial.number}",
        )

        result = replay_historical_trades(trades, params)
        s = score_result(result, min_trades)
        all_results.append((s, result))

        if progress_callback and trial.number % 20 == 0:
            pct = 10 + int(80 * trial.number / n_trials)
            best_wr = max((r.win_rate for _, r in all_results if r.total_signals >= min_trades), default=0)
            progress_callback("Optuna search", f"Trial {trial.number}/{n_trials} (best WR={best_wr:.1f}%)", pct)

        return s

    study = optuna.create_study(direction="maximize")
    study.optimize(objective, n_trials=n_trials, show_progress_bar=False)

    all_results.sort(key=lambda x: x[0], reverse=True)

    elapsed = time.time() - t0
    log.info("Optuna complete: %d trials in %.2fs", n_trials, elapsed)

    if all_results and all_results[0][1].total_signals >= min_trades:
        best = all_results[0][1]
        log.info("Best: WR=%.1f%% (%d signals) vs baseline %.1f%%",
                 best.win_rate, best.total_signals, baseline.win_rate)

    if progress_callback:
        progress_callback("Complete", f"{len(all_results)} trials", 100, done=True)

    return baseline, all_results
