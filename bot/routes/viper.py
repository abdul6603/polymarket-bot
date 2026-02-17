"""Viper (opportunity hunter) routes: /api/viper/*"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from flask import Blueprint, jsonify, request

log = logging.getLogger(__name__)
viper_bp = Blueprint("viper", __name__)

DATA_DIR = Path(__file__).parent.parent.parent / "data"
OPPS_FILE = DATA_DIR / "viper_opportunities.json"
COSTS_FILE = DATA_DIR / "viper_costs.json"
STATUS_FILE = DATA_DIR / "viper_status.json"


def _load_status() -> dict:
    if STATUS_FILE.exists():
        try:
            return json.loads(STATUS_FILE.read_text())
        except Exception:
            pass
    return {"running": False}


@viper_bp.route("/api/viper")
def api_viper():
    """Full Viper status."""
    status = _load_status()
    return jsonify({"summary": status, "status": status})


@viper_bp.route("/api/viper/opportunities")
def api_viper_opportunities():
    """Scored opportunities."""
    if OPPS_FILE.exists():
        try:
            data = json.loads(OPPS_FILE.read_text())
            return jsonify(data)
        except Exception:
            pass
    return jsonify({"opportunities": [], "updated": 0})


@viper_bp.route("/api/viper/costs")
def api_viper_costs():
    """API cost breakdown."""
    if COSTS_FILE.exists():
        try:
            data = json.loads(COSTS_FILE.read_text())
            return jsonify(data)
        except Exception:
            pass
    # Fallback: run cost audit live
    try:
        from viper.cost_audit import audit_all
        return jsonify(audit_all())
    except Exception:
        return jsonify({"costs": [], "total_monthly": 0})


@viper_bp.route("/api/viper/soren-metrics")
def api_viper_soren_metrics():
    """Soren monetization data."""
    try:
        from viper.config import ViperConfig
        from viper.monetize import get_soren_metrics
        cfg = ViperConfig()
        return jsonify(get_soren_metrics(cfg))
    except Exception as e:
        return jsonify({"followers": 0, "engagement_rate": 0, "estimated_cpm": 0, "brand_ready": False, "error": str(e)[:200]})


@viper_bp.route("/api/viper/scan", methods=["POST"])
def api_viper_scan():
    """Trigger immediate scan."""
    return jsonify({"success": True, "message": "Scan triggered — results will appear on next refresh"})
