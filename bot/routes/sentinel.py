"""Robotox/Sentinel (health monitor) routes: /api/sentinel/*"""
from __future__ import annotations

from flask import Blueprint, jsonify

sentinel_bp = Blueprint("sentinel", __name__)


@sentinel_bp.route("/api/sentinel")
def api_sentinel():
    """Robotox health monitor status."""
    try:
        from sentinel.sentinel import Sentinel
        sentinel_agent = Sentinel()
        return jsonify(sentinel_agent.get_status())
    except Exception as e:
        return jsonify({"status": "offline", "error": str(e)})


@sentinel_bp.route("/api/sentinel/scan", methods=["POST"])
def api_sentinel_scan():
    """Trigger a full health scan (skip notifications to avoid blocking)."""
    try:
        from sentinel.core.monitor import HealthMonitor
        monitor = HealthMonitor()
        result = monitor.scan_all(skip_notifications=True)
        return jsonify(result)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)[:500]})


@sentinel_bp.route("/api/sentinel/bugs")
def api_sentinel_bugs():
    """Get bug scan results."""
    try:
        from sentinel.sentinel import Sentinel
        sentinel_agent = Sentinel()
        return jsonify(sentinel_agent.quick_bug_scan())
    except Exception as e:
        return jsonify({"error": str(e)})


@sentinel_bp.route("/api/sentinel/fixes")
def api_sentinel_fixes():
    """Get fix history."""
    try:
        from sentinel.sentinel import Sentinel
        sentinel_agent = Sentinel()
        return jsonify({"fixes": sentinel_agent.get_fix_history()})
    except Exception as e:
        return jsonify({"error": str(e)})


@sentinel_bp.route("/api/sentinel/alerts")
def api_sentinel_alerts():
    """Get alerts."""
    try:
        from sentinel.sentinel import Sentinel
        sentinel_agent = Sentinel()
        return jsonify({"alerts": sentinel_agent.get_alerts()})
    except Exception as e:
        return jsonify({"error": str(e)})
