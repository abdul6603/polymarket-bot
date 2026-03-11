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

import json
import logging
import os
from pathlib import Path

import requests

from viper.outreach.sendgrid_mailer import send_email
from viper.outreach.templates import get_outreach_message, resolve_niche_key
from viper.outreach.outreach_log import already_contacted, log_outreach
from viper.outreach.approval_queue import queue_lead
from viper.prospecting.site_auditor import format_findings_for_email
from viper.outreach.email_sequences import create_sequence
from viper.tg_router import send as tg_send

log = logging.getLogger(__name__)

# Demo URL base — GitHub Pages
_DEMO_BASE = "https://darkcode-ai.github.io/chatbot-demos/"

# ONLY demos that are live and verified on GitHub Pages.
# NEVER auto-generate slugs. If a niche isn't here, use belknapdental-com.
_VERIFIED_DEMOS: dict[str, str] = {
    "dental": "dental-demo",
    "real_estate": "realestate-demo",
}

# Telegram Bot API — read from env or Shelby's .env
_TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
_TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

if not _TG_TOKEN:
    # Try loading from Shelby's .env as fallback
    _shelby_env = Path.home() / "shelby" / ".env"
    if _shelby_env.exists():
        for line in _shelby_env.read_text().splitlines():
            if line.startswith("TELEGRAM_BOT_TOKEN="):
                _TG_TOKEN = line.split("=", 1)[1].strip()
            elif line.startswith("TELEGRAM_CHAT_ID="):
                _TG_CHAT_ID = line.split("=", 1)[1].strip()


def _validate_lead(lead: dict) -> tuple[bool, str]:
    """Pre-send checklist. Returns (ok, reason)."""
    biz = lead.get("business_name", "")

    # 1. Practice name must not be an individual doctor
    if biz.startswith("Dr.") or biz.startswith("Dr "):
        return False, f"Individual doctor, not a practice: {biz}"

    # 2. Greeting must be clean — no junk words
    body = lead.get("body", "")
    if body:
        greeting = body.split("\n")[0]
        bad_words = ["meet", "launch", "staff", "click", "view", "read",
                     "our", "welcome", "schedule", "visit", "call", "contact"]
        if any(bad in greeting.lower() for bad in bad_words):
            return False, f"Bad greeting: {greeting}"

    # 3. Email must exist and look valid (not a URL)
    email = lead.get("email", "")
    if email and email.startswith("http"):
        return False, f"Email is a URL, not an address: {email}"
    if not email or "@" not in email:
        return False, f"No email for {biz}"

    # 4. Subject and body must not be empty
    if not lead.get("subject") or not lead.get("body"):
        return False, f"Empty subject or body for {biz}"

    # 5. No duplicate — check queue for same email
    from viper.outreach.approval_queue import _load_queue
    queue = _load_queue()
    for existing in queue:
        if existing["id"] == lead.get("id"):
            continue
        if existing["status"] in ("pending", "lead_approved", "approved"):
            if existing.get("email") == email:
                return False, f"Duplicate email: {email} already queued as {existing['business_name']}"

    # 6. Every URL in email must be live (200 status)
    dead = _check_urls_live(body)
    if dead:
        return False, f"Dead links in email: {', '.join(dead)}"

    return True, ""


def _check_urls_live(text: str) -> list[str]:
    """Extract all URLs from text and verify each returns HTTP 200.

    Returns list of dead/broken URLs. Empty list = all good.
    """
    import re as _re
    urls = _re.findall(r'https?://[^\s,)<>"]+', text)
    dead: list[str] = []
    for url in urls:
        try:
            resp = requests.head(url, timeout=8, allow_redirects=True)
            if resp.status_code != 200:
                # Retry with GET — some servers reject HEAD
                resp = requests.get(url, timeout=8, allow_redirects=True, stream=True)
                if resp.status_code != 200:
                    dead.append(f"{url} ({resp.status_code})")
        except Exception as e:
            dead.append(f"{url} (unreachable: {e})")
    return dead


def _send_tg(text: str, buttons: list[list[dict]] | None = None) -> bool:
    """Send via tg_router on OUTREACH channel."""
    return tg_send(text, channel="OUTREACH", buttons=buttons)


