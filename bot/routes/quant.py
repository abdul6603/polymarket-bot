"""Quant (Strategy Alchemist) routes: /api/quant/*"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from flask import Blueprint, jsonify

log = logging.getLogger(__name__)
quant_bp = Blueprint("quant", __name__)

DATA_DIR = Path(__file__).parent.parent.parent / "data"
STATUS_FILE = DATA_DIR / "quant_status.json"
RESULTS_FILE = DATA_DIR / "quant_results.json"
RECS_FILE = DATA_DIR / "quant_recommendations.json"
HAWK_REVIEW_FILE = DATA_DIR / "quant_hawk_review.json"
WF_FILE = DATA_DIR / "quant_walk_forward.json"
ANALYTICS_FILE = DATA_DIR / "quant_analytics.json"
LIVE_PARAMS_FILE = DATA_DIR / "quant_live_params.json"
TRADE_STUDIES_FILE = DATA_DIR / "quant_trade_studies.jsonl"
MINI_OPT_FILE = DATA_DIR / "quant_mini_opt.json"

_run_lock = threading.Lock()
_run_running = False
_run_progress = {"step": "", "detail": "", "pct": 0, "done": False, "ts": 0}


def _set_progress(step: str, detail: str = "", pct: int = 0, done: bool = False):
    global _run_progress
    _run_progress = {"step": step, "detail": detail, "pct": pct, "done": done, "ts": time.time()}


def _load_json(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return {}


@quant_bp.route("/api/quant")
def api_quant_status():
    """Quant status + summary."""
    status = _load_json(STATUS_FILE)
    results = _load_json(RESULTS_FILE)
    recs = _load_json(RECS_FILE)

    baseline = results.get("baseline", {})
    top = results.get("top_results", [])
    best = top[0] if top else {}

    return jsonify({
        "running": status.get("running", False),
        "cycle": status.get("cycle", 0),
        "last_run": status.get("last_run", "Never"),
        "mode": status.get("mode", "historical_replay"),
        "trade_count": status.get("trade_count", 0),
        "candle_counts": status.get("candle_counts", {}),
        "total_combos_tested": status.get("total_combos_tested", 0),
        "baseline_win_rate": baseline.get("win_rate", 0),
        "best_win_rate": best.get("win_rate", 0),
        "improvement": recs.get("improvement", 0),
        "baseline_signals": baseline.get("total_signals", 0),
        "baseline_avg_edge": status.get("baseline_avg_edge", 0),
        "filter_reasons": status.get("filter_reasons", {}),
        "best_signals": best.get("total_signals", 0),
        "recommendations_count": len(recs.get("recommendations", [])),
    })


@quant_bp.route("/api/quant/results")
def api_quant_results():
    """Full backtest results (top 20 + sensitivity grid)."""
    data = _load_json(RESULTS_FILE)
    return jsonify(data or {"baseline": {}, "top_results": [], "sensitivity": {}, "updated": ""})


@quant_bp.route("/api/quant/recommendations")
def api_quant_recommendations():
    """Parameter change suggestions for Garves."""
    data = _load_json(RECS_FILE)
    return jsonify(data or {"recommendations": [], "updated": ""})


@quant_bp.route("/api/quant/params")
def api_quant_params():
    """Current live params vs optimal side-by-side."""
    results = _load_json(RESULTS_FILE)
    baseline = results.get("baseline", {})
    top = results.get("top_results", [])
    best = top[0] if top else {}

    # Load current live params
    try:
        from bot.signals import (
            WEIGHTS, CONSENSUS_RATIO, CONSENSUS_FLOOR, MIN_CONFIDENCE,
            UP_CONFIDENCE_PREMIUM, MIN_EDGE_ABSOLUTE, MIN_EDGE_BY_TF,
        )
        current = {
            "min_consensus": f"{CONSENSUS_RATIO:.0%} (floor={CONSENSUS_FLOOR})",
            "min_confidence": MIN_CONFIDENCE,
            "up_confidence_premium": UP_CONFIDENCE_PREMIUM,
            "min_edge_absolute": MIN_EDGE_ABSOLUTE,
            "min_edge_by_tf": dict(MIN_EDGE_BY_TF),
            "weights": dict(WEIGHTS),
        }
    except Exception:
        current = {}

    return jsonify({
        "current": current,
        "current_performance": {
            "win_rate": baseline.get("win_rate", 0),
            "signals": baseline.get("total_signals", 0),
            "score": baseline.get("score", 0),
        },
        "best": best.get("params", {}),
        "best_performance": {
            "win_rate": best.get("win_rate", 0),
            "signals": best.get("total_signals", 0),
            "score": best.get("score", 0),
        },
        "updated": results.get("updated", ""),
    })


@quant_bp.route("/api/quant/analytics")
def api_quant_analytics():
    """Kelly sizing, indicator diversity, strategy decay."""
    data = _load_json(ANALYTICS_FILE)
    return jsonify(data or {"kelly": {}, "diversity": {}, "decay": {}, "updated": ""})


@quant_bp.route("/api/quant/live-params")
def api_quant_live_params():
    """Current auto-applied param overrides from Quant validation."""
    data = _load_json(LIVE_PARAMS_FILE)
    if not data:
        return jsonify({"active": False, "params": {}, "validation": {}})
    return jsonify({
        "active": True,
        "params": data.get("params", {}),
        "validation": data.get("validation", {}),
        "applied_at": data.get("applied_at", ""),
    })


@quant_bp.route("/api/quant/walk-forward")
def api_quant_walk_forward():
    """Walk-forward validation results + bootstrap confidence intervals."""
    data = _load_json(WF_FILE)
    return jsonify(data or {"walk_forward": {}, "confidence_interval": {}, "updated": ""})


@quant_bp.route("/api/quant/trade-learning")
def api_quant_trade_learning():
    """Per-trade learning: recent studies + mini-opt results + indicator accuracy."""
    # Load recent trade studies
    studies = []
    if TRADE_STUDIES_FILE.exists():
        try:
            with open(TRADE_STUDIES_FILE) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        studies.append(json.loads(line))
        except Exception:
            pass

    # Load mini-opt results
    mini_opt = _load_json(MINI_OPT_FILE)

    # Compute aggregate stats from studies
    total = len(studies)
    wins = sum(1 for s in studies if s.get("won"))
    losses = total - wins
    avg_ind_acc = 0.0
    correctly_filtered = 0
    indicator_stats = {}  # per-indicator correct/wrong counts

    for s in studies:
        avg_ind_acc += s.get("indicator_accuracy", 0)
        if s.get("correctly_filtered"):
            correctly_filtered += 1
        for ind in s.get("correct_indicators", []):
            if ind not in indicator_stats:
                indicator_stats[ind] = {"correct": 0, "wrong": 0}
            indicator_stats[ind]["correct"] += 1
        for ind in s.get("wrong_indicators", []):
            if ind not in indicator_stats:
                indicator_stats[ind] = {"correct": 0, "wrong": 0}
            indicator_stats[ind]["wrong"] += 1

    if total:
        avg_ind_acc /= total

    # Build indicator accuracy chips
    indicator_chips = []
    for ind, stats in sorted(indicator_stats.items()):
        ind_total = stats["correct"] + stats["wrong"]
        if ind_total > 0:
            acc = stats["correct"] / ind_total
            indicator_chips.append({
                "name": ind,
                "accuracy": round(acc * 100, 1),
                "votes": ind_total,
            })
    indicator_chips.sort(key=lambda x: x["accuracy"], reverse=True)

    return jsonify({
        "total_studied": total,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / total * 100, 1) if total else 0,
        "avg_indicator_accuracy": round(avg_ind_acc * 100, 1),
        "filter_correctness": round(correctly_filtered / total * 100, 1) if total else 0,
        "indicator_chips": indicator_chips,
        "recent_studies": studies[-10:][::-1],  # last 10, newest first
        "mini_opt": mini_opt,
        "mini_opt_active": bool(mini_opt),
    })


@quant_bp.route("/api/quant/run", methods=["POST"])
def api_quant_run():
    """Trigger manual backtest in background thread."""
    global _run_running

    if _run_running:
        return jsonify({"success": False, "message": "Backtest already running"})

    def _run_backtest():
        global _run_running
        try:
            _run_running = True
            _set_progress("Loading data", "Reading trades and candles...", 5)

            from quant.main import run_single_backtest
            summary = run_single_backtest(progress_callback=_set_progress)

            applied_msg = " | Params AUTO-APPLIED" if summary.get("params_auto_applied") else ""
            _set_progress(
                "Complete",
                f"Baseline {summary['baseline_wr']}% | Best {summary['best_wr']}% | "
                f"{summary['combos_tested']} combos{applied_msg}",
                100,
                done=True,
            )
        except Exception as e:
            log.exception("Manual backtest failed")
            _set_progress("Error", str(e), 0, done=True)
        finally:
            _run_running = False

    thread = threading.Thread(target=_run_backtest, daemon=True)
    thread.start()
    return jsonify({"success": True, "message": "Backtest started"})


@quant_bp.route("/api/quant/run-status")
def api_quant_run_status():
    """Poll backtest progress."""
    return jsonify({"running": _run_running, **_run_progress})


@quant_bp.route("/api/quant/smart-actions")
def api_quant_smart_actions():
    """Dynamic smart actions based on Quant's current state and data."""
    actions = []
    status = _load_json(STATUS_FILE)
    results = _load_json(RESULTS_FILE)
    recs = _load_json(RECS_FILE)
    wf = _load_json(WF_FILE)
    analytics = _load_json(ANALYTICS_FILE)

    combos = status.get("total_combos_tested", 0)
    top = results.get("top_results", [])
    best = top[0] if top else {}
    baseline = results.get("baseline", {})
    recommendations = recs.get("recommendations", [])
    wf_data = wf.get("walk_forward", {})

    # 1. Suggest running a backtest if none done yet or stale
    if combos == 0:
        actions.append({
            "id": "quant_first_backtest",
            "title": "Run First Backtest",
            "description": "No backtests have been run yet. Trigger one to find optimal parameters.",
            "priority": "high", "agent": "quant", "source": "quant_state",
        })
    elif status.get("last_run"):
        try:
            from datetime import datetime
            last = datetime.fromisoformat(status["last_run"].replace("Z", "+00:00"))
            age_hours = (datetime.now(last.tzinfo) - last).total_seconds() / 3600
            if age_hours > 24:
                actions.append({
                    "id": "quant_stale_backtest",
                    "title": "Re-run Backtest (Stale Data)",
                    "description": f"Last backtest was {int(age_hours)}h ago. New trades may shift optimal params.",
                    "priority": "medium", "agent": "quant", "source": "quant_state",
                })
        except Exception:
            pass

    # 2. If there are unreviewed recommendations
    if recommendations:
        actions.append({
            "id": "quant_review_recs",
            "title": f"Review {len(recommendations)} Recommendations",
            "description": "Quant found parameter improvements. Review and decide whether to apply.",
            "priority": "high", "agent": "quant", "source": "quant_recs",
        })

    # 3. If best WR significantly higher than baseline
    if best and baseline:
        best_wr = best.get("win_rate", 0)
        base_wr = baseline.get("win_rate", 0)
        delta = best_wr - base_wr
        if delta > 3:
            actions.append({
                "id": "quant_apply_params",
                "title": f"Potential +{delta:.1f}% WR Improvement",
                "description": f"Best found: {best_wr:.1f}% vs current {base_wr:.1f}%. Consider applying optimized params to Garves.",
                "priority": "high", "agent": "quant", "source": "quant_results",
            })

    # 4. Walk-forward validation needed
    if not wf_data.get("folds") and combos > 0:
        actions.append({
            "id": "quant_need_wf",
            "title": "Walk-Forward Validation Needed",
            "description": "No walk-forward results found. Run WF to check for overfitting.",
            "priority": "medium", "agent": "quant", "source": "quant_wf",
        })

    # 5. Check for overfitting
    if wf_data.get("folds"):
        avg_oos = wf_data.get("avg_oos_wr", 0)
        if best and best.get("win_rate", 0) - avg_oos > 10:
            actions.append({
                "id": "quant_overfit_warning",
                "title": "Overfitting Warning",
                "description": f"In-sample WR ({best.get('win_rate', 0):.1f}%) is {best.get('win_rate', 0) - avg_oos:.1f}% higher than OOS ({avg_oos:.1f}%). Results may be overfit.",
                "priority": "high", "agent": "quant", "source": "quant_wf",
            })

    # 6. Diversity analysis
    diversity = analytics.get("diversity", {})
    if diversity.get("diversity_score", 1) < 0.5:
        actions.append({
            "id": "quant_low_diversity",
            "title": "Low Indicator Diversity",
            "description": "Indicators may be highly correlated. Consider adding diverse signal sources.",
            "priority": "medium", "agent": "quant", "source": "quant_analytics",
        })

    # 7. Kelly sizing suggestion
    kelly = analytics.get("kelly", {})
    if kelly.get("half_kelly") and kelly["half_kelly"] > 0.15:
        actions.append({
            "id": "quant_kelly_sizing",
            "title": f"Kelly Suggests {kelly['half_kelly']*100:.0f}% Position Size",
            "description": "Half-Kelly criterion suggests a meaningful position. Review if Garves stake matches.",
            "priority": "low", "agent": "quant", "source": "quant_analytics",
        })

    # Pull from Atlas improvements if available
    try:
        atlas_imp_file = Path.home() / "atlas" / "data" / "improvements.json"
        if atlas_imp_file.exists():
            imp_data = json.loads(atlas_imp_file.read_text())
            quant_imps = imp_data.get("quant", [])
            for imp in quant_imps[:3]:
                actions.append({
                    "id": f"atlas_quant_{hash(imp.get('title', '')) % 10000}",
                    "title": imp.get("title", "Atlas Suggestion"),
                    "description": imp.get("description", "")[:200],
                    "priority": imp.get("priority", "medium"),
                    "agent": "quant", "source": "atlas_kb",
                })
    except Exception:
        pass

    return jsonify({"actions": actions, "count": len(actions)})
