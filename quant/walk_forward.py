"""Walk-Forward Optimization + Bootstrap Confidence Intervals + Optuna Bayesian Search.

Prevents overfitting by training on a rolling window and testing on held-out data.
Bootstrap CI gives statistical confidence in results.
Optuna replaces grid search for 3-10x more efficient parameter search.
"""
from __future__ import annotations

import logging
import math
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
class WalkForwardV2Result:
    """Enhanced walk-forward validation with strict quality gates."""
    # Aggregated performance
    avg_train_wr: float = 0.0
    avg_test_wr: float = 0.0
    overfit_gap: float = 0.0          # train_wr - test_wr in pp
    # Quality gate verdict
    passed: bool = False
    max_gap: float = 10.0             # configurable threshold (pp)
    rejection_reason: str = ""
    # Stability metrics across folds
    test_wr_std: float = 0.0          # standard deviation of test WRs
    stability_score: float = 0.0      # 0-100, higher = more stable
    min_test_wr: float = 0.0
    max_test_wr: float = 0.0
    # PNL estimation
    estimated_pnl_per_trade: float = 0.0
    estimated_daily_pnl: float = 0.0
    estimated_monthly_pnl: float = 0.0
    # Fold details
    n_folds: int = 0
    fold_results: list[dict] = field(default_factory=list)
    best_params_label: str = ""
    # Method used
    method: str = "anchored"          # "anchored" or "rolling"
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


