"""Parameter sweep engine — generates and tests combinations."""
from __future__ import annotations

import itertools
import logging
import time
from dataclasses import replace

from bot.signals import WEIGHTS, TF_WEIGHT_SCALE, PROB_CLAMP
from bot.signals import MIN_CONSENSUS, MIN_CONFIDENCE, UP_CONFIDENCE_PREMIUM
from bot.signals import MIN_EDGE_ABSOLUTE, MIN_EDGE_BY_TF, ASSET_EDGE_PREMIUM

from quant.backtester import BacktestParams, BacktestResult, replay_historical_trades
from quant.scorer import score_result

log = logging.getLogger(__name__)


def get_live_params() -> BacktestParams:
    """Build BacktestParams from the current live signal engine settings."""
    return BacktestParams(
        weights=dict(WEIGHTS),
        tf_weight_scale={tf: dict(scales) for tf, scales in TF_WEIGHT_SCALE.items()},
        min_consensus=MIN_CONSENSUS,
        min_confidence=MIN_CONFIDENCE,
        up_confidence_premium=UP_CONFIDENCE_PREMIUM,
        min_edge_absolute=MIN_EDGE_ABSOLUTE,
        min_edge_by_tf=dict(MIN_EDGE_BY_TF),
        asset_edge_premiums=dict(ASSET_EDGE_PREMIUM),
        prob_clamp={tf: clamp for tf, clamp in PROB_CLAMP.items()},
        label="live_current",
    )


def generate_weight_grid(base_weights: dict[str, float]) -> list[dict[str, float]]:
    """Generate weight variations for uncertain indicators (45-55% accuracy).

    Varies indicators in the uncertain zone with multipliers [0.0, 0.5, 1.0, 1.5, 2.0].
    High-accuracy indicators (>60%) and disabled ones stay fixed.
    """
    # Uncertain indicators (near coin-flip accuracy: 40-58%)
    uncertain = ["heikin_ashi", "order_flow", "momentum", "ema", "macd", "spot_depth"]
    multipliers = [0.0, 0.5, 1.0, 1.5, 2.0]

    # Generate combinations for 2-3 indicators at a time to keep grid manageable
    grids: list[dict[str, float]] = []

    # Strategy 1: Vary pairs of uncertain indicators
    for combo in itertools.combinations(uncertain, 2):
        for mults in itertools.product(multipliers, repeat=2):
            variant = dict(base_weights)
            for ind, mult in zip(combo, mults):
                variant[ind] = base_weights.get(ind, 1.0) * mult
            grids.append(variant)

    # Strategy 2: Boost top-tier individually
    top_tier = ["news", "volume_spike", "liquidation", "temporal_arb"]
    for ind in top_tier:
        for mult in [1.5, 2.0, 2.5, 3.0]:
            variant = dict(base_weights)
            variant[ind] = base_weights.get(ind, 1.0) * mult
            grids.append(variant)

    # Strategy 3: Re-enable disabled indicators at low weight
    disabled = [k for k, v in base_weights.items() if v <= 0]
    for ind in disabled:
        for w in [0.3, 0.5, 0.8]:
            variant = dict(base_weights)
            variant[ind] = w
            grids.append(variant)

    return grids


def generate_threshold_grid() -> list[dict]:
    """Generate threshold parameter combinations to sweep.

    Only tests values at or above live minimums to avoid impossible combos.
    Live floors: consensus=7, edge=0.08, confidence=0.25.
    """
    # Only test values >= live minimum (consensus < 7 is impossible in live)
    consensus_range = [7, 8, 9, 10]
    # Edge values at or above the hard floor
    edge_range = [0.08, 0.10, 0.12, 0.14]
    # Confidence at or above the live minimum
    confidence_range = [0.20, 0.25, 0.30, 0.35]
    up_premium_range = [0.00, 0.04, 0.08, 0.12]

    grids = []
    for consensus, edge, conf, up_prem in itertools.product(
        consensus_range, edge_range, confidence_range, up_premium_range
    ):
        grids.append({
            "min_consensus": consensus,
            "min_edge_absolute": edge,
            "min_confidence": conf,
            "up_confidence_premium": up_prem,
        })

    return grids


