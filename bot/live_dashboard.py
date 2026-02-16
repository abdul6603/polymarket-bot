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
    try:
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
    except Exception:
        return []
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
    try:
        with open(SOREN_QUEUE_FILE, "w") as f:
            json.dump(queue, f, indent=2)
    except Exception as e:
        return jsonify({"error": f"Failed to write queue file: {e}"}), 500

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


@app.route("/api/atlas/live-research")
def api_atlas_live_research():
    """What Atlas is currently researching — recent URLs, sources, insights."""
    atlas = get_atlas()
    if not atlas:
        return jsonify({"error": "Atlas not available", "articles": []})
    try:
        stats = atlas.researcher.get_research_stats()
        articles = []
        for entry in reversed(stats.get("recent", [])):
            articles.append({
                "agent": entry.get("agent", "?"),
                "query": entry.get("query", ""),
                "source": entry.get("source", ""),
                "url": entry.get("url", ""),
                "insight": entry.get("insight", "")[:200],
                "quality": entry.get("quality_score", 0),
                "timestamp": entry.get("timestamp", ""),
            })
        return jsonify({
            "total_researched": stats.get("total_researches", 0),
            "seen_urls": stats.get("seen_urls", 0),
            "articles": articles,
        })
    except Exception as e:
        return jsonify({"error": str(e)[:200], "articles": []})


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


@app.route("/api/atlas/costs")
def api_atlas_costs():
    """API cost tracker data (Tavily + OpenAI)."""
    cost_file = ATLAS_ROOT / "data" / "cost_tracker.json"
    if not cost_file.exists():
        return jsonify({"today_tavily": 0, "today_openai": 0,
                        "month_tavily": 0, "month_openai": 0,
                        "projected_tavily": 0})
    try:
        with open(cost_file) as f:
            tracker = json.load(f)

        daily = tracker.get("daily", {})
        today = datetime.now(timezone(timedelta(hours=-5))).strftime("%Y-%m-%d")
        today_data = daily.get(today, {})

        # Monthly totals
        month_prefix = today[:7]  # "YYYY-MM"
        month_tavily = 0
        month_openai = 0
        days_in_month = 0
        for day_key, day_data in daily.items():
            if day_key.startswith(month_prefix):
                month_tavily += day_data.get("tavily_calls", 0)
                month_openai += day_data.get("openai_calls", 0)
                days_in_month += 1

        # Project monthly usage (calls + dollars)
        # Budgets: Tavily $90/mo (12k credits), OpenAI $50/mo
        TAVILY_BUDGET = 90.0
        TAVILY_MONTHLY_CREDITS = 12000
        OPENAI_BUDGET = 50.0
        # GPT-4o-mini pricing: $0.15/1M input + $0.60/1M output
        # Atlas uses ~300 output tokens + ~500 input tokens per call
        OPENAI_COST_PER_CALL = (500 * 0.15 + 300 * 0.60) / 1_000_000  # ~$0.000255

        month_openai_tokens = 0
        for day_key, day_data in daily.items():
            if day_key.startswith(month_prefix):
                month_openai_tokens += day_data.get("openai_tokens_est", 0)

        if days_in_month > 0:
            avg_daily_tavily = month_tavily / days_in_month
            avg_daily_openai = month_openai / days_in_month
            projected_tavily = int(avg_daily_tavily * 30)
            projected_openai = int(avg_daily_openai * 30)
        else:
            projected_tavily = 0
            projected_openai = 0

        # Dollar projections
        tavily_cost_projected = round(TAVILY_BUDGET * (projected_tavily / TAVILY_MONTHLY_CREDITS), 2) if TAVILY_MONTHLY_CREDITS else 0
        # OpenAI: use actual token estimates if available, else per-call estimate
        if month_openai_tokens > 0 and days_in_month > 0:
            avg_daily_tokens = month_openai_tokens / days_in_month
            projected_tokens = avg_daily_tokens * 30
            openai_cost_projected = round(projected_tokens * 0.60 / 1_000_000, 2)  # mostly output tokens
        else:
            openai_cost_projected = round(projected_openai * OPENAI_COST_PER_CALL, 2)

        return jsonify({
            "today_tavily": today_data.get("tavily_calls", 0),
            "today_openai": today_data.get("openai_calls", 0),
            "month_tavily": month_tavily,
            "month_openai": month_openai,
            "projected_tavily": projected_tavily,
            "projected_openai": projected_openai,
            "tavily_budget": TAVILY_BUDGET,
            "openai_budget": OPENAI_BUDGET,
            "tavily_cost_projected": tavily_cost_projected,
            "openai_cost_projected": openai_cost_projected,
            "total_cost_projected": round(tavily_cost_projected + openai_cost_projected, 2),
            "total_budget": TAVILY_BUDGET + OPENAI_BUDGET,
            "daily": daily,
        })
    except Exception as e:
        return jsonify({"error": str(e)[:200]})


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


