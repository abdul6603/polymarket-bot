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
            WEIGHTS, MIN_CONSENSUS, MIN_CONFIDENCE,
            UP_CONFIDENCE_PREMIUM, MIN_EDGE_ABSOLUTE, MIN_EDGE_BY_TF,
        )
        current = {
            "min_consensus": MIN_CONSENSUS,
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


@quant_bp.route("/api/quant/walk-forward")
def api_quant_walk_forward():
    """Walk-forward validation results + bootstrap confidence intervals."""
    data = _load_json(WF_FILE)
    return jsonify(data or {"walk_forward": {}, "confidence_interval": {}, "updated": ""})


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

            _set_progress(
                "Complete",
                f"Baseline {summary['baseline_wr']}% | Best {summary['best_wr']}% | "
                f"{summary['combos_tested']} combos",
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
