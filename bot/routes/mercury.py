"""Mercury/Lisa (social media) routes: /api/mercury/*"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from flask import Blueprint, jsonify, request

from bot.shared import (
    SOREN_QUEUE_FILE,
    MERCURY_ROOT,
    MERCURY_POSTING_LOG,
    MERCURY_ANALYTICS_FILE,
    SHELBY_ROOT_DIR,
)

mercury_bp = Blueprint("mercury", __name__)


@mercury_bp.route("/api/mercury")
def api_mercury():
    """Mercury social media manager status."""
    posting_log = []
    if MERCURY_POSTING_LOG.exists():
        try:
            with open(MERCURY_POSTING_LOG) as f:
                posting_log = json.load(f)
        except Exception:
            pass

    analytics = {}
    if MERCURY_ANALYTICS_FILE.exists():
        try:
            with open(MERCURY_ANALYTICS_FILE) as f:
                analytics = json.load(f)
        except Exception:
            pass

    outbox = []
    if SOREN_QUEUE_FILE.exists():
        try:
            with open(SOREN_QUEUE_FILE) as f:
                queue = json.load(f)
            outbox = [q for q in queue if q.get("status") == "approved"]
        except Exception:
            pass

    recent_posts = sorted(posting_log, key=lambda x: x.get("posted_at", ""), reverse=True)[:20]

    platforms = {}
    for post in posting_log:
        p = post.get("platform", "unknown")
        if p not in platforms:
            platforms[p] = {"total": 0, "last_post": ""}
        platforms[p]["total"] += 1
        pa = post.get("posted_at", "")
        if pa > platforms[p]["last_post"]:
            platforms[p]["last_post"] = pa

    # Review stats from posting log
    reviewed = [p for p in posting_log if p.get("review_score") is not None and p.get("review_score", -1) != -1]
    review_stats = {}
    if reviewed:
        scores = [p["review_score"] for p in reviewed]
        review_stats = {
            "total_reviewed": len(reviewed),
            "avg_score": round(sum(scores) / len(scores), 1),
            "passed": sum(1 for s in scores if s >= 7),
            "warned": sum(1 for s in scores if 4 <= s < 7),
            "failed": sum(1 for s in scores if s < 4),
        }

    return jsonify({
        "outbox": outbox[:20],
        "outbox_count": len(outbox),
        "recent_posts": recent_posts,
        "total_posts": len(posting_log),
        "platforms": platforms,
        "analytics_summary": analytics.get("summary", {}),
        "review_stats": review_stats,
    })


@mercury_bp.route("/api/mercury/plan")
def api_mercury_plan():
    """Mercury evolving plan and knowledge dashboard data."""
    try:
        from mercury.core.brain import MercuryBrain
        brain = MercuryBrain()
        return jsonify(brain.get_dashboard_data())
    except Exception as e:
        return jsonify({"error": str(e)})


@mercury_bp.route("/api/mercury/knowledge")
def api_mercury_knowledge():
    """Mercury full knowledge base."""
    try:
        from mercury.core.brain import MercuryBrain
        brain = MercuryBrain()
        return jsonify(brain.get_knowledge())
    except Exception as e:
        return jsonify({"error": str(e)})


@mercury_bp.route("/api/mercury/reply", methods=["POST"])
def api_mercury_reply():
    """Get reply suggestion for a comment."""
    try:
        from mercury.core.brain import MercuryBrain
        brain = MercuryBrain()
        comment = request.json.get("comment", "")
        return jsonify(brain.get_reply_suggestion(comment))
    except Exception as e:
        return jsonify({"error": str(e)})


@mercury_bp.route("/api/mercury/review", methods=["POST"])
def api_mercury_review():
    """Brand review a caption against Soren's voice."""
    try:
        from mercury.core.reviewer import BrandReviewer
        reviewer = BrandReviewer()
        data = request.json or {}
        caption = data.get("caption", "")
        platform = data.get("platform", "instagram")
        pillar = data.get("pillar", "")
        item = {"caption": caption, "pillar": pillar}
        result = reviewer.review(item, platform)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)})


@mercury_bp.route("/api/mercury/review/<item_id>", methods=["POST"])
def api_mercury_review_item(item_id):
    """Brand review a specific outbox item by ID."""
    try:
        from mercury.core.reviewer import BrandReviewer
        reviewer = BrandReviewer()
        platform = (request.json or {}).get("platform", "instagram")
        # Load the item from queue
        if not SOREN_QUEUE_FILE.exists():
            return jsonify({"error": "Queue file not found"})
        with open(SOREN_QUEUE_FILE) as f:
            queue = json.load(f)
        item = next((q for q in queue if q.get("id") == item_id), None)
        if not item:
            return jsonify({"error": f"Item {item_id} not found"})
        result = reviewer.review(item, platform)
        result["item_id"] = item_id
        result["caption_preview"] = (item.get("caption") or item.get("content", ""))[:100]
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)})


@mercury_bp.route("/api/lisa/broadcasts")
def api_lisa_broadcasts():
    """Process and acknowledge broadcasts for Lisa."""
    try:
        sys.path.insert(0, str(SHELBY_ROOT_DIR))
        from core.broadcast import get_unread_broadcasts, acknowledge_broadcast

        lisa_data = MERCURY_ROOT / "data"
        unread = get_unread_broadcasts(lisa_data)
        for bc in unread:
            acknowledge_broadcast("lisa", bc.get("id", ""), lisa_data)

        return jsonify({"processed": len(unread), "agent": "lisa"})
    except Exception as e:
        return jsonify({"error": str(e)[:200]})
