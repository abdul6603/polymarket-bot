"""Soren (content creator) routes: /api/soren/*"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from flask import Blueprint, Response, jsonify, request, send_file

from bot.shared import (
    ET,
    SOREN_QUEUE_FILE,
    SOREN_ROOT,
    SOREN_OUTPUT_DIR,
    SOREN_TRENDS_FILE,
    _generation_status,
    SHELBY_ROOT_DIR,
)

soren_bp = Blueprint("soren", __name__)


def _do_generate(item_id: str, item: dict, mode: str) -> None:
    """Background thread: generate a reel from a queue item."""
    import logging
    import tempfile
    _log = logging.getLogger("soren")
    try:
        _generation_status[item_id] = {"status": "generating"}

        # Read brain notes for Soren
        try:
            from bot.brain_reader import read_brain_notes
            brain_notes = read_brain_notes("soren")
            if brain_notes:
                for note in brain_notes:
                    _log.info("[BRAIN] %s: %s", note.get("topic", "?"), note.get("content", "")[:120])
        except Exception:
            pass

        # Strip hashtags -- only keep the actual caption text
        raw = item.get("content", "")
        # Remove everything from first hashtag onward
        if "\n\n#" in raw:
            raw = raw.split("\n\n#")[0]
        elif "\n#" in raw:
            raw = raw.split("\n#")[0]
        content = raw.strip().split("\n")[0][:200]
        pillar = item.get("pillar", "dark_motivation")
        reel_id = f"queue_{item_id}"

        # Write a temp Python script to avoid shell escaping issues
        if mode == "caption":
            script_code = (
                f"import sys\n"
                f"sys.path.insert(0, {str(SOREN_ROOT)!r})\n"
                f"from generate import create_caption_reel\n"
                f"path = create_caption_reel({content!r}, reel_id={reel_id!r})\n"
                f"print(str(path))\n"
            )
        else:
            # Build shot descriptions based on pillar for proper DALL-E visuals
            pillar_shots = {
                "dark_motivation": [
                    "SHOT 1: Lone hooded figure standing on a dark rooftop overlooking a city at night, back to camera, fog and distant lights",
                    "SHOT 2: Close-up of clenched fist in dramatic side lighting, dark background, determination",
                    "SHOT 3: Silhouette walking alone through dark rain-soaked streets, neon reflections on wet pavement",
                    "SHOT 4: Dark figure standing in a doorway with amber backlight, powerful stance, face hidden in shadow",
                ],
                "gym_warrior": [
                    "SHOT 1: Dark gym interior, heavy barbell on the ground, dramatic single light source from above",
                    "SHOT 2: Silhouette of muscular figure doing deadlifts in a dark gym, chalk dust in the air",
                    "SHOT 3: Close-up of calloused hands gripping a barbell, sweat dripping, intense lighting",
                    "SHOT 4: Figure sitting on a gym bench in the dark, head down, single spotlight, post-workout exhaustion",
                ],
                "heartbreak_to_power": [
                    "SHOT 1: Man sitting alone on the edge of a bed in a dark empty room, single window light",
                    "SHOT 2: Shattered glass or broken mirror on the floor, dark moody close-up",
                    "SHOT 3: Same man now standing tall in a dark suit, city lights behind him, powerful transformation",
                    "SHOT 4: Walking away from camera into bright light at the end of a dark corridor, rebirth",
                ],
                "lone_wolf_lifestyle": [
                    "SHOT 1: Lone figure walking through empty neon-lit city streets at night, fog and rain",
                    "SHOT 2: Dark coffee shop interior, single person sitting alone by the window, rain outside",
                    "SHOT 3: Night cityscape from above, dark and moody, distant lights twinkling",
                    "SHOT 4: Hooded figure standing at the edge of a pier overlooking dark water at night",
                ],
                "stoic_lessons": [
                    "SHOT 1: Ancient marble bust of a Roman philosopher in dramatic side lighting, dark background",
                    "SHOT 2: Old leather-bound book open on a dark wooden desk, candle light, atmospheric",
                    "SHOT 3: Lone figure meditating on a cliff edge overlooking mountains at dawn, silhouette",
                    "SHOT 4: Dark study room with bookshelves, single desk lamp illuminating a journal",
                ],
                "progress_showcase": [
                    "SHOT 1: Dark before-and-after split screen, shadowy figure transforming into a powerful silhouette",
                    "SHOT 2: Close-up of hands writing in a journal under dim desk lamp, determination",
                    "SHOT 3: Figure standing on mountain peak at dawn, arms at sides, overlooking vast landscape",
                    "SHOT 4: Dark room with a single spotlight on a man in a suit, back to camera, powerful stance",
                ],
                "dark_humor": [
                    "SHOT 1: Muscular silhouette in a dark gym, dramatic overhead lighting, barbell loaded heavy",
                    "SHOT 2: Close-up of a stoic face with slight smirk, dramatic side lighting, dark background",
                    "SHOT 3: Figure doing heavy deadlifts in an empty dark gym, chalk dust floating in spotlight",
                    "SHOT 4: Man walking away from camera through dark gym corridor, confident stride",
                ],
                "wisdom_quotes": [
                    "SHOT 1: Lone figure sitting on a dark rooftop ledge overlooking city lights at night, contemplative",
                    "SHOT 2: Close-up of eyes in shadow, intense gaze, dramatic rim lighting",
                    "SHOT 3: Dark silhouette walking through fog on an empty street, atmospheric amber light",
                    "SHOT 4: Figure standing in front of a large window, city skyline behind, back to camera",
                ],
            }
            shots = pillar_shots.get(pillar, pillar_shots["dark_motivation"])
            vo_text = raw[:300]  # Uses hashtag-stripped text
            script = []
            # Interleave shots with voiceover split into parts
            vo_sentences = [s.strip() for s in vo_text.replace(".", ".\n").split("\n") if s.strip()]
            for i, shot in enumerate(shots):
                script.append(shot)
                if i < len(vo_sentences):
                    script.append(f"VOICEOVER: {vo_sentences[i]}")
            # Add remaining sentences as voiceover
            for s in vo_sentences[len(shots):]:
                script.append(f"VOICEOVER: {s}")
            # If no voiceover extracted, use the full content
            if not vo_sentences:
                script.append(f"VOICEOVER: {vo_text}")

            concept = {
                "title": item.get("title", "Untitled"),
                "pillar": pillar,
                "duration": "15s",
                "caption": content,
                "script": script,
            }
            script_code = (
                f"import sys, json\n"
                f"sys.path.insert(0, {str(SOREN_ROOT)!r})\n"
                f"from generate import create_reel\n"
                f"concept = json.loads({json.dumps(concept)!r})\n"
                f"path = create_reel(concept, reel_id={reel_id!r})\n"
                f"print(str(path))\n"
            )

        # Write to temp file and execute
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as tmp:
            tmp.write(script_code)
            tmp_path = tmp.name

        result = subprocess.run(
            [sys.executable, tmp_path],
            capture_output=True, text=True, timeout=900,
            cwd=str(SOREN_ROOT),
        )
        Path(tmp_path).unlink(missing_ok=True)

        if result.returncode == 0 and result.stdout.strip():
            video_path = result.stdout.strip().split("\n")[-1]
            if Path(video_path).exists():
                _generation_status[item_id] = {"status": "done", "video_path": video_path}
                try:
                    with open(SOREN_QUEUE_FILE) as f:
                        q = json.load(f)
                    for qi in q:
                        if qi["id"] == item_id:
                            qi["video_path"] = video_path
                            break
                    with open(SOREN_QUEUE_FILE, "w") as f:
                        json.dump(q, f, indent=2)
                except Exception:
                    pass
            else:
                _generation_status[item_id] = {"status": "error", "error": f"File not found: {video_path}"}
        else:
            err = result.stderr[:500] if result.stderr else result.stdout[:500] or "Generation failed"
            _generation_status[item_id] = {"status": "error", "error": err}
    except subprocess.TimeoutExpired:
        _generation_status[item_id] = {"status": "error", "error": "Generation timed out (10 min)"}
    except Exception as e:
        _generation_status[item_id] = {"status": "error", "error": str(e)}


def _serve_video(video_path: str) -> Response:
    """Serve a video file with HTTP Range request support for browser streaming."""
    file_path = Path(video_path)
    try:
        file_size = file_path.stat().st_size
    except Exception:
        return jsonify({"error": "Video file not found"}), 404

    range_header = request.headers.get("Range")
    if range_header:
        # Parse Range: bytes=START-END
        byte_range = range_header.replace("bytes=", "").strip()
        parts = byte_range.split("-")
        start = int(parts[0]) if parts[0] else 0
        end = int(parts[1]) if parts[1] else file_size - 1
        end = min(end, file_size - 1)
        length = end - start + 1

        try:
            with open(video_path, "rb") as f:
                f.seek(start)
                data = f.read(length)
        except Exception:
            return jsonify({"error": "Failed to read video file"}), 500

        resp = Response(
            data,
            status=206,
            mimetype="video/mp4",
            direct_passthrough=True,
        )
        resp.headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
        resp.headers["Accept-Ranges"] = "bytes"
        resp.headers["Content-Length"] = str(length)
        return resp
    else:
        # No range requested -- send full file with Accept-Ranges header
        resp = send_file(video_path, mimetype="video/mp4")
        resp.headers["Accept-Ranges"] = "bytes"
        resp.headers["Content-Length"] = str(file_size)
        return resp


@soren_bp.route("/api/soren")
def api_soren():
    """Soren content queue and trends data."""

    queue = []
    if SOREN_QUEUE_FILE.exists():
        try:
            with open(SOREN_QUEUE_FILE) as f:
                queue = json.load(f)
        except Exception:
            pass

    pending = [q for q in queue if q.get("status") == "pending"]
    posted = [q for q in queue if q.get("status") == "posted"]
    failed = [q for q in queue if q.get("status") == "failed"]

    # By platform
    by_platform = {}
    for q in queue:
        p = q.get("platform", "unknown")
        by_platform[p] = by_platform.get(p, 0) + 1

    # By pillar
    by_pillar = {}
    for q in queue:
        p = q.get("pillar", "unknown")
        by_pillar[p] = by_pillar.get(p, 0) + 1

    # Trends
    trends = {}
    if SOREN_TRENDS_FILE.exists():
        try:
            with open(SOREN_TRENDS_FILE) as f:
                trends = json.load(f)
        except Exception:
            pass

    # Freshness scoring per item
    try:
        from atlas.soren_optimizer import SorenOptimizer
        for item in queue:
            item["freshness"] = SorenOptimizer.compute_freshness(item)
    except Exception:
        pass

    # Freshness summary
    freshness_summary = {"avg_score": 0, "fresh": 0, "ok": 0, "stale": 0, "expired": 0}
    items_with_freshness = [q for q in queue if q.get("freshness")]
    if items_with_freshness:
        freshness_summary["avg_score"] = round(
            sum(q["freshness"]["score"] for q in items_with_freshness) / len(items_with_freshness), 1
        )
        for q in items_with_freshness:
            label = q["freshness"]["label"]
            if label in freshness_summary:
                freshness_summary[label] += 1

    return jsonify({
        "queue_total": len(queue),
        "pending": len(pending),
        "posted": len(posted),
        "failed": len(failed),
        "by_platform": by_platform,
        "by_pillar": by_pillar,
        "items": sorted(queue, key=lambda x: x.get("scheduled_time", ""))[:30],
        "trends_count": len(trends.get("trending_topics", [])),
        "trends_scanned": trends.get("scanned_at", ""),
        "freshness": freshness_summary,
    })


@soren_bp.route("/api/soren/generate/<item_id>", methods=["POST"])
def api_soren_generate(item_id):
    """Trigger reel generation for a queue item."""
    mode = request.json.get("mode", "caption") if request.is_json else "caption"

    if item_id in _generation_status and _generation_status[item_id].get("status") == "generating":
        return jsonify({"status": "already_generating"})

    # Find the queue item
    queue = []
    if SOREN_QUEUE_FILE.exists():
        try:
            with open(SOREN_QUEUE_FILE) as f:
                queue = json.load(f)
        except Exception:
            pass

    item = next((q for q in queue if q["id"] == item_id), None)
    if not item:
        return jsonify({"error": "Item not found"}), 404

    thread = threading.Thread(target=_do_generate, args=(item_id, item, mode), daemon=True)
    thread.start()
    return jsonify({"status": "generating", "item_id": item_id})


@soren_bp.route("/api/soren/gen-status/<item_id>")
def api_soren_gen_status(item_id):
    """Check generation status."""
    status = _generation_status.get(item_id, {"status": "none"})
    return jsonify(status)


@soren_bp.route("/api/soren/preview/<item_id>")
def api_soren_preview(item_id):
    """Serve generated video for preview (supports HTTP Range requests for streaming)."""
    status = _generation_status.get(item_id, {})
    video_path = status.get("video_path", "")
    if not video_path or not Path(video_path).exists():
        # Check if video exists from a previous generation
        expected = SOREN_OUTPUT_DIR / f"queue_{item_id}.mp4"
        if expected.exists():
            video_path = str(expected)
        else:
            return jsonify({"error": "No video available"}), 404
    return _serve_video(video_path)


@soren_bp.route("/api/soren/download/<item_id>")
def api_soren_download(item_id):
    """Serve generated video as a download attachment."""
    status = _generation_status.get(item_id, {})
    video_path = status.get("video_path", "")
    if not video_path or not Path(video_path).exists():
        expected = SOREN_OUTPUT_DIR / f"queue_{item_id}.mp4"
        if expected.exists():
            video_path = str(expected)
        else:
            return jsonify({"error": "No video available"}), 404
    return send_file(
        video_path,
        mimetype="video/mp4",
        as_attachment=True,
        download_name=f"soren_reel_{item_id}.mp4",
    )


@soren_bp.route("/api/soren/approve/<item_id>", methods=["POST"])
def api_soren_approve(item_id):
    """Approve a queue item -- mark as ready to post."""
    try:
        with open(SOREN_QUEUE_FILE) as f:
            queue = json.load(f)
        for q in queue:
            if q["id"] == item_id:
                q["status"] = "approved"
                q["approved_at"] = datetime.now(ET).isoformat()
                break
        else:
            return jsonify({"error": "Item not found"}), 404
        with open(SOREN_QUEUE_FILE, "w") as f:
            json.dump(queue, f, indent=2)
        return jsonify({"success": True, "status": "approved"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@soren_bp.route("/api/soren/reject/<item_id>", methods=["POST"])
def api_soren_reject(item_id):
    """Reject a queue item."""
    try:
        with open(SOREN_QUEUE_FILE) as f:
            queue = json.load(f)
        for q in queue:
            if q["id"] == item_id:
                q["status"] = "rejected"
                q["rejected_at"] = datetime.now(ET).isoformat()
                break
        else:
            return jsonify({"error": "Item not found"}), 404
        with open(SOREN_QUEUE_FILE, "w") as f:
            json.dump(queue, f, indent=2)
        return jsonify({"success": True, "status": "rejected"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@soren_bp.route("/api/soren/regenerate/<item_id>", methods=["POST"])
def api_soren_regenerate(item_id):
    """Reset a queue item for regeneration -- removes old video reference."""
    try:
        with open(SOREN_QUEUE_FILE) as f:
            queue = json.load(f)
        for q in queue:
            if q["id"] == item_id:
                old_path = q.get("video_path")
                if old_path and Path(old_path).exists():
                    Path(old_path).unlink(missing_ok=True)
                q["status"] = "pending"
                q.pop("video_path", None)
                q.pop("approved_at", None)
                # Clear generation status cache
                _generation_status.pop(item_id, None)
                break
        else:
            return jsonify({"error": "Item not found"}), 404
        with open(SOREN_QUEUE_FILE, "w") as f:
            json.dump(queue, f, indent=2)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@soren_bp.route("/api/soren/custom-generate", methods=["POST"])
def api_soren_custom_generate():
    """Generate a custom reel from user-provided prompt."""
    data = request.get_json()
    if not data or not data.get("prompt"):
        return jsonify({"error": "No prompt provided"}), 400

    prompt = data["prompt"]
    mode = data.get("mode", "full")  # full or caption
    item_id = hashlib.md5(f"{prompt}{time.time()}".encode()).hexdigest()[:12]

    # Create a queue item from the prompt
    item = {
        "id": item_id,
        "title": f"Custom: {prompt[:50]}",
        "content": prompt,
        "pillar": data.get("pillar", "dark_motivation"),
        "platform": "tiktok",
        "type": "custom",
        "status": "generating",
        "created_at": datetime.now(ET).isoformat(),
    }

    # Add to queue file
    queue = []
    if SOREN_QUEUE_FILE.exists():
        try:
            with open(SOREN_QUEUE_FILE) as f:
                queue = json.load(f)
        except Exception:
            pass
    queue.insert(0, item)
    try:
        with open(SOREN_QUEUE_FILE, "w") as f:
            json.dump(queue, f, indent=2)
    except Exception as e:
        return jsonify({"error": f"Failed to write queue file: {e}"}), 500

    # Start generation
    thread = threading.Thread(target=_do_generate, args=(item_id, item, mode), daemon=True)
    thread.start()
    return jsonify({"status": "generating", "item_id": item_id})


@soren_bp.route("/api/soren/broadcasts")
def api_soren_broadcasts():
    """Process and acknowledge broadcasts for Soren."""
    try:
        sys.path.insert(0, str(SHELBY_ROOT_DIR))
        from core.broadcast import get_unread_broadcasts, acknowledge_broadcast

        soren_data = SOREN_ROOT / "data"
        unread = get_unread_broadcasts(soren_data)
        for bc in unread:
            acknowledge_broadcast("soren", bc.get("id", ""), soren_data)

        return jsonify({"processed": len(unread), "agent": "soren"})
    except Exception as e:
        return jsonify({"error": str(e)[:200]})


@soren_bp.route("/api/soren/competitors")
def api_soren_competitors():
    """Soren competitor content intelligence from Atlas."""
    try:
        from atlas.competitor_spy import CompetitorSpy
        spy = CompetitorSpy()
        latest = spy.get_soren_latest()
        if latest:
            return jsonify(latest)
        return jsonify({"competitors": [], "scanned_at": None, "message": "No competitor data yet"})
    except Exception as e:
        return jsonify({"error": str(e)[:200]})
