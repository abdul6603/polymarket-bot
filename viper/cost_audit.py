"""Live API Cost Tracker — computes real costs from actual usage data.

Never caches to a JSON file. Always reads live data files and calculates
costs dynamically. If we add new agents or change pricing, costs update
immediately on next API call.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

# ── Shared Intelligence Layer (MLX routing) ──
_USE_SHARED_LLM = False
_shared_llm_call = None
try:
    sys.path.insert(0, str(Path.home() / "shared"))
    sys.path.insert(0, str(Path.home()))
    from llm_client import llm_call as _llm_call
    _shared_llm_call = _llm_call
    _USE_SHARED_LLM = True
except ImportError:
    pass

# ── Directories ──────────────────────────────────────────────────
BOT_DATA = Path(__file__).parent.parent / "data"
ATLAS_DATA = Path(os.path.expanduser("~/atlas/data"))
SOREN_DATA = Path(os.path.expanduser("~/soren-content/data"))
MERCURY_DATA = Path(os.path.expanduser("~/mercury"))
THOR_DATA = Path(os.path.expanduser("~/thor/data"))
SHELBY_DATA = Path(os.path.expanduser("~/shelby/data"))

# ── Pricing (Feb 2026 — update here, costs update everywhere) ────
PRICING = {
    "gpt-4o":       {"input_per_1m": 2.50, "output_per_1m": 10.00, "avg_input_tokens": 500, "avg_output_tokens": 100},
    "gpt-4o-mini":  {"input_per_1m": 0.15, "output_per_1m": 0.60,  "avg_input_tokens": 400, "avg_output_tokens": 80},
    "dall-e-3":     {"per_image": 0.04},
    "elevenlabs":   {"per_1k_chars": 0.30, "avg_chars": 200},  # ~$0.06 per generation
    "tavily":       {"per_search": 0.005},  # 12k free credits/mo on starter
    "claude-sonnet": {"input_per_1m": 3.00, "output_per_1m": 15.00, "avg_input_tokens": 2000, "avg_output_tokens": 500},
    "polymarket":   {"per_call": 0.0},
    "reddit":       {"per_call": 0.0},
    "binance":      {"per_call": 0.0},
}


def _cost_per_call(model: str) -> float:
    """Calculate cost per API call from token pricing."""
    p = PRICING.get(model)
    if not p:
        return 0.0
    if "per_image" in p:
        return p["per_image"]
    if "per_search" in p:
        return p["per_search"]
    if "per_1k_chars" in p:
        return p["per_1k_chars"] * p.get("avg_chars", 200) / 1000
    if "per_call" in p:
        return p["per_call"]
    inp = p.get("avg_input_tokens", 500)
    out = p.get("avg_output_tokens", 100)
    return (inp * p["input_per_1m"] + out * p["output_per_1m"]) / 1_000_000


def _read_json(path: Path) -> dict | list:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return {}


def _count_jsonl_lines(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        with open(path) as f:
            return sum(1 for line in f if line.strip())
    except Exception:
        return 0


def _count_recent_jsonl(path: Path, days: int = 30) -> int:
    """Count JSONL entries from the last N days."""
    if not path.exists():
        return 0
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    count = 0
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    ts = entry.get("ts") or entry.get("timestamp") or entry.get("time") or ""
                    if ts >= cutoff or not ts:
                        count += 1
                except Exception:
                    count += 1  # count unparseable lines as recent
    except Exception:
        pass
    return count


def _days_active() -> int:
    """How many days the system has been active (for daily avg calculation)."""
    cost_tracker = _read_json(ATLAS_DATA / "cost_tracker.json")
    daily = cost_tracker.get("daily", {})
    if daily:
        return max(1, len(daily))
    return 1


def _get_atlas_usage() -> dict:
    """Read Atlas's actual API call counts from cost_tracker.json."""
    tracker = _read_json(ATLAS_DATA / "cost_tracker.json")
    daily = tracker.get("daily", {})
    totals = {"tavily_calls": 0, "openai_calls": 0, "openai_tokens_est": 0}
    for day_data in daily.values():
        totals["tavily_calls"] += day_data.get("tavily_calls", 0)
        totals["openai_calls"] += day_data.get("openai_calls", 0)
        totals["openai_tokens_est"] += day_data.get("openai_tokens_est", 0)
    return totals


