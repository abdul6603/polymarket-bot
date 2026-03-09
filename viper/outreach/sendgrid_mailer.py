"""Resend email sender for cold outreach.

Requires RESEND_API_KEY in .env.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

_DEFAULT_FROM = "DarkCode AI <darkcodeai@proton.me>"


def _load_config() -> tuple[str, str]:
    """Load Resend config from env or .env file.

    Returns (api_key, from_address).
    """
    api_key = os.environ.get("RESEND_API_KEY", "")
    from_addr = os.environ.get("RESEND_FROM", _DEFAULT_FROM)

    if not api_key:
        env_path = Path.home() / "polymarket-bot" / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if line.startswith("RESEND_API_KEY=") and not api_key:
                    api_key = line.split("=", 1)[1].strip()
                elif line.startswith("RESEND_FROM="):
                    from_addr = line.split("=", 1)[1].strip()

    return api_key, from_addr


def send_email(
    to_email: str,
    subject: str,
    body: str,
    to_name: str = "",
) -> dict:
    """Send a single email via Resend.

    Returns dict with 'success', 'status_code', 'error' keys.
    """
    api_key, from_addr = _load_config()

    if not api_key:
        return {
            "success": False,
            "status_code": 0,
            "error": "RESEND_API_KEY not configured. Add to .env file.",
        }

    try:
        import resend

        resend.api_key = api_key

        resp = resend.Emails.send({
            "from": from_addr,
            "to": [to_email],
            "subject": subject,
            "text": body,
        })

        # Resend returns an object with an 'id' on success
        email_id = getattr(resp, "id", None) or (resp.get("id") if isinstance(resp, dict) else None)
        success = bool(email_id)

        if success:
            log.info("Email sent to %s (id: %s)", to_email, email_id)
        else:
            log.warning("Resend response for %s: %s", to_email, resp)

        return {
            "success": success,
            "status_code": 200 if success else 400,
            "error": "" if success else str(resp),
        }

    except Exception as e:
        log.error("Resend error for %s: %s", to_email, e)
        return {
            "success": False,
            "status_code": 0,
            "error": str(e),
        }
