"""Lisa (social media) routes: /api/lisa/*"""
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


@mercury_bp.route("/api/lisa")
def api_mercury():
    """Lisa social media manager status."""
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

    # Only count actually-posted-live entries (not dry_run)
    live_posts = [p for p in posting_log if not p.get("dry_run", False)]

    recent_posts = sorted(posting_log, key=lambda x: x.get("posted_at", ""), reverse=True)[:20]

    platforms = {}
    for post in live_posts:
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
        "total_posts": len(live_posts),
        "platforms": platforms,
        "analytics_summary": analytics.get("summary", {}),
        "review_stats": review_stats,
    })


@mercury_bp.route("/api/lisa/plan")
def api_mercury_plan():
    """Lisa evolving plan and knowledge dashboard data."""
    try:
        from mercury.core.brain import MercuryBrain
        brain = MercuryBrain()
        return jsonify(brain.get_dashboard_data())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@mercury_bp.route("/api/lisa/knowledge")
def api_mercury_knowledge():
    """Lisa full knowledge base."""
    try:
        from mercury.core.brain import MercuryBrain
        brain = MercuryBrain()
        return jsonify(brain.get_knowledge())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@mercury_bp.route("/api/lisa/reply", methods=["POST"])
def api_mercury_reply():
    """Get reply suggestion for a comment."""
    try:
        from mercury.core.brain import MercuryBrain
        brain = MercuryBrain()
        comment = request.json.get("comment", "")
        return jsonify(brain.get_reply_suggestion(comment))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@mercury_bp.route("/api/lisa/review", methods=["POST"])
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
        return jsonify({"error": str(e)}), 500


@mercury_bp.route("/api/lisa/review/<item_id>", methods=["POST"])
def api_mercury_review_item(item_id):
    """Brand review a specific outbox item by ID."""
    try:
        from mercury.core.reviewer import BrandReviewer
        reviewer = BrandReviewer()
        platform = (request.json or {}).get("platform", "instagram")
        # Load the item from queue
        if not SOREN_QUEUE_FILE.exists():
            return jsonify({"error": "Queue file not found"}), 404
        with open(SOREN_QUEUE_FILE) as f:
            queue = json.load(f)
        item = next((q for q in queue if q.get("id") == item_id), None)
        if not item:
            return jsonify({"error": f"Item {item_id} not found"}), 404
        result = reviewer.review(item, platform)
        result["item_id"] = item_id
        result["caption_preview"] = (item.get("caption") or item.get("content", ""))[:100]
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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
        return jsonify({"error": str(e)[:200]}), 500


@mercury_bp.route("/api/lisa/live-config")
def api_lisa_live_config():
    """Get current live/dry-run config per platform."""
    try:
        from mercury.core.publisher import _load_live_config
        return jsonify(_load_live_config())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


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
        return jsonify({"error": str(e)[:200]}), 500


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
        return jsonify({"error": str(e)[:200]}), 500


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
        return jsonify({"error": str(e)[:200]}), 500


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
        return jsonify({"error": str(e)[:200]}), 500


@mercury_bp.route("/api/lisa/pipeline/stats")
def api_lisa_pipeline_stats():
    """Pipeline stats."""
    try:
        from mercury.core.pipeline import ContentPipeline
        pipeline = ContentPipeline()
        return jsonify(pipeline.get_stats())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


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
        return jsonify({"error": str(e)[:200]}), 500


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
        return jsonify({"error": str(e)[:200]}), 500


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
        return jsonify({"error": str(e)[:200]}), 500


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
        return jsonify({"error": str(e)[:200]}), 500


@mercury_bp.route("/api/lisa/timing")
def api_lisa_timing():
    """Get platform timing data."""
    try:
        from mercury.core.scheduler import PostingScheduler
        scheduler = PostingScheduler()
        return jsonify(scheduler.get_timing_data())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


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
        return jsonify({"error": str(e)[:200]}), 500


