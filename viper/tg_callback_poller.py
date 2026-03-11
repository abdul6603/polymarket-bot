"""Lightweight callback poller for Viper TG bots.

Polls getUpdates for inline button callbacks (BID/SKIP, YES/NO/GO)
and handles them directly — no dependency on Shelby.

Runs as a daemon thread inside the Viper process.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

import requests

log = logging.getLogger(__name__)

_POLL_INTERVAL = 2  # seconds


def _load_bot_tokens() -> dict[str, str]:
    """Load Viper bot tokens from env/.env file."""
    tokens: dict[str, str] = {}
    keys = ["VIPER_INBOUND_BOT_TOKEN", "VIPER_OUTREACH_BOT_TOKEN"]
    for k in keys:
        tokens[k] = os.getenv(k, "")

    if not tokens["VIPER_INBOUND_BOT_TOKEN"]:
        env_path = Path.home() / "polymarket-bot" / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                for k in keys:
                    if line.startswith(f"{k}=") and not tokens[k]:
                        tokens[k] = line.split("=", 1)[1].strip()
    return tokens


def _find_lead_by_hash(lead_hash: str) -> dict | None:
    """Find a lead in viper_leads.json by hash prefix."""
    leads_file = Path.home() / "polymarket-bot" / "data" / "viper_leads.json"
    if not leads_file.exists():
        return None
    try:
        data = json.loads(leads_file.read_text())
        for lead in data.get("leads", []):
            if lead.get("hash", "").startswith(lead_hash):
                return lead
    except Exception:
        pass
    return None


def _handle_callback(bot_token: str, update: dict) -> None:
    """Handle a single callback query from a Viper bot."""
    cb = update.get("callback_query")
    if not cb:
        return

    cb_id = cb["id"]
    data = cb.get("data", "")
    msg = cb.get("message", {})
    chat_id = msg.get("chat", {}).get("id")
    message_id = msg.get("message_id")
    original_text = msg.get("text", "")

    # Determine action
    if data.startswith("viper_bid:"):
        lead_hash = data.replace("viper_bid:", "")
        _handle_bid(bot_token, cb_id, chat_id, message_id, original_text, lead_hash)
    elif data.startswith("viper_skip:"):
        lead_hash = data.replace("viper_skip:", "")
        _handle_skip(bot_token, cb_id, chat_id, message_id, original_text, lead_hash)
    elif data.startswith("outreach_yes:"):
        lead_id = data.replace("outreach_yes:", "")
        _handle_outreach_yes(bot_token, cb_id, chat_id, message_id, original_text, lead_id)
    elif data.startswith("outreach_no:"):
        lead_id = data.replace("outreach_no:", "")
        _handle_outreach_no(bot_token, cb_id, chat_id, message_id, original_text, lead_id)
    elif data.startswith("outreach_go:"):
        lead_id = data.replace("outreach_go:", "")
        _handle_outreach_go(bot_token, cb_id, chat_id, message_id, original_text, lead_id)
    elif data.startswith("outreach_skip:"):
        lead_id = data.replace("outreach_skip:", "")
        _handle_outreach_skip(bot_token, cb_id, chat_id, message_id, original_text, lead_id)
    elif data.startswith("drip_send:"):
        step_id = data.replace("drip_send:", "")
        _handle_drip_send(bot_token, cb_id, chat_id, message_id, original_text, step_id)
    elif data.startswith("drip_stop:"):
        seq_id = data.replace("drip_stop:", "")
        _handle_drip_stop(bot_token, cb_id, chat_id, message_id, original_text, seq_id)
    else:
        _answer_callback(bot_token, cb_id, "Unknown action")


def _handle_bid(bot_token, cb_id, chat_id, message_id, original_text, lead_hash):
    """BID on an inbound lead — mark status + auto-generate proposal."""
    try:
        from viper.lead_writer import mark_lead_status
        found = mark_lead_status(lead_hash, "bid")
        suffix = "\n\nBID — generating proposal..." if found else "\n\nBID (lead not found in DB)"
        _answer_callback(bot_token, cb_id, "Generating proposal...")
        _edit_message(bot_token, chat_id, message_id, original_text + suffix)
        log.info("BID for lead %s (found=%s)", lead_hash, found)

        # Auto-generate proposal
        if found:
            lead = _find_lead_by_hash(lead_hash)
            if lead:
                from viper.proposal_gen import auto_generate_for_lead
                auto_generate_for_lead(lead)
    except Exception as e:
        log.error("BID failed for %s: %s", lead_hash, e)
        _answer_callback(bot_token, cb_id, f"Error: {e}")


def _handle_skip(bot_token, cb_id, chat_id, message_id, original_text, lead_hash):
    """SKIP an inbound lead."""
    try:
        from viper.lead_writer import mark_lead_status
        mark_lead_status(lead_hash, "skip")
        _answer_callback(bot_token, cb_id, "Skipped")
        _edit_message(bot_token, chat_id, message_id, original_text + "\n\nSKIPPED")
        log.info("SKIP for lead %s", lead_hash)
    except Exception as e:
        log.error("SKIP failed for %s: %s", lead_hash, e)
        _answer_callback(bot_token, cb_id, f"Error: {e}")


def _handle_outreach_yes(bot_token, cb_id, chat_id, message_id, original_text, lead_id):
    """Gate 1: Approve outreach lead."""
    try:
        from viper.outreach.approval_queue import approve_lead_gate
        lead = approve_lead_gate(lead_id)
        if not lead:
            _answer_callback(bot_token, cb_id, "Lead not found")
            _edit_message(bot_token, chat_id, message_id, original_text + "\n\nLead not found or already decided.")
            return
        _answer_callback(bot_token, cb_id, "Approved — building draft...")
        _edit_message(bot_token, chat_id, message_id, original_text + f"\n\nAPPROVED: {lead.get('business_name', lead_id)}")

        # Send Gate 2 draft
        from viper.outreach.outreach_engine import send_draft_review
        send_draft_review(lead)
        log.info("Gate 1 YES for %s", lead_id)
    except Exception as e:
        log.error("Gate 1 YES failed for %s: %s", lead_id, e)
        _answer_callback(bot_token, cb_id, f"Error: {e}")


def _handle_outreach_no(bot_token, cb_id, chat_id, message_id, original_text, lead_id):
    """Gate 1: Skip outreach lead."""
    try:
        from viper.outreach.approval_queue import decline_lead
        lead = decline_lead(lead_id)
        name = lead.get("business_name", lead_id) if lead else lead_id
        _answer_callback(bot_token, cb_id, "Skipped")
        _edit_message(bot_token, chat_id, message_id, original_text + f"\n\nSKIPPED: {name}")
        log.info("Gate 1 NO for %s", lead_id)
    except Exception as e:
        log.error("Gate 1 NO failed for %s: %s", lead_id, e)
        _answer_callback(bot_token, cb_id, f"Error: {e}")


def _handle_outreach_go(bot_token, cb_id, chat_id, message_id, original_text, lead_id):
    """Gate 2: Send the email."""
    try:
        from viper.outreach.approval_queue import approve_lead
        from viper.outreach.outreach_engine import send_approved_email
        lead = approve_lead(lead_id)
        if not lead:
            _answer_callback(bot_token, cb_id, "Lead not found")
            return
        if not lead.get("email") or "@" not in lead.get("email", ""):
            _answer_callback(bot_token, cb_id, "No email — blocked")
            _edit_message(bot_token, chat_id, message_id, original_text + "\n\nBLOCKED: No email address.")
            return
        result = send_approved_email(lead)
        if result["success"]:
            _answer_callback(bot_token, cb_id, "Email sent!")
            _edit_message(bot_token, chat_id, message_id, original_text + f"\n\nSENT to {lead['email']}")
        else:
            _answer_callback(bot_token, cb_id, "Send failed")
            _edit_message(bot_token, chat_id, message_id, original_text + f"\n\nFAILED: {result['error']}")
        log.info("Gate 2 GO for %s: %s", lead_id, result.get("success"))
    except Exception as e:
        log.error("Gate 2 GO failed for %s: %s", lead_id, e)
        _answer_callback(bot_token, cb_id, f"Error: {e}")


def _handle_drip_send(bot_token, cb_id, chat_id, message_id, original_text, step_id):
    """Drip: Approve and send a follow-up email."""
    try:
        from viper.drip_runner import get_stored_draft
        from viper.outreach.email_sequences import approve_followup, mark_sent
        from viper.outreach.sendgrid_mailer import send_email

        draft = get_stored_draft(step_id)
        if not draft:
            _answer_callback(bot_token, cb_id, "Draft expired — re-run drip cycle")
            _edit_message(bot_token, chat_id, message_id, original_text + "\n\nDraft expired.")
            return

        approve_followup(step_id)
        result = send_email(
            to_email=draft["to_email"],
            subject=draft["subject"],
            body=draft["body"],
            to_name=draft.get("contact_name", ""),
        )
        if result["success"]:
            mark_sent(step_id)
            _answer_callback(bot_token, cb_id, "Follow-up sent!")
            _edit_message(bot_token, chat_id, message_id, original_text + f"\n\nSENT to {draft['to_email']}")
        else:
            _answer_callback(bot_token, cb_id, "Send failed")
            _edit_message(bot_token, chat_id, message_id, original_text + f"\n\nFAILED: {result['error']}")
        log.info("Drip SEND for %s: success=%s", step_id, result.get("success"))
    except Exception as e:
        log.error("Drip SEND failed for %s: %s", step_id, e)
        _answer_callback(bot_token, cb_id, f"Error: {e}")


def _handle_drip_stop(bot_token, cb_id, chat_id, message_id, original_text, seq_id):
    """Drip: Cancel entire follow-up sequence."""
    try:
        from viper.outreach.email_sequences import cancel_sequence_by_id
        biz_name = cancel_sequence_by_id(seq_id)
        if biz_name:
            _answer_callback(bot_token, cb_id, "Sequence cancelled")
            _edit_message(bot_token, chat_id, message_id, original_text + f"\n\nSEQUENCE CANCELLED for {biz_name}")
        else:
            _answer_callback(bot_token, cb_id, "Sequence not found")
            _edit_message(bot_token, chat_id, message_id, original_text + "\n\nSequence not found or already cancelled.")
        log.info("Drip STOP for seq %s (biz=%s)", seq_id, biz_name)
    except Exception as e:
        log.error("Drip STOP failed for seq %s: %s", seq_id, e)
        _answer_callback(bot_token, cb_id, f"Error: {e}")


def _handle_outreach_skip(bot_token, cb_id, chat_id, message_id, original_text, lead_id):
    """Gate 2: Don't send the email."""
    try:
        from viper.outreach.approval_queue import decline_lead
        lead = decline_lead(lead_id)
        name = lead.get("business_name", lead_id) if lead else lead_id
        _answer_callback(bot_token, cb_id, "Skipped")
        _edit_message(bot_token, chat_id, message_id, original_text + f"\n\nEmail skipped for {name}")
        log.info("Gate 2 SKIP for %s", lead_id)
    except Exception as e:
        log.error("Gate 2 SKIP failed for %s: %s", lead_id, e)
        _answer_callback(bot_token, cb_id, f"Error: {e}")


