"""Email follow-up sequence engine.

After Jordan approves and sends an initial outreach email, this engine
creates a 3-step follow-up sequence. Every follow-up goes through
Jordan's TG approval before sending.

Sequence steps:
  Day 3  — Short check-in
  Day 7  — Case study / value add
  Day 14 — Closing / break-up

Auto-cancels on reply detection.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)

ET = timezone(timedelta(hours=-5))

_SEQUENCES_FILE = Path.home() / "polymarket-bot" / "data" / "outreach_sequences.json"


def _load_sequences() -> list[dict]:
    if _SEQUENCES_FILE.exists():
        return json.loads(_SEQUENCES_FILE.read_text())
    return []


def _save_sequences(sequences: list[dict]) -> None:
    _SEQUENCES_FILE.parent.mkdir(parents=True, exist_ok=True)
    _SEQUENCES_FILE.write_text(json.dumps(sequences, indent=2, ensure_ascii=False))


def create_sequence(lead_data: dict) -> str:
    """Create a 3-step follow-up sequence after initial email is sent.

    Args:
        lead_data: The lead dict from approval_queue (has business_name,
                   email, niche, subject, body, etc.)

    Returns:
        Sequence ID.
    """
    seq_id = str(uuid.uuid4())[:8]
    now = datetime.now(ET)

    # Extract a key finding from the original email for reference
    original_body = lead_data.get("body", "")
    finding_snippet = _extract_finding(original_body)

    steps = [
        {
            "step": 1,
            "step_id": f"{seq_id}-s1",
            "delay_days": 3,
            "send_at": (now + timedelta(days=3)).isoformat(),
            "type": "check_in",
            "status": "pending",
            "draft": None,
            "sent_at": None,
        },
        {
            "step": 2,
            "step_id": f"{seq_id}-s2",
            "delay_days": 7,
            "send_at": (now + timedelta(days=7)).isoformat(),
            "type": "value_add",
            "status": "pending",
            "draft": None,
            "sent_at": None,
        },
        {
            "step": 3,
            "step_id": f"{seq_id}-s3",
            "delay_days": 14,
            "send_at": (now + timedelta(days=14)).isoformat(),
            "type": "closing",
            "status": "pending",
            "draft": None,
            "sent_at": None,
        },
    ]

    sequence = {
        "id": seq_id,
        "lead_id": lead_data.get("id", ""),
        "business_name": lead_data.get("business_name", ""),
        "email": lead_data.get("email", ""),
        "niche": lead_data.get("niche", ""),
        "contact_name": lead_data.get("contact_name", ""),
        "original_subject": lead_data.get("subject", ""),
        "finding_snippet": finding_snippet,
        "created_at": now.isoformat(),
        "status": "active",
        "steps": steps,
    }

    sequences = _load_sequences()
    sequences.append(sequence)
    _save_sequences(sequences)

    log.info("Created follow-up sequence %s for %s (%s)",
             seq_id, lead_data.get("business_name"), lead_data.get("email"))
    return seq_id


def _extract_finding(body: str) -> str:
    """Pull the first audit finding line from the original email body."""
    for line in body.split("\n"):
        stripped = line.strip()
        if stripped.startswith("- ") and len(stripped) > 10:
            return stripped[2:]  # Remove the "- " prefix
    return ""


def get_due_followups() -> list[dict]:
    """Return steps where send_at <= now and status == pending.

    Returns list of dicts with sequence + step info merged.
    """
    sequences = _load_sequences()
    now = datetime.now(ET)
    due = []

    for seq in sequences:
        if seq["status"] != "active":
            continue
        for step in seq["steps"]:
            if step["status"] != "pending":
                continue
            send_at = datetime.fromisoformat(step["send_at"])
            if send_at <= now:
                due.append({
                    "seq_id": seq["id"],
                    "lead_id": seq["lead_id"],
                    "business_name": seq["business_name"],
                    "email": seq["email"],
                    "niche": seq["niche"],
                    "contact_name": seq["contact_name"],
                    "original_subject": seq["original_subject"],
                    "finding_snippet": seq["finding_snippet"],
                    **step,
                })
    return due


def generate_followup_draft(step_info: dict) -> dict:
    """Generate a follow-up email draft based on step type.

    Returns dict with 'subject' and 'body' keys.
    """
    biz = step_info["business_name"]
    contact = step_info.get("contact_name", "")
    greeting = f"Hi {contact}" if contact else "Hi there"
    original_subject = step_info.get("original_subject", "")
    finding = step_info.get("finding_snippet", "")
    step_type = step_info["type"]

    # Re: thread on original subject
    subject = f"Re: {original_subject}" if original_subject else f"Following up — {biz}"

    if step_type == "check_in":
        finding_ref = f" about {finding.lower()}" if finding else ""
        body = (
            f"{greeting},\n\n"
            f"Just making sure my earlier email didn't get buried — wanted to "
            f"see if you had any questions{finding_ref}.\n\n"
            f"Happy to do a quick walkthrough if helpful.\n\n"
            f"Jordan\n"
            f"DarkCode AI"
        )

    elif step_type == "value_add":
        body = (
            f"{greeting},\n\n"
            f"Thought you might find this useful — we recently built a chat "
            f"assistant for a similar business that now handles 90%+ of their "
            f"common questions automatically. Their front desk staff went from "
            f"30+ daily phone calls to under 10.\n\n"
            f"The demo I sent earlier shows exactly how it works for a "
            f"business like {biz}.\n\n"
            f"Worth a 2-minute look?\n\n"
            f"Jordan\n"
            f"DarkCode AI"
        )

    elif step_type == "closing":
        body = (
            f"{greeting},\n\n"
            f"Wanted to close the loop on my earlier email about the chat "
            f"assistant for {biz}.\n\n"
            f"If timing isn't right, no worries at all — I'll check back in a "
            f"few months. But if you're curious, the offer to build a free "
            f"custom demo still stands.\n\n"
            f"Either way, thanks for your time.\n\n"
            f"Jordan\n"
            f"DarkCode AI"
        )

    else:
        body = ""

    return {"subject": subject, "body": body}


def approve_followup(step_id: str) -> bool:
    """Mark a follow-up step as approved (ready to send)."""
    sequences = _load_sequences()
    for seq in sequences:
        for step in seq["steps"]:
            if step["step_id"] == step_id:
                step["status"] = "approved"
                _save_sequences(sequences)
                log.info("Approved follow-up %s", step_id)
                return True
    return False


def mark_sent(step_id: str) -> bool:
    """Mark a follow-up step as sent."""
    sequences = _load_sequences()
    now = datetime.now(ET)
    for seq in sequences:
        for step in seq["steps"]:
            if step["step_id"] == step_id:
                step["status"] = "sent"
                step["sent_at"] = now.isoformat()
                _save_sequences(sequences)
                log.info("Marked follow-up %s as sent", step_id)
                return True
    return False


def cancel_sequence(lead_id: str) -> bool:
    """Cancel all remaining steps for a lead (e.g., on reply detection)."""
    sequences = _load_sequences()
    found = False
    for seq in sequences:
        if seq["lead_id"] == lead_id and seq["status"] == "active":
            seq["status"] = "cancelled"
            for step in seq["steps"]:
                if step["status"] == "pending":
                    step["status"] = "cancelled"
            found = True
            log.info("Cancelled sequence for lead %s (%s)", lead_id, seq["business_name"])
    if found:
        _save_sequences(sequences)
    return found


def cancel_sequence_by_id(seq_id: str) -> str:
    """Cancel a sequence by its seq_id. Returns business_name or empty string."""
    sequences = _load_sequences()
    for seq in sequences:
        if seq["id"] == seq_id and seq["status"] == "active":
            seq["status"] = "cancelled"
            for step in seq["steps"]:
                if step["status"] == "pending":
                    step["status"] = "cancelled"
            _save_sequences(sequences)
            log.info("Cancelled sequence %s (%s)", seq_id, seq["business_name"])
            return seq["business_name"]
    return ""


def mark_replied(lead_id: str) -> bool:
    """Auto-cancel sequence when a reply is detected."""
    return cancel_sequence(lead_id)


def get_sequence_stats() -> dict:
    """Return summary stats for all sequences."""
    sequences = _load_sequences()
    return {
        "total": len(sequences),
        "active": sum(1 for s in sequences if s["status"] == "active"),
        "cancelled": sum(1 for s in sequences if s["status"] == "cancelled"),
        "completed": sum(1 for s in sequences if s["status"] == "completed"),
        "pending_steps": sum(
            1 for s in sequences if s["status"] == "active"
            for st in s["steps"] if st["status"] == "pending"
        ),
    }
