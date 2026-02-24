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
    walk_forward_validation, walk_forward_v2, bootstrap_confidence_interval,
    optuna_full_optimization, HAS_OPTUNA,
)
from quant.reporter import (
    write_status, write_results, write_recommendations,
    write_hawk_review, publish_events, write_live_params,
)
from quant.analytics import (
    compute_kelly, analyze_indicator_diversity, detect_strategy_decay,
    monte_carlo_simulate, cusum_edge_decay,
)
from quant.live_push import validate_push, push_params, get_version_history
from quant.regime import tag_trades_with_regime, analyze_regime_performance
from quant.correlation_guard import check_correlation, write_correlation_report
from quant.self_learner import run_learning_cycle, load_odin_trades
from quant.pnl_estimator import estimate_pnl_impact, write_pnl_impact
from quant.scorer import score_result
from quant.ml_predictor import retrain_model
from quant.odin_backtester import run_multi_asset_backtest
from quant.odin_scorer import score_odin_backtest, write_odin_backtest_report

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
        """Single backtest cycle with Phase 1 intelligence engine."""
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

        # 4a. Walk-forward V1 (legacy compatibility)
        n_folds = self.cfg.wfv2_folds if len(trades) >= 100 else 3
        wf_result = walk_forward_validation(
            trades=trades,
            n_folds=n_folds,
            max_optuna_trials=50,
            min_trades_per_fold=10,
        )

        # 4b. Walk-Forward V2 with strict OOS gates
        wfv2_result = walk_forward_v2(
            trades=trades,
            n_folds=n_folds,
            max_optuna_trials=50,
            min_trades_per_fold=10,
            max_overfit_gap=self.cfg.wfv2_max_overfit_gap,
            method=self.cfg.wfv2_method,
        )
        log.info("WFV2: %s (gap=%.1fpp, stability=%d, PNL=$%.2f/day)",
                 "PASSED" if wfv2_result.passed else f"REJECTED ({wfv2_result.rejection_reason})",
                 wfv2_result.overfit_gap, wfv2_result.stability_score,
                 wfv2_result.estimated_daily_pnl)

        # 5. Monte Carlo Risk Engine (10K simulations)
        mc_result = monte_carlo_simulate(
            trades=trades,
            n_simulations=self.cfg.monte_carlo_sims,
            bankroll=self.cfg.kelly_bankroll,
            ruin_threshold_pct=self.cfg.monte_carlo_ruin_threshold,
        )
        log.info("Monte Carlo: ruin=%.2f%%, avg DD=%.1f%%, Sharpe=%.2f, profitable=%.1f%%",
                 mc_result.ruin_probability, mc_result.avg_max_drawdown_pct,
                 mc_result.avg_sharpe, mc_result.profitable_pct)

        # 6. CUSUM Edge Decay Detection
        cusum_result = cusum_edge_decay(
            trades=trades,
            threshold=self.cfg.cusum_threshold,
            drift=self.cfg.cusum_drift,
            rolling_window=self.cfg.cusum_rolling_window,
        )
        if cusum_result.change_detected:
            log.warning("CUSUM ALERT [%s]: %s", cusum_result.severity, cusum_result.alert_message)

        # ── Phase 2: Regime, Correlation, Self-Learning ──

        # 7. Regime-Tagged Backtesting
        tagged_trades = tag_trades_with_regime(trades, candles)
        regime_analysis = analyze_regime_performance(tagged_trades)
        log.info("Regime analysis: %d regimes, best=%s, worst=%s",
                 regime_analysis.regime_count, regime_analysis.best_regime,
                 regime_analysis.worst_regime)

        # 8. Cross-Trader Correlation Guard
        odin_trades = load_odin_trades() if self.cfg.odin_enabled else []
        corr_report = check_correlation(
            garves_trades=trades,
            odin_trades=odin_trades,
        )
        write_correlation_report(corr_report)
        if corr_report.overall_risk in ("high", "critical"):
            log.warning("CORRELATION ALERT [%s]: %s", corr_report.overall_risk,
                        corr_report.alert_message)

        # 9. Self-Learning from Live Performance
        learning_summary = run_learning_cycle(
            garves_trades=trades,
            odin_trades=odin_trades,
        )
        log.info("Self-learning: accuracy=%.0f%%, combined WR=%.1f%% (%d trades), "
                 "%d outcomes measured, %d Odin insights",
                 learning_summary.get("recommendation_accuracy", 0),
                 learning_summary.get("combined_wr", 0),
                 learning_summary.get("combined_trades", 0),
                 learning_summary.get("outcomes_measured", 0),
                 len(learning_summary.get("odin_insights", [])))

        # 10. PNL Impact Estimator
        pnl_impact = None
        if scored and scored[0][1].total_signals >= 20:
            best_p = scored[0][1].params
            pnl_impact = estimate_pnl_impact(
                trades=trades,
                proposed_params=best_p,
            )
            write_pnl_impact(pnl_impact)
            log.info("PNL Impact: $%.2f/day ($%.2f/mo), %+d trades, WR %+.1fpp",
                     pnl_impact.daily_pnl, pnl_impact.monthly_pnl,
                     pnl_impact.net_trade_change, pnl_impact.wr_delta)

        # 11. Load Hawk trades for calibration review
        hawk_trades = self._load_hawk_trades()

        # 12. Write all reports
        write_status(self.cycle, baseline, len(scored), len(trades), candle_counts)
        write_results(baseline, scored)
        write_recommendations(baseline, scored)
        _write_walk_forward(wf_result, baseline_ci)
        _write_analytics(trades, baseline)
        _write_phase1_reports(wfv2_result, mc_result, cusum_result)
        _write_phase2_reports(regime_analysis, corr_report, learning_summary)
        if self.cfg.hawk_review:
            write_hawk_review(hawk_trades)

        # 13. Live Parameter Push with triple-gate validation
        best_wr = scored[0][1].win_rate if scored and scored[0][1].total_signals >= 20 else 0
        if scored and scored[0][1].total_signals >= 20:
            best_result = scored[0][1]

            # Validate through all three gates
            push_validation = validate_push(
                wfv2=wfv2_result,
                monte_carlo=mc_result,
                cusum=cusum_result,
                baseline_wr=baseline.win_rate,
                best_wr=best_result.win_rate,
                max_ruin_pct=self.cfg.max_ruin_pct,
            )

            if push_validation.passed:
                # Build param dict from best result
                bp = best_result.params
                base_p = baseline.params
                push_p = {}
                if bp.get("min_confidence") != base_p.get("min_confidence"):
                    push_p["min_confidence"] = bp["min_confidence"]
                if bp.get("up_confidence_premium") != base_p.get("up_confidence_premium"):
                    push_p["up_confidence_premium"] = bp["up_confidence_premium"]
                if bp.get("min_edge_absolute") and bp.get("min_edge_absolute") != base_p.get("min_edge_absolute"):
                    push_p["min_edge_absolute"] = bp["min_edge_absolute"]
                if bp.get("min_consensus") and bp.get("min_consensus") != base_p.get("min_consensus"):
                    push_p["consensus_floor"] = bp["min_consensus"]

                if push_p:
                    result = push_params(
                        params=push_p,
                        validation=push_validation,
                        baseline_wr=baseline.win_rate,
                        best_wr=best_result.win_rate,
                        target=self.cfg.push_target,
                        dry_run=self.cfg.push_dry_run,
                    )
                    log.info("Push result: %s", result.message)
            else:
                log.info("Push blocked: %s", "; ".join(push_validation.rejection_reasons))

            # Legacy push (for backward compat with param_loader.py)
            write_live_params(
                baseline=baseline,
                best=best_result,
                wf_test_wr=wf_result.test_win_rate,
                wf_overfit_drop=wf_result.overfit_drop,
            )

        # 14. Odin Strategy Backtest (SMC + regime + conviction on historical candles)
        if self.cfg.odin_backtest_enabled:
            try:
                candle_dir = DATA_DIR / "candles_4h"
                if candle_dir.exists() and list(candle_dir.glob("*.jsonl")):
                    log.info("Running Odin strategy backtest...")
                    odin_bt_results = run_multi_asset_backtest(
                        candle_dir=candle_dir,
                        symbols=self.cfg.odin_backtest_symbols,
                        risk_per_trade_usd=self.cfg.odin_backtest_risk_per_trade,
                        min_trade_score=self.cfg.odin_backtest_min_score,
                        min_confidence=self.cfg.odin_backtest_min_confidence,
                        min_rr=self.cfg.odin_backtest_min_rr,
                        balance=self.cfg.odin_backtest_balance,
                        step_size=self.cfg.odin_backtest_step,
                        window_size=self.cfg.odin_backtest_window,
                    )
                    if odin_bt_results:
                        odin_score = score_odin_backtest(
                            odin_bt_results,
                            starting_balance=self.cfg.odin_backtest_balance,
                        )
                        write_odin_backtest_report(odin_score, DATA_DIR)
                        log.info(
                            "Odin backtest: %d trades, WR=%.1f%%, PnL=$%.2f, "
                            "Sharpe=%.2f, maxDD=%.1f%%",
                            odin_score.total_trades, odin_score.win_rate,
                            odin_score.total_pnl, odin_score.sharpe_ratio,
                            odin_score.max_drawdown_pct,
                        )

                        # Publish to event bus
                        try:
                            import sys as _sys
                            _shared = str(Path.home() / "shared")
                            if _shared not in _sys.path:
                                _sys.path.insert(0, _shared)
                            from events import publish
                            publish(
                                agent="quant",
                                event_type="odin_backtest_complete",
                                severity="info",
                                summary=(
                                    f"Odin BT: {odin_score.total_trades} trades, "
                                    f"WR={odin_score.win_rate:.1f}%, "
                                    f"PnL=${odin_score.total_pnl:.2f}, "
                                    f"Sharpe={odin_score.sharpe_ratio:.2f}"
                                ),
                                data={
                                    "trades": odin_score.total_trades,
                                    "win_rate": odin_score.win_rate,
                                    "total_pnl": odin_score.total_pnl,
                                    "sharpe": odin_score.sharpe_ratio,
                                    "max_dd": odin_score.max_drawdown_pct,
                                },
                            )
                        except Exception:
                            pass
                    else:
                        log.info("Odin backtest: no candle data matched symbols")
                else:
                    log.info("Odin backtest: no 4H candle data in %s (run: .venv/bin/python -m quant.bulk_download --all-assets --interval 4h --months 12)", candle_dir)
            except Exception:
                log.exception("Odin strategy backtest failed (non-fatal)")

        # 15. ML Model retrain (XGBoost on resolved trades)
        # (was step 14 before Odin backtest added)
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

        # 15. Publish to event bus
        publish_events(baseline, scored)

        # 16. Log summary
        log.info("=== Cycle %d complete ===", self.cycle)
        log.info("Baseline: WR=%.1f%% (%d signals) CI=[%.1f%%, %.1f%%]",
                 baseline.win_rate, baseline.total_signals,
                 baseline_ci.ci_lower, baseline_ci.ci_upper)
        log.info("Best found: WR=%.1f%% | WFV2: %s (gap=%.1fpp) | MC ruin=%.2f%% | CUSUM=%s",
                 best_wr,
                 "PASS" if wfv2_result.passed else "FAIL",
                 wfv2_result.overfit_gap,
                 mc_result.ruin_probability,
                 cusum_result.severity)
        log.info("Phase 2: regime=%s, correlation=%s, learning=%.0f%% accuracy, Odin=%d trades",
                 regime_analysis.current_regime.combined,
                 corr_report.overall_risk,
                 learning_summary.get("recommendation_accuracy", 0),
                 learning_summary.get("odin_trades", 0))

        # Brain: record backtest findings + outcome
        if _quant_brain:
            try:
                _did = _quant_brain.remember_decision(
                    context=(
                        f"Backtest cycle {self.cycle}: {len(scored)} combos on {len(trades)} trades. "
                        f"WFV2={'PASS' if wfv2_result.passed else 'FAIL'}, "
                        f"MC ruin={mc_result.ruin_probability:.1f}%, CUSUM={cusum_result.severity}"
                    ),
                    decision=(
                        f"Baseline WR={baseline.win_rate:.1f}%, best WR={best_wr:.1f}%, "
                        f"WFV2 OOS={wfv2_result.avg_test_wr:.1f}%, stability={wfv2_result.stability_score:.0f}"
                    ),
                    confidence=0.5,
                    tags=["backtest", "phase1"],
                )
                _improvement = best_wr - baseline.win_rate
                _score = min(1.0, _improvement / 10.0) if _improvement > 0 else -0.5
                _quant_brain.remember_outcome(
                    _did,
                    f"Improvement={_improvement:+.1f}pp, WFV2={'PASS' if wfv2_result.passed else 'FAIL'}, "
                    f"ruin={mc_result.ruin_probability:.1f}%, CUSUM={cusum_result.severity}",
                    score=_score,
                )
                if _improvement > 2.0 and wfv2_result.passed and mc_result.ruin_probability < 5:
                    _quant_brain.learn_pattern(
                        "strong_validated_backtest",
                        f"+{_improvement:.1f}pp improvement passed all 3 gates "
                        f"(WFV2 gap={wfv2_result.overfit_gap:.1f}pp, ruin={mc_result.ruin_probability:.1f}%)",
                        evidence_count=1, confidence=0.75,
                    )
            except Exception:
                pass

    # ── Per-Trade Learning (event-driven) ──

    async def _poll_trade_events(self):
        """Poll event bus for trade_resolved events and study each one."""
        try:
            import sys
            _shared = str(Path.home() / "shared")
            if _shared not in sys.path:
                sys.path.insert(0, _shared)
            from events import get_unread
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
            _shared = str(Path.home() / "shared")
            if _shared not in sys.path:
                sys.path.insert(0, _shared)
            from events import publish
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


