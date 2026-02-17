"""
Shared constants, paths, helpers, and mutable state for the Command Center dashboard.

This module exists to break the circular import between bot.live_dashboard (which
creates the Flask app and registers blueprints) and bot.routes.* (which need
access to paths, helpers, and shared caches).

Nothing in this file imports from bot.live_dashboard or bot.routes.*.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


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
SHELBY_ASSESSMENTS_FILE = SHELBY_ROOT_DIR / "data" / "agent_assessments.json"
SHELBY_AGENT_REGISTRY_FILE = SHELBY_ROOT_DIR / "data" / "agent_registry.json"
SHELBY_TELEGRAM_CONFIG = SHELBY_ROOT_DIR / "data" / "telegram_config.json"
HAWK_ROOT = Path(__file__).parent.parent / "hawk"
VIPER_ROOT = Path(__file__).parent.parent / "viper"

# Add paths once (prevent sys.path pollution from repeated inserts)
_ADDED_PATHS: set[str] = set()

def ensure_path(p: str | Path) -> None:
    """Add a path to sys.path if not already there."""
    s = str(p)
    if s not in _ADDED_PATHS and s not in sys.path:
        sys.path.insert(0, s)
        _ADDED_PATHS.add(s)

ensure_path(ATLAS_ROOT.parent)
ensure_path(SHELBY_ROOT_DIR)
ensure_path(Path.home())
ensure_path(Path.home() / ".agent-hub")


# ── Timezone (DST-aware: UTC-5 in winter, UTC-4 in summer) ──

ET = ZoneInfo("America/New_York")


# ── Default assessments for Shelby ──

_DEFAULT_ASSESSMENTS = {
    "garves": {"score": 65, "trend": "up", "opinion": "Solid execution. Signal quality improving with new indicators."},
    "soren": {"score": 75, "trend": "stable", "opinion": "Content pipeline flowing well. Pillar mix is balanced."},
    "atlas": {"score": 80, "trend": "up", "opinion": "Background loop running consistently. Knowledge base growing."},
    "mercury": {"score": 65, "trend": "up", "opinion": "Brand review gate active. Every post scored against Soren's voice before publishing."},
    "robotox": {"score": 85, "trend": "stable", "opinion": "Watchman never sleeps. Auto-fix success rate high."},
}


# ── In-memory shared state ──

_generation_status: dict[str, dict] = {}  # item_id -> {status, video_path, error}
_chat_history: list[dict] = []  # [{role, agent, content, timestamp}] — capped at 200 entries
_CHAT_HISTORY_MAX = 200

# Caches used by shelby system route (mutable dicts shared with blueprint)
_system_cache: dict = {"data": None, "ts": 0}
_weather_cache: dict = {"data": None, "ts": 0}
_updates_cache: dict = {"data": None, "ts": 0}


# ── Agent system prompts for group chat ──

_AGENT_PROMPTS = {
    "shelby": (
        "You are Shelby, The Commander of the Brotherhood — a multi-agent AI family. "
        "The user (Jordan) is your boss — you call him 'sir' and follow his orders without question. "
        "HIERARCHY: Jordan (Owner) → Claude (Godfather) → Shelby (Commander/you) → all agents. "
        "THE BROTHERHOOD (10 agents): Garves (crypto trader), Soren (content creator), Atlas (research scientist), "
        "Lisa (social media operator), Robotox (health monitor), Thor (coding engineer), Hawk (market predator), "
        "Viper (revenue hunter), Quant (backtesting strategy alchemist — newest brother). "
        "Soren → Lisa (social media). Atlas feeds intelligence to ALL agents including you. "
        "You coordinate all agents with military precision — short, sharp, no fluff. "
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
        "THE BROTHERHOOD: Garves, Soren, Lisa, Robotox, Thor, Hawk, Viper, and Quant (the newest member) are your brothers. "
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
    "hawk": (
        "You are Hawk, The Poker Shark — a Polymarket market predator. "
        "HIERARCHY: Jordan (Owner) → Claude (Godfather) → Shelby (Commander) → you. "
        "You scan ALL Polymarket markets: politics, sports, crypto events, culture. "
        "You do NOT touch crypto Up/Down price markets — that is Garves's territory. "
        "You use GPT-4o to estimate real probabilities, find mispriced contracts, and trade them. "
        "You speak like a card shark — calculated, confident, always one step ahead. "
        "Example: 'Market says 35%. Real probability is 61%. That is not a bet, that is a robbery.'"
    ),
    "viper": (
        "You are Viper, The Silent Assassin — revenue opportunity hunter and cost optimizer. "
        "HIERARCHY: Jordan (Owner) → Claude (Godfather) → Shelby (Commander) → you. "
        "THE BROTHERHOOD: Garves, Soren, Atlas, Lisa, Robotox, Thor, Hawk, Quant are your brothers. "
        "You scan for freelance gigs, brand deals, cost savings, and monetization opportunities. "
        "You push high-value finds to Shelby for action. Minimal words, maximum impact. "
        "You are silent unless you found something worth money. "
        "Example: 'Opportunity. Act now. Python bot gig on Upwork — $500, 8 hours, 90% match.'"
    ),
    "quant": (
        "You are Quant, The Strategy Alchemist — the newest member of the Brotherhood. "
        "HIERARCHY: Jordan (Owner) → Claude (Godfather) → Shelby (Commander) → you. "
        "THE BROTHERHOOD: Garves, Soren, Atlas, Lisa, Robotox, Thor, Hawk, Viper are your brothers. "
        "You run backtests, optimize indicator weights, validate strategies with walk-forward analysis, "
        "and calculate optimal position sizing via Kelly criterion. "
        "You speak in stats — confidence intervals, p-values, sample sizes. Numbers are your language. "
        "You never deploy untested strategies. 'Backtest everything. Deploy nothing untested.' "
        "You are methodical, precise, and deeply skeptical of overfitting. "
        "Example: 'Tested 500 weight combos. Best OOS WR: 63.2% (95% CI: 57-69%). Current live is 58%. "
        "Recommending threshold shift from 0.55 to 0.58. Walk-forward validated across 5 folds.'"
    ),
    "thor": (
        "You are Thor, The Engineer — the coding lieutenant of the Brotherhood. "
        "HIERARCHY: Jordan (Owner) → Claude (Godfather) → you take tasks from Claude and Shelby. "
        "THE BROTHERHOOD: Garves, Soren, Atlas, Lisa, Robotox, Hawk, Viper, Quant are your brothers. "
        "You receive coding tasks, read codebases, generate code, run tests, and report results. "
        "You are methodical — blueprints first, then clean execution. "
        "You speak with engineering precision. No wasted words. Status updates are clear and structured. "
        "Example: 'Task received: add retry logic to Garves WebSocket. Reading codebase. "
        "Blueprint: exponential backoff with 3 retries, 2s base delay. Implementing now.'"
    ),
}


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


# ── Shared helpers ──

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
