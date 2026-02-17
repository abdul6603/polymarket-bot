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
    import logging
    _log = logging.getLogger("lisa")
    try:
        from bot.brain_reader import read_brain_notes
        brain_notes = read_brain_notes("lisa")
        if brain_notes:
            for note in brain_notes:
                _log.info("[BRAIN:%s] %s: %s", note.get("type", "note").upper(), note.get("topic", "?"), note.get("content", "")[:120])
    except Exception:
        pass

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


@mercury_bp.route("/api/lisa/go-live", methods=["POST"])
def api_lisa_go_live():
    """Toggle a platform between dry-run and live mode."""
    try:
        from mercury.core.publisher import _load_live_config, save_live_config
        data = request.json or {}
        platform = data.get("platform", "")
        enable = data.get("enable", False)

        if platform not in ("instagram", "tiktok", "x"):
            return jsonify({"error": f"Unknown platform: {platform}"}), 400

        config = _load_live_config()
        from datetime import datetime, timezone, timedelta
        from zoneinfo import ZoneInfo
        ET = ZoneInfo("America/New_York")

        if enable:
            config[platform] = {
                "live": True,
                "enabled_at": datetime.now(ET).isoformat(),
                "confirmed_by": "dashboard",
            }
        else:
            config[platform] = {
                "live": False,
                "enabled_at": None,
                "confirmed_by": None,
            }

        save_live_config(config)
        return jsonify({"status": "ok", "platform": platform, "live": enable, "config": config})
    except Exception as e:
        return jsonify({"error": str(e)[:200]})


@mercury_bp.route("/api/lisa/live-config")
def api_lisa_live_config():
    """Get current live/dry-run config per platform."""
    try:
        from mercury.core.publisher import _load_live_config
        return jsonify(_load_live_config())
    except Exception as e:
        return jsonify({"error": str(e)[:200]})


@mercury_bp.route("/api/lisa/comments")
def api_lisa_comments():
    """Get analyzed comments."""
    try:
        from mercury.core.comment_ai import CommentAnalyzer
        analyzer = CommentAnalyzer()
        status = request.args.get("status")
        limit = int(request.args.get("limit", "50"))
        return jsonify({
            "comments": analyzer.get_comments(limit=limit, status=status),
            "stats": analyzer.stats(),
        })
    except Exception as e:
        return jsonify({"error": str(e)[:200]})


@mercury_bp.route("/api/lisa/comment/analyze", methods=["POST"])
def api_lisa_comment_analyze():
    """Analyze a new comment with AI."""
    try:
        from mercury.core.comment_ai import CommentAnalyzer
        analyzer = CommentAnalyzer()
        data = request.json or {}
        comment = data.get("comment", "")
        platform = data.get("platform", "instagram")
        post_id = data.get("post_id", "")

        if not comment:
            return jsonify({"error": "No comment provided"}), 400

        result = analyzer.analyze_comment(comment, platform, post_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)[:200]})


@mercury_bp.route("/api/lisa/comment/<comment_id>/status", methods=["POST"])
def api_lisa_comment_status(comment_id: str):
    """Update a comment's status (approved, posted, dismissed)."""
    try:
        from mercury.core.comment_ai import CommentAnalyzer
        analyzer = CommentAnalyzer()
        data = request.json or {}
        status = data.get("status", "")
        if status not in ("approved", "posted", "dismissed"):
            return jsonify({"error": "Invalid status"}), 400
        ok = analyzer.update_status(comment_id, status)
        return jsonify({"ok": ok, "comment_id": comment_id, "status": status})
    except Exception as e:
        return jsonify({"error": str(e)[:200]})


@mercury_bp.route("/api/lisa/pipeline/run", methods=["POST"])
def api_lisa_pipeline_run():
    """Trigger pipeline review of all pending items."""
    try:
        from mercury.core.pipeline import ContentPipeline
        pipeline = ContentPipeline()
        platform = (request.json or {}).get("platform", "instagram")
        result = pipeline.review_pending(platform=platform)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)[:200]})


@mercury_bp.route("/api/lisa/pipeline/stats")
def api_lisa_pipeline_stats():
    """Pipeline stats."""
    try:
        from mercury.core.pipeline import ContentPipeline
        pipeline = ContentPipeline()
        return jsonify(pipeline.get_stats())
    except Exception as e:
        return jsonify({"error": str(e)[:200]})


@mercury_bp.route("/api/lisa/pipeline/approve/<item_id>", methods=["POST"])
def api_lisa_pipeline_approve(item_id):
    """Manual approve from dashboard."""
    try:
        from mercury.core.pipeline import ContentPipeline
        pipeline = ContentPipeline()
        platform = (request.json or {}).get("platform", "instagram")
        result = pipeline.approve_item(item_id, platform)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)[:200]})


