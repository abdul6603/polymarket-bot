"""One-time backfill — fetch last 24h from all channels and process."""
from __future__ import annotations

import logging
import time
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from discord_scraper.config import CHANNELS, CHANNEL_IDS
from discord_scraper.bot import _fetch_messages, process_message
from discord_scraper import db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def backfill(limit_per_channel: int = 50) -> int:
    """Fetch recent messages from all channels and process them."""
    db.init_db()
    total = 0

    for channel_id in CHANNEL_IDS:
        cfg = CHANNELS[channel_id]
        log.info("[BACKFILL] Fetching #%s (limit=%d)...", cfg["name"], limit_per_channel)

        messages = _fetch_messages(channel_id)
        if not messages:
            log.info("[BACKFILL] #%s — no messages", cfg["name"])
            time.sleep(1)
            continue

        # Sort oldest first
        messages.sort(key=lambda m: m.get("id", ""))

        for msg in messages:
            process_message(msg, channel_id)
            total += 1

        log.info("[BACKFILL] #%s — processed %d messages", cfg["name"], len(messages))
        time.sleep(1.5)  # rate limit safety

    log.info("[BACKFILL] Done. Total processed: %d", total)
    return total


if __name__ == "__main__":
    backfill()