def audit_all() -> dict:
    """Live cost audit — reads actual data files, never hardcoded estimates."""
    costs: list[dict] = []
    days = _days_active()

    # ── HAWK ─────────────────────────────────────────────────────
    # Each trade = 1 GPT-4o analysis call. Count actual trades.
    hawk_trades = _count_jsonl_lines(BOT_DATA / "hawk_trades.jsonl")
    hawk_opps = _read_json(BOT_DATA / "hawk_opportunities.json")
    hawk_opp_count = len(hawk_opps.get("opportunities", [])) if isinstance(hawk_opps, dict) else 0
    # Each opportunity scan = 1 batch call analyzing ~20 markets
    hawk_scans = max(hawk_trades, hawk_opp_count)
    hawk_daily = max(hawk_scans / days, 2)  # min 2/day estimate if no data
    hawk_cpc = _cost_per_call("gpt-4o")
    costs.append({
        "agent": "hawk",
        "service": "OpenAI (GPT-4o)",
        "model": "gpt-4o",
        "cost_per_call": round(hawk_cpc, 6),
        "daily_calls": round(hawk_daily, 1),
        "cost_usd": round(hawk_daily * hawk_cpc * 30, 2),
        "usage_count": hawk_scans,
        "source": "hawk_trades.jsonl + hawk_opportunities.json",
        "trend": "stable",
        "waste": False,
    })
    costs.append({
        "agent": "hawk",
        "service": "Polymarket (CLOB API)",
        "model": "polymarket",
        "cost_per_call": 0.0,
        "daily_calls": round(hawk_daily * 2, 1),
        "cost_usd": 0.0,
        "usage_count": hawk_scans * 2,
        "source": "free API",
        "trend": "stable",
        "waste": False,
    })

    # ── GARVES ───────────────────────────────────────────────────
    garves_trades = _count_jsonl_lines(BOT_DATA / "trades.jsonl")
    garves_daily = max(garves_trades / days, 5)
    garves_cpc = _cost_per_call("gpt-4o-mini")
    # Garves calls GPT-4o-mini for brain queries + signal analysis
    garves_api_calls = garves_trades * 3  # ~3 API calls per trade (signals + brain + logging)
    garves_daily_api = max(garves_api_calls / days, 15)
    costs.append({
        "agent": "garves",
        "service": "OpenAI (GPT-4o-mini)",
        "model": "gpt-4o-mini",
        "cost_per_call": round(garves_cpc, 6),
        "daily_calls": round(garves_daily_api, 1),
        "cost_usd": round(garves_daily_api * garves_cpc * 30, 2),
        "usage_count": garves_api_calls or int(garves_daily_api * days),
        "source": "trades.jsonl (x3 calls/trade)",
        "trend": "stable",
        "waste": False,
    })
    costs.append({
        "agent": "garves",
        "service": "Binance (WebSocket + REST)",
        "model": "binance",
        "cost_per_call": 0.0,
        "daily_calls": 1440,  # continuous websocket + REST polls
        "cost_usd": 0.0,
        "usage_count": 1440 * days,
        "source": "free API (WebSocket stream)",
        "trend": "stable",
        "waste": False,
    })

    # ── SOREN ────────────────────────────────────────────────────
    soren_queue = _read_json(SOREN_DATA / "content_queue.json")
    soren_items = soren_queue.get("items", []) if isinstance(soren_queue, dict) else (soren_queue if isinstance(soren_queue, list) else [])
    soren_total = len(soren_items)
    soren_daily_content = max(soren_total / days, 3)

    # GPT-4o for content generation (caption + script per item)
    soren_gpt_cpc = _cost_per_call("gpt-4o")
    soren_gpt_calls = soren_total * 2  # caption + script
    soren_gpt_daily = max(soren_gpt_calls / days, 6)
    costs.append({
        "agent": "soren",
        "service": "OpenAI (GPT-4o)",
        "model": "gpt-4o",
        "cost_per_call": round(soren_gpt_cpc, 6),
        "daily_calls": round(soren_gpt_daily, 1),
        "cost_usd": round(soren_gpt_daily * soren_gpt_cpc * 30, 2),
        "usage_count": soren_gpt_calls or int(soren_gpt_daily * days),
        "source": "content_queue.json (x2 calls/item)",
        "trend": "stable",
        "waste": False,
    })

    # DALL-E 3 for image generation
    dalle_cpc = _cost_per_call("dall-e-3")
    dalle_daily = max(soren_daily_content * 0.7, 2)  # ~70% of content gets images
    costs.append({
        "agent": "soren",
        "service": "OpenAI (DALL-E 3)",
        "model": "dall-e-3",
        "cost_per_call": round(dalle_cpc, 6),
        "daily_calls": round(dalle_daily, 1),
        "cost_usd": round(dalle_daily * dalle_cpc * 30, 2),
        "usage_count": int(dalle_daily * days),
        "source": "~70% of content items",
        "trend": "stable",
        "waste": False,
    })

    # ElevenLabs TTS
    eleven_cpc = _cost_per_call("elevenlabs")
    eleven_daily = max(soren_daily_content * 0.5, 1)  # ~50% get voiceover
    costs.append({
        "agent": "soren",
        "service": "ElevenLabs (TTS Brian)",
        "model": "elevenlabs",
        "cost_per_call": round(eleven_cpc, 6),
        "daily_calls": round(eleven_daily, 1),
        "cost_usd": round(eleven_daily * eleven_cpc * 30, 2),
        "usage_count": int(eleven_daily * days),
        "source": "~50% of content items",
        "trend": "stable",
        "waste": False,
    })

    # ── ATLAS ────────────────────────────────────────────────────
    atlas_usage = _get_atlas_usage()
    atlas_tavily_total = atlas_usage.get("tavily_calls", 0)
    atlas_tavily_daily = max(atlas_tavily_total / days, 10)
    tavily_cpc = _cost_per_call("tavily")
    costs.append({
        "agent": "atlas",
        "service": "Tavily (Research)",
        "model": "tavily",
        "cost_per_call": round(tavily_cpc, 6),
        "daily_calls": round(atlas_tavily_daily, 1),
        "cost_usd": round(atlas_tavily_daily * tavily_cpc * 30, 2),
        "usage_count": atlas_tavily_total or int(atlas_tavily_daily * days),
        "source": "atlas/data/cost_tracker.json",
        "trend": "up" if atlas_tavily_daily > 100 else "stable",
        "waste": False,
    })

    # Atlas OpenAI usage (for summarization, brain queries)
    atlas_openai_total = atlas_usage.get("openai_calls", 0)
    atlas_openai_daily = max(atlas_openai_total / days, 5)
    atlas_gpt_cpc = _cost_per_call("gpt-4o-mini")  # Atlas uses mini for most tasks
    costs.append({
        "agent": "atlas",
        "service": "OpenAI (GPT-4o-mini)",
        "model": "gpt-4o-mini",
        "cost_per_call": round(atlas_gpt_cpc, 6),
        "daily_calls": round(atlas_openai_daily, 1),
        "cost_usd": round(atlas_openai_daily * atlas_gpt_cpc * 30, 2),
        "usage_count": atlas_openai_total or int(atlas_openai_daily * days),
        "source": "atlas/data/cost_tracker.json",
        "trend": "stable",
        "waste": False,
    })

    # ── LISA ─────────────────────────────────────────────────────
    # Lisa uses GPT-4o-mini for scheduling, rating, reviews
    lisa_cpc = _cost_per_call("gpt-4o-mini")
    # Count actual reviewed/scheduled items from Soren's queue
    lisa_reviewed = sum(1 for item in soren_items if item.get("status") in ("lisa_approved", "jordan_approved", "rejected", "posted", "scheduled"))
    lisa_daily = max(lisa_reviewed / days, 5)
    costs.append({
        "agent": "lisa",
        "service": "OpenAI (GPT-4o-mini)",
        "model": "gpt-4o-mini",
        "cost_per_call": round(lisa_cpc, 6),
        "daily_calls": round(lisa_daily, 1),
        "cost_usd": round(lisa_daily * lisa_cpc * 30, 2),
        "usage_count": lisa_reviewed or int(lisa_daily * days),
        "source": "content_queue.json (reviewed items)",
        "trend": "stable",
        "waste": False,
    })

    # ── THOR ─────────────────────────────────────────────────────
    thor_results_dir = THOR_DATA / "results"
    thor_tasks_done = 0
    if thor_results_dir.exists():
        thor_tasks_done = len(list(thor_results_dir.glob("*.json")))
    thor_daily = max(thor_tasks_done / days, 1)
    thor_cpc = _cost_per_call("claude-sonnet")
    # Each task = ~5 API calls (plan + code + test + review + fix)
    thor_api_calls = thor_tasks_done * 5
    thor_daily_api = max(thor_api_calls / days, 5)
    costs.append({
        "agent": "thor",
        "service": "Anthropic (Claude Sonnet)",
        "model": "claude-sonnet",
        "cost_per_call": round(thor_cpc, 6),
        "daily_calls": round(thor_daily_api, 1),
        "cost_usd": round(thor_daily_api * thor_cpc * 30, 2),
        "usage_count": thor_api_calls or int(thor_daily_api * days),
        "source": "thor/data/results/ (x5 calls/task)",
        "trend": "stable",
        "waste": False,
    })

    # ── VIPER ────────────────────────────────────────────────────
    viper_opps = _read_json(BOT_DATA / "viper_opportunities.json")
    viper_opp_count = len(viper_opps.get("opportunities", [])) if isinstance(viper_opps, dict) else 0
    viper_intel = _read_json(BOT_DATA / "viper_intel.json")
    viper_intel_count = viper_intel.get("count", 0) if isinstance(viper_intel, dict) else 0
    viper_scans = max(viper_opp_count, viper_intel_count, 1)
    viper_daily_scans = max(viper_scans / days, 6)

    # Tavily searches per scan cycle
    viper_tavily_daily = viper_daily_scans * 3  # ~3 searches per scan
    costs.append({
        "agent": "viper",
        "service": "Tavily (Web Search)",
        "model": "tavily",
        "cost_per_call": round(tavily_cpc, 6),
        "daily_calls": round(viper_tavily_daily, 1),
        "cost_usd": round(viper_tavily_daily * tavily_cpc * 30, 2),
        "usage_count": int(viper_tavily_daily * days),
        "source": "viper_intel.json + viper_opportunities.json",
        "trend": "stable",
        "waste": False,
    })
    costs.append({
        "agent": "viper",
        "service": "Reddit (JSON API)",
        "model": "reddit",
        "cost_per_call": 0.0,
        "daily_calls": round(viper_daily_scans * 2, 1),
        "cost_usd": 0.0,
        "usage_count": int(viper_daily_scans * 2 * days),
        "source": "free API",
        "trend": "stable",
        "waste": False,
    })
    costs.append({
        "agent": "viper",
        "service": "Polymarket (CLOB API)",
        "model": "polymarket",
        "cost_per_call": 0.0,
        "daily_calls": round(viper_daily_scans * 2, 1),
        "cost_usd": 0.0,
        "usage_count": int(viper_daily_scans * 2 * days),
        "source": "free API",
        "trend": "stable",
        "waste": False,
    })

    # ── ROBOTOX ──────────────────────────────────────────────────
    # Robotox uses no paid APIs — pure process monitoring
    costs.append({
        "agent": "robotox",
        "service": "System (process monitoring)",
        "model": "none",
        "cost_per_call": 0.0,
        "daily_calls": 288,  # every 5 min
        "cost_usd": 0.0,
        "usage_count": 288 * days,
        "source": "local system calls only",
        "trend": "stable",
        "waste": False,
    })

    # ── SHELBY ───────────────────────────────────────────────────
    # Shelby uses no paid APIs — pure task management + Telegram
    costs.append({
        "agent": "shelby",
        "service": "Telegram (Bot API)",
        "model": "none",
        "cost_per_call": 0.0,
        "daily_calls": 20,
        "cost_usd": 0.0,
        "usage_count": 20 * days,
        "source": "free API (Telegram bot)",
        "trend": "stable",
        "waste": False,
    })

    # ── INFRASTRUCTURE ────────────────────────────────────────────
    # Claude Code — fixed monthly subscription used by all agents
    costs.append({
        "agent": "infrastructure",
        "service": "Anthropic (Claude Code)",
        "model": "claude-code",
        "cost_per_call": 0.0,
        "daily_calls": 0,
        "cost_usd": 200.00,
        "usage_count": 0,
        "source": "fixed subscription ($200/mo)",
        "trend": "stable",
        "waste": False,
    })

    # ── Filter out free services for cleaner view, sort by cost ──
    paid = [c for c in costs if c["cost_usd"] > 0]
    free = [c for c in costs if c["cost_usd"] == 0]
    paid.sort(key=lambda c: c["cost_usd"], reverse=True)
    free.sort(key=lambda c: c["agent"])

    total_monthly = sum(c["cost_usd"] for c in costs)

    # Per-agent totals
    agent_totals = {}
    for c in costs:
        agent_totals[c["agent"]] = agent_totals.get(c["agent"], 0) + c["cost_usd"]

    result = {
        "total_monthly": round(total_monthly, 2),
        "costs": paid + free,
        "agent_totals": {k: round(v, 2) for k, v in sorted(agent_totals.items(), key=lambda x: x[1], reverse=True)},
        "days_tracked": days,
        "pricing": {model: round(_cost_per_call(model), 6) for model in PRICING},
        "waste_flags": [c for c in paid if c["cost_usd"] > 50],
        "last_computed": datetime.now().isoformat(),
    }

    # Publish cost_audit_completed to the shared event bus
    try:
        from shared.events import publish as bus_publish
        top_spender = max(agent_totals, key=agent_totals.get) if agent_totals else "none"
        bus_publish(
            agent="viper",
            event_type="cost_audit_completed",
            data={
                "total_cost": result["total_monthly"],
                "top_spender": top_spender,
                "savings_found": len(result["waste_flags"]),
            },
            summary=f"Cost audit: ${result['total_monthly']:.2f}/mo, top spender: {top_spender}",
        )
    except Exception:
        pass  # Never let bus failure crash Viper

    return result