def _write_phase1_reports(wfv2, mc, cusum):
    """Write Phase 1 intelligence reports to JSON."""
    from quant.reporter import _now_et
    from quant.live_push import get_version_history

    output = {
        "walk_forward_v2": {
            "passed": wfv2.passed,
            "avg_train_wr": wfv2.avg_train_wr,
            "avg_test_wr": wfv2.avg_test_wr,
            "overfit_gap": wfv2.overfit_gap,
            "max_gap_threshold": wfv2.max_gap,
            "stability_score": wfv2.stability_score,
            "test_wr_std": wfv2.test_wr_std,
            "min_test_wr": wfv2.min_test_wr,
            "max_test_wr": wfv2.max_test_wr,
            "method": wfv2.method,
            "n_folds": wfv2.n_folds,
            "fold_results": wfv2.fold_results,
            "rejection_reason": wfv2.rejection_reason,
            "pnl_per_trade": wfv2.estimated_pnl_per_trade,
            "daily_pnl": wfv2.estimated_daily_pnl,
            "monthly_pnl": wfv2.estimated_monthly_pnl,
            "elapsed_seconds": wfv2.elapsed_seconds,
        },
        "monte_carlo": {
            "n_simulations": mc.n_simulations,
            "n_trades_per_sim": mc.n_trades_per_sim,
            "avg_max_drawdown_pct": mc.avg_max_drawdown_pct,
            "worst_max_drawdown_pct": mc.worst_max_drawdown_pct,
            "drawdown_95th_pct": mc.drawdown_95th_pct,
            "ruin_probability": mc.ruin_probability,
            "ruin_threshold_pct": mc.ruin_threshold_pct,
            "avg_final_pnl": mc.avg_final_pnl,
            "median_final_pnl": mc.median_final_pnl,
            "pnl_95th_lower": mc.pnl_95th_lower,
            "pnl_95th_upper": mc.pnl_95th_upper,
            "avg_sharpe": mc.avg_sharpe,
            "profitable_pct": mc.profitable_pct,
            "pnl_percentiles": mc.pnl_percentiles,
            "elapsed_seconds": mc.elapsed_seconds,
        },
        "cusum": {
            "change_detected": cusum.change_detected,
            "severity": cusum.severity,
            "alert_message": cusum.alert_message,
            "cusum_pos": cusum.cusum_pos,
            "cusum_neg": cusum.cusum_neg,
            "threshold": cusum.threshold,
            "target_wr": cusum.target_wr,
            "current_rolling_wr": cusum.current_rolling_wr,
            "change_point_index": cusum.change_point_index,
            "pre_change_wr": cusum.pre_change_wr,
            "post_change_wr": cusum.post_change_wr,
            "wr_drop_pp": cusum.wr_drop_pp,
            "trades_since_change": cusum.trades_since_change,
        },
        "version_history": get_version_history(5),
        "updated": _now_et(),
    }
    DATA_DIR.mkdir(exist_ok=True)
    (DATA_DIR / "quant_phase1.json").write_text(json.dumps(output, indent=2))
    log.info("Wrote quant_phase1.json (WFV2=%s, MC ruin=%.2f%%, CUSUM=%s)",
             "PASS" if wfv2.passed else "FAIL", mc.ruin_probability, cusum.severity)