def run_optimization(
    trades: list[dict],
    max_combinations: int = 500,
    min_trades: int = 20,
    progress_callback=None,
) -> tuple[BacktestResult, list[tuple[float, BacktestResult]]]:
    """Run full parameter sweep. Returns (baseline_result, sorted_results).

    1. Test current live params as baseline
    2. Generate weight variations
    3. Generate threshold variations
    4. Run all combos, score, sort
    """
    t0 = time.time()

    # 1. Baseline — current live params
    live_params = get_live_params()
    baseline = replay_historical_trades(trades, live_params)
    baseline_score = score_result(baseline, min_trades)
    log.info("Baseline: WR=%.1f%%, signals=%d, score=%.1f, avg_edge=%.2f%%",
             baseline.win_rate, baseline.total_signals, baseline_score,
             baseline.avg_edge * 100)

    if progress_callback:
        progress_callback("Baseline tested", f"WR={baseline.win_rate:.1f}%", 15)

    # 2. Generate weight grid
    weight_grids = generate_weight_grid(dict(WEIGHTS))
    log.info("Generated %d weight variations", len(weight_grids))

    # 3. Generate threshold grid
    threshold_grids = generate_threshold_grid()
    log.info("Generated %d threshold variations", len(threshold_grids))

    # 4. Combine: sample from weight grids + threshold grids
    all_combos: list[BacktestParams] = []

    # Weight-only combos (use live thresholds)
    for i, wg in enumerate(weight_grids[:max_combinations // 2]):
        p = BacktestParams(
            weights=wg,
            tf_weight_scale=live_params.tf_weight_scale,
            min_consensus=live_params.min_consensus,
            min_confidence=live_params.min_confidence,
            up_confidence_premium=live_params.up_confidence_premium,
            min_edge_absolute=live_params.min_edge_absolute,
            min_edge_by_tf=live_params.min_edge_by_tf,
            asset_edge_premiums=live_params.asset_edge_premiums,
            prob_clamp=live_params.prob_clamp,
            label=f"weight_v{i}",
        )
        all_combos.append(p)

    # Threshold-only combos (use live weights)
    for i, tg in enumerate(threshold_grids[:max_combinations // 2]):
        p = BacktestParams(
            weights=dict(WEIGHTS),
            tf_weight_scale=live_params.tf_weight_scale,
            min_consensus=tg["min_consensus"],
            min_confidence=tg["min_confidence"],
            up_confidence_premium=tg["up_confidence_premium"],
            min_edge_absolute=tg["min_edge_absolute"],
            min_edge_by_tf={tf: max(tg["min_edge_absolute"], v)
                           for tf, v in MIN_EDGE_BY_TF.items()},
            asset_edge_premiums=live_params.asset_edge_premiums,
            prob_clamp=live_params.prob_clamp,
            label=f"thresh_c{tg['min_consensus']}_e{tg['min_edge_absolute']:.2f}_"
                  f"conf{tg['min_confidence']:.2f}_up{tg['up_confidence_premium']:.2f}",
        )
        all_combos.append(p)

    # Trim to max
    all_combos = all_combos[:max_combinations]
    log.info("Testing %d parameter combinations against %d trades", len(all_combos), len(trades))

    if progress_callback:
        progress_callback("Sweep starting", f"{len(all_combos)} combos", 25)

    # 5. Run all
    scored: list[tuple[float, BacktestResult]] = []
    for idx, params in enumerate(all_combos):
        result = replay_historical_trades(trades, params)
        s = score_result(result, min_trades)
        scored.append((s, result))

        if progress_callback and idx % 50 == 0:
            pct = 25 + int(65 * (idx + 1) / len(all_combos))
            progress_callback(
                "Running sweep",
                f"{idx + 1}/{len(all_combos)} tested (best WR: "
                f"{max(r.win_rate for _, r in scored if r.total_signals >= min_trades) if scored else 0:.1f}%)",
                pct,
            )

    # Sort descending by score
    scored.sort(key=lambda x: x[0], reverse=True)

    elapsed = time.time() - t0
    log.info("Optimization complete: %d combos in %.2fs", len(all_combos), elapsed)

    if scored and scored[0][1].total_signals >= min_trades:
        best = scored[0][1]
        log.info("Best: WR=%.1f%% (%d signals, score=%.1f) vs baseline %.1f%%",
                 best.win_rate, best.total_signals, scored[0][0], baseline.win_rate)

    if progress_callback:
        progress_callback("Complete", f"{len(scored)} results", 100, done=True)

    return baseline, scored
