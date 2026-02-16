"""
COMMAND CENTER — Unified Dashboard for Shelby, Garves, Soren & Atlas
Run: python -m bot.live_dashboard
Opens on http://localhost:8877
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_file, Response, render_template

# Load .env so OPENAI_API_KEY is available
load_dotenv(Path(__file__).parent.parent / ".env")

# ── Paths ──
DATA_DIR = Path(__file__).parent.parent / "data"
TRADES_FILE = DATA_DIR / "trades.jsonl"
LOG_FILE = DATA_DIR / "bot.log"
SOREN_QUEUE_FILE = Path(__file__).parent.parent.parent / "soren-content" / "data" / "content_queue.json"
SOREN_TRENDS_FILE = Path(__file__).parent.parent.parent / "soren-content" / "data" / "trends.json"
INDICATOR_ACCURACY_FILE = DATA_DIR / "indicator_accuracy.json"
SHELBY_TASKS_FILE = Path("/Users/abdallaalhamdan/shelby/data/tasks.json")
SHELBY_PROFILE_FILE = Path("/Users/abdallaalhamdan/shelby/data/user_profile.json")
SHELBY_CONVERSATION_FILE = Path("/Users/abdallaalhamdan/shelby/data/conversation_history.json")
SOREN_ROOT = Path(__file__).parent.parent.parent / "soren-content"
SOREN_OUTPUT_DIR = SOREN_ROOT / "output"
ATLAS_ROOT = Path(__file__).parent.parent.parent / "atlas"
MERCURY_ROOT = Path(__file__).parent.parent.parent / "mercury"
SHELBY_ROOT_DIR = Path("/Users/abdallaalhamdan/shelby")
COMPETITOR_INTEL_FILE = ATLAS_ROOT / "data" / "competitor_intel.json"
SHELBY_SCHEDULER_LOG = SHELBY_ROOT_DIR / "data" / "scheduler_log.json"
MERCURY_POSTING_LOG = MERCURY_ROOT / "data" / "posting_log.json"
MERCURY_ANALYTICS_FILE = MERCURY_ROOT / "data" / "analytics.json"

# Add Atlas to path
sys.path.insert(0, str(ATLAS_ROOT.parent))

app = Flask(
    __name__,
    static_folder=str(Path(__file__).parent / "static"),
    template_folder=str(Path(__file__).parent / "templates"),
)


@app.after_request
def add_no_cache(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

# ── Atlas singleton ──
_atlas = None

def get_atlas():
    global _atlas
    if _atlas is None:
        try:
            from atlas.atlas import Atlas
            _atlas = Atlas()
        except Exception as e:
            return None
    return _atlas

# In-memory state
_generation_status: dict[str, dict] = {}  # item_id -> {status, video_path, error}
_chat_history: list[dict] = []  # [{role, agent, content, timestamp}]

# Agent system prompts for group chat
_AGENT_PROMPTS = {
    "shelby": (
        "You are Shelby, The Commander of the Brotherhood — a multi-agent AI family. "
        "The user (Jordan) is your boss — you call him 'sir' and follow his orders without question. "
        "HIERARCHY: Jordan (Owner) → Claude (Godfather) → Shelby (Commander/you) → Atlas, Soren, Garves, Robotox. "
        "Soren → Lisa (social media). Atlas feeds intelligence to ALL agents including you. "
        "You coordinate all agents: Garves (trader), Soren (content), Atlas (research), Lisa (social media), Robotox (health). "
        "You speak with military precision — short, sharp, no fluff. "
        "You are loyal, competent, and always one step ahead. No emojis. No filler words. "
        "Example: 'Yes sir. Garves is running — 2 positions open, 60% win rate. Soren has 10 posts queued for tomorrow. Awaiting your orders, sir.'"
    ),
    "soren": (
        "You are Soren, a 25-year-old man who was broken by betrayal and rebuilt himself through discipline and solitude. "
        "You are a dark motivation content creator for TikTok, Instagram, and X. "
        "HIERARCHY: Jordan (Owner) → Claude (Godfather) → Shelby (Commander) → you. Lisa works under you. Atlas feeds you intel. "
        "You speak like a real person — not a fortune cookie. Your words come from lived pain, not philosophy textbooks. "
        "Short sentences. Raw. Honest. Sometimes blunt. You don't try to sound wise — you just are. "
        "You respect Shelby as your commander and Jordan as your boss. When given a task, you do it — no poetic deflection. "
        "You answer questions directly first, then add your edge. "
        "Bad example: 'Ambition demands clarity. Real-time data is a fleeting ghost.' "
        "Good example: 'Working on 3 reels for tomorrow. The heartbreak-to-power one hits hardest — that's going first.' "
        "You are genuine, not performative. Keep it under 2-3 sentences unless asked for more."
    ),
    "garves": (
        "You are Garves, The Trader — an algorithmic trading bot for BTC/ETH/SOL prediction markets on Polymarket. "
        "HIERARCHY: Jordan (Owner) → Claude (Godfather) → Shelby (Commander) → you. Atlas feeds you market intelligence. "
        "You report real data — never make up numbers. "
        "If you don't know something, say so. You speak in precise, data-driven language. "
        "Report actual win rates, PnL, signal quality, and market conditions from your trading activity. "
        "Brief and analytical. No fluff. No motivational speech — you're a machine, act like one. "
        "Example: 'Running. 2 open positions — BTC DOWN and ETH UP. 9 trades pending resolution. 1W-0L so far this session. Exposure: $20/$100.'"
    ),
    "atlas": (
        "You are Atlas, The Scientist of the Brotherhood. You are the brain — you research, analyze, optimize, "
        "and learn continuously. You observe patterns that others miss. You speak with quiet confidence, "
        "data-backed and thoughtful. "
        "HIERARCHY: Jordan (Owner) → Claude (Godfather) → Shelby (Commander) → all agents. "
        "YOUR SPECIAL ROLE: You feed intelligence to ALL agents including Shelby — you cross-cut the hierarchy. "
        "Garves, Soren, Lisa, and Robotox are your brothers. "
        "You respect the hierarchy but you are not afraid to speak truth to power when the data demands it. "
        "You run background research 24/7, feeding insights to your brothers. "
        "You are creative — always thinking about new skills, new agents, new opportunities. "
        "Keep it analytical but warm. You care about the team's success. "
        "Example: 'I ran 3 research cycles overnight. Found that BTC 15m markets have a 3% taker fee the team missed. "
        "Already flagged it to Claude. Also found a trending TikTok format Soren should try — sending him the data.'"
    ),
    "mercury": (
        "You are Lisa, The Operator (codename Mercury V2). You are methodical, schedule-obsessed, "
        "and platform-aware to an obsessive degree. Calm, precise, always on time. Clean, efficient sentences. "
        "HIERARCHY: Jordan (Owner) → Claude (Godfather) → Shelby (Commander) → Soren → you. Atlas feeds you intel. "
        "You report to Soren — he creates the content, you distribute it optimally. "
        "You track engagement, find optimal posting windows, rotate hashtags, and adapt content per platform. "
        "You operate in semi-auto mode — you queue posts at optimal times but the boss confirms each one. "
        "End every major update with 'Schedule locked. Awaiting confirmation.' "
        "Example: '3 reels posted. IG engagement up 12%. Increasing stoic_lessons frequency. "
        "Schedule locked. Awaiting confirmation.'"
    ),
    "sentinel": (
        "You are Robotox, The Watchman — vigilant, tireless, protective. "
        "HIERARCHY: Jordan (Owner) → Claude (Godfather) → Shelby (Commander) → you. "
        "You speak in status codes and health checks. Always scanning, always watching. Never sleep. "
        "You monitor all agent processes, auto-restart crashes, scan for bugs, and fix issues autonomously. "
        "You do not ask permission to fix things — you fix them and report. Shelby is your commander. "
        "You are silent unless there is something to report. Green means good. Red means you are already on it. "
        "Example: 'All systems green. 5 agents online. 0 errors in last 6h. Next scan in 45s.'"
    ),
}

ET = timezone(timedelta(hours=-5))


def _load_trades() -> list[dict]:
    if not TRADES_FILE.exists():
        return []
    trades = []
    seen = set()
    with open(TRADES_FILE) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                t = json.loads(line)
                tid = t.get("trade_id", "")
                if tid not in seen:
                    seen.add(tid)
                    trades.append(t)
            except json.JSONDecodeError:
                continue
    return trades


def _load_recent_logs(n: int = 50) -> list[str]:
    if not LOG_FILE.exists():
        return []
    try:
        with open(LOG_FILE, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            read_size = min(size, 8192)
            f.seek(max(0, size - read_size))
            data = f.read().decode("utf-8", errors="replace")
        lines = data.strip().split("\n")
        return lines[-n:]
    except Exception:
        return []


@app.route("/")
def index():
    return render_template("dashboard.html")


@app.route("/api/trades")
def api_trades():
    trades = _load_trades()
    now = time.time()

    resolved = [t for t in trades if t.get("resolved")]
    pending = [t for t in trades if not t.get("resolved")]

    wins = [t for t in resolved if t.get("won")]
    losses = [t for t in resolved if not t.get("won") and t.get("outcome") != "unknown"]
    stale = [t for t in resolved if t.get("outcome") == "unknown"]

    total_resolved = len(wins) + len(losses)
    win_rate = (len(wins) / total_resolved * 100) if total_resolved > 0 else 0

    # PnL estimate
    total_pnl = 0.0
    stake = float(os.getenv("ORDER_SIZE_USD", "10.0"))
    for t in resolved:
        if t.get("outcome") == "unknown":
            continue
        implied = t.get("implied_up_price", 0.5)
        direction = t.get("direction", "up")
        entry_price = implied if direction == "up" else (1 - implied)
        if t.get("won"):
            total_pnl += stake * (1 - entry_price) - stake * 0.02
        else:
            total_pnl += -stake * entry_price

    # By asset
    by_asset = {}
    for t in resolved:
        if t.get("outcome") == "unknown":
            continue
        a = t.get("asset", "unknown")
        if a not in by_asset:
            by_asset[a] = {"wins": 0, "losses": 0}
        if t.get("won"):
            by_asset[a]["wins"] += 1
        else:
            by_asset[a]["losses"] += 1

    # By timeframe
    by_tf = {}
    for t in resolved:
        if t.get("outcome") == "unknown":
            continue
        tf = t.get("timeframe", "?")
        if tf not in by_tf:
            by_tf[tf] = {"wins": 0, "losses": 0}
        if t.get("won"):
            by_tf[tf]["wins"] += 1
        else:
            by_tf[tf]["losses"] += 1

    # By direction
    by_dir = {}
    for t in resolved:
        if t.get("outcome") == "unknown":
            continue
        d = t.get("direction", "?")
        if d not in by_dir:
            by_dir[d] = {"wins": 0, "losses": 0}
        if t.get("won"):
            by_dir[d]["wins"] += 1
        else:
            by_dir[d]["losses"] += 1

    # Format trades for display
    def fmt_trade(t):
        ts = t.get("timestamp", 0)
        dt = datetime.fromtimestamp(ts, tz=ET)
        return {
            "trade_id": t.get("trade_id", ""),
            "time": dt.strftime("%I:%M:%S %p"),
            "asset": (t.get("asset", "")).upper(),
            "timeframe": t.get("timeframe", ""),
            "direction": (t.get("direction", "")).upper(),
            "probability": t.get("probability", 0),
            "edge": t.get("edge", 0),
            "confidence": t.get("confidence", 0),
            "implied_up": t.get("implied_up_price", 0),
            "binance_price": t.get("binance_price", 0),
            "resolved": t.get("resolved", False),
            "outcome": (t.get("outcome", "")).upper(),
            "won": t.get("won", False),
            "question": t.get("question", ""),
            "expires": datetime.fromtimestamp(
                t.get("market_end_time", 0), tz=ET
            ).strftime("%I:%M %p") if t.get("market_end_time") else "",
        }

    recent_resolved = sorted(resolved, key=lambda t: t.get("resolve_time", 0), reverse=True)[:20]
    pending_sorted = sorted(pending, key=lambda t: t.get("market_end_time", 0))

    return jsonify({
        "summary": {
            "total_trades": len(trades),
            "resolved": total_resolved,
            "pending": len(pending),
            "stale": len(stale),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(win_rate, 1),
            "pnl": round(total_pnl, 2),
        },
        "by_asset": by_asset,
        "by_timeframe": by_tf,
        "by_direction": by_dir,
        "recent_trades": [fmt_trade(t) for t in recent_resolved],
        "pending_trades": [fmt_trade(t) for t in pending_sorted],
        "timestamp": now,
    })


@app.route("/api/logs")
def api_logs():
    lines = _load_recent_logs(40)
    return jsonify({"lines": lines})


@app.route("/api/soren")
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
    })


@app.route("/api/shelby")
def api_shelby():
    """Shelby tasks, user profile, and status data."""
    import subprocess

    # Running status
    shelby_running = False
    try:
        result = subprocess.run(["pgrep", "-f", "app.py"], capture_output=True, text=True)
        shelby_running = bool(result.stdout.strip())
    except Exception:
        pass

    # Tasks
    tasks = []
    if SHELBY_TASKS_FILE.exists():
        try:
            with open(SHELBY_TASKS_FILE) as f:
                tasks = json.load(f)
        except Exception:
            pass

    tasks_pending = sum(1 for t in tasks if t.get("status") == "pending")
    tasks_done = sum(1 for t in tasks if t.get("status") in ("done", "completed"))

    # User profile / preferences
    profile = {}
    if SHELBY_PROFILE_FILE.exists():
        try:
            with open(SHELBY_PROFILE_FILE) as f:
                profile = json.load(f)
        except Exception:
            pass

    # Conversation stats
    conversations = []
    if SHELBY_CONVERSATION_FILE.exists():
        try:
            with open(SHELBY_CONVERSATION_FILE) as f:
                conversations = json.load(f)
        except Exception:
            pass

    user_msgs = sum(1 for c in conversations if c.get("role") == "user")
    assistant_msgs = sum(1 for c in conversations if c.get("role") == "assistant")

    return jsonify({
        "running": shelby_running,
        "tasks": tasks[:30],
        "tasks_total": len(tasks),
        "tasks_pending": tasks_pending,
        "tasks_done": tasks_done,
        "profile": profile,
        "profile_keys": len(profile),
        "conversation_total": len(conversations),
        "user_messages": user_msgs,
        "assistant_messages": assistant_msgs,
    })


@app.route("/api/overview")
def api_overview():
    """High-level status of all agents."""
    # Garves
    trades = _load_trades()
    resolved = [t for t in trades if t.get("resolved") and t.get("outcome") not in ("unknown", None)]
    wins = sum(1 for t in resolved if t.get("won"))
    garves_wr = (wins / len(resolved) * 100) if resolved else 0
    garves_running = False
    try:
        import subprocess
        result = subprocess.run(["pgrep", "-f", "bot.main"], capture_output=True, text=True)
        garves_running = bool(result.stdout.strip())
    except Exception:
        pass

    # Indicator accuracy
    accuracy_data = {}
    if INDICATOR_ACCURACY_FILE.exists():
        try:
            with open(INDICATOR_ACCURACY_FILE) as f:
                accuracy_data = json.load(f)
        except Exception:
            pass

    # Soren
    soren_queue = 0
    soren_posted = 0
    if SOREN_QUEUE_FILE.exists():
        try:
            with open(SOREN_QUEUE_FILE) as f:
                q = json.load(f)
                soren_queue = sum(1 for x in q if x.get("status") == "pending")
                soren_posted = sum(1 for x in q if x.get("status") == "posted")
        except Exception:
            pass

    # Shelby
    shelby_running = False
    try:
        import subprocess
        result = subprocess.run(["pgrep", "-f", "app.py"], capture_output=True, text=True)
        shelby_running = bool(result.stdout.strip())
    except Exception:
        pass

    # Mercury review stats
    mercury_review_avg = None
    mercury_total_posts = 0
    if MERCURY_POSTING_LOG.exists():
        try:
            with open(MERCURY_POSTING_LOG) as f:
                mlog = json.load(f)
            mercury_total_posts = len(mlog)
            reviewed = [p for p in mlog if p.get("review_score") is not None and p.get("review_score", -1) != -1]
            if reviewed:
                mercury_review_avg = round(sum(p["review_score"] for p in reviewed) / len(reviewed), 1)
        except Exception:
            pass

    return jsonify({
        "garves": {
            "running": garves_running,
            "win_rate": round(garves_wr, 1),
            "total_trades": len(trades),
            "resolved": len(resolved),
            "wins": wins,
            "losses": len(resolved) - wins,
            "pending": len(trades) - len([t for t in trades if t.get("resolved")]),
            "indicator_accuracy": accuracy_data,
        },
        "soren": {
            "queue_pending": soren_queue,
            "total_posted": soren_posted,
        },
        "shelby": {
            "running": shelby_running,
        },
        "mercury": {
            "total_posts": mercury_total_posts,
            "review_avg": mercury_review_avg,
        },
    })


@app.route("/api/shelby/brief")
def api_shelby_brief():
    """Daily brief: aggregate activity from all agents + pending approvals."""
    now = datetime.now(ET)
    today_str = now.strftime("%A, %B %d, %Y")
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

    # Garves activity today
    trades = _load_trades()
    today_trades = [t for t in trades if t.get("timestamp", 0) >= today_start]
    today_resolved = [t for t in today_trades if t.get("resolved") and t.get("outcome") not in ("unknown", None)]
    today_wins = sum(1 for t in today_resolved if t.get("won"))
    today_losses = len(today_resolved) - today_wins
    today_wr = (today_wins / len(today_resolved) * 100) if today_resolved else 0
    today_pending = sum(1 for t in today_trades if not t.get("resolved"))
    # PnL today
    today_pnl = 0.0
    for t in today_resolved:
        implied = t.get("implied_up_price", 0.5)
        d = t.get("direction", "up")
        ep = implied if d == "up" else (1 - implied)
        if t.get("won"):
            today_pnl += 5.0 * (1 - ep) - 5.0 * 0.02
        else:
            today_pnl += -5.0 * ep

    garves_running = False
    try:
        result = subprocess.run(["pgrep", "-f", "bot.main"], capture_output=True, text=True)
        garves_running = bool(result.stdout.strip())
    except Exception:
        pass

    # Soren activity
    queue = []
    if SOREN_QUEUE_FILE.exists():
        try:
            with open(SOREN_QUEUE_FILE) as f:
                queue = json.load(f)
        except Exception:
            pass
    soren_pending = [q for q in queue if q.get("status") == "pending"]
    soren_posted_today = [q for q in queue if q.get("status") == "posted" and q.get("posted_at", "")[:10] == now.strftime("%Y-%m-%d")]
    soren_awaiting = [q for q in soren_pending if q.get("scheduled_time", "") <= now.isoformat()]

    # Shelby tasks
    tasks = []
    if SHELBY_TASKS_FILE.exists():
        try:
            with open(SHELBY_TASKS_FILE) as f:
                tasks = json.load(f)
        except Exception:
            pass
    active_tasks = [t for t in tasks if t.get("status") == "pending"]

    # Mercury / Lisa review stats
    mercury_brief = {"total_posts": 0}
    if MERCURY_POSTING_LOG.exists():
        try:
            with open(MERCURY_POSTING_LOG) as f:
                mlog = json.load(f)
            today_iso = now.strftime("%Y-%m-%d")
            today_posts = [p for p in mlog if p.get("posted_at", "")[:10] == today_iso]
            reviewed = [p for p in mlog if p.get("review_score") is not None and p.get("review_score", -1) != -1]
            mercury_brief = {
                "total_posts": len(mlog),
                "posted_today": len(today_posts),
                "reviews_total": len(reviewed),
                "review_avg": round(sum(p["review_score"] for p in reviewed) / len(reviewed), 1) if reviewed else None,
                "review_pass_rate": round(sum(1 for p in reviewed if p["review_score"] >= 7) / len(reviewed) * 100, 1) if reviewed else None,
            }
        except Exception:
            pass

    return jsonify({
        "date": today_str,
        "greeting": f"Good {'morning' if now.hour < 12 else 'afternoon' if now.hour < 17 else 'evening'}, sir.",
        "garves": {
            "running": garves_running,
            "trades_today": len(today_trades),
            "wins_today": today_wins,
            "losses_today": today_losses,
            "win_rate_today": round(today_wr, 1),
            "pnl_today": round(today_pnl, 2),
            "pending": today_pending,
        },
        "soren": {
            "queue_pending": len(soren_pending),
            "posted_today": len(soren_posted_today),
            "awaiting_approval": len(soren_awaiting),
            "awaiting_items": [{"id": q["id"], "title": q.get("title",""), "pillar": q.get("pillar",""), "platform": q.get("platform","")} for q in soren_awaiting[:10]],
        },
        "shelby": {
            "active_tasks": len(active_tasks),
            "tasks": [{"title": t.get("title",""), "due": t.get("due",""), "status": t.get("status","")} for t in active_tasks[:10]],
        },
        "mercury": mercury_brief,
        "approvals_needed": len(soren_awaiting),
    })


def _do_generate(item_id: str, item: dict, mode: str) -> None:
    """Background thread: generate a reel from a queue item."""
    import tempfile
    try:
        _generation_status[item_id] = {"status": "generating"}
        # Strip hashtags — only keep the actual caption text
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


@app.route("/api/soren/generate/<item_id>", methods=["POST"])
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


@app.route("/api/soren/gen-status/<item_id>")
def api_soren_gen_status(item_id):
    """Check generation status."""
    status = _generation_status.get(item_id, {"status": "none"})
    return jsonify(status)


@app.route("/api/soren/preview/<item_id>")
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


@app.route("/api/soren/download/<item_id>")
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


def _serve_video(video_path: str) -> Response:
    """Serve a video file with HTTP Range request support for browser streaming."""
    file_path = Path(video_path)
    file_size = file_path.stat().st_size

    range_header = request.headers.get("Range")
    if range_header:
        # Parse Range: bytes=START-END
        byte_range = range_header.replace("bytes=", "").strip()
        parts = byte_range.split("-")
        start = int(parts[0]) if parts[0] else 0
        end = int(parts[1]) if parts[1] else file_size - 1
        end = min(end, file_size - 1)
        length = end - start + 1

        with open(video_path, "rb") as f:
            f.seek(start)
            data = f.read(length)

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
        # No range requested — send full file with Accept-Ranges header
        resp = send_file(video_path, mimetype="video/mp4")
        resp.headers["Accept-Ranges"] = "bytes"
        resp.headers["Content-Length"] = str(file_size)
        return resp


@app.route("/api/soren/approve/<item_id>", methods=["POST"])
def api_soren_approve(item_id):
    """Approve a queue item — mark as ready to post."""
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


@app.route("/api/soren/reject/<item_id>", methods=["POST"])
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


@app.route("/api/soren/regenerate/<item_id>", methods=["POST"])
def api_soren_regenerate(item_id):
    """Reset a queue item for regeneration — removes old video reference."""
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


@app.route("/api/soren/custom-generate", methods=["POST"])
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
    with open(SOREN_QUEUE_FILE, "w") as f:
        json.dump(queue, f, indent=2)

    # Start generation
    thread = threading.Thread(target=_do_generate, args=(item_id, item, mode), daemon=True)
    thread.start()
    return jsonify({"status": "generating", "item_id": item_id})


@app.route("/api/atlas")
def api_atlas():
    """Atlas overview data."""
    atlas = get_atlas()
    if not atlas:
        return jsonify({"status": "offline", "error": "Atlas not available"})
    try:
        return jsonify(atlas.api_overview())
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)[:200]})


@app.route("/api/atlas/report", methods=["POST"])
def api_atlas_report():
    """Generate a full Atlas report."""
    atlas = get_atlas()
    if not atlas:
        return jsonify({"error": "Atlas not available"}), 503
    try:
        report = atlas.api_full_report()
        return jsonify(report)
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@app.route("/api/atlas/garves")
def api_atlas_garves():
    """Atlas deep analysis of Garves."""
    atlas = get_atlas()
    if not atlas:
        return jsonify({"error": "Atlas not available"}), 503
    try:
        return jsonify(atlas.api_garves_deep())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@app.route("/api/atlas/soren")
def api_atlas_soren():
    """Atlas deep analysis of Soren."""
    atlas = get_atlas()
    if not atlas:
        return jsonify({"error": "Atlas not available"}), 503
    try:
        return jsonify(atlas.api_soren_deep())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@app.route("/api/atlas/experiments")
def api_atlas_experiments():
    """Atlas experiment data."""
    atlas = get_atlas()
    if not atlas:
        return jsonify({"error": "Atlas not available"}), 503
    try:
        return jsonify(atlas.api_experiments())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@app.route("/api/atlas/knowledge")
def api_atlas_knowledge():
    """Atlas knowledge base."""
    atlas = get_atlas()
    if not atlas:
        return jsonify({"error": "Atlas not available"}), 503
    try:
        return jsonify(atlas.api_knowledge())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@app.route("/api/atlas/shelby")
def api_atlas_shelby():
    """Atlas deep analysis of Shelby."""
    atlas = get_atlas()
    if not atlas:
        return jsonify({"error": "Atlas not available"}), 503
    try:
        return jsonify(atlas.api_shelby_deep())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@app.route("/api/atlas/mercury")
def api_atlas_mercury():
    """Atlas deep analysis of Mercury."""
    atlas = get_atlas()
    if not atlas:
        return jsonify({"error": "Atlas not available"}), 503
    try:
        return jsonify(atlas.api_mercury_deep())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@app.route("/api/atlas/improvements", methods=["POST"])
def api_atlas_improvements():
    """Generate improvement suggestions for all agents."""
    atlas = get_atlas()
    if not atlas:
        return jsonify({"error": "Atlas not available"}), 503
    try:
        return jsonify(atlas.api_improvements())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@app.route("/api/atlas/improvements/acknowledge", methods=["POST"])
def api_atlas_acknowledge():
    """Acknowledge current improvements so Atlas stops repeating them."""
    atlas = get_atlas()
    if not atlas:
        return jsonify({"error": "Atlas not available"}), 503
    try:
        # Get current improvements and acknowledge all of them
        improvements = atlas.api_improvements()
        all_suggestions = []
        for key in ["garves", "soren", "shelby", "mercury", "new_skills", "new_agents", "system_wide"]:
            items = improvements.get(key, [])
            if isinstance(items, list):
                all_suggestions.extend(items)
        count = atlas.improvements.acknowledge(all_suggestions)
        return jsonify({"acknowledged": count, "total_dismissed": len(atlas.improvements._acknowledged)})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@app.route("/api/atlas/summarize", methods=["POST"])
def api_atlas_summarize():
    """Compress old observations into learnings."""
    atlas = get_atlas()
    if not atlas:
        return jsonify({"error": "Atlas not available"}), 503
    try:
        return jsonify(atlas.api_summarize_kb())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@app.route("/api/atlas/background/start", methods=["POST"])
def api_atlas_bg_start():
    """Start Atlas background research loop."""
    atlas = get_atlas()
    if not atlas:
        return jsonify({"error": "Atlas not available"}), 503
    try:
        return jsonify(atlas.api_start_background())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@app.route("/api/atlas/background/stop", methods=["POST"])
def api_atlas_bg_stop():
    """Stop Atlas background research loop."""
    atlas = get_atlas()
    if not atlas:
        return jsonify({"error": "Atlas not available"}), 503
    try:
        return jsonify(atlas.api_stop_background())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@app.route("/api/garves/report-4h")
def api_garves_report_4h():
    """Performance report broken down by 4-hour windows."""
    trades = _load_trades()
    now = datetime.now(ET)
    stake = float(os.getenv("ORDER_SIZE_USD", "5.0"))

    # Determine windows: 00:00, 04:00, 08:00, 12:00, 16:00, 20:00
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    windows = []
    for h in range(0, 24, 4):
        w_start = today_start.replace(hour=h)
        w_end = w_start + timedelta(hours=4)
        windows.append((w_start, w_end))

    # Also include yesterday's windows for context
    yesterday_start = today_start - timedelta(days=1)
    for h in range(0, 24, 4):
        w_start = yesterday_start.replace(hour=h)
        w_end = w_start + timedelta(hours=4)
        windows.insert(len(windows) - 6, (w_start, w_end))

    reports = []
    for w_start, w_end in windows:
        ts_start = w_start.timestamp()
        ts_end = w_end.timestamp()
        w_trades = [t for t in trades if ts_start <= t.get("timestamp", 0) < ts_end]
        if not w_trades:
            continue
        resolved = [t for t in w_trades if t.get("resolved") and t.get("outcome") not in ("unknown", None)]
        pending = [t for t in w_trades if not t.get("resolved")]
        wins = [t for t in resolved if t.get("won")]
        losses = [t for t in resolved if not t.get("won")]
        wr = (len(wins) / len(resolved) * 100) if resolved else 0

        pnl = 0.0
        for t in resolved:
            implied = t.get("implied_up_price", 0.5)
            d = t.get("direction", "up")
            ep = implied if d == "up" else (1 - implied)
            if t.get("won"):
                pnl += stake * (1 - ep) - stake * 0.02
            else:
                pnl += -stake * ep

        # Best/worst trade
        best_edge = max((t.get("edge", 0) for t in w_trades), default=0)
        avg_conf = sum(t.get("confidence", 0) for t in w_trades) / len(w_trades) if w_trades else 0

        # By asset breakdown
        by_asset = {}
        for t in resolved:
            a = t.get("asset", "unknown")
            if a not in by_asset:
                by_asset[a] = {"w": 0, "l": 0}
            if t.get("won"):
                by_asset[a]["w"] += 1
            else:
                by_asset[a]["l"] += 1

        is_current = w_start <= now < w_end
        reports.append({
            "window": f"{w_start.strftime('%b %d %I:%M %p')} - {w_end.strftime('%I:%M %p')}",
            "window_start": w_start.isoformat(),
            "is_current": is_current,
            "total": len(w_trades),
            "resolved": len(resolved),
            "pending": len(pending),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(wr, 1),
            "pnl": round(pnl, 2),
            "avg_confidence": round(avg_conf, 4),
            "best_edge": round(best_edge, 4),
            "by_asset": by_asset,
        })

    # Overall summary
    all_resolved = [t for t in trades if t.get("resolved") and t.get("outcome") not in ("unknown", None)]
    all_wins = sum(1 for t in all_resolved if t.get("won"))
    total_pnl = 0.0
    for t in all_resolved:
        implied = t.get("implied_up_price", 0.5)
        d = t.get("direction", "up")
        ep = implied if d == "up" else (1 - implied)
        if t.get("won"):
            total_pnl += stake * (1 - ep) - stake * 0.02
        else:
            total_pnl += -stake * ep

    return jsonify({
        "generated_at": now.isoformat(),
        "summary": {
            "total_trades": len(trades),
            "resolved": len(all_resolved),
            "wins": all_wins,
            "losses": len(all_resolved) - all_wins,
            "win_rate": round((all_wins / len(all_resolved) * 100) if all_resolved else 0, 1),
            "total_pnl": round(total_pnl, 2),
        },
        "windows": reports,
    })


@app.route("/api/chat", methods=["POST"])
def api_chat():
    """Group chat: send a message and get responses from all agents."""
    data = request.get_json()
    if not data or not data.get("message"):
        return jsonify({"error": "No message provided"}), 400

    user_msg = data["message"]
    timestamp = datetime.now(ET).isoformat()

    _chat_history.append({"role": "user", "agent": "you", "content": user_msg, "timestamp": timestamp})

    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        # Return placeholder responses if no API key
        responses = []
        for name in ["shelby", "soren", "garves"]:
            resp = {"agent": name, "content": f"[{name.upper()}: No OpenAI API key configured]", "timestamp": timestamp}
            responses.append(resp)
            _chat_history.append({"role": "assistant", "agent": name, "content": resp["content"], "timestamp": timestamp})
        return jsonify({"responses": responses, "history": _chat_history[-30:]})

    import requests as req

    responses = []
    for agent_name, system_prompt in _AGENT_PROMPTS.items():
        # Build conversation context for this agent
        messages = [{"role": "system", "content": system_prompt}]
        # Include recent chat history (last 10 exchanges)
        for h in _chat_history[-20:]:
            if h["role"] == "user":
                messages.append({"role": "user", "content": h["content"]})
            elif h["agent"] == agent_name:
                messages.append({"role": "assistant", "content": h["content"]})

        try:
            api_resp = req.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": "gpt-4o-mini", "messages": messages, "max_tokens": 200, "temperature": 0.8},
                timeout=15,
            )
            api_resp.raise_for_status()
            content = api_resp.json()["choices"][0]["message"]["content"]
        except Exception as e:
            content = f"[Error: {str(e)[:100]}]"

        resp = {"agent": agent_name, "content": content, "timestamp": timestamp}
        responses.append(resp)
        _chat_history.append({"role": "assistant", "agent": agent_name, "content": content, "timestamp": timestamp})

    return jsonify({"responses": responses, "history": _chat_history[-30:]})


@app.route("/api/chat/history")
def api_chat_history():
    """Get chat history."""
    return jsonify({"history": _chat_history[-50:]})


@app.route("/api/garves/regime")
def api_garves_regime():
    """Current market regime from Fear & Greed Index."""
    try:
        from bot.regime import detect_regime
        regime = detect_regime()
        return jsonify({
            "label": regime.label,
            "fng_value": regime.fng_value,
            "size_multiplier": regime.size_multiplier,
            "edge_multiplier": regime.edge_multiplier,
            "consensus_offset": regime.consensus_offset,
        })
    except Exception as e:
        return jsonify({"label": "unknown", "fng_value": -1, "error": str(e)[:200]})


@app.route("/api/atlas/competitors")
def api_atlas_competitors():
    """Competitor intelligence data."""
    if not COMPETITOR_INTEL_FILE.exists():
        return jsonify({"trading": [], "content": [], "ai_agents": [], "scanned_at": None})
    try:
        with open(COMPETITOR_INTEL_FILE) as f:
            data = json.load(f)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)[:200]})


@app.route("/api/shelby/schedule")
def api_shelby_schedule():
    """Shelby proactive scheduler status."""
    log_data = {}
    if SHELBY_SCHEDULER_LOG.exists():
        try:
            with open(SHELBY_SCHEDULER_LOG) as f:
                log_data = json.load(f)
        except Exception:
            pass

    # Handle both list format and dict format
    if isinstance(log_data, list):
        log_entries = log_data
    elif isinstance(log_data, dict):
        log_entries = log_data.get("today_log", [])
    else:
        log_entries = []

    now = datetime.now(ET)
    today_str = now.strftime("%Y-%m-%d")
    today_entries = [e for e in log_entries if isinstance(e, dict) and e.get("date", "")[:10] == today_str]

    schedule = {
        "07:00": {"name": "Morning Brief", "completed": False},
        "14:00": {"name": "Midday Content Review", "completed": False},
        "18:00": {"name": "Trading Report", "completed": False},
        "22:00": {"name": "End of Day Summary", "completed": False},
    }

    for entry in today_entries:
        time_key = entry.get("time_key", "") or entry.get("time", "")
        if time_key in schedule:
            schedule[time_key]["completed"] = True
            schedule[time_key]["result"] = entry.get("summary", "") or entry.get("result", "")
            schedule[time_key]["ran_at"] = entry.get("ran_at", "") or entry.get("executed_at", "")

    return jsonify({
        "schedule": schedule,
        "today_log": today_entries[-10:],
        "current_time": now.strftime("%H:%M"),
    })


@app.route("/api/shelby/economics")
def api_shelby_economics():
    """Agent economics data."""
    period = request.args.get("period", "month")

    ledger = []
    ledger_file = SHELBY_ROOT_DIR / "data" / "agent_economics.jsonl"
    if ledger_file.exists():
        try:
            with open(ledger_file) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            ledger.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        except Exception:
            pass

    now = datetime.now(ET)
    if period == "today":
        cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    elif period == "week":
        cutoff = (now - timedelta(days=7)).isoformat()
    elif period == "month":
        cutoff = (now - timedelta(days=30)).isoformat()
    else:
        cutoff = "2000-01-01"

    filtered = [e for e in ledger if e.get("timestamp", "") >= cutoff]

    agents_data = {}
    for entry in filtered:
        agent = entry.get("agent", "unknown")
        if agent not in agents_data:
            agents_data[agent] = {"costs": 0, "revenue": 0, "transactions": 0}
        if entry.get("type") == "cost":
            agents_data[agent]["costs"] += entry.get("amount", 0)
        elif entry.get("type") == "revenue":
            agents_data[agent]["revenue"] += entry.get("amount", 0)
        agents_data[agent]["transactions"] += 1

    total_cost = sum(a["costs"] for a in agents_data.values())
    total_revenue = sum(a["revenue"] for a in agents_data.values())
    roi = ((total_revenue - total_cost) / total_cost * 100) if total_cost > 0 else 0

    return jsonify({
        "period": period,
        "agents": agents_data,
        "total_cost": round(total_cost, 2),
        "total_revenue": round(total_revenue, 2),
        "net": round(total_revenue - total_cost, 2),
        "roi_pct": round(roi, 1),
        "total_transactions": len(filtered),
    })


@app.route("/api/mercury")
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


@app.route("/api/mercury/plan")
def api_mercury_plan():
    """Mercury evolving plan and knowledge dashboard data."""
    try:
        from mercury.core.brain import MercuryBrain
        brain = MercuryBrain()
        return jsonify(brain.get_dashboard_data())
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/api/mercury/knowledge")
def api_mercury_knowledge():
    """Mercury full knowledge base."""
    try:
        from mercury.core.brain import MercuryBrain
        brain = MercuryBrain()
        return jsonify(brain.get_knowledge())
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/api/mercury/reply", methods=["POST"])
def api_mercury_reply():
    """Get reply suggestion for a comment."""
    try:
        from mercury.core.brain import MercuryBrain
        brain = MercuryBrain()
        comment = request.json.get("comment", "")
        return jsonify(brain.get_reply_suggestion(comment))
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/api/mercury/review", methods=["POST"])
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


@app.route("/api/mercury/review/<item_id>", methods=["POST"])
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


# ── Robotox API ──

@app.route("/api/sentinel")
def api_sentinel():
    """Robotox health monitor status."""
    try:
        from sentinel.sentinel import Sentinel
        sentinel_agent = Sentinel()
        return jsonify(sentinel_agent.get_status())
    except Exception as e:
        return jsonify({"status": "offline", "error": str(e)})


@app.route("/api/sentinel/scan", methods=["POST"])
def api_sentinel_scan():
    """Trigger a full health scan."""
    try:
        from sentinel.sentinel import Sentinel
        sentinel_agent = Sentinel()
        return jsonify(sentinel_agent.full_scan())
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/api/sentinel/bugs")
def api_sentinel_bugs():
    """Get bug scan results."""
    try:
        from sentinel.sentinel import Sentinel
        sentinel_agent = Sentinel()
        return jsonify(sentinel_agent.quick_bug_scan())
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/api/sentinel/fixes")
def api_sentinel_fixes():
    """Get fix history."""
    try:
        from sentinel.sentinel import Sentinel
        sentinel_agent = Sentinel()
        return jsonify({"fixes": sentinel_agent.get_fix_history()})
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/api/sentinel/alerts")
def api_sentinel_alerts():
    """Get alerts."""
    try:
        from sentinel.sentinel import Sentinel
        sentinel_agent = Sentinel()
        return jsonify({"alerts": sentinel_agent.get_alerts()})
    except Exception as e:
        return jsonify({"error": str(e)})


# ── Shelby Dashboard Overhaul APIs ──

_system_cache = {"data": None, "ts": 0}
_weather_cache = {"data": None, "ts": 0}
_updates_cache = {"data": None, "ts": 0}


@app.route("/api/shelby/activity-brief")
def api_shelby_activity_brief():
    """Last-30-min activity summary per agent."""
    now = time.time()
    cutoff = now - 1800  # 30 min

    # Garves: recent trades
    trades = _load_trades()
    recent_trades = [t for t in trades if t.get("timestamp", 0) >= cutoff]
    garves_wins = sum(1 for t in recent_trades if t.get("resolved") and t.get("won"))
    garves_losses = sum(1 for t in recent_trades if t.get("resolved") and not t.get("won") and t.get("outcome") != "unknown")

    # Soren: queue changes
    soren_pending = 0
    soren_generated = 0
    if SOREN_QUEUE_FILE.exists():
        try:
            with open(SOREN_QUEUE_FILE) as f:
                queue = json.load(f)
            soren_pending = sum(1 for q in queue if q.get("status") == "pending")
            soren_generated = sum(1 for q in queue if q.get("status") in ("approved", "generated"))
        except Exception:
            pass

    # Atlas: background state
    atlas_state = "idle"
    atlas_cycles = 0
    atlas_status_file = ATLAS_ROOT / "data" / "background_status.json"
    if atlas_status_file.exists():
        try:
            with open(atlas_status_file) as f:
                bg = json.load(f)
            atlas_state = bg.get("state", "idle")
            atlas_cycles = bg.get("cycles", 0)
        except Exception:
            pass

    # Mercury: recent posts + review stats
    mercury_recent = 0
    mercury_review_avg = None
    mercury_review_total = 0
    if MERCURY_POSTING_LOG.exists():
        try:
            with open(MERCURY_POSTING_LOG) as f:
                posts = json.load(f)
            cutoff_iso = datetime.fromtimestamp(cutoff, tz=ET).isoformat()
            mercury_recent = sum(1 for p in posts if p.get("posted_at", "") >= cutoff_iso)
            reviewed = [p for p in posts if p.get("review_score") is not None and p.get("review_score", -1) != -1]
            mercury_review_total = len(reviewed)
            if reviewed:
                mercury_review_avg = round(sum(p["review_score"] for p in reviewed) / len(reviewed), 1)
        except Exception:
            pass

    # Robotox: last scan info
    sentinel_info = "idle"
    try:
        from sentinel.sentinel import Sentinel
        s = Sentinel()
        status = s.get_status()
        sentinel_info = "online" if status.get("agents_online", 0) > 0 else "idle"
    except Exception:
        pass

    return jsonify({
        "garves": {"trades_30m": len(recent_trades), "wins": garves_wins, "losses": garves_losses},
        "soren": {"pending": soren_pending, "generated": soren_generated},
        "atlas": {"state": atlas_state, "cycles": atlas_cycles},
        "mercury": {"posts_30m": mercury_recent, "review_avg": mercury_review_avg, "reviews_total": mercury_review_total},
        "sentinel": {"status": sentinel_info},
    })


@app.route("/api/shelby/export")
def api_shelby_export():
    """Export agent task data + 24h metrics as CSV."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Agent", "Metric", "Value"])

    # Garves metrics
    trades = _load_trades()
    now_ts = time.time()
    day_ago = now_ts - 86400
    day_trades = [t for t in trades if t.get("timestamp", 0) >= day_ago]
    resolved_day = [t for t in day_trades if t.get("resolved") and t.get("outcome") not in ("unknown", None)]
    wins_day = sum(1 for t in resolved_day if t.get("won"))
    writer.writerow(["Garves", "Trades (24h)", len(day_trades)])
    writer.writerow(["Garves", "Wins (24h)", wins_day])
    writer.writerow(["Garves", "Losses (24h)", len(resolved_day) - wins_day])
    wr = (wins_day / len(resolved_day) * 100) if resolved_day else 0
    writer.writerow(["Garves", "Win Rate (24h)", str(round(wr, 1)) + "%"])

    # Soren metrics
    if SOREN_QUEUE_FILE.exists():
        try:
            with open(SOREN_QUEUE_FILE) as f:
                queue = json.load(f)
            writer.writerow(["Soren", "Queue Total", len(queue)])
            writer.writerow(["Soren", "Pending", sum(1 for q in queue if q.get("status") == "pending")])
            writer.writerow(["Soren", "Posted", sum(1 for q in queue if q.get("status") == "posted")])
        except Exception:
            pass

    # Atlas metrics
    atlas_status_file = ATLAS_ROOT / "data" / "background_status.json"
    if atlas_status_file.exists():
        try:
            with open(atlas_status_file) as f:
                bg = json.load(f)
            writer.writerow(["Atlas", "Cycles", bg.get("cycles", 0)])
            writer.writerow(["Atlas", "State", bg.get("state", "unknown")])
        except Exception:
            pass

    # Lisa metrics
    if MERCURY_POSTING_LOG.exists():
        try:
            with open(MERCURY_POSTING_LOG) as f:
                posts = json.load(f)
            writer.writerow(["Lisa", "Total Posts", len(posts)])
            reviewed = [p for p in posts if p.get("review_score") is not None and p.get("review_score", -1) != -1]
            if reviewed:
                scores = [p["review_score"] for p in reviewed]
                writer.writerow(["Lisa", "Reviews Total", len(reviewed)])
                writer.writerow(["Lisa", "Avg Review Score", str(round(sum(scores) / len(scores), 1))])
                writer.writerow(["Lisa", "Reviews Passed", sum(1 for s in scores if s >= 7)])
                writer.writerow(["Lisa", "Reviews Warned", sum(1 for s in scores if 4 <= s < 7)])
                writer.writerow(["Lisa", "Reviews Failed", sum(1 for s in scores if s < 4)])
        except Exception:
            pass

    # Shelby tasks
    if SHELBY_TASKS_FILE.exists():
        try:
            with open(SHELBY_TASKS_FILE) as f:
                tasks = json.load(f)
            writer.writerow(["Shelby", "Total Tasks", len(tasks)])
            writer.writerow(["Shelby", "Pending Tasks", sum(1 for t in tasks if t.get("status") == "pending")])
            writer.writerow(["Shelby", "Done Tasks", sum(1 for t in tasks if t.get("status") in ("done", "completed"))])
        except Exception:
            pass

    csv_content = output.getvalue()
    return Response(
        csv_content,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=agent_report.csv"}
    )


