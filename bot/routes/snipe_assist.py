"""Snipe Assist API — timing recommendations, accuracy, and overrides."""
from __future__ import annotations

import json
import time
from pathlib import Path

from flask import Blueprint, jsonify, request

snipe_assist_bp = Blueprint("snipe_assist", __name__)

ASSIST_FILE = Path(__file__).parent.parent.parent / "data" / "snipe_assist.json"
OVERRIDE_FILE = Path(__file__).parent.parent.parent / "data" / "snipe_assist_override.json"


@snipe_assist_bp.route("/api/snipe-assist/status")
def snipe_assist_status():
    """Read current timing recommendation from snipe_assist.json."""
    try:
        if not ASSIST_FILE.exists():
            return jsonify({"active": False, "message": "No timing data yet"})
        data = json.loads(ASSIST_FILE.read_text())
        age = time.time() - data.get("timestamp", 0)
        data["age_s"] = round(age, 1)
        data["stale"] = age > 120
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@snipe_assist_bp.route("/api/snipe-assist/accuracy")
def snipe_assist_accuracy():
    """Query self-learning accuracy with optional filters."""
    try:
        from bot.snipe.timing_learner import TimingLearner
        learner = TimingLearner()
        agent = request.args.get("agent")
        direction = request.args.get("direction")
        timeframe = request.args.get("timeframe")
        regime = request.args.get("regime")
        window = int(request.args.get("window", 50))
        acc = learner.get_accuracy(agent, direction, timeframe, regime, window)
        optimal = learner.get_optimal_timing_range(direction, window)
        status = learner.get_status()
        return jsonify({"accuracy": acc, "optimal_timing": optimal, "status": status})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@snipe_assist_bp.route("/api/snipe-assist/override", methods=["POST"])
def snipe_assist_override():
    """Set a manual override (force_execute, force_skip, clear)."""
    try:
        body = request.get_json(force=True)
        action = body.get("action", "")

        if action == "clear":
            OVERRIDE_FILE.unlink(missing_ok=True)
            return jsonify({"status": "ok", "message": "Override cleared"})

        if action not in ("force_execute", "force_skip"):
            return jsonify({"error": "action must be force_execute, force_skip, or clear"}), 400

        # Map to internal action names
        mapped = "auto_execute" if action == "force_execute" else "auto_skip"
        duration = int(body.get("duration_s", 300))  # Default 5 min

        override_data = {
            "action": mapped,
            "expires_at": time.time() + duration,
            "set_by": "dashboard",
            "set_at": time.time(),
        }
        OVERRIDE_FILE.parent.mkdir(parents=True, exist_ok=True)
        OVERRIDE_FILE.write_text(json.dumps(override_data, indent=2))
        return jsonify({"status": "ok", "override": override_data})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@snipe_assist_bp.route("/api/resolution-scalper/status")
def resolution_scalper_status():
    """Resolution Scalper status, calibration, stats, and recent history."""
    try:
        from bot.snipe.resolution_learner import ResolutionLearner
        learner = ResolutionLearner()
        stats = learner.get_stats()
        recent = learner.get_recent(20)
        return jsonify({
            "engine": "resolution_scalper",
            "stats": stats,
            "recent": recent,
        })
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@snipe_assist_bp.route("/api/snipe-assist/thresholds", methods=["POST"])
def snipe_assist_thresholds():
    """Adjust auto/conservative thresholds dynamically."""
    try:
        body = request.get_json(force=True)
        auto_thresh = body.get("auto")
        conservative_thresh = body.get("conservative")

        # Write to a config file so TimingAssistant picks it up
        config_file = ASSIST_FILE.parent / "snipe_assist_config.json"
        config = {}
        if config_file.exists():
            config = json.loads(config_file.read_text())

        if auto_thresh is not None:
            config["auto_threshold"] = max(50, min(100, int(auto_thresh)))
        if conservative_thresh is not None:
            config["conservative_threshold"] = max(30, min(99, int(conservative_thresh)))

        config_file.write_text(json.dumps(config, indent=2))
        return jsonify({"status": "ok", "thresholds": config})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500
