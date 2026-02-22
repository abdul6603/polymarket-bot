"""Backfill — fetch historical messages from Discord channels and process.

Usage:
    # Quick backfill (last 10 per channel):
    python -m discord_scraper.backfill

    # Deep backfill of specific channel (all history):
    python -m discord_scraper.backfill --deep --channel ut-education

    # Deep backfill all channels:
    python -m discord_scraper.backfill --deep
"""
from __future__ import annotations

import argparse
import json
import logging
import time
import urllib.request
import urllib.error
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from discord_scraper.config import CHANNELS, CHANNEL_IDS, DISCORD_TOKEN, MESSAGE_FETCH_LIMIT
from discord_scraper.bot import process_message
from discord_scraper import db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

BASE_URL = "https://discord.com/api/v10"


def _discord_get(path: str) -> dict | list | None:
    url = f"{BASE_URL}{path}"
    req = urllib.request.Request(url, headers={
        "Authorization": DISCORD_TOKEN,
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 429:
            retry = int(e.headers.get("Retry-After", "10"))
            log.warning("[BACKFILL] Rate limited, sleeping %ds", retry)
            time.sleep(retry + 1)
            return _discord_get(path)  # retry
        log.error("[BACKFILL] HTTP %d: %s", e.code, path)
        return None
    except Exception as e:
        log.error("[BACKFILL] Request failed: %s", e)
        return None


def _fetch_page(channel_id: int, before: str | None = None, limit: int = 50) -> list[dict]:
    """Fetch a page of messages, optionally before a message ID."""
    path = f"/channels/{channel_id}/messages?limit={limit}"
    if before:
        path += f"&before={before}"
    result = _discord_get(path)
    return result if isinstance(result, list) else []


def deep_backfill_channel(channel_id: int, max_pages: int = 100) -> int:
    """Paginate through full channel history and process all messages."""
    cfg = CHANNELS.get(channel_id)
    if not cfg:
        log.error("[BACKFILL] Unknown channel %d", channel_id)
        return 0

    log.info("[BACKFILL] Deep backfill #%s (max %d pages)...", cfg["name"], max_pages)

    total = 0
    before = None
    all_messages = []

    for page in range(max_pages):
        messages = _fetch_page(channel_id, before=before, limit=50)
        if not messages:
            log.info("[BACKFILL] #%s — no more messages after page %d", cfg["name"], page + 1)
            break

        all_messages.extend(messages)
        before = messages[-1]["id"]  # oldest message on this page
        log.info("[BACKFILL] #%s page %d: %d msgs (total so far: %d)",
                 cfg["name"], page + 1, len(messages), len(all_messages))
        time.sleep(1.5)  # rate limit

        if len(messages) < 50:
            break  # last page

    # Process oldest first
    all_messages.sort(key=lambda m: m.get("id", ""))
    for msg in all_messages:
        process_message(msg, channel_id)
        total += 1

    log.info("[BACKFILL] #%s complete: %d messages processed", cfg["name"], total)
    return total


def quick_backfill() -> int:
    """Fetch last 10 messages per channel."""
    total = 0
    for channel_id in CHANNEL_IDS:
        cfg = CHANNELS[channel_id]
        log.info("[BACKFILL] Fetching #%s...", cfg["name"])
        messages = _fetch_page(channel_id, limit=MESSAGE_FETCH_LIMIT)
        if not messages:
            time.sleep(1)
            continue
        messages.sort(key=lambda m: m.get("id", ""))
        for msg in messages:
            process_message(msg, channel_id)
            total += 1
        log.info("[BACKFILL] #%s — %d messages", cfg["name"], len(messages))
        time.sleep(1.5)
    log.info("[BACKFILL] Quick backfill done: %d total", total)
    return total


def _find_channel_id(name: str) -> int | None:
    """Find channel ID by name."""
    for cid, cfg in CHANNELS.items():
        if cfg["name"] == name or name in cfg["name"]:
            return cid
    return None


if __name__ == "__main__":
    db.init_db()

    parser = argparse.ArgumentParser()
    parser.add_argument("--deep", action="store_true", help="Deep backfill (full history)")
    parser.add_argument("--channel", type=str, help="Channel name to backfill")
    parser.add_argument("--max-pages", type=int, default=100, help="Max pages for deep backfill")
    args = parser.parse_args()

    if args.deep:
        if args.channel:
            cid = _find_channel_id(args.channel)
            if cid:
                deep_backfill_channel(cid, max_pages=args.max_pages)
            else:
                log.error("Channel '%s' not found", args.channel)
        else:
            for channel_id in CHANNEL_IDS:
                deep_backfill_channel(channel_id, max_pages=args.max_pages)
    else:
        quick_backfill()
