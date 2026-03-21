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
    except Exception as e:
        log.warning("[TG_CALLBACK] _find_lead_by_hash failed: %s", str(e)[:100])
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

    # Dispatch via registry
    for prefix, handler in _CALLBACK_HANDLERS.items():
        if data.startswith(prefix):
            payload = data[len(prefix):]
            handler(bot_token, cb_id, chat_id, message_id, original_text, payload)
            return

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
    """Gate 1: Approve outreach lead → delete msg → stats → build demo → Gate 2."""
    try:
        from viper.outreach.approval_queue import approve_lead_gate
        lead = approve_lead_gate(lead_id)
        if not lead:
            _answer_callback(bot_token, cb_id, "Lead not found")
            _edit_message(bot_token, chat_id, message_id, original_text + "\n\nLead not found or already decided.")
            return
        _answer_callback(bot_token, cb_id, "Approved!")
        _delete_message(bot_token, chat_id, message_id)
        _send_gate1_stats(bot_token, chat_id, "YES", lead)

        # Build demo + deploy + Gate 2 in background thread (takes ~60-90s)
        t = threading.Thread(
            target=_build_demo_and_review,
            args=(bot_token, lead),
            daemon=True,
        )
        t.start()
        log.info("Gate 1 YES for %s — demo build started", lead_id)
    except Exception as e:
        log.error("Gate 1 YES failed for %s: %s", lead_id, e)
        _answer_callback(bot_token, cb_id, f"Error: {e}")


def _build_demo_and_review(bot_token: str, lead: dict) -> None:
    """Background: build custom demo → deploy → regenerate email → Gate 2.

    This runs in a separate thread so the callback poller isn't blocked.
    Takes ~60-90 seconds (scrape + build + git push + GitHub Pages deploy).
    """
    biz = lead.get("business_name", "Unknown")
    niche = lead.get("niche", "dental")
    website = lead.get("prospect_data", {}).get("website", "")
    lead_id = lead.get("id", "")

    try:
        # 1. Build the custom demo HTML
        from viper.outreach.demo_builder import build_demo_html
        log.info("[DEMO_FLOW] Building demo for %s (niche=%s, site=%s)", biz, niche, website)
        html = build_demo_html(
            business_name=biz,
            niche=niche,
            website=website,
            prospect_data=lead.get("prospect_data", {}),
        )

        # 2. Quality gate — 7 test questions must ALL pass
        from viper.outreach.demo_builder import run_quality_gate
        gate_pass, gate_failures = run_quality_gate(html)
        if not gate_pass:
            fail_text = "\n".join(f"  - {f}" for f in gate_failures)
            log.warning("[DEMO_FLOW] Quality gate FAILED for %s:\n%s", biz, fail_text)
            from viper.outreach.outreach_engine import _send_tg
            _send_tg(f"DEMO QUALITY GATE FAILED for {biz}:\n{fail_text}\n\n"
                     f"Lead {lead_id} blocked — demo needs fixes before Gate 2.")
            return

        # 3. Deploy to GitHub Pages
        from viper.outreach.demo_deployer import deploy_demo
        demo_url, deployed = deploy_demo(biz, html, niche)

        if not deployed or not demo_url:
            log.error("[DEMO_FLOW] Deploy failed for %s", biz)
            from viper.outreach.outreach_engine import _send_tg
            _send_tg(f"DEMO BUILD FAILED for {biz} — could not deploy to GitHub Pages. "
                     f"Lead {lead_id} is stuck at lead_approved. Fix and retry.")
            return

        # 4. Update lead with custom demo URL
        lead["demo_url"] = demo_url
        lead["demo_is_custom"] = True

        # 5. Sonnet personalization (V3) — two-pass personalizer
        from viper.outreach.templates import get_outreach_message, resolve_niche_key
        from viper.prospecting.site_auditor import format_findings_for_email

        niche_key = resolve_niche_key(niche)
        findings_text = ""
        pitch_angle = lead.get("prospect_data", {}).get("pitch_angle", "")
        if pitch_angle:
            findings_text = pitch_angle

        # Try Sonnet personalization
        personalized_opener = ""
        personalized_subject = ""
        try:
            from viper.outreach.sonnet_personalizer import personalize_email
            prospect_data = lead.get("prospect_data", {})
            contact_name = lead.get("contact_name", "")
            personalized = personalize_email(
                prospect_data=prospect_data,
                crawl_data=None,
                gbp_data=prospect_data.get("gbp_data"),
                niche=niche,
                contact_name=contact_name,
            )
            personalized_opener = personalized.get("opener", "")
            personalized_subject = personalized.get("subject", "")
            if personalized_opener:
                log.info("[DEMO_FLOW] Sonnet personalized opener for %s", biz)
                # Store on prospect for reference
                lead["prospect_data"]["personalized_opener"] = personalized_opener
        except Exception as e:
            log.warning("[DEMO_FLOW] Sonnet personalization failed for %s: %s (using template)", biz, e)

        msg = get_outreach_message(
            niche=niche_key,
            business_name=biz,
            demo_url=demo_url,
            contact_name=lead.get("contact_name", ""),
            findings=findings_text,
            personalized_opener=personalized_opener,
            personalized_subject=personalized_subject,
        )
        lead["subject"] = msg["subject"]
        lead["body"] = msg["body"]

        # 6. Save updated lead back to queue
        from viper.outreach.approval_queue import _load_queue, _save_queue
        queue = _load_queue()
        for entry in queue:
            if entry["id"] == lead_id:
                entry["demo_url"] = demo_url
                entry["demo_is_custom"] = True
                entry["subject"] = msg["subject"]
                entry["body"] = msg["body"]
                break
        _save_queue(queue)

        # 7. Send Gate 2 draft review
        from viper.outreach.outreach_engine import send_draft_review
        send_draft_review(lead)
        log.info("[DEMO_FLOW] Demo deployed + Gate 2 sent for %s → %s", biz, demo_url)

    except Exception as e:
        log.error("[DEMO_FLOW] Failed for %s: %s", biz, e, exc_info=True)
        try:
            from viper.outreach.outreach_engine import _send_tg
            _send_tg(f"DEMO BUILD ERROR for {biz}: {e}\nLead {lead_id} needs manual attention.")
        except Exception as e2:
            log.warning("[TG_CALLBACK] Failed to send TG error alert: %s", str(e2)[:100])


