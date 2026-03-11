"""Viper Telegram alerts — sends job leads and summaries to Jordan.

Uses tg_router for channel routing (INBOUND channel for Pipeline 2 leads).
"""
from __future__ import annotations

import logging
import re

from viper.tg_router import send as tg_send

log = logging.getLogger(__name__)

# Source display names
_SOURCE_NAME = {
    "HackerNews": "HN",
    "GoogleAlerts": "Google Alerts",
    "Reddit": "Reddit",
    "IndieHackers": "Indie Hackers",
    "ProductHunt": "Product Hunt",
    "n8n_community": "n8n Community",
    "Make_community": "Make Community",
    "RemoteOK": "RemoteOK",
    "WeWorkRemotely": "WeWorkRemotely",
}

# Skills we can definitely do
_CAN_DO_SKILLS = {
    "chatbot", "bot", "automation", "scraper", "scraping", "api",
    "telegram", "discord", "whatsapp", "python", "ai", "llm", "gpt",
    "n8n", "zapier", "flask", "django", "selenium", "backend",
    "data pipeline", "booking", "appointment", "lead capture",
    "virtual assistant", "ai agent", "web scraping",
}


def _assess_fit(skills: list[str], category: str) -> str:
    """Return YES / MAYBE / NO based on skill match."""
    if not skills:
        return "MAYBE"
    skill_set = {s.lower() for s in skills}
    overlap = skill_set & _CAN_DO_SKILLS
    if len(overlap) >= 2:
        return "YES"
    if len(overlap) >= 1 or category == "coding":
        return "MAYBE"
    return "NO"


def _summarize_need(title: str, description: str) -> str:
    """Extract 1-2 sentence summary of what they need."""
    desc = re.sub(r"<[^>]+>", "", description).strip()
    if not desc:
        return title[:120]
    # Take first 1-2 sentences
    sentences = re.split(r"[.!?]\s+", desc)
    summary = ". ".join(sentences[:2])
    if len(summary) > 150:
        summary = summary[:147] + "..."
    return summary


def _extract_name(title: str, source: str) -> str:
    """Extract company/person name from title."""
    # HN posts often start with "Company Name |" or "Company Name -"
    for sep in [" | ", " - ", " — ", " – "]:
        if sep in title:
            return title.split(sep)[0].strip()[:60]
    return title[:60]


def send_job_alert(
    title: str,
    source: str,
    category: str,
    skills: list[str],
    budget: str,
    url: str,
    score: int,
    bid_count: int | None = None,
    description: str = "",
    suggested_bid: str = "",
    suggested_delivery: str = "",
    client_country: str = "",
    job_hash: str = "",
) -> bool:
    """Send a job lead alert to Jordan's TG — clean, actionable format."""
    source_display = _SOURCE_NAME.get(source, source).lower()
    name = _extract_name(title, source)
    need_summary = _summarize_need(title, description)
    budget_display = budget if budget else "Not stated"
    fit = _assess_fit(skills, category)

    # Score on /10 scale (input is /100)
    score_10 = round(score / 10, 1)
    if score_10 == int(score_10):
        score_10 = int(score_10)

    # HOT label for 8.0+ leads
    hot_label = " \U0001f525 HOT" if score_10 >= 8.0 else ""

    # Escape HTML
    name = name.replace("<", "&lt;").replace(">", "&gt;")
    need_summary = need_summary.replace("<", "&lt;").replace(">", "&gt;")

    text = (
        f"\U0001f4b0 <b>INBOUND LEAD{hot_label}</b>\n\n"
        f"Company/Person: {name}\n"
        f"Website: {url}\n"
        f"What they need: {need_summary}\n"
        f"Budget: {budget_display}\n"
        f"Score: {score_10}/10{hot_label}\n"
        f"Source: {source_display}\n"
        f"Can we do this: <b>{fit}</b>"
    )

    buttons = [
        [
            {"text": "BID", "callback_data": f"viper_bid:{job_hash[:20]}"},
            {"text": "SKIP", "callback_data": f"viper_skip:{job_hash[:20]}"},
        ],
    ]

    return tg_send(text, channel="INBOUND", buttons=buttons)


def send_summary(total_scanned: int, new_matches: int, alerts_sent: int) -> bool:
    """Send end-of-cycle summary."""
    text = (
        f"<b>Viper Scan Complete</b>\n\n"
        f"Scanned: {total_scanned}\n"
        f"Matches (score 70+): {new_matches}\n"
        f"Alerts sent: {alerts_sent}"
    )
    return tg_send(text, channel="INBOUND")