# ── TG API helpers ──────────────────────────────────────────────────

def _answer_callback(bot_token: str, cb_id: str, text: str) -> None:
    """Answer a callback query (dismisses the loading spinner)."""
    try:
        requests.post(
            f"https://api.telegram.org/bot{bot_token}/answerCallbackQuery",
            json={"callback_query_id": cb_id, "text": text},
            timeout=5,
        )
    except Exception:
        pass


def _edit_message(bot_token: str, chat_id: int, message_id: int, text: str) -> None:
    """Edit the original message (removes buttons, adds status)."""
    try:
        requests.post(
            f"https://api.telegram.org/bot{bot_token}/editMessageText",
            json={
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
            },
            timeout=5,
        )
    except Exception:
        pass


# ── Poller loop ─────────────────────────────────────────────────────

def _poll_bot(bot_token: str, name: str) -> None:
    """Poll a single bot for callback queries."""
    offset = 0
    while True:
        try:
            resp = requests.get(
                f"https://api.telegram.org/bot{bot_token}/getUpdates",
                params={"offset": offset, "timeout": 30, "allowed_updates": '["callback_query"]'},
                timeout=35,
            )
            if resp.status_code != 200:
                log.warning("[POLLER:%s] HTTP %d", name, resp.status_code)
                time.sleep(5)
                continue

            data = resp.json()
            if not data.get("ok"):
                time.sleep(5)
                continue

            for update in data.get("result", []):
                offset = update["update_id"] + 1
                _handle_callback(bot_token, update)

        except requests.Timeout:
            continue
        except Exception as e:
            log.error("[POLLER:%s] Error: %s", name, e)
            time.sleep(5)


def start_polling() -> None:
    """Start callback pollers for all configured Viper bots. Non-blocking."""
    tokens = _load_bot_tokens()

    if tokens["VIPER_INBOUND_BOT_TOKEN"]:
        t = threading.Thread(
            target=_poll_bot,
            args=(tokens["VIPER_INBOUND_BOT_TOKEN"], "INBOUND"),
            daemon=True,
            name="viper-inbound-poller",
        )
        t.start()
        log.info("[POLLER] Inbound bot callback poller started")

    if tokens["VIPER_OUTREACH_BOT_TOKEN"]:
        t = threading.Thread(
            target=_poll_bot,
            args=(tokens["VIPER_OUTREACH_BOT_TOKEN"], "OUTREACH"),
            daemon=True,
            name="viper-outreach-poller",
        )
        t.start()
        log.info("[POLLER] Outreach bot callback poller started")