def _handle_outreach_no(bot_token, cb_id, chat_id, message_id, original_text, lead_id):
    """Gate 1: Skip outreach lead → delete msg → stats."""
    try:
        from viper.outreach.approval_queue import decline_lead
        lead = decline_lead(lead_id)
        name = lead.get("business_name", lead_id) if lead else lead_id
        _answer_callback(bot_token, cb_id, "Skipped")
        _delete_message(bot_token, chat_id, message_id)
        _send_gate1_stats(bot_token, chat_id, "NO", lead or {"business_name": name})
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
            _delete_message(bot_token, chat_id, message_id)
            _send_outreach_stats(bot_token, chat_id, "SENT", lead.get("business_name", lead_id))
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
        _delete_message(bot_token, chat_id, message_id)
        _send_outreach_stats(bot_token, chat_id, "SKIPPED", name)
        log.info("Gate 2 SKIP for %s", lead_id)
    except Exception as e:
        log.error("Gate 2 SKIP failed for %s: %s", lead_id, e)
        _answer_callback(bot_token, cb_id, f"Error: {e}")


def _handle_batch_go(bot_token, cb_id, chat_id, message_id, original_text, niche_key):
    """Batch Gate 2: Send ALL emails for a niche in one tap."""
    try:
        from viper.outreach.approval_queue import _load_queue, _save_queue
        from viper.outreach.outreach_engine import send_approved_email
        import time

        queue = _load_queue()
        targets = [
            l for l in queue
            if l.get("status") == "lead_approved"
            and l.get("demo_is_custom", False)
            and _normalize_niche(l.get("niche", "")) == niche_key
            and l.get("email")
        ]

        _answer_callback(bot_token, cb_id, f"Sending {len(targets)} emails...")
        _edit_message(bot_token, chat_id, message_id,
                      original_text + f"\n\n⏳ Sending {len(targets)} emails...")

        sent = 0
        failed = 0
        for lead in targets:
            try:
                result = send_approved_email(lead)
                if result["success"]:
                    sent += 1
                else:
                    failed += 1
                    log.warning("[BATCH_GO] Failed for %s: %s", lead.get("business_name"), result.get("error"))
            except Exception as e:
                failed += 1
                log.error("[BATCH_GO] Exception for %s: %s", lead.get("business_name"), e)
            time.sleep(0.3)  # avoid rate limits

        _edit_message(bot_token, chat_id, message_id,
                      original_text + f"\n\n✅ SENT: {sent} | ❌ FAILED: {failed}")
        log.info("[BATCH_GO] niche=%s sent=%d failed=%d", niche_key, sent, failed)

    except Exception as e:
        log.error("[BATCH_GO] failed for niche %s: %s", niche_key, e)
        _answer_callback(bot_token, cb_id, f"Error: {e}")