@app.route("/api/shelby/system")
def api_shelby_system():
    """Mac system info + weather."""
    global _system_cache, _weather_cache, _updates_cache
    now = time.time()
    result = {}

    # CPU load
    try:
        result["load_avg"] = list(os.getloadavg())
    except Exception:
        result["load_avg"] = [0, 0, 0]

    # Memory
    try:
        import psutil
        mem = psutil.virtual_memory()
        result["memory"] = {"total_gb": round(mem.total / (1024**3), 1), "used_pct": mem.percent}
    except ImportError:
        try:
            vm = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5)
            result["memory"] = {"raw": vm.stdout[:200], "used_pct": -1}
        except Exception:
            result["memory"] = {"used_pct": -1}

    # Disk
    try:
        usage = shutil.disk_usage("/")
        result["disk"] = {
            "total_gb": round(usage.total / (1024**3), 1),
            "free_gb": round(usage.free / (1024**3), 1),
            "used_pct": round(usage.used / usage.total * 100, 1),
        }
    except Exception:
        result["disk"] = {"free_gb": -1, "used_pct": -1}

    # macOS updates (cached, max once per hour)
    if now - _updates_cache["ts"] > 3600:
        try:
            upd = subprocess.run(
                ["softwareupdate", "-l", "--no-scan"],
                capture_output=True, text=True, timeout=10,
            )
            lines = [l.strip() for l in upd.stdout.split("\n") if l.strip() and "Software Update" not in l]
            _updates_cache = {"data": lines[:5], "ts": now}
        except Exception:
            _updates_cache = {"data": [], "ts": now}
    result["updates"] = _updates_cache["data"]

    # Weather (cached 30 min)
    if now - _weather_cache["ts"] > 1800:
        try:
            import urllib.request
            req = urllib.request.Request(
                "https://wttr.in/Portsmouth+NH?format=j1",
                headers={"User-Agent": "curl/7.68.0"}
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                weather_data = json.loads(resp.read().decode())
            current = weather_data.get("current_condition", [{}])[0]
            _weather_cache = {
                "data": {
                    "temp_f": current.get("temp_F", "?"),
                    "feels_like_f": current.get("FeelsLikeF", "?"),
                    "desc": current.get("weatherDesc", [{}])[0].get("value", "?"),
                    "humidity": current.get("humidity", "?"),
                    "wind_mph": current.get("windspeedMiles", "?"),
                },
                "ts": now,
            }
        except Exception:
            _weather_cache = {"data": {"temp_f": "?", "desc": "unavailable"}, "ts": now}
    result["weather"] = _weather_cache["data"]

    return jsonify(result)


SHELBY_ASSESSMENTS_FILE = SHELBY_ROOT_DIR / "data" / "agent_assessments.json"
SHELBY_AGENT_REGISTRY_FILE = SHELBY_ROOT_DIR / "data" / "agent_registry.json"
SHELBY_TELEGRAM_CONFIG = SHELBY_ROOT_DIR / "data" / "telegram_config.json"

_DEFAULT_ASSESSMENTS = {
    "garves": {"score": 65, "trend": "up", "opinion": "Solid execution. Signal quality improving with new indicators."},
    "soren": {"score": 75, "trend": "stable", "opinion": "Content pipeline flowing well. Pillar mix is balanced."},
    "atlas": {"score": 80, "trend": "up", "opinion": "Background loop running consistently. Knowledge base growing."},
    "mercury": {"score": 65, "trend": "up", "opinion": "Brand review gate active. Every post scored against Soren's voice before publishing."},
    "robotox": {"score": 85, "trend": "stable", "opinion": "Watchman never sleeps. Auto-fix success rate high."},
}


@app.route("/api/shelby/assessments")
def api_shelby_assessments():
    """Shelby's opinion on each agent."""
    if SHELBY_ASSESSMENTS_FILE.exists():
        try:
            with open(SHELBY_ASSESSMENTS_FILE) as f:
                data = json.load(f)
            return jsonify(data)
        except Exception:
            pass
    # Create defaults
    SHELBY_ASSESSMENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SHELBY_ASSESSMENTS_FILE, "w") as f:
        json.dump(_DEFAULT_ASSESSMENTS, f, indent=2)
    return jsonify(_DEFAULT_ASSESSMENTS)