@app.route("/api/atlas/background/status")
def api_atlas_bg_status():
    """Get Atlas background loop status — state, cycles, last cycle, errors, research stats."""
    atlas = get_atlas()
    if not atlas:
        return jsonify({"running": False, "state": "offline", "error": "Atlas not available"})
    try:
        bg = atlas.background
        status = bg.get_status()
        research = status.get("research_stats", {})
        data_feed = status.get("data_feed", {})
        # Build a clean response
        state = status.get("state", "idle")
        state_labels = {
            "idle": "Idle",
            "running": "Waiting for next cycle",
            "researching": "Researching agents",
            "feeding_agents": "Feeding data to agents",
            "teaching_lisa": "Teaching Lisa",
            "analyzing": "Analyzing agents",
            "spying": "Competitor intelligence",
            "v2_anomaly_detection": "Anomaly detection",
            "v2_experiment_runner": "Running experiments",
            "v2_onchain": "On-chain analysis",
            "generating_improvements": "Generating improvements",
            "summarizing_kb": "Summarizing knowledge base",
            "learning": "Learning from patterns",
            "v2_report_delivery": "Delivering reports",
            "stopped": "Stopped",
        }
        return jsonify({
            "running": status.get("running", False),
            "state": state,
            "state_label": state_labels.get(state, state.replace("_", " ").title()),
            "cycles": status.get("cycles", 0),
            "started_at": status.get("started_at", None),
            "last_cycle": status.get("last_cycle", None),
            "last_findings": status.get("last_findings", 0),
            "last_error": status.get("last_error", None),
            "total_researches": research.get("total_researches", 0),
            "unique_urls": research.get("seen_urls", 0) if isinstance(research.get("seen_urls"), int) else len(research.get("seen_urls", [])),
            "data_feed_active": data_feed.get("active", False),
            "data_feed_sources": data_feed.get("sources_count", 0),
            "current_target": status.get("current_target", None),
            "recent_learn_count": status.get("recent_learn_count", 0),
            "cycle_minutes": 45,
        })
    except Exception as e:
        return jsonify({"running": False, "state": "error", "error": str(e)[:200]})


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
    try:
        with open(SHELBY_ASSESSMENTS_FILE, "w") as f:
            json.dump(_DEFAULT_ASSESSMENTS, f, indent=2)
    except Exception:
        pass
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
    try:
        with open(SHELBY_AGENT_REGISTRY_FILE, "w") as f:
            json.dump(registry, f, indent=2)
    except Exception as e:
        return jsonify({"error": f"Failed to write registry: {e}"}), 500

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





