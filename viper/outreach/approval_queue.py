"""Outreach approval queue — stores leads pending Jordan's approval on Telegram.

Two-gate flow:
    1. Outreach engine queues a lead (status: pending)
    2. Shelby sends Gate 1 TG (lead info, YES/NO)
    3. Jordan taps YES → status: lead_approved → Gate 2 TG (email draft, GO/SKIP)
    4. Jordan taps GO → status: approved → Resend fires
    5. Jordan taps NO (Gate 1) or SKIP (Gate 2) → status: declined
    6. No reply in 24h → status: expired
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

_QUEUE_PATH = Path.home() / "polymarket-bot" / "data" / "outreach_queue.json"
_TZ = ZoneInfo("America/New_York")
_AUTO_SKIP_HOURS = 24


def _load_queue() -> list[dict]:
    if _QUEUE_PATH.exists():
        try:
            return json.loads(_QUEUE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            return []
    return []


def _save_queue(queue: list[dict]) -> None:
    _QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _QUEUE_PATH.write_text(json.dumps(queue, indent=2, ensure_ascii=False))


def queue_lead(
    business_name: str,
    email: str,
    niche: str,
    city: str,
    score: float,
    chatbot_confidence: str,
    subject: str,
    body: str,
    demo_url: str,
    contact_name: str = "",
    prospect_data: dict | None = None,
) -> str:
    """Add a lead to the approval queue. Returns the lead_id."""
    lead_id = str(uuid.uuid4())[:8]
    now = datetime.now(_TZ).isoformat(timespec="seconds")

    entry = {
        "id": lead_id,
        "business_name": business_name,
        "email": email,
        "contact_name": contact_name,
        "niche": niche,
        "city": city,
        "score": score,
        "chatbot_confidence": chatbot_confidence,
        "subject": subject,
        "body": body,
        "demo_url": demo_url,
        "status": "pending",  # pending, approved, declined, expired
        "queued_at": now,
        "decided_at": "",
        "prospect_data": prospect_data or {},
    }

    queue = _load_queue()
    queue.append(entry)
    _save_queue(queue)
    log.info("Queued lead %s: %s (%s)", lead_id, business_name, email)
    return lead_id


def approve_lead_gate(lead_id: str) -> dict | None:
    """Gate 1: Jordan approved the lead. Move to draft review stage."""
    queue = _load_queue()
    for entry in queue:
        if entry["id"] == lead_id and entry["status"] == "pending":
            entry["status"] = "lead_approved"
            entry["decided_at"] = datetime.now(_TZ).isoformat(timespec="seconds")
            _save_queue(queue)
            log.info("Lead %s passed Gate 1: %s", lead_id, entry["business_name"])
            return entry
    return None


def approve_lead(lead_id: str) -> dict | None:
    """Gate 2: Jordan approved the email draft. Ready to send."""
    queue = _load_queue()
    for entry in queue:
        if entry["id"] == lead_id and entry["status"] == "lead_approved":
            entry["status"] = "approved"
            entry["decided_at"] = datetime.now(_TZ).isoformat(timespec="seconds")
            _save_queue(queue)
            log.info("Lead %s passed Gate 2 (GO): %s", lead_id, entry["business_name"])
            return entry
    return None


def decline_lead(lead_id: str) -> dict | None:
    """Mark lead as declined. Works from pending or lead_approved status."""
    queue = _load_queue()
    for entry in queue:
        if entry["id"] == lead_id and entry["status"] in ("pending", "lead_approved"):
            entry["status"] = "declined"
            entry["decided_at"] = datetime.now(_TZ).isoformat(timespec="seconds")
            _save_queue(queue)
            log.info("Lead %s declined: %s", lead_id, entry["business_name"])
            return entry
    return None


def get_lead(lead_id: str) -> dict | None:
    """Get a lead by ID."""
    queue = _load_queue()
    for entry in queue:
        if entry["id"] == lead_id:
            return entry
    return None


def get_expired_leads() -> list[dict]:
    """Find leads that have been pending longer than _AUTO_SKIP_HOURS."""
    queue = _load_queue()
    now = datetime.now(_TZ)
    cutoff = now - timedelta(hours=_AUTO_SKIP_HOURS)
    expired = []

    changed = False
    for entry in queue:
        if entry["status"] != "pending":
            continue
        queued_at = datetime.fromisoformat(entry["queued_at"])
        if queued_at < cutoff:
            entry["status"] = "expired"
            entry["decided_at"] = now.isoformat(timespec="seconds")
            expired.append(entry)
            changed = True

    if changed:
        _save_queue(queue)
    return expired


def get_pending_count() -> int:
    """Count of leads awaiting Jordan's decision."""
    return sum(1 for e in _load_queue() if e["status"] == "pending")


def get_queue_stats() -> dict:
    """Stats for reporting."""
    queue = _load_queue()
    return {
        "pending": sum(1 for e in queue if e["status"] == "pending"),
        "approved": sum(1 for e in queue if e["status"] == "approved"),
        "declined": sum(1 for e in queue if e["status"] == "declined"),
        "expired": sum(1 for e in queue if e["status"] == "expired"),
        "total": len(queue),
    }