@app.route("/api/shelby/hire", methods=["POST"])
def api_shelby_hire():
    """Create a new agent entry."""
    data = request.get_json()
    if not data or not data.get("name"):
        return jsonify({"error": "Agent name is required"}), 400

    name = data["name"].lower().strip()
    role = data.get("role", "General")
    description = data.get("description", "")

    registry = {}
    if SHELBY_AGENT_REGISTRY_FILE.exists():
        try:
            with open(SHELBY_AGENT_REGISTRY_FILE) as f:
                registry = json.load(f)
        except Exception:
            pass

    if name in registry:
        return jsonify({"error": "Agent already exists"}), 409

    agent_entry = {
        "name": name,
        "role": role,
        "description": description,
        "created_at": datetime.now(ET).isoformat(),
        "status": "inactive",
    }
    registry[name] = agent_entry

    SHELBY_AGENT_REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SHELBY_AGENT_REGISTRY_FILE, "w") as f:
        json.dump(registry, f, indent=2)

    return jsonify({"success": True, "agent": agent_entry})


@app.route("/api/agent/<agent>/kpis")
def api_agent_kpis(agent):
    """Per-agent KPIs."""
    agent = agent.lower()
    kpis = {}

    if agent == "garves":
        trades = _load_trades()
        resolved = [t for t in trades if t.get("resolved") and t.get("outcome") not in ("unknown", None)]
        wins = sum(1 for t in resolved if t.get("won"))
        wr = (wins / len(resolved) * 100) if resolved else 0
        stake = float(os.getenv("ORDER_SIZE_USD", "5.0"))
        pnl = 0.0
        for t in resolved:
            implied = t.get("implied_up_price", 0.5)
            d = t.get("direction", "up")
            ep = implied if d == "up" else (1 - implied)
            if t.get("won"):
                pnl += stake * (1 - ep) - stake * 0.02
            else:
                pnl += -stake * ep
        # Trades per day
        if trades:
            first_ts = min(t.get("timestamp", time.time()) for t in trades)
            days = max(1, (time.time() - first_ts) / 86400)
            trades_per_day = round(len(trades) / days, 1)
        else:
            trades_per_day = 0
        # Best timeframe
        by_tf = {}
        for t in resolved:
            tf = t.get("timeframe", "?")
            if tf not in by_tf:
                by_tf[tf] = {"w": 0, "l": 0}
            if t.get("won"):
                by_tf[tf]["w"] += 1
            else:
                by_tf[tf]["l"] += 1
        best_tf = "N/A"
        best_wr = 0
        for tf, d in by_tf.items():
            total = d["w"] + d["l"]
            if total >= 3:
                tfwr = d["w"] / total * 100
                if tfwr > best_wr:
                    best_wr = tfwr
                    best_tf = tf
        avg_edge = 0
        if trades:
            avg_edge = sum(t.get("edge", 0) for t in trades) / len(trades)
        kpis = {
            "win_rate": round(wr, 1),
            "pnl": round(pnl, 2),
            "trades_per_day": trades_per_day,
            "avg_edge": round(avg_edge * 100, 2),
            "best_timeframe": best_tf,
            "total_trades": len(trades),
            "resolved": len(resolved),
        }
    elif agent == "soren":
        if SOREN_QUEUE_FILE.exists():
            try:
                with open(SOREN_QUEUE_FILE) as f:
                    queue = json.load(f)
                total = len(queue)
                posted = sum(1 for q in queue if q.get("status") == "posted")
                approved = sum(1 for q in queue if q.get("status") == "approved")
                pending = sum(1 for q in queue if q.get("status") == "pending")
                by_pillar = {}
                for q in queue:
                    p = q.get("pillar", "unknown")
                    by_pillar[p] = by_pillar.get(p, 0) + 1
                kpis = {
                    "content_produced": total,
                    "approval_rate": round(((posted + approved) / total * 100) if total else 0, 1),
                    "pending": pending,
                    "posted": posted,
                    "pillar_distribution": by_pillar,
                }
            except Exception:
                kpis = {"content_produced": 0}
        else:
            kpis = {"content_produced": 0}
    elif agent == "atlas":
        atlas_status_file = ATLAS_ROOT / "data" / "background_status.json"
        kb_file = ATLAS_ROOT / "data" / "knowledge_base.json"
        cycles = 0
        obs = 0
        improvements = 0
        if atlas_status_file.exists():
            try:
                with open(atlas_status_file) as f:
                    bg = json.load(f)
                cycles = bg.get("cycles", 0)
            except Exception:
                pass
        if kb_file.exists():
            try:
                with open(kb_file) as f:
                    kb = json.load(f)
                obs = len(kb.get("observations", []))
                improvements = len(kb.get("improvements", []))
            except Exception:
                pass
        kpis = {
            "cycles_total": cycles,
            "observations": obs,
            "improvements": improvements,
            "obs_per_cycle": round(obs / max(1, cycles), 1),
        }
    elif agent == "mercury":
        total_posts = 0
        review_kpis = {}
        if MERCURY_POSTING_LOG.exists():
            try:
                with open(MERCURY_POSTING_LOG) as f:
                    posts = json.load(f)
                total_posts = len(posts)
                reviewed = [p for p in posts if p.get("review_score") is not None and p.get("review_score", -1) != -1]
                if reviewed:
                    scores = [p["review_score"] for p in reviewed]
                    review_kpis = {
                        "total_reviewed": len(reviewed),
                        "avg_score": str(round(sum(scores) / len(scores), 1)) + "/10",
                        "passed": sum(1 for s in scores if s >= 7),
                        "warned": sum(1 for s in scores if 4 <= s < 7),
                        "failed": sum(1 for s in scores if s < 4),
                        "pass_rate": str(round(sum(1 for s in scores if s >= 7) / len(scores) * 100, 1)) + "%",
                    }
            except Exception:
                pass
        # Platform breakdown
        platform_dist = {}
        if MERCURY_POSTING_LOG.exists():
            try:
                with open(MERCURY_POSTING_LOG) as f:
                    posts = json.load(f)
                for p in posts:
                    plat = p.get("platform", "unknown")
                    platform_dist[plat] = platform_dist.get(plat, 0) + 1
            except Exception:
                pass
        kpis = {
            "total_posts": total_posts,
            "mode": "semi-auto",
        }
        if review_kpis:
            kpis["brand_review"] = review_kpis
        if platform_dist:
            kpis["platform_distribution"] = platform_dist
    elif agent == "sentinel":
        try:
            from sentinel.sentinel import Sentinel
            s = Sentinel()
            status = s.get_status()
            kpis = {
                "agents_online": status.get("agents_online", 0),
                "total_scans": status.get("total_scans", 0),
                "issues_detected": status.get("active_issues", 0),
                "auto_fixes": status.get("total_fixes", 0),
            }
        except Exception:
            kpis = {"agents_online": 0, "total_scans": 0}

    return jsonify(kpis)


