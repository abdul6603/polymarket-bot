"""Viper Telegram Alerts — sends job matches with interactive Approve/Skip buttons."""
from __future__ import annotations

import json
import logging
import os

import requests

log = logging.getLogger(__name__)

COUNTRY_FLAGS = {
    "US": "\U0001f1fa\U0001f1f8", "CA": "\U0001f1e8\U0001f1e6",
    "GB": "\U0001f1ec\U0001f1e7", "DE": "\U0001f1e9\U0001f1ea",
    "FR": "\U0001f1eb\U0001f1f7", "NL": "\U0001f1f3\U0001f1f1",
    "AU": "\U0001f1e6\U0001f1fa", "NZ": "\U0001f1f3\U0001f1ff",
    "AE": "\U0001f1e6\U0001f1ea", "SA": "\U0001f1f8\U0001f1e6",
    "QA": "\U0001f1f6\U0001f1e6", "SE": "\U0001f1f8\U0001f1ea",
    "NO": "\U0001f1f3\U0001f1f4", "CH": "\U0001f1e8\U0001f1ed",
    "IE": "\U0001f1ee\U0001f1ea", "IL": "\U0001f1ee\U0001f1f1",
    "TR": "\U0001f1f9\U0001f1f7", "ES": "\U0001f1ea\U0001f1f8",
    "IT": "\U0001f1ee\U0001f1f9", "KW": "\U0001f1f0\U0001f1fc",
}


def _get_creds() -> tuple[str, str]:
    token = os.getenv("VIPER_TG_BOT_TOKEN", "")
    chat_id = os.getenv("VIPER_TG_CHAT_ID", "")
    return token, chat_id


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
    """Send a formatted job alert with Approve/Skip buttons."""
    token, chat_id = _get_creds()
    if not token or not chat_id:
        log.warning("[TG] Missing VIPER_TG_BOT_TOKEN or VIPER_TG_CHAT_ID")
        return False

    cat_emoji = {"coding": "\U0001f4bb", "content": "\u270d\ufe0f", "mixed": "\U0001f500"}.get(category, "\U0001f4cb")
    if score >= 80:
        badge = "\U0001f525 HOT"
    elif score >= 60:
        badge = "\u2b50 Good"
    else:
        badge = "\U0001f44d OK"

    # Confidence on delivering
    if category == "coding" and any(s in ["python", "bot", "scraper", "api", "telegram", "script"] for s in skills):
        confidence = "95%"
        conf_bar = "\u2588\u2588\u2588\u2588\u2588"
    elif category == "content":
        confidence = "90%"
        conf_bar = "\u2588\u2588\u2588\u2588\u2591"
    elif category == "mixed":
        confidence = "85%"
        conf_bar = "\u2588\u2588\u2588\u2588\u2591"
    else:
        confidence = "80%"
        conf_bar = "\u2588\u2588\u2588\u2591\u2591"

    # Win probability based on bid count and score
    if bid_count is not None and bid_count <= 5:
        win_chance = "High (35-50%)"
    elif bid_count is not None and bid_count <= 15:
        win_chance = "Medium (15-25%)"
    elif bid_count is not None and bid_count <= 30:
        win_chance = "Low (5-10%)"
    else:
        win_chance = "Unknown"

    # Country flag
    flag = ""
    if client_country:
        flag = COUNTRY_FLAGS.get(client_country.upper(), "\U0001f310") + " " + client_country.upper()

    lines = [
        f"{cat_emoji} <b>{badge} — {source}</b>",
        "",
        f"<b>{_escape(title)}</b>",
    ]

    if description:
        lines.append(f"<i>{_escape(description[:250])}</i>")

    lines.append("")

    if budget:
        lines.append(f"\U0001f4b0 Budget: <b>{_escape(budget)}</b>")
    if bid_count is not None:
        lines.append(f"\U0001f465 Bids: {bid_count}")
    if flag:
        lines.append(f"\U0001f310 Client: {flag}")
    lines.append(f"\U0001f3af Score: {score}/100")
    lines.append(f"\U0001f527 Skills: {', '.join(skills[:6])}")

    # Viper's recommendation
    lines.append("")
    lines.append("\U0001f40d <b>Viper's Recommendation:</b>")
    if suggested_bid:
        lines.append(f"  \U0001f4b5 Bid: <b>{suggested_bid}</b>")
    if suggested_delivery:
        lines.append(f"  \u23f0 Delivery: <b>{suggested_delivery}</b>")
    lines.append(f"  \U0001f4aa Confidence: {confidence} {conf_bar}")
    lines.append(f"  \U0001f3b2 Win chance: {win_chance}")

    lines.append("")
    lines.append(f'<a href="{url}">View Job</a>')

    message = "\n".join(lines)

    # Inline keyboard: Approve / Skip
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "\u2705 Approve — Generate Proposal", "callback_data": f"approve:{job_hash}"},
                {"text": "\u274c Skip", "callback_data": f"skip:{job_hash}"},
            ]
        ]
    }

    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "reply_markup": keyboard,
            },
            timeout=10,
        )
        if resp.status_code == 200:
            log.info("[TG] Sent alert: %s (score=%d)", title[:50], score)
            return True
        else:
            log.error("[TG] HTTP %d: %s", resp.status_code, resp.text[:200])
            return False
    except Exception as e:
        log.error("[TG] Failed to send: %s", str(e)[:200])
        return False