@app.route("/api/intelligence")
def api_intelligence():
    """Intelligence meter for all agents — 5 dimensions each, 0-100 scale."""
    from datetime import datetime, timezone, timedelta
    ET = timezone(timedelta(hours=-5))

    result = {}

    # ── GARVES — The Trader ──
    try:
        garves = {"dimensions": {}, "overall": 0, "title": "The Trader"}
        trades_file = DATA_DIR / "trades.jsonl"
        trades = []
        if trades_file.exists():
            with open(trades_file) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        trades.append(json.loads(line))

        resolved = [t for t in trades if t.get("resolved")]
        wins = sum(1 for t in resolved if t.get("won"))
        wr = (wins / len(resolved) * 100) if resolved else 0
        total = len(trades)

        # 1. Market Knowledge — indicators, regime awareness, asset coverage
        indicators_file = DATA_DIR / "indicator_accuracy.json"
        indicator_count = 0
        if indicators_file.exists():
            with open(indicators_file) as f:
                ind = json.load(f)
            indicator_count = len(ind)
        regime_file = DATA_DIR / "regime_state.json"
        has_regime = regime_file.exists()
        assets = set(t.get("asset", "") for t in trades)
        knowledge = min(100, indicator_count * 6 + (20 if has_regime else 0) + len(assets) * 10)
        garves["dimensions"]["Market Knowledge"] = knowledge

        # 2. Accuracy — win rate performance
        accuracy = min(100, int(wr * 1.3)) if resolved else 15
        garves["dimensions"]["Accuracy"] = accuracy

        # 3. Experience — total trades placed
        experience = min(100, int(total * 0.5)) if total else 5
        garves["dimensions"]["Experience"] = experience

        # 4. Risk Management — edge thresholds, straddle awareness
        straddle_file = DATA_DIR / "straddle_trades.json"
        has_straddle = straddle_file.exists()
        avg_edge = 0
        if resolved:
            edges = [t.get("edge", 0) for t in resolved if t.get("edge")]
            avg_edge = sum(edges) / len(edges) if edges else 0
        risk = 40 + (20 if has_straddle else 0) + min(40, int(avg_edge * 400))
        garves["dimensions"]["Risk Management"] = min(100, risk)

        # 5. Adaptability — multi-asset, multi-timeframe, regime switching
        timeframes = set(t.get("timeframe", "") for t in trades)
        adaptability = min(100, len(assets) * 15 + len(timeframes) * 15 + (25 if has_regime else 0))
        garves["dimensions"]["Adaptability"] = adaptability

        scores = list(garves["dimensions"].values())
        garves["overall"] = int(sum(scores) / len(scores)) if scores else 0
        result["garves"] = garves
    except Exception:
        result["garves"] = {"dimensions": {}, "overall": 0, "title": "The Trader"}

    # ── SOREN — The Thinker ──
    try:
        soren = {"dimensions": {}, "overall": 0, "title": "The Thinker"}
        queue_file = Path.home() / "soren-content" / "data" / "content_queue.json"
        queue = []
        if queue_file.exists():
            with open(queue_file) as f:
                queue = json.load(f)

        posted = [i for i in queue if i.get("status") == "posted"]
        pending = [i for i in queue if i.get("status") == "pending"]
        approved = [i for i in queue if i.get("status") == "approved"]
        pillars = set(i.get("pillar", "") for i in queue if i.get("pillar"))
        platforms = set(i.get("platform", "") for i in queue if i.get("platform"))

        # 1. Creativity — pillar diversity, total content generated
        creativity = min(100, len(pillars) * 15 + len(queue) * 2)
        soren["dimensions"]["Creativity"] = creativity

        # 2. Productivity — content output volume
        productivity = min(100, len(posted) * 10 + len(approved) * 5 + len(pending) * 2)
        soren["dimensions"]["Productivity"] = productivity

        # 3. Brand Consistency — voice, archetype adherence
        # Based on: has voice config, uses consistent pillars, A/B testing
        ab_file = Path.home() / "soren-content" / "data" / "ab_results.json"
        has_ab = ab_file.exists()
        consistency = 55 + (15 if has_ab else 0) + min(30, len(pillars) * 6)
        soren["dimensions"]["Brand Voice"] = min(100, consistency)

        # 4. Platform Awareness — multi-platform coverage
        platform_score = min(100, len(platforms) * 25 + 20)
        soren["dimensions"]["Platform Reach"] = platform_score

        # 5. Trend Awareness — from Atlas learnings about Soren
        trend_file = Path.home() / "soren-content" / "data" / "trend_topics.json"
        trends_count = 0
        if trend_file.exists():
            with open(trend_file) as f:
                trends_count = len(json.load(f))
        trend_score = min(100, 30 + trends_count * 5)
        soren["dimensions"]["Trend Awareness"] = trend_score

        scores = list(soren["dimensions"].values())
        soren["overall"] = int(sum(scores) / len(scores)) if scores else 0
        result["soren"] = soren
    except Exception:
        result["soren"] = {"dimensions": {}, "overall": 0, "title": "The Thinker"}

    # ── ATLAS — The Scientist ──
    try:
        atlas_intel = {"dimensions": {}, "overall": 0, "title": "The Scientist"}
        atlas = get_atlas()
        kb_stats = atlas.kb.stats() if atlas else {}
        bg = atlas.background.get_status() if atlas else {}
        research = atlas.researcher.get_research_stats() if atlas else {}

        obs = kb_stats.get("total_observations", 0)
        learnings = kb_stats.get("total_learnings", 0)
        cycles = bg.get("cycles", 0)
        quality = research.get("avg_quality_score", 0)
        seen = research.get("seen_urls", 0)

        # 1. Knowledge Depth — observations + learnings accumulated
        depth = min(100, int(obs * 0.2 + learnings * 1.5))
        atlas_intel["dimensions"]["Knowledge Depth"] = depth

        # 2. Research Quality — avg quality score from LLM synthesis
        rq = min(100, int(quality * 10)) if quality else 20
        atlas_intel["dimensions"]["Research Quality"] = rq

        # 3. Learning Rate — learnings per cycle
        lr = min(100, int((learnings / max(cycles, 1)) * 25))
        atlas_intel["dimensions"]["Learning Rate"] = lr

        # 4. Coverage — how many agents it covers + URL diversity
        agents_covered = len(kb_stats.get("agents_observed", []))
        coverage = min(100, agents_covered * 15 + min(40, seen))
        atlas_intel["dimensions"]["Agent Coverage"] = coverage

        # 5. Synthesis — ability to turn raw data into insights (experiments + improvements)
        exp_stats = atlas.hypothesis.stats() if atlas else {}
        improvements_file = Path.home() / "atlas" / "data" / "improvements.json"
        imp_count = 0
        if improvements_file.exists():
            with open(improvements_file) as f:
                imp_data = json.load(f)
            imp_count = sum(len(v) for v in imp_data.values() if isinstance(v, list))
        synthesis = min(100, exp_stats.get("completed", 0) * 10 + imp_count * 3 + 20)
        atlas_intel["dimensions"]["Synthesis"] = synthesis

        scores = list(atlas_intel["dimensions"].values())
        atlas_intel["overall"] = int(sum(scores) / len(scores)) if scores else 0
        result["atlas"] = atlas_intel
    except Exception:
        result["atlas"] = {"dimensions": {}, "overall": 0, "title": "The Scientist"}

    # ── SHELBY — The Commander ──
    try:
        shelby = {"dimensions": {}, "overall": 0, "title": "The Commander"}

        # 1. Team Awareness — assessments quality, monitoring breadth
        assess_file = SHELBY_ROOT_DIR / "data" / "agent_assessments.json"
        assessments = {}
        if assess_file.exists():
            with open(assess_file) as f:
                assessments = json.load(f)
        awareness = min(100, len(assessments) * 15 + 25)
        shelby["dimensions"]["Team Awareness"] = awareness

        # 2. Task Management — completion rate
        tasks_file = SHELBY_ROOT_DIR / "data" / "tasks.json"
        task_completion = 50
        if tasks_file.exists():
            with open(tasks_file) as f:
                tasks = json.load(f)
            done = sum(1 for t in tasks if t.get("done") or t.get("status") == "done")
            total = len(tasks)
            if total > 0:
                task_completion = min(100, int(done / total * 100) + 10)
        shelby["dimensions"]["Task Management"] = task_completion

        # 3. Communication — broadcasts sent, telegram connected
        bc_file = SHELBY_ROOT_DIR / "data" / "broadcasts.json"
        bc_count = 0
        if bc_file.exists():
            with open(bc_file) as f:
                bc_count = len(json.load(f))
        comm = min(100, 40 + bc_count * 5)
        shelby["dimensions"]["Communication"] = comm

        # 4. Scheduling — routine reliability
        sched_file = SHELBY_ROOT_DIR / "data" / "scheduler_log.json"
        sched_count = 0
        if sched_file.exists():
            with open(sched_file) as f:
                sched_count = len(json.load(f))
        scheduling = min(100, 35 + sched_count * 3)
        shelby["dimensions"]["Scheduling"] = scheduling

        # 5. Decision Quality — agent scores average
        avg_score = 0
        if assessments:
            agent_scores = [a.get("score", 0) for a in assessments.values()]
            avg_score = sum(agent_scores) / len(agent_scores) if agent_scores else 0
        decision = min(100, int(avg_score * 1.2) + 10)
        shelby["dimensions"]["Decision Quality"] = decision

        scores = list(shelby["dimensions"].values())
        shelby["overall"] = int(sum(scores) / len(scores)) if scores else 0
        result["shelby"] = shelby
    except Exception:
        result["shelby"] = {"dimensions": {}, "overall": 0, "title": "The Commander"}

    # ── LISA — The Operator ──
    try:
        lisa = {"dimensions": {}, "overall": 0, "title": "The Operator"}
        mercury_root = Path.home() / "mercury"

        # 1. Platform Knowledge — brain insights, strategy plan depth
        brain_file = mercury_root / "data" / "brain.json"
        insights_count = 0
        if brain_file.exists():
            with open(brain_file) as f:
                brain = json.load(f)
            insights_count = len(brain.get("insights", []))
        platform_knowledge = min(100, 25 + insights_count * 3)
        lisa["dimensions"]["Platform Knowledge"] = platform_knowledge

        # 2. Posting Discipline — consistency, outbox management
        posting_log = mercury_root / "data" / "posting_log.json"
        post_count = 0
        if posting_log.exists():
            with open(posting_log) as f:
                post_count = len(json.load(f))
        discipline = min(100, 20 + post_count * 5)
        lisa["dimensions"]["Posting Discipline"] = discipline

        # 3. Brand Alignment — review scores
        review_file = mercury_root / "data" / "brand_reviews.json"
        avg_review = 0
        if review_file.exists():
            with open(review_file) as f:
                reviews = json.load(f)
            if reviews:
                scores_list = [r.get("score", 0) for r in reviews if r.get("score")]
                avg_review = sum(scores_list) / len(scores_list) if scores_list else 0
        brand = min(100, int(avg_review * 10) + 20) if avg_review else 40
        lisa["dimensions"]["Brand Alignment"] = brand

        # 4. Strategy Depth — plan evolution
        plan_file = mercury_root / "data" / "strategy_plan.json"
        plan_depth = 30
        if plan_file.exists():
            with open(plan_file) as f:
                plan = json.load(f)
            plan_depth = min(100, 30 + len(str(plan)) // 100)
        lisa["dimensions"]["Strategy Depth"] = plan_depth

        # 5. Engagement IQ — reply intelligence, audience understanding
        reply_file = mercury_root / "data" / "reply_templates.json"
        reply_count = 0
        if reply_file.exists():
            with open(reply_file) as f:
                reply_count = len(json.load(f))
        engagement = min(100, 25 + reply_count * 5 + insights_count * 2)
        lisa["dimensions"]["Engagement IQ"] = engagement

        scores = list(lisa["dimensions"].values())
        lisa["overall"] = int(sum(scores) / len(scores)) if scores else 0
        result["lisa"] = lisa
    except Exception:
        result["lisa"] = {"dimensions": {}, "overall": 0, "title": "The Operator"}

    # ── ROBOTOX — The Watchman ──
    try:
        robotox = {"dimensions": {}, "overall": 0, "title": "The Watchman"}
        sentinel_root = Path.home() / "sentinel"

        # 1. Detection — issues found, scan coverage
        scan_file = sentinel_root / "data" / "scan_results.json"
        total_issues = 0
        scan_count = 0
        if scan_file.exists():
            with open(scan_file) as f:
                scans = json.load(f)
            if isinstance(scans, list):
                scan_count = len(scans)
                total_issues = sum(s.get("issues_found", s.get("issues", 0)) for s in scans)
        detection = min(100, 40 + scan_count * 3 + total_issues * 2)
        robotox["dimensions"]["Detection"] = detection

        # 2. Auto-Fix — fixes applied without human intervention
        fix_file = sentinel_root / "data" / "fix_log.json"
        fix_count = 0
        if fix_file.exists():
            with open(fix_file) as f:
                fix_count = len(json.load(f))
        autofix = min(100, 30 + fix_count * 8)
        robotox["dimensions"]["Auto-Fix"] = autofix

        # 3. Vigilance — monitoring uptime, scan frequency
        vigilance = min(100, 35 + scan_count * 5)
        robotox["dimensions"]["Vigilance"] = vigilance

        # 4. Coverage — number of agents/ports monitored
        config_file = sentinel_root / "config.json"
        agents_monitored = 5  # default
        if config_file.exists():
            with open(config_file) as f:
                cfg = json.load(f)
            agents_monitored = len(cfg.get("agents", {}))
        coverage = min(100, agents_monitored * 15 + 20)
        robotox["dimensions"]["Coverage"] = coverage

        # 5. Response Speed — how fast issues get resolved
        response = 50 + min(50, fix_count * 5)
        robotox["dimensions"]["Response Speed"] = min(100, response)

        scores = list(robotox["dimensions"].values())
        robotox["overall"] = int(sum(scores) / len(scores)) if scores else 0
        result["robotox"] = robotox
    except Exception:
        result["robotox"] = {"dimensions": {}, "overall": 0, "title": "The Watchman"}

    # ── TEAM — Collective Intelligence ──
    try:
        team = {"dimensions": {}, "overall": 0, "title": "Brotherhood"}
        agents = ["garves", "soren", "atlas", "shelby", "lisa", "robotox"]
        agent_scores = {a: result.get(a, {}).get("overall", 0) for a in agents}

        # 1. Collective Knowledge — sum of all agents' knowledge/depth
        # Weighted: Atlas (research powerhouse) contributes most
        atlas_depth = result.get("atlas", {}).get("dimensions", {}).get("Knowledge Depth", 0)
        garves_know = result.get("garves", {}).get("dimensions", {}).get("Market Knowledge", 0)
        soren_brand = result.get("soren", {}).get("dimensions", {}).get("Brand Voice", 0)
        shelby_aware = result.get("shelby", {}).get("dimensions", {}).get("Team Awareness", 0)
        lisa_plat = result.get("lisa", {}).get("dimensions", {}).get("Platform Knowledge", 0)
        robotox_cov = result.get("robotox", {}).get("dimensions", {}).get("Coverage", 0)
        collective_knowledge = int((atlas_depth * 0.3 + garves_know * 0.2 + soren_brand * 0.15 +
                                    shelby_aware * 0.15 + lisa_plat * 0.1 + robotox_cov * 0.1))
        team["dimensions"]["Collective Knowledge"] = min(100, collective_knowledge)

        # 2. Coordination — how well agents communicate and sync
        shelby_comm = result.get("shelby", {}).get("dimensions", {}).get("Communication", 0)
        shelby_sched = result.get("shelby", {}).get("dimensions", {}).get("Scheduling", 0)
        atlas_coverage = result.get("atlas", {}).get("dimensions", {}).get("Agent Coverage", 0)
        coordination = int((shelby_comm * 0.35 + shelby_sched * 0.25 + atlas_coverage * 0.4))
        team["dimensions"]["Coordination"] = min(100, coordination)

        # 3. Performance — execution quality across all agents
        garves_acc = result.get("garves", {}).get("dimensions", {}).get("Accuracy", 0)
        soren_prod = result.get("soren", {}).get("dimensions", {}).get("Productivity", 0)
        atlas_quality = result.get("atlas", {}).get("dimensions", {}).get("Research Quality", 0)
        shelby_task = result.get("shelby", {}).get("dimensions", {}).get("Task Management", 0)
        lisa_disc = result.get("lisa", {}).get("dimensions", {}).get("Posting Discipline", 0)
        robotox_det = result.get("robotox", {}).get("dimensions", {}).get("Detection", 0)
        performance = int((garves_acc + soren_prod + atlas_quality + shelby_task + lisa_disc + robotox_det) / 6)
        team["dimensions"]["Performance"] = min(100, performance)

        # 4. Autonomy — how much runs without human intervention
        robotox_fix = result.get("robotox", {}).get("dimensions", {}).get("Auto-Fix", 0)
        atlas_synth = result.get("atlas", {}).get("dimensions", {}).get("Synthesis", 0)
        atlas_lr = result.get("atlas", {}).get("dimensions", {}).get("Learning Rate", 0)
        garves_risk = result.get("garves", {}).get("dimensions", {}).get("Risk Management", 0)
        autonomy = int((robotox_fix * 0.25 + atlas_synth * 0.25 + atlas_lr * 0.25 + garves_risk * 0.25))
        team["dimensions"]["Autonomy"] = min(100, autonomy)

        # 5. Adaptability — how fast the system evolves
        garves_adapt = result.get("garves", {}).get("dimensions", {}).get("Adaptability", 0)
        soren_trend = result.get("soren", {}).get("dimensions", {}).get("Trend Awareness", 0)
        lisa_strat = result.get("lisa", {}).get("dimensions", {}).get("Strategy Depth", 0)
        shelby_dec = result.get("shelby", {}).get("dimensions", {}).get("Decision Quality", 0)
        adaptability = int((garves_adapt * 0.3 + soren_trend * 0.2 + lisa_strat * 0.2 + shelby_dec * 0.3))
        team["dimensions"]["Adaptability"] = min(100, adaptability)

        team_scores = list(team["dimensions"].values())
        team["overall"] = int(sum(team_scores) / len(team_scores)) if team_scores else 0
        team["agent_scores"] = agent_scores
        result["team"] = team
    except Exception:
        result["team"] = {"dimensions": {}, "overall": 0, "title": "Brotherhood", "agent_scores": {}}

    return jsonify(result)


@app.route("/api/broadcasts")
def api_broadcasts():
    """Recent broadcasts with acknowledgment status."""
    try:
        sys.path.insert(0, str(SHELBY_ROOT_DIR))
        from core.broadcast import get_recent_broadcasts, check_acknowledgments

        broadcasts = get_recent_broadcasts(limit=15)
        result = []
        for bc in reversed(broadcasts):
            bc_id = bc.get("id", "")
            acks = check_acknowledgments(bc_id) if bc_id else {}
            result.append({
                "id": bc_id,
                "message": bc.get("message", ""),
                "priority": bc.get("priority", "normal"),
                "from": bc.get("from", "shelby"),
                "timestamp": bc.get("timestamp", ""),
                "delivered_to": bc.get("delivered_to", []),
                "acked": acks.get("acked", []),
                "pending": acks.get("pending", []),
            })
        return jsonify({"broadcasts": result})
    except Exception as e:
        return jsonify({"error": str(e)[:200], "broadcasts": []})


@app.route("/api/garves/broadcasts")
def api_garves_broadcasts():
    """Process and acknowledge broadcasts for Garves."""
    try:
        sys.path.insert(0, str(SHELBY_ROOT_DIR))
        from core.broadcast import get_unread_broadcasts, acknowledge_broadcast

        garves_data = DATA_DIR
        unread = get_unread_broadcasts(garves_data)
        for bc in unread:
            acknowledge_broadcast("garves", bc.get("id", ""), garves_data)

        return jsonify({"processed": len(unread), "agent": "garves"})
    except Exception as e:
        return jsonify({"error": str(e)[:200]})


@app.route("/api/soren/broadcasts")
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


@app.route("/api/lisa/broadcasts")
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

    # Auto-process broadcasts for agents without active loops (Soren, Lisa, Garves)
    def _broadcast_processor():
        """Periodically ack broadcasts for agents that don't have their own loops."""
        import time as _time
        _time.sleep(10)  # Wait for app to start
        while True:
            try:
                sys.path.insert(0, str(SHELBY_ROOT_DIR))
                from core.broadcast import get_unread_broadcasts, acknowledge_broadcast

                for agent, data_dir in [
                    ("garves", DATA_DIR),
                    ("soren", SOREN_ROOT / "data"),
                    ("lisa", MERCURY_ROOT / "data"),
                ]:
                    unread = get_unread_broadcasts(data_dir)
                    for bc in unread:
                        acknowledge_broadcast(agent, bc.get("id", ""), data_dir)
            except Exception:
                pass
            _time.sleep(30)

    threading.Thread(target=_broadcast_processor, daemon=True, name="broadcast-ack").start()

    threading.Timer(2.0, _auto_start_atlas).start()
    threading.Timer(1.0, lambda: webbrowser.open("http://localhost:8877")).start()
    app.run(host="0.0.0.0", port=8877, debug=False, threaded=True)
