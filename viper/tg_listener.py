"""Viper TG Listener — handles Approve/Skip button callbacks.

When Jordan taps Approve:
1. Fetches the full job description from the platform
2. Uses LLM to craft a personalized, compelling proposal
3. Sends it back with bid amount, exact delivery, and milestones
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

import requests

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
JOB_LOG_FILE = DATA_DIR / "viper_job_log.jsonl"
LAST_UPDATE_FILE = DATA_DIR / "viper_tg_last_update.txt"


def _get_token() -> str:
    return os.getenv("VIPER_TG_BOT_TOKEN", "")


def _load_job_by_hash(job_hash: str) -> dict | None:
    if not JOB_LOG_FILE.exists():
        return None
    for line in reversed(JOB_LOG_FILE.read_text().strip().split("\n")):
        try:
            job = json.loads(line)
            if job.get("hash") == job_hash:
                return job
        except Exception:
            continue
    return None


def _get_last_update_id() -> int:
    if LAST_UPDATE_FILE.exists():
        try:
            return int(LAST_UPDATE_FILE.read_text().strip())
        except Exception:
            pass
    return 0


def _save_last_update_id(uid: int) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LAST_UPDATE_FILE.write_text(str(uid))


def _answer_callback(token: str, callback_id: str, text: str) -> None:
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/answerCallbackQuery",
            json={"callback_query_id": callback_id, "text": text},
            timeout=5,
        )
    except Exception:
        pass


def _fetch_full_description(job: dict) -> str:
    """Fetch the complete job description from the platform."""
    source = job.get("source", "")
    job_id = job.get("job_id", "")

    if source == "Freelancer" and job_id:
        try:
            from viper.sources.freelancer_api import fetch_full_job
            full = fetch_full_job(job_id)
            if full:
                return full.get("description", "") or full.get("preview_description", "")
        except Exception as e:
            log.error("[TG_LISTENER] Failed to fetch full job: %s", str(e)[:200])

    # Fallback to what we have
    return job.get("description", "")


def _generate_proposal_with_llm(job: dict, full_description: str) -> str:
    """Use shared LLM to generate a personalized, compelling proposal."""
    title = job.get("title", "")
    category = job.get("category", "coding")
    skills = job.get("skills", [])
    bid_usd = job.get("suggested_bid_usd", 50)
    bid_local = job.get("suggested_bid_local", 0)
    currency = job.get("currency_code", "USD")
    days = job.get("suggested_delivery_days", 5)
    budget_max = job.get("budget_usd_max", 0)

    # Bid string in their currency
    if currency != "USD" and bid_local > 0:
        bid_str = f"{currency} {bid_local:,.0f}"
    else:
        bid_str = f"${bid_usd:.0f}"

    system_prompt = """You are a freelance proposal writer. You write SHORT, direct, compelling proposals that win jobs.

Rules:
- Max 150 words. Clients hate long proposals.
- First sentence: show you READ and UNDERSTOOD their specific project. Reference something specific from their description.
- Second: briefly state your relevant experience (1-2 sentences max). Sound confident, not desperate.
- Third: state your approach — what you'll do and how, in 1-2 sentences.
- End with a confident closer. No begging. No "I hope to hear from you."
- Sound human. No corporate buzzwords. No "I am excited to." No "leverage synergies."
- NO greetings like "Dear Sir" or "Hello". Just start talking about their project.
- Do NOT mention AI or that you use AI tools.
- Write as a solo developer/writer, first person "I".
- Do NOT include pricing or timeline in the proposal text — that goes separately."""

    user_prompt = f"""Write a winning freelance proposal for this job:

Title: {title}
Category: {category}
Skills needed: {', '.join(skills)}
Full description:
{full_description[:1500]}