def _handle_batch_skip(bot_token, cb_id, chat_id, message_id, original_text, niche_key):
    """Batch Gate 2: Skip (decline) all leads for a niche."""
    try:
        from viper.outreach.approval_queue import _load_queue, _save_queue

        queue = _load_queue()
        count = 0
        for lead in queue:
            if (lead.get("status") == "lead_approved"
                    and _normalize_niche(lead.get("niche", "")) == niche_key):
                lead["status"] = "declined"
                count += 1
        _save_queue(queue)

        _answer_callback(bot_token, cb_id, f"Skipped {count} {niche_key} leads")
        _edit_message(bot_token, chat_id, message_id,
                      original_text + f"\n\nSKIPPED {count} leads")
        log.info("[BATCH_SKIP] niche=%s count=%d", niche_key, count)

    except Exception as e:
        log.error("[BATCH_SKIP] failed for niche %s: %s", niche_key, e)
        _answer_callback(bot_token, cb_id, f"Error: {e}")


def _normalize_niche(niche: str) -> str:
    """Normalize niche string to a stable key for batch callbacks."""
    n = niche.lower().strip()
    if "dental" in n:
        return "dental"
    if "hvac" in n or "heating" in n or "cooling" in n:
        return "hvac"
    if "med" in n and "spa" in n:
        return "medspa"
    if "medical spa" in n:
        return "medspa"
    if "injury" in n or "lawyer" in n or "legal" in n or "attorney" in n:
        return "legal"
    if "commercial" in n and ("real" in n or "estate" in n):
        return "commercial_re"
    if "real estate" in n or "real_estate" in n or "realtor" in n or "realty" in n:
        return "realestate"
    return n.replace(" ", "_")



# ── Callback dispatch registry ──────────────────────────────────────────────
# Maps callback data prefix -> handler function.
# Each handler receives (bot_token, cb_id, chat_id, message_id, original_text, payload)
# where payload is the string after the "prefix:" in the callback data.
_CALLBACK_HANDLERS: dict[str, callable] = {
    "viper_bid:":           _handle_bid,
    "viper_skip:":          _handle_skip,
    "outreach_yes:":        _handle_outreach_yes,
    "outreach_no:":         _handle_outreach_no,
    "outreach_go:":         _handle_outreach_go,
    "outreach_skip:":       _handle_outreach_skip,
    "outreach_batch_go:":   _handle_batch_go,
    "outreach_batch_skip:": _handle_batch_skip,
    "drip_send:":           _handle_drip_send,
    "drip_stop:":           _handle_drip_stop,
}


# ── TG API helpers ──────────────────────────────────────────────────

def _answer_callback(bot_token: str, cb_id: str, text: str) -> None:
    """Answer a callback query (dismisses the loading spinner)."""
    try:
        requests.post(
            f"https://api.telegram.org/bot{bot_token}/answerCallbackQuery",
            json={"callback_query_id": cb_id, "text": text},
            timeout=5,
        )
    except Exception as e:
        log.warning("[TG_CALLBACK] answerCallbackQuery failed: %s", str(e)[:100])


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
    except Exception as e:
        log.warning("[TG_CALLBACK] editMessageText failed: %s", str(e)[:100])


def _delete_message(bot_token: str, chat_id: int, message_id: int) -> None:
    """Delete a message from the chat."""
    try:
        requests.post(
            f"https://api.telegram.org/bot{bot_token}/deleteMessage",
            json={"chat_id": chat_id, "message_id": message_id},
            timeout=5,
        )
    except Exception as e:
        log.warning("[TG_CALLBACK] deleteMessage failed: %s", str(e)[:100])


