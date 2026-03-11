"""Drip sequence runner — checks for due follow-ups and sends them to TG.

Runs as part of the Viper loop. Every cycle:
1. Check for due follow-up emails (Day 3/7/14)
2. Generate the draft
3. Send to Jordan's TG (OUTREACH channel) with SEND/STOP buttons
4. Jordan approves → email fires via Resend
5. Jordan hits STOP → entire sequence cancelled

No email sends without Jordan pressing SEND.
"""
from __future__ import annotations

import logging

from viper.outreach.email_sequences import (
    get_due_followups,
    generate_followup_draft,
    approve_followup,
    mark_sent,
    cancel_sequence,
)
from viper.tg_router import send as tg_send

log = logging.getLogger(__name__)

_STEP_LABELS = {
    "check_in": "Day 3 — Follow-up",
    "value_add": "Day 7 — Case Study",
    "closing": "Day 14 — Final",
}


def run_drip_cycle() -> int:
    """Check for due follow-ups and send drafts to TG for approval.

    Returns number of follow-ups queued for Jordan's review.
    """
    due = get_due_followups()
    if not due:
        return 0

    sent_count = 0
    for step_info in due:
        draft = generate_followup_draft(step_info)
        step_label = _STEP_LABELS.get(step_info["type"], step_info["type"])
        step_id = step_info["step_id"]
        seq_id = step_info["seq_id"]

        text = (
            f"<b>FOLLOW-UP DUE</b>\n\n"
            f"Business: {step_info['business_name']}\n"
            f"Email: {step_info['email']}\n"
            f"Stage: <b>{step_label}</b>\n"
            f"Original subject: {step_info.get('original_subject', '')}\n\n"
            f"<b>--- Draft ---</b>\n"
            f"Subject: {draft['subject']}\n\n"
            f"{_escape_html(draft['body'])}\n\n"
            f"<i>Approve to send, or stop the entire sequence.</i>"
        )

        # Store draft + recipient info so callback handler can send it
        draft["to_email"] = step_info["email"]
        draft["contact_name"] = step_info.get("contact_name", "")
        _store_draft(step_id, draft)

        buttons = [[
            {"text": "SEND", "callback_data": f"drip_send:{step_id}"},
            {"text": "STOP sequence", "callback_data": f"drip_stop:{seq_id}"},
        ]]

        ok = tg_send(text, channel="OUTREACH", buttons=buttons)
        if ok:
            sent_count += 1
            log.info("[DRIP] Queued %s for %s (%s)",
                     step_label, step_info["business_name"], step_id)

    if sent_count:
        log.info("[DRIP] %d follow-up(s) sent to TG for review", sent_count)

    return sent_count


def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# Draft storage — simple in-memory cache for callback handler
_DRAFT_CACHE: dict[str, dict] = {}


def _store_draft(step_id: str, draft: dict) -> None:
    _DRAFT_CACHE[step_id] = draft


def get_stored_draft(step_id: str) -> dict | None:
    return _DRAFT_CACHE.get(step_id)