@mercury_bp.route("/api/lisa/jordan-queue")
def api_lisa_jordan_queue():
    """Items awaiting Jordan's approval."""
    try:
        from mercury.core.pipeline import ContentPipeline
        pipeline = ContentPipeline()
        items = pipeline.get_jordan_queue()
        return jsonify({"items": items, "count": len(items)})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


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
        return jsonify({"error": str(e)[:200]}), 500


@mercury_bp.route("/api/lisa/posting-schedule")
def api_lisa_posting_schedule():
    """Get all scheduled posts sorted by time."""
    try:
        from mercury.core.pipeline import ContentPipeline
        pipeline = ContentPipeline()
        schedule = pipeline.get_posting_schedule()
        return jsonify({"schedule": schedule, "count": len(schedule)})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@mercury_bp.route("/api/lisa/platform-status")
def api_lisa_platform_status():
    """Check platform connectivity status for Go-Live panel."""
    platforms = {}
    for plat in ["x", "tiktok", "instagram"]:
        platforms[plat] = {"connected": False, "reason": ""}

    # Check X (Twitter) — look for OAuth keys in env
    try:
        import os
        x_key = os.environ.get("X_API_KEY") or os.environ.get("TWITTER_API_KEY")
        x_secret = os.environ.get("X_API_SECRET") or os.environ.get("TWITTER_API_SECRET")
        x_token = os.environ.get("X_ACCESS_TOKEN") or os.environ.get("TWITTER_ACCESS_TOKEN")
        if x_key and x_secret and x_token:
            platforms["x"]["connected"] = True
        else:
            # Try loading from mercury .env
            mercury_env = MERCURY_ROOT / ".env"
            if mercury_env.exists():
                env_text = mercury_env.read_text()
                has_keys = "X_API_KEY" in env_text or "TWITTER_API_KEY" in env_text
                platforms["x"]["connected"] = has_keys
                if not has_keys:
                    platforms["x"]["reason"] = "API keys not configured"
            else:
                platforms["x"]["reason"] = "No .env file"
    except Exception:
        platforms["x"]["reason"] = "Error checking"

    # TikTok — check if posting module exists
    tiktok_module = MERCURY_ROOT / "core" / "tiktok_publisher.py"
    platforms["tiktok"]["connected"] = tiktok_module.exists()
    if not platforms["tiktok"]["connected"]:
        platforms["tiktok"]["reason"] = "Publisher module not installed"

    # Instagram — check if posting module exists
    ig_module = MERCURY_ROOT / "core" / "ig_publisher.py"
    platforms["instagram"]["connected"] = ig_module.exists()
    if not platforms["instagram"]["connected"]:
        platforms["instagram"]["reason"] = "Publisher module not installed"

    return jsonify(platforms)


@mercury_bp.route("/api/lisa/broadcasts")
def api_lisa_broadcasts():
    """Process and acknowledge broadcasts for Lisa."""
    try:
        # Path already added via bot.shared.ensure_path
        from core.broadcast import get_unread_broadcasts, acknowledge_broadcast

        lisa_data = MERCURY_ROOT / "data"
        unread = get_unread_broadcasts(lisa_data)
        for bc in unread:
            acknowledge_broadcast("lisa", bc.get("id", ""), lisa_data)

        return jsonify({"processed": len(unread), "agent": "lisa"})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


# ── X Integration Routes ──


@mercury_bp.route("/api/lisa/x-test")
def api_lisa_x_test():
    """Test X API credentials."""
    try:
        from mercury.core.x_client import XClient
        xclient = XClient()
        return jsonify(xclient.test_connection())
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


@mercury_bp.route("/api/lisa/x-mentions")
def api_lisa_x_mentions():
    """Fetch recent X mentions."""
    try:
        from mercury.core.x_client import XClient
        xclient = XClient()
        since_id = request.args.get("since_id")
        limit = int(request.args.get("limit", "20"))
        mentions = xclient.get_mentions(since_id=since_id, max_results=limit)
        return jsonify({"mentions": mentions, "count": len(mentions)})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@mercury_bp.route("/api/lisa/x-competitors")
