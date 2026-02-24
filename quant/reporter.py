"""Reporter — writes data files and publishes to event bus."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

from quant.backtester import BacktestResult
from quant.scorer import score_result

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
from zoneinfo import ZoneInfo
ET = ZoneInfo("America/New_York")


def _now_et() -> str:
    return datetime.now(ET).strftime("%Y-%m-%d %I:%M %p ET")


def write_status(cycle: int, baseline: BacktestResult, total_combos: int,
                 trade_count: int, candle_counts: dict[str, int]) -> None:
    """Write data/quant_status.json — cycle info and data stats."""
    status = {
        "running": True,
        "cycle": cycle,
        "last_run": _now_et(),
        "total_combos_tested": total_combos,
        "baseline_win_rate": round(baseline.win_rate, 1),
        "baseline_signals": baseline.total_signals,
        "baseline_avg_edge": round(baseline.avg_edge * 100, 2),
        "trade_count": trade_count,
        "candle_counts": candle_counts,
        "filter_reasons": baseline.filter_reasons,
        "mode": "historical_replay",
    }
    DATA_DIR.mkdir(exist_ok=True)
    (DATA_DIR / "quant_status.json").write_text(json.dumps(status, indent=2))
    log.info("Wrote quant_status.json (cycle %d)", cycle)


def write_results(baseline: BacktestResult,
                  scored: list[tuple[float, BacktestResult]],
                  top_n: int = 20) -> None:
    """Write data/quant_results.json — top results + param sensitivity grid."""
    top_results = []
    for rank, (s, r) in enumerate(scored[:top_n], 1):
        top_results.append({
            "rank": rank,
            "label": r.label,
            "score": round(s, 1),
            "win_rate": round(r.win_rate, 1),
            "profit_factor": round(r.profit_factor, 2),
            "total_signals": r.total_signals,
            "wins": r.wins,
            "losses": r.losses,
            "max_consecutive_losses": r.max_consecutive_losses,
            "avg_edge": round(r.avg_edge * 100, 2),
            "avg_confidence": round(r.avg_confidence * 100, 1),
            "filter_reasons": r.filter_reasons,
            "params": r.params,
        })

    # Build sensitivity grid: min_consensus -> win_rate
    consensus_sensitivity = {}
    edge_sensitivity = {}
    for s, r in scored:
        if r.total_signals < 10:
            continue
        mc = r.params.get("min_consensus", 7)
        me = r.params.get("min_edge_absolute", 0.08)
        key_c = str(mc)
        key_e = f"{me:.2f}"
        if key_c not in consensus_sensitivity:
            consensus_sensitivity[key_c] = {"win_rates": [], "signal_counts": []}
        consensus_sensitivity[key_c]["win_rates"].append(r.win_rate)
        consensus_sensitivity[key_c]["signal_counts"].append(r.total_signals)
        if key_e not in edge_sensitivity:
            edge_sensitivity[key_e] = {"win_rates": [], "signal_counts": []}
        edge_sensitivity[key_e]["win_rates"].append(r.win_rate)
        edge_sensitivity[key_e]["signal_counts"].append(r.total_signals)

    # Average the sensitivity grids
    for grid in [consensus_sensitivity, edge_sensitivity]:
        for key, vals in grid.items():
            wrs = vals["win_rates"]
            scs = vals["signal_counts"]
            vals["avg_win_rate"] = round(sum(wrs) / len(wrs), 1) if wrs else 0
            vals["avg_signals"] = round(sum(scs) / len(scs), 1) if scs else 0
            vals["count"] = len(wrs)
            del vals["win_rates"]
            del vals["signal_counts"]

    baseline_data = {
        "label": baseline.label,
        "score": round(score_result(baseline), 1),
        "win_rate": round(baseline.win_rate, 1),
        "profit_factor": round(baseline.profit_factor, 2),
        "total_signals": baseline.total_signals,
        "wins": baseline.wins,
        "losses": baseline.losses,
        "avg_edge": round(baseline.avg_edge * 100, 2),
        "filter_reasons": baseline.filter_reasons,
        "params": baseline.params,
    }

    output = {
        "baseline": baseline_data,
        "top_results": top_results,
        "sensitivity": {
            "consensus": consensus_sensitivity,
            "edge": edge_sensitivity,
        },
        "updated": _now_et(),
    }
    (DATA_DIR / "quant_results.json").write_text(json.dumps(output, indent=2))
    log.info("Wrote quant_results.json (%d top results)", len(top_results))


def write_recommendations(baseline: BacktestResult,
                          scored: list[tuple[float, BacktestResult]]) -> None:
    """Write data/quant_recommendations.json — specific parameter changes for Garves."""
    recommendations = []
    baseline_wr = baseline.win_rate
    baseline_score = score_result(baseline)

    if not scored:
        output = {"recommendations": [], "updated": _now_et()}
        (DATA_DIR / "quant_recommendations.json").write_text(json.dumps(output, indent=2))
        return

    best_score, best_result = scored[0]

    if best_score > baseline_score and best_result.total_signals >= 20:
        # Compare parameters
        bp = baseline.params
        bestp = best_result.params

        if bestp.get("min_consensus") != bp.get("min_consensus"):
            recommendations.append({
                "param": "MIN_CONSENSUS",
                "current": bp.get("min_consensus"),
                "suggested": bestp.get("min_consensus"),
                "impact": f"WR {baseline_wr:.1f}% -> {best_result.win_rate:.1f}%",
                "reasoning": f"Consensus of {bestp.get('min_consensus')} filters noise better "
                           f"({best_result.total_signals} signals vs {baseline.total_signals})",
                "confidence": "high" if best_result.total_signals >= 40 else "medium",
            })

        if bestp.get("min_edge_absolute") and bestp.get("min_edge_absolute") != bp.get("min_edge_absolute"):
            recommendations.append({
                "param": "MIN_EDGE_ABSOLUTE",
                "current": bp.get("min_edge_absolute"),
                "suggested": bestp.get("min_edge_absolute"),
                "impact": f"Edge floor change filters weak signals",
                "reasoning": f"Tighter edge filter at {bestp.get('min_edge_absolute')} "
                           f"gives WR={best_result.win_rate:.1f}%",
                "confidence": "high" if best_result.total_signals >= 40 else "medium",
            })

        if bestp.get("min_confidence") != bp.get("min_confidence"):
            recommendations.append({
                "param": "MIN_CONFIDENCE",
                "current": bp.get("min_confidence"),
                "suggested": bestp.get("min_confidence"),
                "impact": f"Confidence floor adjustment",
                "reasoning": f"Confidence of {bestp.get('min_confidence')} "
                           f"with WR={best_result.win_rate:.1f}%",
                "confidence": "high" if best_result.total_signals >= 40 else "medium",
            })

        if bestp.get("up_confidence_premium") != bp.get("up_confidence_premium"):
            recommendations.append({
                "param": "UP_CONFIDENCE_PREMIUM",
                "current": bp.get("up_confidence_premium"),
                "suggested": bestp.get("up_confidence_premium"),
                "impact": f"UP direction premium adjustment",
                "reasoning": f"UP premium of {bestp.get('up_confidence_premium')} "
                           f"optimizes directional bias filter",
                "confidence": "medium",
            })

        # Weight comparison with exact values
        best_weights = bestp.get("weights", {})
        current_weights = bp.get("weights", {})
        weight_changes = []
        for ind in set(list(best_weights.keys()) + list(current_weights.keys())):
            curr_w = current_weights.get(ind, 0)
            best_w = best_weights.get(ind, 0)
            if abs(curr_w - best_w) > 0.05:
                weight_changes.append({
                    "indicator": ind,
                    "current": round(curr_w, 3),
                    "suggested": round(best_w, 3),
                    "change": f"{'+' if best_w > curr_w else ''}{best_w - curr_w:.2f}",
                })

        if weight_changes:
            recommendations.append({
                "param": "WEIGHTS",
                "current": current_weights,
                "suggested": best_weights,
                "changes": weight_changes,
                "impact": f"Weight rebalancing improves WR by {best_result.win_rate - baseline_wr:.1f}pp",
                "reasoning": f"Specific indicator weight changes based on "
                           f"{best_result.total_signals} trade replay",
                "confidence": "medium",
            })

    # Indicator observations
    if baseline.indicator_contributions:
        for ind, stats in baseline.indicator_contributions.items():
            acc = stats.get("accuracy", 0)
            votes = stats.get("votes", 0)
            if acc < 0.45 and votes >= 10:
                recommendations.append({
                    "param": f"WEIGHT_{ind.upper()}",
                    "current": "enabled",
                    "suggested": "disable_or_reduce",
                    "impact": f"{ind} accuracy is {acc:.1%} across {votes} votes",
                    "reasoning": f"Below coin-flip accuracy — this indicator adds noise",
                    "confidence": "high" if votes >= 30 else "medium",
                })

    # Regime breakdown observations
    if baseline.signals_by_regime:
        for regime_label, stats in baseline.signals_by_regime.items():
            w = stats.get("wins", 0)
            l = stats.get("losses", 0)
            total = w + l
            if total >= 5:
                wr = w / total * 100
                if wr < 40:
                    recommendations.append({
                        "param": f"REGIME_{regime_label.upper()}",
                        "current": f"{wr:.0f}% WR ({total} trades)",
                        "suggested": "tighten_filters",
                        "impact": f"Poor performance in {regime_label} regime",
                        "reasoning": f"{regime_label} regime has {wr:.0f}% WR across {total} trades",
                        "confidence": "medium" if total >= 10 else "low",
                    })

    # General data sufficiency
    if baseline.total_signals < 20:
        recommendations.append({
            "param": "DATA",
            "current": baseline.total_signals,
            "suggested": "more_trades_needed",
            "impact": "Insufficient data for reliable backtesting",
            "reasoning": f"Only {baseline.total_signals} qualifying trades. "
                       f"Need 20+ for statistical significance.",
            "confidence": "high",
        })

    output = {
        "recommendations": recommendations,
        "best_win_rate": round(best_result.win_rate, 1) if scored else 0,
        "baseline_win_rate": round(baseline_wr, 1),
        "improvement": round(best_result.win_rate - baseline_wr, 1) if scored else 0,
        "best_params": best_result.params if scored else {},
        "updated": _now_et(),
    }
    (DATA_DIR / "quant_recommendations.json").write_text(json.dumps(output, indent=2))
    log.info("Wrote quant_recommendations.json (%d recommendations)", len(recommendations))


def write_live_params(
    baseline: BacktestResult,
    best: BacktestResult,
    wf_test_wr: float,
    wf_overfit_drop: float,
) -> bool:
    """Write validated optimal params to quant_live_params.json for Garves to pick up.

    Safety rails — only writes if ALL conditions pass:
    1. Best WR > baseline WR + 2pp (meaningful improvement)
    2. Walk-forward OOS WR > 50% (strategy works on unseen data)
    3. Overfit drop < 5pp (not just fitting noise)
    4. 40+ trades analyzed (statistical significance)

    Returns True if params were written, False if safety check failed.
    """
    improvement = best.win_rate - baseline.win_rate
    min_trades = 40

    # Safety checks
    if best.total_signals < min_trades:
        log.info("Live params: skip — only %d signals (need %d)", best.total_signals, min_trades)
        return False
    if improvement < 2.0:
        log.info("Live params: skip — improvement %.1fpp < 2pp threshold", improvement)
        return False
    if wf_test_wr < 50.0:
        log.info("Live params: skip — walk-forward OOS WR %.1f%% < 50%%", wf_test_wr)
        return False
    if wf_overfit_drop > 5.0:
        log.info("Live params: skip — overfit drop %.1fpp > 5pp", wf_overfit_drop)
        return False

    # Extract the tunable params from best result
    bp = best.params
    live_params = {}

    # Only override params that differ from baseline
    base_p = baseline.params
    if bp.get("min_confidence") != base_p.get("min_confidence"):
        live_params["min_confidence"] = bp["min_confidence"]
    if bp.get("up_confidence_premium") != base_p.get("up_confidence_premium"):
        live_params["up_confidence_premium"] = bp["up_confidence_premium"]
    if bp.get("min_edge_absolute") and bp.get("min_edge_absolute") != base_p.get("min_edge_absolute"):
        live_params["min_edge_absolute"] = bp["min_edge_absolute"]
    if bp.get("min_consensus") and bp.get("min_consensus") != base_p.get("min_consensus"):
        # Quant optimizes min_consensus directly; map to consensus_floor for signals.py
        live_params["consensus_floor"] = bp["min_consensus"]

    if not live_params:
        log.info("Live params: skip — best params identical to baseline")
        return False

    output = {
        "params": live_params,
        "validation": {
            "walk_forward_passed": True,
            "baseline_wr": round(baseline.win_rate, 1),
            "best_wr": round(best.win_rate, 1),
            "improvement_pp": round(improvement, 1),
            "wf_oos_wr": round(wf_test_wr, 1),
            "overfit_drop": round(wf_overfit_drop, 1),
            "trades_analyzed": best.total_signals,
            "best_label": best.label,
        },
        "applied_at": _now_et(),
    }

    import os
    tmp = (DATA_DIR / "quant_live_params.json.tmp")
    try:
        DATA_DIR.mkdir(exist_ok=True)
        with open(tmp, "w") as f:
            json.dump(output, indent=2, fp=f)
        os.replace(str(tmp), str(DATA_DIR / "quant_live_params.json"))
        log.info(
            "LIVE PARAMS UPDATED: %s (WR %.1f%% → %.1f%%, OOS %.1f%%, overfit %.1fpp)",
            live_params, baseline.win_rate, best.win_rate, wf_test_wr, wf_overfit_drop,
        )

        # Publish event for dashboard visibility
        try:
            import sys
            _shared = str(Path.home() / "shared")
            if _shared not in sys.path:
                sys.path.insert(0, _shared)
            from events import publish
            publish(
                agent="quant",
                event_type="live_params_updated",
                severity="warning",
                summary=f"Quant auto-applied params: WR {baseline.win_rate:.1f}% → {best.win_rate:.1f}% "
                        f"(+{improvement:.1f}pp, OOS {wf_test_wr:.1f}%, overfit {wf_overfit_drop:.1f}pp)",
                data={"params": live_params, "validation": output["validation"]},
            )
        except Exception:
            pass

        return True
    except Exception:
        log.exception("Failed to write quant_live_params.json")
        return False


def write_hawk_review(hawk_trades: list[dict]) -> None:
    """Write data/quant_hawk_review.json — Hawk trade calibration analysis."""
    if not hawk_trades:
        output = {"trades": [], "summary": {}, "updated": _now_et()}
        (DATA_DIR / "quant_hawk_review.json").write_text(json.dumps(output, indent=2))
        return

    resolved = [t for t in hawk_trades if t.get("resolved")]
    wins = sum(1 for t in resolved if t.get("won"))
    losses = len(resolved) - wins

    output = {
        "trades": resolved[-20:],  # last 20
        "summary": {
            "total": len(resolved),
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / len(resolved) * 100, 1) if resolved else 0,
            "open_bets": len(hawk_trades) - len(resolved),
        },
        "updated": _now_et(),
    }
    (DATA_DIR / "quant_hawk_review.json").write_text(json.dumps(output, indent=2))
    log.info("Wrote quant_hawk_review.json (%d Hawk trades reviewed)", len(resolved))


def publish_events(baseline: BacktestResult,
                   scored: list[tuple[float, BacktestResult]]) -> None:
    """Publish to shared event bus."""
    try:
        import sys
        _shared = str(Path.home() / "shared")
        if _shared not in sys.path:
            sys.path.insert(0, _shared)
        from events import publish

        best_score, best = scored[0] if scored else (0, baseline)

        publish(
            agent="quant",
            event_type="backtest_completed",
            severity="info",
            summary=f"Quant backtest: {len(scored)} combos tested. "
                   f"Baseline WR={baseline.win_rate:.1f}%, "
                   f"Best WR={best.win_rate:.1f}%",
            data={
                "baseline_wr": baseline.win_rate,
                "best_wr": best.win_rate,
                "combos_tested": len(scored),
                "best_label": best.label,
                "baseline_avg_edge": round(baseline.avg_edge * 100, 2),
                "filter_reasons": baseline.filter_reasons,
            },
        )

        if scored and best.win_rate > baseline.win_rate + 5:
            publish(
                agent="quant",
                event_type="recommendation_generated",
                severity="warning",
                summary=f"Quant found {best.win_rate - baseline.win_rate:.1f}pp improvement: "
                       f"WR {baseline.win_rate:.1f}% -> {best.win_rate:.1f}%",
                data={
                    "improvement_pp": round(best.win_rate - baseline.win_rate, 1),
                    "best_params": best.params,
                },
            )
    except Exception as e:
        log.warning("Failed to publish events: %s", e)