def walk_forward_v2(
    trades: list[dict],
    n_folds: int = 5,
    max_optuna_trials: int = 100,
    min_trades_per_fold: int = 10,
    max_overfit_gap: float = 10.0,
    method: str = "anchored",
    avg_trades_per_day: float = 3.0,
    avg_bet_size: float = 15.0,
    progress_callback=None,
) -> WalkForwardV2Result:
    """Walk-Forward V2 with strict OOS gates and PNL estimation.

    Two modes:
      anchored: Expanding window — fold k trains on ALL data up to fold k.
      rolling:  Fixed-size sliding window of 2 folds before test fold.

    Quality gates (ALL must pass):
      1. Overfit gap < max_overfit_gap (default 10pp)
      2. Every fold test WR > 45% (above random chance)
      3. Stability: test WR std < 15pp across folds
      4. At least 3 valid folds completed

    PNL estimation:
      Uses OOS test WR and average edge to estimate expected PNL per trade,
      then scales to daily/monthly using avg_trades_per_day.
    """
    t0 = time.time()
    result = WalkForwardV2Result(n_folds=n_folds, max_gap=max_overfit_gap, method=method)

    sorted_trades = sorted(trades, key=lambda t: t.get("timestamp", 0))

    # Adaptive fold count
    fold_size = len(sorted_trades) // n_folds
    if fold_size < min_trades_per_fold:
        n_folds = max(3, len(sorted_trades) // min_trades_per_fold)
        fold_size = len(sorted_trades) // n_folds
        result.n_folds = n_folds

    if n_folds < 3:
        result.rejection_reason = f"Insufficient data: need {min_trades_per_fold * 3}+ trades, have {len(sorted_trades)}"
        result.elapsed_seconds = time.time() - t0
        return result

    folds = []
    for i in range(n_folds):
        start = i * fold_size
        end = start + fold_size if i < n_folds - 1 else len(sorted_trades)
        folds.append(sorted_trades[start:end])

    log.info("WFV2 [%s]: %d folds, ~%d trades each, gap threshold=%.0fpp",
             method, n_folds, fold_size, max_overfit_gap)

    train_wrs: list[float] = []
    test_wrs: list[float] = []
    test_edges: list[float] = []
    all_fold_results: list[dict] = []
    best_score = -1.0
    best_label = ""

    for k in range(1, n_folds):
        # Build training set based on method
        if method == "rolling":
            # Rolling: use only the 2 folds immediately before test fold
            start_fold = max(0, k - 2)
            train_data = []
            for j in range(start_fold, k):
                train_data.extend(folds[j])
        else:
            # Anchored: use ALL folds before test fold (expanding window)
            train_data = []
            for j in range(k):
                train_data.extend(folds[j])

        test_data = folds[k]

        if len(train_data) < min_trades_per_fold or len(test_data) < min_trades_per_fold:
            continue

        if progress_callback:
            pct = int(10 + 80 * (k / (n_folds - 1)))
            progress_callback("WFV2", f"Fold {k}/{n_folds-1} ({method})", pct)

        # Optimize on training data
        best_params = _optimize_fold(train_data, max_trials=max_optuna_trials)

        # In-sample evaluation
        train_result = replay_historical_trades(train_data, best_params)
        train_wr = train_result.win_rate if train_result.total_signals > 0 else 0.0

        # Out-of-sample evaluation
        test_result = replay_historical_trades(test_data, best_params)
        test_wr = test_result.win_rate if test_result.total_signals > 0 else 0.0
        test_edge = test_result.avg_edge if test_result.total_signals > 0 else 0.0

        train_score = score_result(train_result, min_trades=5)
        test_score = score_result(test_result, min_trades=5)

        if test_score > best_score:
            best_score = test_score
            best_label = best_params.label

        train_wrs.append(train_wr)
        test_wrs.append(test_wr)
        test_edges.append(test_edge)

        fold_info = {
            "fold": k,
            "method": method,
            "train_size": len(train_data),
            "test_size": len(test_data),
            "train_wr": round(train_wr, 1),
            "test_wr": round(test_wr, 1),
            "gap_pp": round(train_wr - test_wr, 1),
            "test_edge": round(test_edge * 100, 2),
            "train_signals": train_result.total_signals,
            "test_signals": test_result.total_signals,
            "train_score": round(train_score, 1),
            "test_score": round(test_score, 1),
            "params_label": best_params.label,
        }
        all_fold_results.append(fold_info)
        log.info("WFV2 fold %d: train=%.1f%% test=%.1f%% gap=%.1fpp edge=%.2f%%",
                 k, train_wr, test_wr, train_wr - test_wr, test_edge * 100)

    # Aggregate results
    result.fold_results = all_fold_results
    result.best_params_label = best_label
    result.elapsed_seconds = time.time() - t0

    if not test_wrs:
        result.rejection_reason = "No valid folds completed"
        return result

    result.avg_train_wr = round(sum(train_wrs) / len(train_wrs), 1)
    result.avg_test_wr = round(sum(test_wrs) / len(test_wrs), 1)
    result.overfit_gap = round(result.avg_train_wr - result.avg_test_wr, 1)
    result.min_test_wr = round(min(test_wrs), 1)
    result.max_test_wr = round(max(test_wrs), 1)

    # Stability metrics
    if len(test_wrs) >= 2:
        result.test_wr_std = round(float(np.std(test_wrs)), 1)
        # Stability score: 100 when std=0, 0 when std>=15
        result.stability_score = round(max(0, min(100, 100 - result.test_wr_std * (100 / 15))), 1)
    else:
        result.stability_score = 50.0  # uncertain with 1 fold

    # PNL estimation from OOS performance
    avg_test_edge = sum(test_edges) / len(test_edges) if test_edges else 0.0
    test_wr_frac = result.avg_test_wr / 100.0
    # Expected value per trade: P(win) * avg_edge * bet_size - P(loss) * avg_loss
    # For binary markets: win = edge * bet_size, loss ≈ bet_size * (1 - implied_price)
    # Simplified: EV = (WR * edge - (1-WR) * loss_rate) * bet_size
    if test_wr_frac > 0:
        result.estimated_pnl_per_trade = round(
            (test_wr_frac * avg_test_edge - (1 - test_wr_frac) * avg_test_edge) * avg_bet_size, 4
        )
        result.estimated_daily_pnl = round(result.estimated_pnl_per_trade * avg_trades_per_day, 2)
        result.estimated_monthly_pnl = round(result.estimated_daily_pnl * 30, 2)

    # Quality gates
    valid_folds = len(test_wrs)
    if valid_folds < 3:
        result.rejection_reason = f"Only {valid_folds} valid folds (need 3+)"
    elif result.overfit_gap > max_overfit_gap:
        result.rejection_reason = f"Overfit gap {result.overfit_gap:.1f}pp > {max_overfit_gap:.0f}pp threshold"
    elif result.min_test_wr < 45.0:
        result.rejection_reason = f"Min fold test WR {result.min_test_wr:.1f}% < 45% floor"
    elif result.test_wr_std > 15.0:
        result.rejection_reason = f"Test WR std {result.test_wr_std:.1f}pp > 15pp (unstable)"
    else:
        result.passed = True

    status = "PASSED" if result.passed else f"REJECTED ({result.rejection_reason})"
    log.info("WFV2 %s: train=%.1f%% test=%.1f%% gap=%.1fpp std=%.1f stability=%d | %s",
             method, result.avg_train_wr, result.avg_test_wr,
             result.overfit_gap, result.test_wr_std, result.stability_score, status)
    if result.estimated_daily_pnl != 0:
        log.info("WFV2 PNL estimate: $%.2f/trade, $%.2f/day, $%.2f/month",
                 result.estimated_pnl_per_trade, result.estimated_daily_pnl, result.estimated_monthly_pnl)

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