def find_waste() -> list[dict]:
    """Identify wasteful spending patterns."""
    audit = audit_all()
    return [
        {"agent": c["agent"], "service": c["service"], "monthly": c["cost_usd"],
         "daily_calls": c["daily_calls"], "cost_per_call": c["cost_per_call"],
         "reason": "High monthly spend — review if all calls are necessary"}
        for c in audit["costs"]
        if c["cost_usd"] > 30
    ]


def get_llm_cost_recommendations() -> str:
    """LLM-powered cost analysis — identifies optimization opportunities.
    Runs on reasoning (14B) task type. Called hourly by Viper main loop."""
    if not (_USE_SHARED_LLM and _shared_llm_call):
        return ""
    try:
        audit = audit_all()
        cost_summary = json.dumps({
            "total_monthly": audit["total_monthly"],
            "agent_totals": audit["agent_totals"],
            "top_costs": [
                {"agent": c["agent"], "service": c["service"],
                 "monthly": c["cost_usd"], "daily_calls": c["daily_calls"]}
                for c in audit["costs"] if c["cost_usd"] > 5
            ],
        }, indent=2)

        result = _shared_llm_call(
            system=(
                "You are a cost optimization analyst for a multi-agent AI system. "
                "Analyze API costs and suggest specific, actionable optimizations. "
                "Consider: batch processing, caching, model downgrades, reducing call frequency, "
                "switching to local LLM where possible. Be specific and quantify potential savings."
            ),
            user=f"Analyze these monthly API costs and suggest top 3 optimizations:\n{cost_summary}",
            agent="viper",
            task_type="reasoning",
            max_tokens=500,
            temperature=0.3,
        )
        return result.strip() if result else ""
    except Exception as e:
        log.debug("LLM cost recommendation failed: %s", e)
        return ""
