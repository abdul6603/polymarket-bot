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
BRIEFING_FILE = DATA_DIR / "hawk_briefing.json"
SOREN_OPPS_FILE = DATA_DIR / "soren_opportunities.json"
PNL_FILE = DATA_DIR / "brotherhood_pnl.json"

_scan_lock = threading.Lock()
_scan_running = False
_scan_progress = {"step": "", "detail": "", "pct": 0, "done": False, "ts": 0}


def _set_progress(step: str, detail: str = "", pct: int = 0, done: bool = False):
    global _scan_progress
    _scan_progress = {"step": step, "detail": detail, "pct": pct, "done": done, "ts": time.time()}


def _load_status() -> dict:
    if STATUS_FILE.exists():
        try:
            return json.loads(STATUS_FILE.read_text())
        except Exception:
            pass
    return {"running": False}


@viper_bp.route("/api/viper")
def api_viper():
    """Full Viper status with Hawk briefing info."""
    status = _load_status()

    # Add briefing info
    briefing_info = {"active": False, "age_minutes": None, "briefed_markets": 0}
    if BRIEFING_FILE.exists():
        try:
            bf = json.loads(BRIEFING_FILE.read_text())
            age = time.time() - bf.get("generated_at", 0)
            briefing_info["active"] = age < 7200
            briefing_info["age_minutes"] = round(age / 60, 1)
            briefing_info["briefed_markets"] = bf.get("briefed_markets", 0)
        except Exception:
            pass

    return jsonify({"summary": status, "status": status, "hawk_briefing": briefing_info})


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


@viper_bp.route("/api/viper/soren-opportunities")
def api_viper_soren_opportunities():
    """Soren opportunity feed — brand deals, affiliates, trending content."""
    if SOREN_OPPS_FILE.exists():
        try:
            data = json.loads(SOREN_OPPS_FILE.read_text())
            return jsonify(data)
        except Exception:
            pass
    return jsonify({"opportunities": [], "count": 0, "updated": 0, "types": {}})


# ── Brand Channel Endpoints ─────────────────────────────────────────


@viper_bp.route("/api/viper/brand-channel")
def api_viper_brand_channel():
    """Brand Channel pipeline — messages + stats."""
    try:
        from shared.brand_channel import get_messages, get_channel_stats
        status_filter = request.args.get("status")
        limit = int(request.args.get("limit", 50))
        messages = get_messages(status=status_filter, limit=limit)
        stats = get_channel_stats()
        return jsonify({"messages": messages, "stats": stats})
    except Exception as e:
        log.exception("Brand channel read failed")
        return jsonify({"messages": [], "stats": {}, "error": str(e)[:200]})


@viper_bp.route("/api/viper/brand-channel/<msg_id>/approve", methods=["POST"])
def api_viper_brand_approve(msg_id):
    """Jordan approves a needs-review opportunity."""
    try:
        from shared.brand_channel import update_status
        result = update_status(msg_id, "approved", by="jordan")
        if result:
            return jsonify({"success": True, "message": result})
        return jsonify({"success": False, "error": "Message not found"}), 404
    except Exception as e:
        log.exception("Brand channel approve failed")
        return jsonify({"success": False, "error": str(e)[:200]})


@viper_bp.route("/api/viper/brand-channel/<msg_id>/reject", methods=["POST"])
def api_viper_brand_reject(msg_id):
    """Jordan rejects a needs-review opportunity."""
    try:
        from shared.brand_channel import update_status
        body = request.get_json(silent=True) or {}
        reason = body.get("reason", "")
        result = update_status(msg_id, "rejected", by="jordan", notes=reason)
        if result:
            return jsonify({"success": True, "message": result})
        return jsonify({"success": False, "error": "Message not found"}), 404
    except Exception as e:
        log.exception("Brand channel reject failed")
        return jsonify({"success": False, "error": str(e)[:200]})


@viper_bp.route("/api/viper/brand-channel/<msg_id>/plan", methods=["POST"])
def api_viper_brand_plan(msg_id):
    """Lisa marks an approved opportunity as content_planned."""
    try:
        from shared.brand_channel import update_status
        body = request.get_json(silent=True) or {}
        notes = body.get("notes", "")
        result = update_status(msg_id, "content_planned", by="lisa", notes=notes)
        if result:
            return jsonify({"success": True, "message": result})
        return jsonify({"success": False, "error": "Message not found"}), 404
    except Exception as e:
        log.exception("Brand channel plan failed")
        return jsonify({"success": False, "error": str(e)[:200]})


@viper_bp.route("/api/viper/pnl")
def api_viper_pnl():
    """Brotherhood P&L — revenue vs costs."""
    if PNL_FILE.exists():
        try:
            return jsonify(json.loads(PNL_FILE.read_text()))
        except Exception:
            pass
    return jsonify({"date": "", "revenue": {}, "costs": {}, "net_daily": 0, "trend": "unknown"})


@viper_bp.route("/api/viper/digests")
def api_viper_digests():
    """All agent digest files with freshness info."""
    agents = ["garves", "hawk", "soren", "shelby", "atlas"]
    digests = {}
    for agent in agents:
        path = DATA_DIR / f"viper_{agent}_digest.json"
        if path.exists():
            try:
                d = json.loads(path.read_text())
                age = time.time() - d.get("generated_at", 0)
                d["fresh"] = age < 1800  # 30 min freshness
                d["age_minutes"] = round(age / 60, 1)
                digests[agent] = d
            except Exception:
                digests[agent] = {"fresh": False, "error": "parse_failed"}
        else:
            digests[agent] = {"fresh": False, "items": [], "item_count": 0}
    return jsonify(digests)


@viper_bp.route("/api/viper/anomalies")
def api_viper_anomalies():
    """Current anomaly alerts."""
    status = _load_status()
    anomalies = status.get("anomalies", [])
    return jsonify({"anomalies": anomalies, "count": len(anomalies)})


@viper_bp.route("/api/viper/scan-status")
def api_viper_scan_status():
    """Poll scan progress."""
    return jsonify({"scanning": _scan_running, **_scan_progress})


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
            _set_progress("Scanning sources...", "Fetching news, Reddit, market activity", 15)

            from viper.config import ViperConfig
            from viper.main import run_single_scan
            cfg = ViperConfig()

            _set_progress("Running intelligence scan", "Tavily news + Reddit + Polymarket activity", 40)
            result = run_single_scan(cfg)

            intel_count = result.get("intel_count", 0)
            matched = result.get("matched", 0)
            soren_count = result.get("soren_opportunities", 0)
            soren_note = f" | {soren_count} Soren opps" if soren_count and soren_count > 0 else ""
            _set_progress(
                "Scan complete",
                f"Found {intel_count} intel items | {matched} market matches{soren_note}",
                100,
                done=True,
            )
            log.info("Viper scan triggered: %d items, %d matched", intel_count, matched)

        except Exception as e:
            log.exception("Triggered Viper scan failed")
            _set_progress("Scan failed", str(e)[:200], 0, done=True)
        finally:
            _scan_running = False

    with _scan_lock:
        _set_progress("Starting scan...", "Initializing intelligence sources", 5)
        thread = threading.Thread(target=_run_scan, daemon=True)
        thread.start()

    return jsonify({"success": True, "message": "Scan started"})
