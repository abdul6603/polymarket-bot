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
ET = timezone(timedelta(hours=-5))


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
        "trade_count": trade_count,
        "candle_counts": candle_counts,
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

        if bestp.get("weights_hash") != bp.get("weights_hash"):
            recommendations.append({
                "param": "WEIGHTS",
                "current": "current_weights",
                "suggested": "optimized_weights",
                "impact": f"Weight rebalancing improves WR by {best_result.win_rate - baseline_wr:.1f}pp",
                "reasoning": "Weight optimization found better signal combination",
                "confidence": "medium",
            })

    # General observations
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

    output = {
        "recommendations": recommendations,
        "best_win_rate": round(best_result.win_rate, 1) if scored else 0,
        "baseline_win_rate": round(baseline_wr, 1),
        "improvement": round(best_result.win_rate - baseline_wr, 1) if scored else 0,
        "updated": _now_et(),
    }
    (DATA_DIR / "quant_recommendations.json").write_text(json.dumps(output, indent=2))
    log.info("Wrote quant_recommendations.json (%d recommendations)", len(recommendations))


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
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
        from shared.events import publish

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
