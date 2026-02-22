"""Discord Alpha Scraper — Core Bot.

Monitors Discord channels via REST API polling (no selfbot library needed).
Parses messages, extracts signals, routes to agents via event bus.
"""
from __future__ import annotations

import json
import logging
import signal
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from discord_scraper.config import (
    DISCORD_TOKEN, CHANNELS, CHANNEL_IDS,
    POLL_INTERVAL_SECONDS, MESSAGE_FETCH_LIMIT, VISION_DAILY_CAP,
)
from discord_scraper import db, analyzer

ET = ZoneInfo("America/New_York")
log = logging.getLogger(__name__)

BASE_URL = "https://discord.com/api/v10"

# Track last seen message ID per channel to avoid duplicates
_last_seen: dict[int, str] = {}
_running = True


def _discord_get(path: str) -> dict | list | None:
    """Make an authenticated GET request to Discord API."""
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
            retry = int(e.headers.get("Retry-After", "5"))
            log.warning("[DISCORD] Rate limited, sleeping %ds", retry)
            time.sleep(retry)
            return None
        log.error("[DISCORD] HTTP %d: %s", e.code, path)
        return None
    except Exception as e:
        log.error("[DISCORD] Request failed: %s", e)
        return None


def _fetch_messages(channel_id: int, after: str | None = None) -> list[dict]:
    """Fetch recent messages from a channel."""
    path = f"/channels/{channel_id}/messages?limit={MESSAGE_FETCH_LIMIT}"
    if after:
        path += f"&after={after}"
    result = _discord_get(path)
    return result if isinstance(result, list) else []


def _extract_image_urls(msg: dict) -> list[str]:
    """Extract image URLs from attachments and embeds."""
    urls = []
    for att in msg.get("attachments", []):
        ct = att.get("content_type", "")
        if ct.startswith("image/") or att.get("url", "").split("?")[0].split(".")[-1] in ("png", "jpg", "jpeg", "gif", "webp"):
            urls.append(att["url"])
    for embed in msg.get("embeds", []):
        if embed.get("image", {}).get("url"):
            urls.append(embed["image"]["url"])
        if embed.get("thumbnail", {}).get("url"):
            urls.append(embed["thumbnail"]["url"])
    return urls


def _publish_signal(signal_data: dict, channel_cfg: dict, author: str, msg_id: int) -> None:
    """Publish a parsed signal to the event bus."""
    try:
        from shared.events import publish as bus_publish
        bus_publish(
            agent="discord_scraper",
            event_type="discord_signal",
            severity="critical" if channel_cfg["priority"] == "CRITICAL" else "info",
            summary=f"{author}: {signal_data.get('ticker', '?')} {signal_data.get('direction', '?')} via #{channel_cfg['name']}",
            data={
                "channel": channel_cfg["name"],
                "priority": channel_cfg["priority"],
                "consumers": channel_cfg["consumers"],
                "author": author,
                "db_message_id": msg_id,
                **{k: v for k, v in signal_data.items() if k != "is_trade_signal"},
            },
        )
        log.info("[DISCORD] Published signal: %s %s %s from %s",
                 signal_data.get("ticker"), signal_data.get("direction"),
                 channel_cfg["name"], author)
    except ImportError:
        log.warning("[DISCORD] Event bus not available")


def _generate_agent_discussion(signal_data: dict, channel_cfg: dict, author: str, signal_id: int, msg_id: int) -> None:
    """Generate agent reactions/discussion about a signal."""
    try:
        from shared.llm_client import llm_call
    except ImportError:
        return

    for consumer in channel_cfg["consumers"]:
        prompt = (
            f"You are {consumer.upper()}, a trading agent. A Discord trader '{author}' "
            f"just posted a signal in #{channel_cfg['name']}:\n"
            f"Ticker: {signal_data.get('ticker', 'unknown')}\n"
            f"Direction: {signal_data.get('direction', 'unknown')}\n"
            f"Entry: {signal_data.get('entry_price', 'not specified')}\n"
            f"SL: {signal_data.get('stop_loss', 'not specified')}\n"
            f"Strategy: {signal_data.get('strategy', 'unknown')}\n"
            f"Approach: {signal_data.get('approach', 'unknown')}\n\n"
            f"As {consumer.upper()}, briefly react: Do you agree? Would you act on this? Why/why not? "
            f"(2-3 sentences max)"
        )

        try:
            reaction = llm_call(
                system=f"You are {consumer.upper()}, a crypto trading agent. Be concise.",
                user=prompt,
                agent=consumer,
                task_type="fast",
                max_tokens=200,
            )
            if reaction:
                action = "monitoring" if channel_cfg["priority"] in ("CONTEXT", "KNOWLEDGE", "MEDIUM") else "evaluating for trade"
                db.save_agent_discussion(
                    agent=consumer,
                    signal_id=signal_id,
                    message_id=msg_id,
                    reaction=reaction.strip(),
                    reasoning=f"Signal from {author} in #{channel_cfg['name']}",
                    action_taken=action,
                )
        except Exception as e:
            log.debug("[DISCORD] Agent discussion error for %s: %s", consumer, e)