def api_lisa_x_competitors():
    """Get competitor intel data."""
    try:
        from mercury.core.x_scanner import XCompetitorScanner
        scanner = XCompetitorScanner()
        data = scanner.get_latest()
        usage = scanner.get_usage_stats()
        return jsonify({**data, "usage": usage})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@mercury_bp.route("/api/lisa/x-competitors/scan", methods=["POST"])
def api_lisa_x_competitors_scan():
    """Trigger competitor scan."""
    try:
        from mercury.core.x_scanner import XCompetitorScanner
        scanner = XCompetitorScanner()
        results = scanner.scan_cycle()
        # Feed results to Lisa's brain
        try:
            from mercury.core.brain import MercuryBrain
            brain = MercuryBrain()
            brain.ingest_x_competitor_intel(results)
        except Exception:
            pass
        return jsonify(results)
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@mercury_bp.route("/api/lisa/generate-image", methods=["POST"])
def api_lisa_generate_image():
    """Generate a branded X image."""
    try:
        from mercury.core.x_image_gen import XImageGenerator
        img_gen = XImageGenerator()
        data = request.json or {}
        style = data.get("style", "cinematic")
        caption = data.get("caption", "dark motivation")
        pillar = data.get("pillar", "dark_motivation")

        if style == "quote":
            path = img_gen.generate_quote_card(caption, pillar)
        else:
            path = img_gen.generate_post_image(caption, pillar, style)

        if path:
            return jsonify({
                "ok": True,
                "filename": path.name,
                "path": str(path),
                "costs": img_gen.get_cost_summary(),
            })
        return jsonify({"ok": False, "error": "Budget exceeded or generation failed",
                        "costs": img_gen.get_cost_summary()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


@mercury_bp.route("/api/lisa/image-costs")
def api_lisa_image_costs():
    """Image generation cost summary."""
    try:
        from mercury.core.x_image_gen import XImageGenerator
        img_gen = XImageGenerator()
        return jsonify(img_gen.get_cost_summary())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@mercury_bp.route("/api/lisa/post-now/<item_id>", methods=["POST"])
def api_lisa_post_now(item_id):
    """Immediately post an approved item to X."""
    try:
        from mercury.core.auto_poster import AutoPoster
        poster = AutoPoster()
        result = poster.post_single(item_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


@mercury_bp.route("/api/lisa/auto-poster-status")
def api_lisa_auto_poster_status():
    """Auto-poster daemon status."""
    try:
        status_file = MERCURY_ROOT / "data" / "auto_poster_status.json"
        if status_file.exists():
            data = json.loads(status_file.read_text())
        else:
            data = {"running": False, "posts_made": 0}
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@mercury_bp.route("/api/lisa/x-reply/<comment_id>", methods=["POST"])
def api_lisa_x_reply(comment_id):
    """Post an approved reply to X."""
    try:
        from mercury.core.x_client import XClient
        from mercury.core.comment_ai import CommentAnalyzer

        analyzer = CommentAnalyzer()
        data = request.json or {}
        reply_text = data.get("reply_text", "")
        tweet_id = data.get("tweet_id", comment_id)

        if not reply_text:
            return jsonify({"ok": False, "error": "No reply text provided"}), 400

        xclient = XClient()
        result = xclient.reply_to_tweet(tweet_id, reply_text)

        if result.get("ok"):
            analyzer.update_status(comment_id, "posted")

        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


@mercury_bp.route("/api/lisa/reply-opportunities")
def api_lisa_reply_opportunities():
    """Get current reply opportunities from Reply Hunter."""
    try:
        from mercury.core.reply_hunter import ReplyHunter
        hunter = ReplyHunter()
        opps = hunter.get_opportunities(limit=20)
        status = hunter.get_status()
        return jsonify({"opportunities": opps, "status": status})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@mercury_bp.route("/api/lisa/reply-hunt", methods=["POST"])
def api_lisa_reply_hunt():
    """Trigger a reply hunter scan cycle."""
    try:
        from mercury.core.reply_hunter import ReplyHunter
        hunter = ReplyHunter()
        result = hunter.run_cycle()
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@mercury_bp.route("/api/lisa/reply-post/<opp_id>", methods=["POST"])
def api_lisa_reply_post(opp_id):
    """Post a reply to a target tweet from Reply Hunter."""
    try:
        from mercury.core.reply_hunter import ReplyHunter
        hunter = ReplyHunter()
        data = request.json or {}
        reply_idx = data.get("reply_idx", 0)

        # Find the opportunity
        opps = hunter.get_opportunities(limit=50)
        opp = next((o for o in opps if o.get("id") == opp_id), None)
        if not opp:
            return jsonify({"ok": False, "error": f"Opportunity {opp_id} not found"}), 404

        replies = opp.get("suggested_replies", [])
        if not replies or reply_idx >= len(replies):
            # Use custom reply text if provided
            reply_text = data.get("reply_text", "")
            if not reply_text:
                return jsonify({"ok": False, "error": "No reply available"}), 400
        else:
            reply_text = replies[reply_idx].get("text", "")

        result = hunter.post_reply(opp["tweet_id"], reply_text)
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


@mercury_bp.route("/api/lisa/reply-history")
def api_lisa_reply_history():
    """Reply hunter log + engagement results."""
    try:
        from mercury.core.reply_hunter import ReplyHunter
        hunter = ReplyHunter()
        history = hunter.get_reply_history(limit=50)
        status = hunter.get_status()
        return jsonify({"history": history, "status": status})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@mercury_bp.route("/api/lisa/competitor-playbook")
def api_lisa_competitor_playbook():
    """Get Lisa's compiled competitor playbook."""
    try:
        from mercury.core.brain import MercuryBrain
        brain = MercuryBrain()
        return jsonify(brain.get_competitor_playbook())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


# ── Intelligence Routes ──


@mercury_bp.route("/api/lisa/intelligence")
def api_lisa_intelligence():
    """Lisa's full intelligence dashboard — learnings, memory, guidance."""
    try:
        result = {}

        # Engagement learnings
        try:
            from mercury.core.engagement_learner import EngagementLearner
            learner = EngagementLearner()
            result["engagement"] = learner.get_full_insights()
            result["performance"] = learner.get_performance_summary()
        except Exception:
            result["engagement"] = {}
            result["performance"] = {}

        # Conversation memory
        try:
            from mercury.core.memory import ConversationMemory
            mem = ConversationMemory()
            result["memory"] = mem.get_stats()
            result["top_engagers"] = mem.get_top_engagers(10)
            result["collab_candidates"] = mem.get_collab_candidates()
        except Exception:
            result["memory"] = {}
            result["top_engagers"] = []
            result["collab_candidates"] = []

        # Reply guidance
        try:
            from mercury.core.engagement_learner import EngagementLearner
            learner = EngagementLearner()
            result["reply_guidance"] = learner.get_reply_guidance()
            result["posting_guidance"] = learner.get_posting_guidance()
            result["content_guidance"] = learner.get_content_guidance()
        except Exception:
            result["reply_guidance"] = {}
            result["posting_guidance"] = {}
            result["content_guidance"] = {}

        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@mercury_bp.route("/api/lisa/intelligence/check-engagement", methods=["POST"])
def api_lisa_check_engagement():
    """Trigger engagement checking for recent posts/replies."""
    try:
        from mercury.core.engagement_learner import EngagementLearner
        learner = EngagementLearner()
        result = learner.check_engagement()
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@mercury_bp.route("/api/lisa/intelligence/memory")
def api_lisa_memory():
    """Conversation memory details."""
    try:
        from mercury.core.memory import ConversationMemory
        mem = ConversationMemory()
        return jsonify({
            "stats": mem.get_stats(),
            "top_engagers": mem.get_top_engagers(20),
            "collab_candidates": mem.get_collab_candidates(),
        })
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@mercury_bp.route("/api/lisa/intelligence/user/<username>")
def api_lisa_user_context(username):
    """Get Lisa's memory of a specific user."""
    try:
        from mercury.core.memory import ConversationMemory
        mem = ConversationMemory()
        ctx = mem.get_user_context(username)
        if ctx:
            return jsonify(ctx)
        return jsonify({"error": f"No memory of @{username}"}), 404
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500