def _write_phase2_reports(regime_analysis, corr_report, learning_summary):
    """Write Phase 2 intelligence reports to JSON."""
    from quant.reporter import _now_et

    output = {
        "regime": {
            "current": regime_analysis.current_regime.combined if regime_analysis.current_regime else "unknown",
            "current_vol": regime_analysis.current_regime.volatility if regime_analysis.current_regime else "unknown",
            "current_trend": regime_analysis.current_regime.trend if regime_analysis.current_regime else "unknown",
            "best_regime": regime_analysis.best_regime,
            "worst_regime": regime_analysis.worst_regime,
            "regime_count": regime_analysis.regime_count,
            "distribution": regime_analysis.regime_distribution,
            "performance": regime_analysis.regime_performance,
        },
        "correlation": {
            "overall_risk": corr_report.overall_risk,
            "alert_message": corr_report.alert_message,
            "direct_overlaps": corr_report.direct_overlaps,
            "correlated_overlaps": corr_report.correlated_overlaps,
            "garves_exposure": round(corr_report.garves_total_exposure, 2),
            "odin_exposure": round(corr_report.odin_total_exposure, 2),
            "combined_exposure": round(corr_report.combined_exposure, 2),
            "trade_correlation": corr_report.trade_correlation,
            "recommendations": corr_report.recommendations,
        },
        "learning": learning_summary,
        "updated": _now_et(),
    }
    DATA_DIR.mkdir(exist_ok=True)
    (DATA_DIR / "quant_phase2.json").write_text(json.dumps(output, indent=2))
    log.info("Wrote quant_phase2.json (regime=%s, corr=%s, learning=%.0f%%)",
             regime_analysis.current_regime.combined if regime_analysis.current_regime else "?",
             corr_report.overall_risk,
             learning_summary.get("recommendation_accuracy", 0))


