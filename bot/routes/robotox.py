"""Robotox (health monitor) routes: /api/sentinel/*, /api/robotox/*"""
from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify

robotox_bp = Blueprint("robotox", __name__)

# Singleton — preserve restart counters and log watcher state across API calls
_sentinel_instance = None


def _get_sentinel():
    """Get or create the singleton Sentinel instance."""
    global _sentinel_instance
    if _sentinel_instance is None:
        from sentinel.sentinel import Sentinel
        _sentinel_instance = Sentinel()
    return _sentinel_instance


@robotox_bp.route("/api/sentinel")
def api_sentinel():
    """Robotox health monitor status."""
    try:
        return jsonify(_get_sentinel().get_status())
    except Exception as e:
        return jsonify({"status": "offline", "error": str(e)}), 500


@robotox_bp.route("/api/sentinel/scan", methods=["POST"])
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
        return jsonify({"error": str(e)[:500]}), 500


@robotox_bp.route("/api/sentinel/bugs")
def api_sentinel_bugs():
    """Get bug scan results."""
    try:
        return jsonify(_get_sentinel().quick_bug_scan())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@robotox_bp.route("/api/sentinel/fixes")
def api_sentinel_fixes():
    """Get fix history."""
    try:
        return jsonify({"fixes": _get_sentinel().get_fix_history()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@robotox_bp.route("/api/sentinel/alerts")
def api_sentinel_alerts():
    """Get alerts."""
    try:
        return jsonify({"alerts": _get_sentinel().get_alerts()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@robotox_bp.route("/api/robotox/log-alerts")
def api_robotox_log_alerts():
    """Get recent log watcher alerts (smart pattern detection)."""
    try:
        s = _get_sentinel()
        return jsonify({
            "alerts": s.get_log_watcher_alerts(),
            "patterns": s.get_log_watcher_patterns(),
        })
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@robotox_bp.route("/api/robotox/perf")
def api_robotox_perf():
    """Get current performance metrics for all agents."""
    try:
        s = _get_sentinel()
        return jsonify({
            "current": s.get_perf_current(),
            "baselines": s.get_perf_baselines(),
        })
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@robotox_bp.route("/api/robotox/dep-health")
def api_robotox_dep_health():
    """Get external dependency connectivity status."""
    try:
        s = _get_sentinel()
        return jsonify(s.get_dep_health())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@robotox_bp.route("/api/robotox/dep-health/check", methods=["POST"])
def api_robotox_dep_health_check():
    """Trigger a fresh dependency health check."""
    try:
        s = _get_sentinel()
        return jsonify(s.check_dep_health())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@robotox_bp.route("/api/robotox/intel-feed")
def api_robotox_intel_feed():
    """Get the shared intelligence feed."""
    try:
        from shared.intelligence_feed import get_all, get_stats
        return jsonify({
            "items": get_all(limit=30),
            "stats": get_stats(),
        })
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@robotox_bp.route("/api/robotox/deploy-watches")
def api_robotox_deploy_watches():
    """Get active deployment watches and rollback history."""
    try:
        s = _get_sentinel()
        return jsonify({
            "active_watches": s.get_deploy_watches(),
            "rollback_history": s.get_rollback_history(),
        })
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@robotox_bp.route("/api/robotox/correlator")
def api_robotox_correlator():
    """Get alert correlation statistics."""
    try:
        s = _get_sentinel()
        return jsonify(s.get_correlator_stats())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@robotox_bp.route("/api/robotox/dependencies")
def api_robotox_dependencies():
    """Get dependency version check report."""
    try:
        from sentinel.core.dep_checker import DependencyChecker
        checker = DependencyChecker()
        return jsonify(checker.get_latest())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@robotox_bp.route("/api/robotox/dependencies/check", methods=["POST"])
def api_robotox_dep_check():
    """Trigger a fresh dependency check."""
    try:
        from sentinel.core.dep_checker import DependencyChecker
        checker = DependencyChecker()
        return jsonify(checker.full_check())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@robotox_bp.route("/api/robotox/portfolio")
def api_robotox_portfolio():
    """V2: Portfolio correlation guard — check for conflicting positions."""
    try:
        from sentinel.core.portfolio_guard import get_portfolio_summary
        return jsonify(get_portfolio_summary())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@robotox_bp.route("/api/robotox/pnl")
def api_robotox_pnl():
    """V2: PnL impact data — revenue stats per trading agent."""
    try:
        from sentinel.core.pnl_estimator import get_agent_revenue_stats, get_llm_cost_summary
        return jsonify({
            "revenue": get_agent_revenue_stats(),
            "llm_costs_24h": get_llm_cost_summary(hours=24),
        })
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@robotox_bp.route("/api/robotox/breakers")
def api_robotox_breakers():
    """V2: Circuit breaker states for all agents."""
    try:
        s = _get_sentinel()
        return jsonify(s.self_healer.get_breaker_states())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@robotox_bp.route("/api/robotox/agent-health")
def api_robotox_agent_health():
    """V2: Agent-specific health checks."""
    try:
        from sentinel.core.agent_health import run_all_agent_checks
        issues = run_all_agent_checks()
        return jsonify({
            "issues": [
                {"agent": i.agent, "check": i.check, "severity": i.severity,
                 "message": i.message, "fix_hint": i.fix_hint}
                for i in issues
            ],
            "total": len(issues),
            "critical": sum(1 for i in issues if i.severity == "critical"),
        })
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@robotox_bp.route("/api/robotox/scorecards")
def api_robotox_scorecards():
    """V2 Phase 3: Performance scorecards for all agents."""
    try:
        s = _get_sentinel()
        return jsonify(s.get_scorecards())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@robotox_bp.route("/api/robotox/predictive")
def api_robotox_predictive():
    """V2 Phase 3: Predictive monitors — memory leaks, error acceleration."""
    try:
        s = _get_sentinel()
        return jsonify(s.get_predictive())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@robotox_bp.route("/api/robotox/quiet-hours")
def api_robotox_quiet_hours():
    """V2 Phase 3: Quiet hours status and pending digest."""
    try:
        s = _get_sentinel()
        return jsonify(s.get_quiet_hours_status())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500
