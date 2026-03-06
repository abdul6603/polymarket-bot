"""Viper Lead Writer — transforms job scanner output into viper_leads.json.

Writes the lead format that Claude Overseer reads every cycle.
Scores leads using the 5-dimension system (fit, rate, effort, competition, client).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")

DATA_DIR = Path(__file__).parent.parent / "data"
LEADS_FILE = DATA_DIR / "viper_leads.json"

# Also write to Claude Overseer data dir
OVERSEER_LEADS = Path.home() / "claude_overseer" / "data" / "viper_leads.json"

# Agency service mapping for recommended_service
SERVICE_MAP = {
    "chatbot": "small_business_bot_standard",
    "bot": "small_business_bot_standard",
    "automation": "automation_workflow",
    "scraper": "web_scraping_pipeline",
    "scraping": "web_scraping_pipeline",
    "telegram": "telegram_bot_custom",
    "discord": "discord_bot_custom",
    "api": "api_integration",
    "seo": "seo_content_package",
    "content": "content_writing",
    "n8n": "automation_workflow",
    "zapier": "automation_workflow",
    "ai": "ai_chatbot_premium",
    "llm": "ai_chatbot_premium",
    "gpt": "ai_chatbot_premium",
    "whatsapp": "whatsapp_bot_standard",
}

# Score weights (from plan)
WEIGHTS = {
    "fit": 0.30,
    "rate": 0.25,
    "effort": 0.20,
    "competition": 0.15,
    "client": 0.10,
}

COMPOSITE_THRESHOLD = 6.0  # Only surface leads >= 6.0
MAX_LEADS_KEPT = 50


def _score_dimensions(job: dict) -> dict:
    """Score a job on 5 dimensions (1-10 each)."""
    # Fit score: how well does this match our services?
    skills = job.get("skills", [])
    category = job.get("category", "")
    ai_keywords = {"chatbot", "bot", "ai", "automation", "llm", "gpt", "scraper", "telegram", "whatsapp", "n8n"}
    skill_overlap = len(set(s.lower() for s in skills) & ai_keywords)

    if skill_overlap >= 3:
        fit = 10
    elif skill_overlap >= 2:
        fit = 8
    elif skill_overlap >= 1:
        fit = 6
    elif category == "coding":
        fit = 5
    else:
        fit = 3

    # Rate score: $/hr or fixed — worth our time?
    budget_max = job.get("budget_usd_max", 0) or 0
    budget_min = job.get("budget_usd_min", 0) or 0
    budget = budget_max or budget_min

    if budget >= 1000:
        rate = 10
    elif budget >= 500:
        rate = 8
    elif budget >= 200:
        rate = 6
    elif budget >= 100:
        rate = 5
    elif budget >= 50:
        rate = 3
    else:
        rate = 2  # Unknown budget

    # Effort score: estimated hours (higher = easier = better score)
    desc_len = len(job.get("description", ""))
    if budget >= 500 and desc_len < 500:
        effort = 9  # Good pay, simple scope
    elif budget >= 200 and desc_len < 300:
        effort = 8
    elif budget >= 200:
        effort = 6
    elif budget >= 100:
        effort = 5
    else:
        effort = 4

    # Competition score: how many bids?
    bid_count = job.get("bid_count") or 0
    if bid_count == 0:
        competition = 10
    elif bid_count <= 3:
        competition = 9
    elif bid_count <= 10:
        competition = 7
    elif bid_count <= 20:
        competition = 5
    elif bid_count <= 40:
        competition = 3
    else:
        competition = 1

    # Client score: reviews, history, signals
    source = job.get("source", "")
    client_country = job.get("client_country", "")
    if source == "HackerNews":
        client = 8  # HN clients tend to be quality
    elif source == "Freelancer" and budget >= 200:
        client = 7
    elif client_country in ("US", "CA", "GB", "AU", "DE"):
        client = 7
    else:
        client = 5

    scores = {
        "fit": fit,
        "rate": rate,
        "effort": effort,
        "competition": competition,
        "client": client,
    }

    # Composite
    composite = round(sum(scores[k] * WEIGHTS[k] for k in WEIGHTS), 1)
    scores["composite"] = composite

    return scores


def _recommend_service(job: dict) -> str:
    """Match job to our service catalog."""
    for skill in job.get("skills", []):
        s = skill.lower()
        if s in SERVICE_MAP:
            return SERVICE_MAP[s]
    return "custom_project"


def _recommend_bid(job: dict) -> str:
    """Suggest bid based on budget and service type."""
    budget_max = job.get("budget_usd_max", 0) or 0
    suggested = job.get("suggested_bid", "")
    if suggested:
        return suggested

    if budget_max >= 500:
        return f"${int(budget_max * 0.8)} setup + $200/mo retainer"
    elif budget_max >= 200:
        return f"${int(budget_max * 0.9)}"
    else:
        return "$200 minimum"


def write_leads(jobs: list[dict]) -> int:
    """Write jobs to viper_leads.json in Claude-readable format.

    Only includes leads with composite >= COMPOSITE_THRESHOLD.
    Returns number of leads written.
    """
    # Load existing leads
    existing = []
    if LEADS_FILE.exists():
        try:
            data = json.loads(LEADS_FILE.read_text())
            existing = data.get("leads", [])
        except Exception:
            existing = []

    # Existing hashes for dedup
    existing_hashes = {l.get("hash") for l in existing}

    # Score and convert new jobs
    new_leads = []
    for job in jobs:
        h = job.get("hash", "")
        if h in existing_hashes:
            continue

        scores = _score_dimensions(job)
        if scores["composite"] < COMPOSITE_THRESHOLD:
            continue

        lead = {
            "id": f"lead_{h[:8]}",
            "source": job.get("source", "").lower().replace(" ", "_"),
            "url": job.get("url", ""),
            "title": job.get("title", ""),
            "description": job.get("description", "")[:300],
            "budget": job.get("budget", ""),
            "scores": scores,
            "recommended_service": _recommend_service(job),
            "recommended_bid": _recommend_bid(job),
            "surfaced_at": datetime.now(ET).isoformat(),
            "status": "new",
            "hash": h,
            "skills": job.get("skills", []),
            "bid_count": job.get("bid_count"),
            "client_country": job.get("client_country", ""),
        }
        new_leads.append(lead)

    # Merge: new first, then existing
    all_leads = new_leads + existing

    # Keep only MAX_LEADS_KEPT, prioritize by composite score
    all_leads.sort(key=lambda l: l.get("scores", {}).get("composite", 0), reverse=True)
    all_leads = all_leads[:MAX_LEADS_KEPT]

    # Write to both locations
    output = {"leads": all_leads, "updated_at": datetime.now(ET).isoformat()}
    output_json = json.dumps(output, indent=2)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LEADS_FILE.write_text(output_json)

    if OVERSEER_LEADS.parent.exists():
        OVERSEER_LEADS.write_text(output_json)

    log.info("[LEAD_WRITER] Wrote %d leads (%d new, composite >= %.1f)",
             len(all_leads), len(new_leads), COMPOSITE_THRESHOLD)
    return len(new_leads)