def _write_analytics(trades: list[dict], baseline: BacktestResult):
    """Write Kelly, diversity, and decay analysis to JSON."""
    from quant.reporter import _now_et

    kelly = compute_kelly(baseline.wins, baseline.losses, baseline.avg_edge, trades=trades)
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
            "expected_pnl_per_trade": kelly.expected_pnl_per_trade,
            "per_asset": kelly.per_asset,
            "per_timeframe": kelly.per_timeframe,
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

    Returns summary dict for the API response, now including Phase 1 intelligence.
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

    # Walk-forward V1
    n_folds = 5 if len(trades) >= 100 else 3
    wf_result = walk_forward_validation(
        trades=trades,
        n_folds=n_folds,
        max_optuna_trials=50,
        min_trades_per_fold=10,
    )

    # Walk-Forward V2 (Phase 1)
    wfv2_result = walk_forward_v2(
        trades=trades,
        n_folds=n_folds,
        max_optuna_trials=50,
        min_trades_per_fold=10,
    )

    # Monte Carlo (Phase 1)
    mc_result = monte_carlo_simulate(trades=trades)

    # CUSUM (Phase 1)
    cusum_result = cusum_edge_decay(trades=trades)

    # Phase 2: Regime + Correlation + Self-Learning
    tagged_trades = tag_trades_with_regime(trades, candles)
    regime_analysis = analyze_regime_performance(tagged_trades)
    odin_trades = load_odin_trades()
    corr_report = check_correlation(garves_trades=trades, odin_trades=odin_trades)
    write_correlation_report(corr_report)
    learning_summary = run_learning_cycle(garves_trades=trades, odin_trades=odin_trades)

    # Phase 3: PNL Impact Estimator
    pnl_impact_data = {}
    if scored and scored[0][1].total_signals >= 20:
        pnl_impact = estimate_pnl_impact(
            trades=trades,
            proposed_params=scored[0][1].params,
        )
        write_pnl_impact(pnl_impact)
        pnl_impact_data = {
            "daily_pnl": pnl_impact.daily_pnl,
            "monthly_pnl": pnl_impact.monthly_pnl,
            "wr_delta": pnl_impact.wr_delta,
            "net_trades": pnl_impact.net_trade_change,
        }

    # Write reports
    candle_counts = {asset: len(c) for asset, c in candles.items()}
    write_status(0, baseline, len(scored), len(trades), candle_counts)
    write_results(baseline, scored)
    write_recommendations(baseline, scored)
    _write_walk_forward(wf_result, baseline_ci)
    _write_analytics(trades, baseline)
    _write_phase1_reports(wfv2_result, mc_result, cusum_result)
    _write_phase2_reports(regime_analysis, corr_report, learning_summary)

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

    # Odin strategy backtest
    odin_bt_summary = {}
    try:
        candle_dir = DATA_DIR / "candles_4h"
        if candle_dir.exists() and list(candle_dir.glob("*.jsonl")):
            odin_bt_results = run_multi_asset_backtest(
                candle_dir=candle_dir,
                symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"],
            )
            if odin_bt_results:
                odin_score = score_odin_backtest(odin_bt_results)
                write_odin_backtest_report(odin_score, DATA_DIR)
                odin_bt_summary = {
                    "trades": odin_score.total_trades,
                    "win_rate": odin_score.win_rate,
                    "total_pnl": odin_score.total_pnl,
                    "sharpe": odin_score.sharpe_ratio,
                    "max_dd": odin_score.max_drawdown_pct,
                    "profit_factor": odin_score.profit_factor,
                }
    except Exception:
        log.exception("Odin backtest in single run failed (non-fatal)")

    # Live push with triple-gate validation (Phase 1)
    params_applied = False
    push_status = "no_improvement"
    best = scored[0][1] if scored and scored[0][1].total_signals >= 20 else baseline
    if best is not baseline:
        push_validation = validate_push(
            wfv2=wfv2_result,
            monte_carlo=mc_result,
            cusum=cusum_result,
            baseline_wr=baseline.win_rate,
            best_wr=best.win_rate,
        )
        if push_validation.passed:
            push_status = "validated"
        else:
            push_status = f"blocked: {'; '.join(push_validation.rejection_reasons)}"

        # Legacy push (backward compat)
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
        "walk_forward_v2": {
            "passed": wfv2_result.passed,
            "train_wr": wfv2_result.avg_train_wr,
            "test_wr": wfv2_result.avg_test_wr,
            "overfit_gap": wfv2_result.overfit_gap,
            "stability": wfv2_result.stability_score,
            "daily_pnl": wfv2_result.estimated_daily_pnl,
        },
        "monte_carlo": {
            "ruin_pct": mc_result.ruin_probability,
            "avg_drawdown": mc_result.avg_max_drawdown_pct,
            "sharpe": mc_result.avg_sharpe,
            "profitable_pct": mc_result.profitable_pct,
        },
        "cusum": {
            "change_detected": cusum_result.change_detected,
            "severity": cusum_result.severity,
            "current_wr": cusum_result.current_rolling_wr,
        },
        "optimizer": "optuna" if HAS_OPTUNA else "grid",
        "params_auto_applied": params_applied,
        "push_status": push_status,
        "pnl_impact": pnl_impact_data,
        "regime": regime_analysis.current_regime.combined if regime_analysis.current_regime else "unknown",
        "correlation_risk": corr_report.overall_risk,
        "odin_backtest": odin_bt_summary,
    }
