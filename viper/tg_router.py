"""Viper TG Router — routes messages to INBOUND or OUTREACH bots.

Each pipeline has its own Telegram bot:
  INBOUND  — Pipeline 2 (job scanner leads)  → @viper_jobhunter_bot
  OUTREACH — Pipeline 1 (prospector/outreach) → @VIPER_OUTRACH_BOT

Reads VIPER_INBOUND_BOT_TOKEN/CHAT_ID and VIPER_OUTREACH_BOT_TOKEN/CHAT_ID.
Fallback: single TELEGRAM_BOT_TOKEN + CHAT_ID with [INBOUND]/[OUTREACH] prefix.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import requests

log = logging.getLogger(__name__)


def _load_env() -> dict[str, str]:
    """Load env vars, falling back to .env files."""
    vals: dict[str, str] = {}
    keys = [
        "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
        "VIPER_INBOUND_BOT_TOKEN", "VIPER_INBOUND_CHAT_ID",
        "VIPER_OUTREACH_BOT_TOKEN", "VIPER_OUTREACH_CHAT_ID",
    ]
    for k in keys:
        vals[k] = os.getenv(k, "")

    # Fallback: read from .env files if not in environment
    if not vals["VIPER_INBOUND_BOT_TOKEN"]:
        for env_path in [Path.home() / "polymarket-bot" / ".env",
                         Path.home() / "shelby" / ".env"]:
            if env_path.exists():
                for line in env_path.read_text().splitlines():
                    for k in keys:
                        if line.startswith(f"{k}=") and not vals[k]:
                            vals[k] = line.split("=", 1)[1].strip()
    return vals


_ENV = _load_env()


def _resolve(channel: str) -> tuple[str, str, str]:
    """Return (bot_token, chat_id, prefix) for the given channel."""
    if channel == "INBOUND" and _ENV["VIPER_INBOUND_BOT_TOKEN"]:
        return _ENV["VIPER_INBOUND_BOT_TOKEN"], _ENV["VIPER_INBOUND_CHAT_ID"], ""
    if channel == "OUTREACH" and _ENV["VIPER_OUTREACH_BOT_TOKEN"]:
        return _ENV["VIPER_OUTREACH_BOT_TOKEN"], _ENV["VIPER_OUTREACH_CHAT_ID"], ""
    # Fallback: single bot with prefix
    return _ENV["TELEGRAM_BOT_TOKEN"], _ENV["TELEGRAM_CHAT_ID"], f"[{channel}] "


def send(
    text: str,
    channel: str = "INBOUND",
    buttons: list[list[dict]] | None = None,
) -> bool:
    """Send a Telegram message via the appropriate Viper bot.

    Args:
        text: HTML-formatted message text.
        channel: "INBOUND" or "OUTREACH".
        buttons: Optional inline keyboard buttons.
    """
    bot_tk, chat_id, prefix = _resolve(channel)

    if not bot_tk:
        log.warning("[TG_ROUTER] No bot token for channel %s", channel)
        return False
    if not chat_id:
        log.warning("[TG_ROUTER] No chat_id for channel %s", channel)
        return False

    full_text = f"{prefix}{text}" if prefix else text

    url = f"https://api.telegram.org/bot{bot_tk}/sendMessage"
    payload: dict = {
        "chat_id": chat_id,
        "text": full_text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if buttons:
        payload["reply_markup"] = json.dumps({"inline_keyboard": buttons})

    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            return True
        log.error("[TG_ROUTER] API error %d: %s", resp.status_code, resp.text[:200])
        return False
    except Exception as e:
        log.error("[TG_ROUTER] Send failed: %s", e)
        return False