def _send_approval_request(lead_id: str, prospect, niche_key: str) -> None:
    """Gate 1: Send TG message with lead info only. YES/NO buttons."""
    chatbot_line = "No" if prospect.chatbot_confidence == "NOT_FOUND" else "Unknown (scanner uncertain)"

    email_line = prospect.email if prospect.email else "NO EMAIL FOUND"
    contact_line = ""
    if not prospect.email:
        form_url = getattr(prospect, "contact_form_url", "")
        if form_url:
            contact_line = f"\nContact form: {form_url}"
        else:
            contact_line = "\nNeeds manual lookup (contact form on site)"

    hot_label = " \U0001f525 HOT" if prospect.score >= 8.0 else ""

    text = (
        f"<b>GATE 1 — New Lead{hot_label}</b>\n\n"
        f"Business: {prospect.business_name}\n"
        f"Niche: {niche_key}\n"
        f"Email: {email_line}{contact_line}\n"
        f"Website: {prospect.website}\n"
        f"Phone: {prospect.phone}\n"
        f"Has chatbot: {chatbot_line}\n"
        f"Score: {prospect.score}/10{hot_label}\n\n"
        f"Approve this lead?"
    )

    buttons = [
        [
            {"text": "YES — build email", "callback_data": f"outreach_yes:{lead_id}"},
            {"text": "NO — skip", "callback_data": f"outreach_no:{lead_id}"},
        ],
    ]

    if _send_tg(text, buttons):
        log.info("Gate 1 sent for %s (lead %s)", prospect.business_name, lead_id)
    else:
        print(f"  [TG FALLBACK] Gate 1 approval needed for {prospect.business_name} ({prospect.email}) — lead_id: {lead_id}")


def send_draft_review(lead: dict) -> None:
    """Gate 2: Send TG message with full email draft. GO/SKIP buttons."""
    # Pre-send validation — block junk before it reaches Jordan
    ok, reason = _validate_lead(lead)
    if not ok:
        log.warning("Gate 2 BLOCKED for %s: %s", lead.get("business_name"), reason)
        _send_tg(f"BLOCKED: {reason}")
        from viper.outreach.approval_queue import decline_lead
        decline_lead(lead["id"])
        return

    text = (
        f"<b>GATE 2 — Email Draft</b>\n\n"
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

    if _send_tg(text, buttons):
        log.info("Gate 2 draft sent for %s (lead %s)", lead["business_name"], lead["id"])
    else:
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

    # Create follow-up sequence after successful send
    if result.get("status_code") in (200, 202):
        try:
            seq_id = create_sequence(lead)
            log.info("Follow-up sequence %s created for %s", seq_id, lead["business_name"])
        except Exception as e:
            log.error("Failed to create follow-up sequence: %s", e)

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

        # No email = hard skip. Jordan doesn't want leads without emails.
        if not p.email:
            log.info("Skipping %s — no email found", p.business_name)
            stats["skipped"] += 1
            continue

        # Dedup check
        if already_contacted(p.email, niche, city):
            log.info("Already contacted %s — skipping", p.business_name)
            stats["already_contacted"] += 1
            continue

        # Build demo URL — ONLY use verified, live demos
        if demo_slug:
            demo_url = f"{_DEMO_BASE}{demo_slug}/"
        else:
            demo_url = f"{_DEMO_BASE}{_VERIFIED_DEMOS.get(niche_key, 'belknapdental-com')}/"

        # Filter individual doctors at outreach level too
        if p.business_name.startswith("Dr.") or p.business_name.startswith("Dr "):
            log.info("Skipping %s — individual doctor, not a practice", p.business_name)
            stats["skipped"] += 1
            continue

        # Format audit findings for email (if available)
        findings_text = ""
        if hasattr(p, "audit_findings") and p.audit_findings:
            findings_text = format_findings_for_email(p.audit_findings)

        # Build message
        msg = get_outreach_message(
            niche=niche_key,
            business_name=p.business_name,
            demo_url=demo_url,
            contact_name=p.contact_name,
            findings=findings_text,
        )

        # Greeting sanity check before queuing
        greeting = msg["body"].split("\n")[0] if msg.get("body") else ""
        bad_words = ["meet", "launch", "staff", "click", "view", "read",
                     "our", "welcome", "schedule", "visit", "call", "contact"]
        if any(bad in greeting.lower() for bad in bad_words):
            log.warning("Bad greeting for %s: %s — skipping", p.business_name, greeting)
            stats["skipped"] += 1
            continue

        if dry_run:
            print(f"  [DRY RUN] Would queue {p.business_name} ({p.email}) for Jordan approval")
            stats["queued"] += 1
            continue

        # Queue for approval (dedup built in — returns "" if duplicate)
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

        if not lead_id:
            stats["skipped"] += 1
            continue

        # Send Gate 1 TG approval request to Jordan (lead info only)
        _send_approval_request(lead_id, p, niche_key)
        stats["queued"] += 1
        print(f"  [outreach] Queued {p.business_name} ({p.email}) → TG sent to Jordan (lead {lead_id})")

    print(f"\n  [outreach] Done: {stats['queued']} queued, "
          f"{stats['skipped']} skipped (no email/chatbot/bad greeting), "
          f"{stats['already_contacted']} already contacted")

    return stats
