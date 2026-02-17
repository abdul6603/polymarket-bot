"""Viper (24/7 intelligence engine) routes: /api/viper/*"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from flask import Blueprint, jsonify, request

log = logging.getLogger(__name__)
viper_bp = Blueprint("viper", __name__)

DATA_DIR = Path(__file__).parent.parent.parent / "data"
OPPS_FILE = DATA_DIR / "viper_opportunities.json"
COSTS_FILE = DATA_DIR / "viper_costs.json"
STATUS_FILE = DATA_DIR / "viper_status.json"
INTEL_FILE = DATA_DIR / "viper_intel.json"

_scan_lock = threading.Lock()
_scan_running = False


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
    """Intel feed — scored intelligence items."""
    if OPPS_FILE.exists():
        try:
            data = json.loads(OPPS_FILE.read_text())
            return jsonify(data)
        except Exception:
            pass
    return jsonify({"opportunities": [], "updated": 0})


@viper_bp.route("/api/viper/intel")
def api_viper_intel():
    """Raw intelligence feed."""
    if INTEL_FILE.exists():
        try:
            data = json.loads(INTEL_FILE.read_text())
            return jsonify(data)
        except Exception:
            pass
    return jsonify({"items": [], "count": 0, "updated": 0})


@viper_bp.route("/api/viper/costs")
def api_viper_costs():
    """Live API cost breakdown — always computed fresh from real data."""
    try:
        from viper.cost_audit import audit_all
        return jsonify(audit_all())
    except Exception as e:
        log.exception("Cost audit failed")
        return jsonify({"costs": [], "total_monthly": 0, "error": str(e)[:200]})


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
    """Trigger immediate intelligence scan in background thread."""
    global _scan_running

    if _scan_running:
        return jsonify({"success": False, "message": "Scan already running"})

    def _run_scan():
        global _scan_running
        try:
            _scan_running = True
            from viper.config import ViperConfig
            from viper.main import run_single_scan
            cfg = ViperConfig()
            result = run_single_scan(cfg)
            log.info("Viper scan triggered: %d items, %d matched", result.get("intel_count", 0), result.get("matched", 0))
        except Exception:
            log.exception("Triggered Viper scan failed")
        finally:
            _scan_running = False

    with _scan_lock:
        thread = threading.Thread(target=_run_scan, daemon=True)
        thread.start()

    return jsonify({"success": True, "message": "Intelligence scan running — results in ~15 seconds"})
