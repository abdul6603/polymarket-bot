"""Outreach engine — orchestrates the Viper→Shelby→Jordan approval pipeline.

TWO-GATE approval flow (nothing sends without Jordan's explicit GO):

Gate 1 — LEAD APPROVAL:
  1. DETECTED chatbot → auto-skip
  2. NOT_FOUND / UNCERTAIN → TG message to Jordan with lead info
  3. Jordan taps YES → move to Gate 2. NO → decline.

Gate 2 — EMAIL DRAFT REVIEW:
  4. Full email draft sent to Jordan on Telegram
  5. Jordan taps GO → Resend fires the email
  6. Jordan taps SKIP → email NOT sent, lead declined

No reply in 24h → auto-skip, notify Jordan.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from viper.outreach.sendgrid_mailer import send_email
from viper.outreach.templates import get_outreach_message, resolve_niche_key
from viper.outreach.outreach_log import already_contacted, log_outreach
from viper.outreach.approval_queue import queue_lead

log = logging.getLogger(__name__)

# Demo URL base — GitHub Pages
_DEMO_BASE = "https://darkcode-ai.github.io/chatbot-demos/"


def _notify_jordan(message: str) -> None:
    """Send Telegram notification to Jordan via shared notifier."""
    try:
        sys.path.insert(0, str(Path.home()))
        from shared.telegram_notify import notify, NotifyType, Urgency
        notify(NotifyType.ALERT, message, Urgency.IMMEDIATE)
    except Exception as e:
        log.warning("TG notification failed: %s — printing instead", e)
        print(f"  [TG] {message}")


def _send_approval_request(lead_id: str, prospect, niche_key: str) -> None:
    """Gate 1: Send TG message with lead info only. YES/NO buttons."""
    try:
        sys.path.insert(0, str(Path.home()))
        from shelby.core.telegram import get_bot

        chatbot_line = "No" if prospect.chatbot_confidence == "NOT_FOUND" else "Unknown (scanner uncertain)"

        text = (
            f"GATE 1 — New Lead\n\n"
            f"Business: {prospect.business_name}\n"
            f"Niche: {niche_key}\n"
            f"Email: {prospect.email}\n"
            f"Has chatbot: {chatbot_line}\n"
            f"Score: {prospect.score}/10\n\n"
            f"Approve this lead?"
        )

        buttons = [
            [
                {"text": "YES — build email", "callback_data": f"outreach_yes:{lead_id}"},
                {"text": "NO — skip", "callback_data": f"outreach_no:{lead_id}"},
            ],
        ]

        bot = get_bot()
        bot.send_with_keyboard(text, buttons)
        log.info("Gate 1 sent for %s (lead %s)", prospect.business_name, lead_id)
    except Exception as e:
        log.error("Failed to send Gate 1 TG for %s: %s", prospect.business_name, e)
        print(f"  [TG FALLBACK] Gate 1 approval needed for {prospect.business_name} ({prospect.email}) — lead_id: {lead_id}")


def send_draft_review(lead: dict) -> None:
    """Gate 2: Send TG message with full email draft. GO/SKIP buttons."""
    try:
        sys.path.insert(0, str(Path.home()))
        from shelby.core.telegram import get_bot

        text = (
            f"GATE 2 — Email Draft\n\n"
            f"To: {lead['email']} ({lead['business_name']})\n"
            f"Subject: {lead['subject']}\n\n"
            f"{lead['body']}\n\n"
            f"Send this email?"
        )

        buttons = [
            [
                {"text": "GO — send it", "callback_data": f"outreach_go:{lead['id']}"},
                {"text": "SKIP — don't send", "callback_data": f"outreach_skip:{lead['id']}"},
            ],
        ]

        bot = get_bot()
        bot.send_with_keyboard(text, buttons)
        log.info("Gate 2 draft sent for %s (lead %s)", lead["business_name"], lead["id"])
    except Exception as e:
        log.error("Failed to send Gate 2 TG for %s: %s", lead["business_name"], e)
        print(f"  [TG FALLBACK] Gate 2 draft review needed for {lead['business_name']} — lead_id: {lead['id']}")


def send_approved_email(lead: dict) -> dict:
    """Send the actual email for an approved lead. Called by Shelby callback."""
    result = send_email(
        to_email=lead["email"],
        subject=lead["subject"],
        body=lead["body"],
        to_name=lead.get("contact_name", ""),
    )

    log_outreach(
        business_name=lead["business_name"],
        email=lead["email"],
        niche=lead["niche"],
        city=lead["city"],
        subject=lead["subject"],
        score=lead["score"],
        demo_url=lead["demo_url"],
        sendgrid_status=result["status_code"],
        error=result.get("error", ""),
        prospect_data=lead.get("prospect_data", {}),
    )

    return result


def run_outreach(
    prospects: list,
    niche: str,
    city: str,
    min_score: float = 7.0,
    demo_slug: str = "",
    dry_run: bool = False,
) -> dict:
    """Queue outreach leads for Jordan's Telegram approval.

    Nothing sends without Jordan's YES.
    """
    niche_key = resolve_niche_key(niche)
    stats = {"queued": 0, "skipped": 0, "already_contacted": 0}

    qualified = [p for p in prospects if p.score >= min_score]
    if not qualified:
        print(f"  No prospects scored >= {min_score}. Nothing to queue.")
        return stats

    print(f"\n  [outreach] {len(qualified)} prospects qualify (score >= {min_score})")

    for p in qualified:
        # DETECTED = auto-skip (already has a chatbot)
        if p.chatbot_confidence == "DETECTED":
            log.info("Skipping %s — chatbot DETECTED (%s)", p.business_name, p.chatbot_name)
            stats["skipped"] += 1
            continue

        # Must have email
        if not p.email:
            log.debug("Skipping %s — no email", p.business_name)
            stats["skipped"] += 1
            continue

        # Dedup check
        if already_contacted(p.email, niche, city):
            log.info("Already contacted %s — skipping", p.business_name)
            stats["already_contacted"] += 1
            continue

        # Build demo URL
        if demo_slug:
            demo_url = f"{_DEMO_BASE}{demo_slug}/"
        else:
            slug = p.business_name.lower().replace(" ", "-").replace(".", "")
            slug = "".join(c for c in slug if c.isalnum() or c == "-")
            demo_url = f"{_DEMO_BASE}{slug}/"

        # Build message
        msg = get_outreach_message(
            niche=niche_key,
            business_name=p.business_name,
            demo_url=demo_url,
            contact_name=p.contact_name,
        )

        if dry_run:
            print(f"  [DRY RUN] Would queue {p.business_name} ({p.email}) for Jordan approval")
            stats["queued"] += 1
            continue

        # Queue for approval
        lead_id = queue_lead(
            business_name=p.business_name,
            email=p.email,
            niche=niche,
            city=city,
            score=p.score,
            chatbot_confidence=p.chatbot_confidence,
            subject=msg["subject"],
            body=msg["body"],
            demo_url=demo_url,
            contact_name=p.contact_name,
            prospect_data=p.to_dict(),
        )

        # Send Gate 1 TG approval request to Jordan (lead info only)
        _send_approval_request(lead_id, p, niche_key)
        stats["queued"] += 1
        print(f"  [outreach] Queued {p.business_name} → TG sent to Jordan (lead {lead_id})")

    print(f"\n  [outreach] Done: {stats['queued']} queued for Jordan's approval, "
          f"{stats['skipped']} skipped (chatbot/no email), "
          f"{stats['already_contacted']} already contacted")

    return stats