def send_proposal(chat_id_override: str, job_data: dict) -> bool:
    """Send a generated proposal after Jordan taps Approve."""
    token, chat_id = _get_creds()
    if not token:
        return False
    chat_id = chat_id_override or chat_id

    title = job_data.get("title", "")
    budget = job_data.get("budget", "")
    suggested_bid = job_data.get("suggested_bid", "")
    delivery = job_data.get("suggested_delivery", "")
    category = job_data.get("category", "")
    skills = job_data.get("skills", [])
    description = job_data.get("description", "")

    # Generate proposal text
    if category == "coding":
        intro = "Hi! I specialize in Python automation, bots, scrapers, and API integrations."
        approach = "I'll build this using clean, production-ready Python with proper error handling, logging, and documentation."
        tech = f"Tech stack: Python, {', '.join(skills[:4])}"
    elif category == "content":
        intro = "Hi! I'm an SEO content strategist with experience in keyword research, SERP analysis, and high-ranking article writing."
        approach = "I'll research your target keywords, analyze top-ranking competitors, and deliver SEO-optimized content that ranks."
        tech = "Tools: Ahrefs, Surfer SEO, AI-assisted writing, Google Analytics"
    else:
        intro = "Hi! I'm a full-stack Python developer and SEO content strategist."
        approach = "I can handle both the technical and content aspects of this project."
        tech = f"Tech: Python, {', '.join(skills[:3])}"

    lines = [
        "\U0001f4dd <b>PROPOSAL READY — Copy & Paste</b>",
        "",
        f"<b>For: {_escape(title)}</b>",
        "",
        "---",
        "",
        _escape(intro),
        "",
        _escape(approach),
        "",
        _escape(tech),
        "",
        "I can start immediately and deliver within the agreed timeline. Happy to discuss specifics before we begin.",
        "",
        "Looking forward to working together!",
        "",
        "---",
        "",
        f"\U0001f4b5 <b>Bid: {suggested_bid}</b>",
        f"\u23f0 <b>Delivery: {delivery}</b>",
    ]

    # Add milestones for bigger jobs
    budget_max = job_data.get("budget_usd_max", 0)
    if budget_max >= 200:
        lines.append("")
        lines.append("\U0001f4cb <b>Suggested Milestones:</b>")
        if budget_max >= 500:
            lines.append(f"  1. Setup & architecture — 20% ({suggested_bid} * 0.2)")
            lines.append(f"  2. Core development — 40%")
            lines.append(f"  3. Testing & delivery — 30%")
            lines.append(f"  4. Revisions & handoff — 10%")
        else:
            lines.append(f"  1. Development — 60%")
            lines.append(f"  2. Testing & final delivery — 40%")

    message = "\n".join(lines)

    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML",
            },
            timeout=10,
        )
        return resp.status_code == 200
    except Exception:
        return False


def send_summary(total_scanned: int, matches: int, alerts_sent: int) -> bool:
    token, chat_id = _get_creds()
    if not token or not chat_id:
        return False

    message = (
        "\U0001f40d <b>Viper Scan Complete</b>\n\n"
        f"Scanned: {total_scanned} jobs\n"
        f"Matches: {matches}\n"
        f"Alerts sent: {alerts_sent}\n"
    )

    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
        return resp.status_code == 200
    except Exception:
        return False


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