def _send_gate1_stats(bot_token: str, chat_id: int, action: str, lead: dict) -> None:
    """Send rich stats dashboard after Gate 1 YES/NO."""
    try:
        from viper.outreach.approval_queue import _load_queue
        from collections import Counter
        queue = _load_queue()

        sent = sum(1 for l in queue if l["status"] in ("approved", "sent"))
        declined = sum(1 for l in queue if l["status"] == "declined")
        gate2_waiting = sum(1 for l in queue if l["status"] == "lead_approved")
        pending = sum(1 for l in queue if l["status"] == "pending")
        held = sum(1 for l in queue if l["status"] == "needs_contact_name")

        # Niche breakdown for approved + sent
        active = [l for l in queue if l["status"] in ("lead_approved", "approved", "sent")]
        niche_counts = Counter(l.get("niche", "unknown") for l in active)
        niche_lines = "\n".join(f"    {n}: {c}" for n, c in niche_counts.most_common())

        # States & cities from pending (what's left to review)
        pending_leads = [l for l in queue if l["status"] == "pending"]
        states = set()
        cities = set()
        for l in pending_leads:
            city = l.get("city", "")
            if city:
                cities.add(city)
                parts = city.split()
                if len(parts) >= 2:
                    states.add(parts[-1])

        biz = lead.get("business_name", "?")
        niche = lead.get("niche", "")
        icon = "\u2705" if action == "YES" else "\u274c"
        action_word = "APPROVED" if action == "YES" else "SKIPPED"
        building = "\n\n\U0001f527 <i>Building custom demo... Gate 2 coming shortly</i>" if action == "YES" else ""

        text = (
            f"{icon} <b>{action_word}:</b> {biz} ({niche}){building}\n\n"
            f"\U0001f4ca <b>Pipeline Stats</b>\n"
            f"\u2709\ufe0f Sent: {sent}\n"
            f"\U0001f3d7 Gate 2 (demo building): {gate2_waiting}\n"
            f"\U0001f4cb Gate 1 remaining: {pending}\n"
            f"\u274c Skipped: {declined}\n"
            f"\U0001f50d Needs contact name: {held}\n\n"
            f"\U0001f3af <b>Approved Niches</b>\n{niche_lines if niche_lines else '    (none yet)'}\n\n"
            f"\U0001f5fa <b>Pending Review</b>\n"
            f"    States: {', '.join(sorted(states)) if states else 'none'}\n"
            f"    Cities: {', '.join(sorted(cities)) if cities else 'none'}"
        )
        requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=5,
        )
    except Exception as e:
        log.error("Gate 1 stats send failed: %s", e)


def _send_outreach_stats(bot_token: str, chat_id: int, action: str, lead_name: str) -> None:
    """Send a stats summary after each GO/SKIP action."""
    try:
        from viper.outreach.approval_queue import _load_queue
        queue = _load_queue()
        sent = sum(1 for l in queue if l["status"] in ("approved", "sent"))
        skipped = sum(1 for l in queue if l["status"] == "declined")
        waiting = sum(1 for l in queue if l["status"] == "lead_approved")
        pending = sum(1 for l in queue if l["status"] == "pending")
        held = sum(1 for l in queue if l["status"] == "needs_contact_name")

        icon = "\u2705" if action == "SENT" else "\u274c"
        text = (
            f"{icon} <b>{action}:</b> {lead_name}\n\n"
            f"\U0001f4ca <b>Outreach Stats</b>\n"
            f"\u2709\ufe0f Sent: {sent}\n"
            f"\u274c Skipped: {skipped}\n"
            f"\u23f3 Gate 2 waiting: {waiting}\n"
            f"\U0001f4cb Pending Gate 1: {pending}\n"
            f"\U0001f50d Needs contact name: {held}"
        )
        requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=5,
        )
    except Exception as e:
        log.error("Stats send failed: %s", e)


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


def _auto_regen_gate2_emails() -> None:
    """Auto-regenerate all lead_approved emails with latest templates.

    Runs ONCE on Viper startup. Ensures code/template changes
    automatically propagate to existing leads without manual intervention.
    Also cleans up duplicate Gate 2 messages.
    """
    try:
        from viper.outreach.approval_queue import _load_queue, _save_queue
        from viper.outreach.templates import get_outreach_message, resolve_niche_key

        queue = _load_queue()
        regen = 0
        for lead in queue:
            if lead.get("status") != "lead_approved":
                continue
            if not lead.get("contact_name"):
                continue

            nk = resolve_niche_key(lead.get("niche", ""))
            findings = lead.get("prospect_data", {}).get("pitch_angle", "")
            msg = get_outreach_message(
                niche=nk,
                business_name=lead["business_name"],
                demo_url=lead.get("demo_url", ""),
                contact_name=lead.get("contact_name", ""),
                findings=findings,
            )
            if lead.get("subject") != msg["subject"] or lead.get("body") != msg["body"]:
                lead["subject"] = msg["subject"]
                lead["body"] = msg["body"]
                regen += 1
                log.info("[STARTUP] Regenerated email for %s", lead["business_name"])

        if regen:
            _save_queue(queue)
            log.info("[STARTUP] Auto-regenerated %d lead_approved emails with latest templates", regen)
        else:
            log.info("[STARTUP] All lead_approved emails are up-to-date")
    except Exception as e:
        log.error("[STARTUP] Auto-regen failed: %s", e)


def start_polling() -> None:
    """Start callback pollers for all configured Viper bots. Non-blocking."""
    # Auto-regenerate lead_approved emails with latest templates
    _auto_regen_gate2_emails()

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