My strengths: Python developer (bots, scrapers, automation, APIs, data pipelines) and SEO content strategist (keyword research, article writing, SERP analysis). I deliver fast and clean."""

    try:
        from shared.llm_client import llm_call
        proposal_text = llm_call(
            system=system_prompt,
            user=user_prompt,
            agent="viper",
            task_type="proposal",
            max_tokens=300,
            temperature=0.7,
        )
        if proposal_text and len(proposal_text.strip()) > 30:
            return proposal_text.strip()
    except Exception as e:
        log.error("[TG_LISTENER] LLM call failed: %s", str(e)[:200])

    # Fallback — manual proposal
    if category == "coding":
        return (
            f"Your {title.lower()} project is right in my wheelhouse. "
            f"I build Python bots, scrapers, and automation tools daily — "
            f"clean code, proper error handling, delivered fast. "
            f"I can start today and have a working version ready for review within the first milestone."
        )
    else:
        return (
            f"I can deliver exactly what you need for {title.lower()}. "
            f"I do keyword research, competitor analysis, and write content that actually ranks. "
            f"No fluff, no filler — just SEO-optimized content backed by real data."
        )


def _send_proposal_message(chat_id: str, job: dict, proposal_text: str) -> bool:
    """Format and send the complete proposal to Telegram."""
    token = _get_token()
    if not token:
        return False

    title = job.get("title", "")
    bid_usd = job.get("suggested_bid_usd", 50)
    bid_local = job.get("suggested_bid_local", 0)
    currency = job.get("currency_code", "USD")
    days = job.get("suggested_delivery_days", 5)
    budget_max = job.get("budget_usd_max", 0)
    url = job.get("url", "")

    # Bid in their currency for the proposal
    if currency != "USD" and bid_local > 0:
        bid_for_proposal = f"{currency} {bid_local:,.0f}"
        payout_note = f"(You get ~${bid_usd:.0f} USD after fees)"
    else:
        bid_for_proposal = f"${bid_usd:.0f} USD"
        payout_note = f"(~${bid_usd * 0.9:.0f} after platform fees)"

    lines = [
        "\U0001f4dd <b>PROPOSAL — Copy & Paste Below</b>",
        f"<b>Job: {_escape(title)}</b>",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
        _escape(proposal_text),
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
        "\U0001f4b5 <b>Bid Details:</b>",
        f"  Amount: <b>{bid_for_proposal}</b>",
        f"  Delivery: <b>{days} days</b>",
        f"  {payout_note}",
    ]

    # Milestones for bigger projects
    if budget_max >= 200:
        lines.append("")
        lines.append("\U0001f4cb <b>Milestones:</b>")
        if budget_max >= 500:
            m1 = round(bid_local * 0.2) if currency != "USD" and bid_local > 0 else round(bid_usd * 0.2)
            m2 = round(bid_local * 0.4) if currency != "USD" and bid_local > 0 else round(bid_usd * 0.4)
            m3 = round(bid_local * 0.3) if currency != "USD" and bid_local > 0 else round(bid_usd * 0.3)
            m4 = round(bid_local * 0.1) if currency != "USD" and bid_local > 0 else round(bid_usd * 0.1)
            cur = currency if currency != "USD" else "$"
            lines.append(f"  1. Setup & planning — {cur} {m1:,}")
            lines.append(f"  2. Core build — {cur} {m2:,}")
            lines.append(f"  3. Testing & review — {cur} {m3:,}")
            lines.append(f"  4. Final delivery — {cur} {m4:,}")
        else:
            m1 = round(bid_local * 0.5) if currency != "USD" and bid_local > 0 else round(bid_usd * 0.5)
            m2 = round(bid_local * 0.5) if currency != "USD" and bid_local > 0 else round(bid_usd * 0.5)
            cur = currency if currency != "USD" else "$"
            lines.append(f"  1. Development — {cur} {m1:,}")
            lines.append(f"  2. Testing & delivery — {cur} {m2:,}")

    if url:
        lines.append("")
        lines.append(f'<a href="{url}">View Job</a>')

    message = "\n".join(lines)

    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        return resp.status_code == 200
    except Exception as e:
        log.error("[TG_LISTENER] Failed to send proposal: %s", str(e)[:200])
        return False


def poll_callbacks() -> None:
    """Poll for button callbacks and handle Approve/Skip."""
    token = _get_token()
    if not token:
        log.warning("[TG_LISTENER] No VIPER_TG_BOT_TOKEN set")
        return

    last_id = _get_last_update_id()
    log.info("[TG_LISTENER] Starting callback listener (offset=%d)", last_id)

    while True:
        try:
            resp = requests.get(
                f"https://api.telegram.org/bot{token}/getUpdates",
                params={
                    "offset": last_id + 1,
                    "timeout": 30,
                    "allowed_updates": '["callback_query"]',
                },
                timeout=35,
            )

            if resp.status_code != 200:
                time.sleep(5)
                continue

            data = resp.json()
            for update in data.get("result", []):
                uid = update.get("update_id", 0)
                if uid > last_id:
                    last_id = uid
                    _save_last_update_id(last_id)

                cb = update.get("callback_query")
                if not cb:
                    continue

                cb_id = cb.get("id", "")
                cb_data = cb.get("data", "")
                chat_id = str(cb.get("message", {}).get("chat", {}).get("id", ""))

                if cb_data.startswith("approve:"):
                    job_hash = cb_data.split(":", 1)[1]
                    job = _load_job_by_hash(job_hash)
                    if job:
                        _answer_callback(token, cb_id, "Reading job & writing proposal...")

                        # Fetch full description
                        full_desc = _fetch_full_description(job)

                        # Generate proposal with LLM
                        proposal = _generate_proposal_with_llm(job, full_desc)

                        # Send to Jordan
                        _send_proposal_message(chat_id, job, proposal)
                        log.info("[TG_LISTENER] Proposal sent for: %s", job.get("title", "")[:50])
                    else:
                        _answer_callback(token, cb_id, "Job not found in log")

                elif cb_data.startswith("skip:"):
                    _answer_callback(token, cb_id, "Skipped")
                    log.info("[TG_LISTENER] Skipped: %s", cb_data)

        except requests.exceptions.Timeout:
            continue
        except Exception as e:
            log.error("[TG_LISTENER] Error: %s", str(e)[:200])
            time.sleep(5)


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