def process_message(msg: dict, channel_id: int) -> None:
    """Process a single Discord message."""
    channel_cfg = CHANNELS.get(channel_id)
    if not channel_cfg:
        return

    msg_id_str = msg.get("id", "")
    author_info = msg.get("author", {})
    author = author_info.get("username", "unknown")
    author_id = author_info.get("id", "")
    content = msg.get("content", "")
    created_at = msg.get("timestamp", datetime.now(ET).isoformat())

    image_urls = _extract_image_urls(msg)
    has_image = len(image_urls) > 0

    # Save to DB
    row_id = db.save_message(
        discord_msg_id=msg_id_str,
        channel_id=str(channel_id),
        channel_name=channel_cfg["name"],
        author=author,
        author_id=author_id,
        content=content,
        has_image=has_image,
        image_urls=image_urls,
        priority=channel_cfg["priority"],
        created_at=created_at,
    )
    if row_id is None:
        return  # duplicate

    log.info("[DISCORD] New message in #%s from %s: %s",
             channel_cfg["name"], author, content[:80] if content else "(image)")

    # Analyze — vision or text
    signal_data = None
    if has_image and channel_cfg.get("vision"):
        signal_data = analyzer.analyze_image_message(
            content, image_urls, author, channel_cfg["name"],
            vision_cap=VISION_DAILY_CAP,
        )
    elif content:
        signal_data = analyzer.analyze_text_message(content, author, channel_cfg["name"])

    if signal_data:
        # Save signal
        signal_id = db.save_signal(
            message_id=row_id,
            ticker=signal_data.get("ticker"),
            direction=signal_data.get("direction"),
            entry_price=signal_data.get("entry_price"),
            stop_loss=signal_data.get("stop_loss"),
            take_profit=signal_data.get("take_profit"),
            strategy=signal_data.get("strategy"),
            approach=signal_data.get("approach"),
            confidence=signal_data.get("confidence"),
            raw_analysis=json.dumps(signal_data),
            priority=channel_cfg["priority"],
            consumers=channel_cfg["consumers"],
        )

        # Track trader call for leaderboard
        db.save_trader_call(
            author=author,
            author_id=author_id,
            signal_id=signal_id,
            ticker=signal_data.get("ticker"),
            direction=signal_data.get("direction"),
            entry_price=signal_data.get("entry_price"),
        )

        # Publish to event bus
        _publish_signal(signal_data, channel_cfg, author, row_id)

        # Generate agent discussions (async-ish — runs inline but fast calls)
        _generate_agent_discussion(signal_data, channel_cfg, author, signal_id, row_id)


def poll_channels() -> int:
    """Poll all channels for new messages. Returns count of new messages."""
    total_new = 0
    for channel_id in CHANNEL_IDS:
        after = _last_seen.get(channel_id)
        messages = _fetch_messages(channel_id, after=after)

        if not messages:
            continue

        # Messages come newest first — reverse for chronological processing
        messages.sort(key=lambda m: m.get("id", ""))

        for msg in messages:
            process_message(msg, channel_id)
            _last_seen[channel_id] = msg["id"]
            total_new += 1

        # Small delay between channels to avoid rate limits
        time.sleep(0.5)

    return total_new


def _shutdown(signum, frame):
    global _running
    log.info("[DISCORD] Shutting down...")
    _running = False


def run() -> None:
    """Main loop — poll channels forever."""
    global _running

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    log.info("[DISCORD] Starting Discord Alpha Scraper")
    log.info("[DISCORD] Monitoring %d channels in server %s",
             len(CHANNEL_IDS), "UT TRADERS ACADEMY")

    # Init DB
    db.init_db()

    # Initial fetch to set cursors (don't process old messages)
    log.info("[DISCORD] Setting initial cursors...")
    for channel_id in CHANNEL_IDS:
        messages = _fetch_messages(channel_id)
        if messages:
            newest = max(messages, key=lambda m: m.get("id", ""))
            _last_seen[channel_id] = newest["id"]
            log.info("[DISCORD] #%s cursor set to %s",
                     CHANNELS[channel_id]["name"], newest["id"])
        time.sleep(0.5)

    log.info("[DISCORD] Ready. Polling every %ds...", POLL_INTERVAL_SECONDS)

    cycle = 0
    while _running:
        try:
            cycle += 1
            new_count = poll_channels()
            if new_count > 0:
                log.info("[DISCORD] Cycle %d: %d new messages processed", cycle, new_count)
            time.sleep(POLL_INTERVAL_SECONDS)
        except KeyboardInterrupt:
            break
        except Exception as e:
            log.error("[DISCORD] Cycle error: %s", e)
            time.sleep(30)

    log.info("[DISCORD] Stopped.")
