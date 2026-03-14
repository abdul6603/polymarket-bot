"""Instantly.ai email sender for cold outreach.

Same send_email() interface as sendgrid_mailer.py for zero-downtime migration.
Feature flag EMAIL_SENDER=resend|instantly in .env controls which is active.

Requires INSTANTLY_API_KEY in .env.
Jordan task: Sign up at https://instantly.ai/ + buy domains + warmup.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import requests

log = logging.getLogger(__name__)

_API_BASE_V1 = "https://api.instantly.ai/api/v1"
_API_BASE_V2 = "https://api.instantly.ai/api/v2"
_TIMEOUT = 15


def _load_config() -> tuple[str, str]:
    """Load Instantly config from env/.env.

    Returns (api_key, campaign_id).
    """
    api_key = os.getenv("INSTANTLY_API_KEY", "")
    campaign_id = os.getenv("INSTANTLY_CAMPAIGN_ID", "")

    if not api_key:
        env_path = Path.home() / "polymarket-bot" / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if line.startswith("INSTANTLY_API_KEY=") and not api_key:
                    api_key = line.split("=", 1)[1].strip()
                elif line.startswith("INSTANTLY_CAMPAIGN_ID=") and not campaign_id:
                    campaign_id = line.split("=", 1)[1].strip()

    return api_key, campaign_id


def send_email(
    to_email: str,
    subject: str,
    body: str,
    to_name: str = "",
) -> dict:
    """Send a single email via Instantly.ai.

    Same interface as sendgrid_mailer.send_email() for drop-in replacement.
    Returns dict with 'success', 'status_code', 'error' keys.
    """
    api_key, campaign_id = _load_config()

    if not api_key:
        return {
            "success": False,
            "status_code": 0,
            "error": "INSTANTLY_API_KEY not configured. Add to .env file.",
        }

    if not campaign_id:
        return {
            "success": False,
            "status_code": 0,
            "error": "INSTANTLY_CAMPAIGN_ID not configured. Create a campaign in Instantly first.",
        }

    # Add lead to campaign (Instantly handles sending via its campaign scheduler)
    try:
        lead_data = {
            "api_key": api_key,
            "campaign_id": campaign_id,
            "skip_if_in_workspace": True,
            "leads": [
                {
                    "email": to_email,
                    "first_name": to_name.split()[0] if to_name else "",
                    "last_name": " ".join(to_name.split()[1:]) if to_name and " " in to_name else "",
                    "company_name": "",
                    "custom_variables": {
                        "subject": subject,
                        "body": body,
                    },
                }
            ],
        }

        resp = requests.post(
            f"{_API_BASE_V1}/lead/add",
            json=lead_data,
            timeout=_TIMEOUT,
        )

        if resp.status_code == 200:
            log.info("Lead added to Instantly campaign: %s", to_email)
            return {
                "success": True,
                "status_code": 200,
                "error": "",
            }
        else:
            error_msg = resp.text[:200]
            log.error("Instantly add lead failed for %s: %s", to_email, error_msg)
            return {
                "success": False,
                "status_code": resp.status_code,
                "error": error_msg,
            }

    except Exception as e:
        log.error("Instantly error for %s: %s", to_email, e)
        return {
            "success": False,
            "status_code": 0,
            "error": str(e),
        }


def add_lead_to_campaign(
    email: str,
    first_name: str = "",
    last_name: str = "",
    company_name: str = "",
    custom_variables: dict | None = None,
) -> bool:
    """Add a lead to the active Instantly campaign.

    Returns True on success.
    """
    api_key, campaign_id = _load_config()
    if not api_key or not campaign_id:
        return False

    payload = {
        "api_key": api_key,
        "campaign_id": campaign_id,
        "skip_if_in_workspace": True,
        "leads": [
            {
                "email": email,
                "first_name": first_name,
                "last_name": last_name,
                "company_name": company_name,
                "custom_variables": custom_variables or {},
            }
        ],
    }

    try:
        resp = requests.post(f"{_API_BASE_V1}/lead/add", json=payload, timeout=_TIMEOUT)
        return resp.status_code == 200
    except Exception as e:
        log.error("Instantly add_lead failed: %s", e)
        return False


def _v2_headers(api_key: str) -> dict:
    """Build v2 auth headers."""
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def create_campaign(
    name: str,
    subject: str = "",
    body: str = "",
    schedule: dict | None = None,
    sending_accounts: list[str] | None = None,
) -> str:
    """Create a new Instantly campaign via v2 API.

    Returns campaign_id or empty string on failure.
    """
    api_key, _ = _load_config()
    if not api_key:
        return ""

    payload = {"name": name}

    try:
        resp = requests.post(
            f"{_API_BASE_V2}/campaigns",
            json=payload,
            headers=_v2_headers(api_key),
            timeout=_TIMEOUT,
        )
        if resp.status_code in (200, 201):
            data = resp.json()
            campaign_id = data.get("id", "")
            log.info("Created Instantly campaign: %s (%s)", name, campaign_id)
            return campaign_id
        else:
            log.error("Instantly create_campaign %d: %s", resp.status_code, resp.text[:200])
    except Exception as e:
        log.error("Instantly create_campaign failed: %s", e)

    return ""


def get_campaign_analytics(campaign_id: str = "") -> dict:
    """Get campaign analytics — open rate, reply rate, bounce rate.

    Returns dict with analytics data.
    """
    api_key, default_campaign = _load_config()
    if not api_key:
        return {}

    cid = campaign_id or default_campaign
    if not cid:
        return {}

    try:
        resp = requests.get(
            f"{_API_BASE_V1}/analytics/campaign/summary",
            params={"api_key": api_key, "campaign_id": cid},
            timeout=_TIMEOUT,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        log.error("Instantly analytics failed: %s", e)

    return {}


def check_warmup_status() -> list[dict]:
    """Check warmup status for all connected email accounts.

    Returns list of account warmup statuses.
    """
    api_key, _ = _load_config()
    if not api_key:
        return []

    try:
        resp = requests.get(
            f"{_API_BASE_V2}/accounts",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            accounts = data.get("items", [])
            statuses = []
            for acc in accounts:
                statuses.append({
                    "email": acc.get("email", ""),
                    "warmup_status": acc.get("warmup_status", 0),
                    "warmup_score": acc.get("stat_warmup_score", 0),
                    "status": acc.get("status", 0),
                })
            return statuses
    except Exception as e:
        log.error("Instantly warmup check failed: %s", e)

    return []