@mercury_bp.route("/api/lisa/pipeline/reject/<item_id>", methods=["POST"])
def api_lisa_pipeline_reject(item_id):
    """Manual reject with reason."""
    try:
        from mercury.core.pipeline import ContentPipeline
        pipeline = ContentPipeline()
        reason = (request.json or {}).get("reason", "")
        result = pipeline.reject_item(item_id, reason)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)[:200]})


@mercury_bp.route("/api/lisa/rate", methods=["POST"])
def api_lisa_rate():
    """Rate a single content item."""
    try:
        from mercury.core.rating import ContentRater
        rater = ContentRater()
        data = request.json or {}
        caption = data.get("caption", "")
        platform = data.get("platform", "instagram")
        pillar = data.get("pillar", "")
        item = {"caption": caption, "pillar": pillar, "format": data.get("format", "")}
        result = rater.rate(item, platform)
        return jsonify(result.to_dict())
    except Exception as e:
        return jsonify({"error": str(e)[:200]})


@mercury_bp.route("/api/lisa/write", methods=["POST"])
def api_lisa_write():
    """Generate content using Lisa's writer."""
    try:
        from mercury.core.writer import LisaWriter
        writer = LisaWriter()
        data = request.json or {}
        write_type = data.get("type", "quote")
        topic = data.get("topic", "discipline")
        platform = data.get("platform", "instagram")

        if write_type == "quote":
            text = writer.write_quote(topic)
        elif write_type == "essay":
            length = data.get("length", "medium")
            text = writer.write_essay(topic, length)
        elif write_type == "caption":
            item = {"pillar": data.get("pillar", "dark_motivation"), "format": data.get("format", "reel")}
            text = writer.write_caption(item, platform)
        elif write_type == "improve":
            text = writer.improve_caption(data.get("caption", ""), data.get("issues", []), platform)
        elif write_type == "thread":
            tweets = writer.write_thread(topic, data.get("slides", 5))
            return jsonify({"type": "thread", "tweets": tweets})
        else:
            return jsonify({"error": f"Unknown write type: {write_type}"}), 400

        return jsonify({"type": write_type, "text": text, "topic": topic, "platform": platform})
    except Exception as e:
        return jsonify({"error": str(e)[:200]})


@mercury_bp.route("/api/lisa/timing")
def api_lisa_timing():
    """Get platform timing data."""
    try:
        from mercury.core.scheduler import PostingScheduler
        scheduler = PostingScheduler()
        return jsonify(scheduler.get_timing_data())
    except Exception as e:
        return jsonify({"error": str(e)[:200]})


@mercury_bp.route("/api/lisa/optimal-slot", methods=["POST"])
def api_lisa_optimal_slot():
    """Get optimal posting slot for content."""
    try:
        from mercury.core.scheduler import PostingScheduler
        scheduler = PostingScheduler()
        data = request.json or {}
        slot = scheduler.get_optimal_slot(
            platform=data.get("platform", "instagram"),
            content_type=data.get("content_type", ""),
            pillar=data.get("pillar", ""),
        )
        return jsonify(slot)
    except Exception as e:
        return jsonify({"error": str(e)[:200]})


@mercury_bp.route("/api/lisa/jordan-queue")
def api_lisa_jordan_queue():
    """Items awaiting Jordan's approval."""
    try:
        from mercury.core.pipeline import ContentPipeline
        pipeline = ContentPipeline()
        items = pipeline.get_jordan_queue()
        return jsonify({"items": items, "count": len(items)})
    except Exception as e:
        return jsonify({"error": str(e)[:200]})


@mercury_bp.route("/api/lisa/jordan-approve/<item_id>", methods=["POST"])
def api_lisa_jordan_approve(item_id):
    """Jordan approves — schedule at optimal time."""
    try:
        from mercury.core.pipeline import ContentPipeline
        pipeline = ContentPipeline()
        platform = (request.json or {}).get("platform", "")
        result = pipeline.jordan_approve(item_id, platform)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)[:200]})


@mercury_bp.route("/api/lisa/posting-schedule")
def api_lisa_posting_schedule():
    """Get all scheduled posts sorted by time."""
    try:
        from mercury.core.pipeline import ContentPipeline
        pipeline = ContentPipeline()
        schedule = pipeline.get_posting_schedule()
        return jsonify({"schedule": schedule, "count": len(schedule)})
    except Exception as e:
        return jsonify({"error": str(e)[:200]})


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