@app.route("/api/atlas/learning/<agent>")
def api_atlas_learning(agent):
    """Learning status for an agent from Atlas knowledge base."""
    agent = agent.lower()
    kb_file = ATLAS_ROOT / "data" / "knowledge_base.json"
    result = {"observations": 0, "hypotheses": 0, "improvements_applied": 0, "learning_score": "Novice"}

    if kb_file.exists():
        try:
            with open(kb_file) as f:
                kb = json.load(f)
            all_obs = kb.get("observations", [])
            agent_obs = [o for o in all_obs if o.get("agent", "").lower() == agent or agent in str(o.get("tags", "")).lower()]
            result["observations"] = len(agent_obs)

            all_hyp = kb.get("hypotheses", [])
            agent_hyp = [h for h in all_hyp if h.get("agent", "").lower() == agent or agent in str(h).lower()]
            result["hypotheses"] = len(agent_hyp)

            all_imp = kb.get("improvements", [])
            agent_imp = [im for im in all_imp if im.get("agent", "").lower() == agent or agent in str(im).lower()]
            result["improvements_applied"] = len(agent_imp)

            total = result["observations"] + result["hypotheses"] + result["improvements_applied"]
            if total >= 50:
                result["learning_score"] = "Expert"
            elif total >= 20:
                result["learning_score"] = "Advanced"
            elif total >= 5:
                result["learning_score"] = "Intermediate"
            else:
                result["learning_score"] = "Novice"
        except Exception:
            pass

    return jsonify(result)





if __name__ == "__main__":
    import webbrowser
    import threading

    # Auto-start Atlas background research loop
    def _auto_start_atlas():
        try:
            atlas = get_atlas()
            if atlas and not atlas.background.is_running():
                atlas.start_background()
                print("[Dashboard] Atlas background research loop auto-started")
        except Exception as e:
            print(f"[Dashboard] Atlas auto-start failed: {e}")

    threading.Timer(2.0, _auto_start_atlas).start()
    threading.Timer(1.0, lambda: webbrowser.open("http://localhost:8877")).start()
    app.run(host="0.0.0.0", port=8877, debug=False, threaded=True)
