"""Discord Alpha Scraper — Core Bot.

Monitors Discord channels via REST API polling (no selfbot library needed).
Parses messages, extracts signals, routes to agents via event bus.
"""
from __future__ import annotations

import json
import logging
import re
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


def _resolve_trader_call(author: str, ticker: str | None, outcome: str, r_value: float | None, note: str) -> None:
    """Resolve a trader's most recent pending call based on their posted result."""
    import sqlite3
    conn = sqlite3.connect(str(db.DB_PATH))
    conn.row_factory = sqlite3.Row

    # Find most recent pending call from this author, optionally matching ticker
    if ticker:
        row = conn.execute(
            """SELECT id FROM trader_scores
               WHERE author = ? AND outcome = 'pending' AND ticker = ?
               ORDER BY id DESC LIMIT 1""",
            (author, ticker),
        ).fetchone()
    else:
        row = conn.execute(
            """SELECT id FROM trader_scores
               WHERE author = ? AND outcome = 'pending'
               ORDER BY id DESC LIMIT 1""",
            (author,),
        ).fetchone()

    if row:
        pnl_pct = (r_value or 0) * 100 if r_value else None
        conn.execute(
            """UPDATE trader_scores SET outcome = ?, pnl_pct = ?, resolved_at = ?
               WHERE id = ?""",
            (outcome, pnl_pct, datetime.now(ET).isoformat(), row["id"]),
        )
        conn.commit()
        log.info("[DISCORD] Resolved call #%d for %s: %s (R=%s, note=%s)",
                 row["id"], author, outcome, r_value, note)
    else:
        log.debug("[DISCORD] No pending call found for %s/%s to resolve", author, ticker)

    conn.close()


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


_RESULT_PATTERNS = re.compile(
    r"(?:sl\s*hit|tp\s*hit|tp[12]\s*hit|stopped?\s*out|take\s*tp|"
    r"closed?\s*(?:in\s*profit|here|at)|[+-]\d*\.?\d+\s*[rR]\b|"
    r"\bfail\b|\bloss\b|\bliquidated\b)",
    re.I,
)
_CANCEL_PATTERNS = re.compile(
    r"(?:\bcancel(?:led)?\b|\binvalidated?\b|\bignore\b|\bskip\b|"
    r"\bvoid\b|\bdont\s*take\b|\bdon.t\s*take\b|\bdisregard\b)",
    re.I,
)
_R_PATTERN = re.compile(r"([+-]?\d*\.?\d+)\s*[rR]\b")

# Ticker detection for override
_TICKERS = [
    "BTC", "ETH", "SOL", "XRP", "DOGE", "AVAX", "LINK", "ADA",
    "DOT", "MATIC", "IOTA", "NEAR", "APT", "ARB", "OP", "SUI",
    "INJ", "TIA", "SEI", "JUP", "PEPE", "WIF", "BONK", "HYPE",
    "COW", "ETHBTC", "MOR", "WLFI", "ARC",
]


def _detect_ticker(text: str) -> str | None:
    upper = text.upper()
    for t in _TICKERS:
        if f"${t}" in upper or f" {t} " in f" {upper} ":
            return t
    return None


def _override_msg_type(content: str, signal_data: dict) -> dict:
    """Override LLM classification if content is clearly a result/cancel."""
    if signal_data.get("msg_type") == "result":
        return signal_data  # already correct

    text = content.upper() if content else ""
    if _CANCEL_PATTERNS.search(content or ""):
        signal_data["msg_type"] = "result"
        signal_data["result_outcome"] = "cancelled"
        signal_data["result_r"] = 0.0
        signal_data["result_note"] = "cancelled"
        signal_data["is_trade_signal"] = True
    elif _RESULT_PATTERNS.search(content or ""):
        signal_data["msg_type"] = "result"
        r_match = _R_PATTERN.search(content or "")
        if r_match:
            r_val = float(r_match.group(1))
            signal_data["result_outcome"] = "win" if r_val > 0 else "loss"
            signal_data["result_r"] = r_val
            signal_data["result_note"] = r_match.group(0).strip()
        elif any(kw in text for kw in ["FAIL", "SL HIT", "STOPPED OUT", "LOSS", "LIQUIDATED"]):
            signal_data["result_outcome"] = "loss"
            signal_data["result_r"] = -1.0
            signal_data["result_note"] = "sl hit"
        else:
            signal_data["result_outcome"] = "win"
            signal_data["result_r"] = 1.0
            signal_data["result_note"] = "tp hit"
        signal_data["is_trade_signal"] = True
    return signal_data


def _detect_result_from_text(content: str) -> dict | None:
    """Catch obvious results even when LLM returned None."""
    if not content:
        return None
    text = content.upper()
    ticker = _detect_ticker(text)

    if _CANCEL_PATTERNS.search(content):
        return {
            "msg_type": "result", "ticker": ticker, "is_trade_signal": True,
            "result_outcome": "cancelled", "result_r": 0.0, "result_note": "cancelled",
        }

    if _RESULT_PATTERNS.search(content):
        r_match = _R_PATTERN.search(content)
        if r_match:
            r_val = float(r_match.group(1))
            outcome = "win" if r_val > 0 else "loss"
            return {
                "msg_type": "result", "ticker": ticker, "is_trade_signal": True,
                "result_outcome": outcome, "result_r": r_val, "result_note": r_match.group(0).strip(),
            }
        if any(kw in text for kw in ["FAIL", "SL HIT", "STOPPED OUT", "LOSS", "LIQUIDATED"]):
            return {
                "msg_type": "result", "ticker": ticker, "is_trade_signal": True,
                "result_outcome": "loss", "result_r": -1.0, "result_note": "sl hit",
            }
        if any(kw in text for kw in ["TP HIT", "TP1 HIT", "TP2 HIT", "TAKE TP", "TAKE PROFIT", "TARGET HIT"]):
            return {
                "msg_type": "result", "ticker": ticker, "is_trade_signal": True,
                "result_outcome": "win", "result_r": 1.0, "result_note": "tp hit",
            }
    return None


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

    # Post-LLM override: catch obvious results/cancellations the LLM missed
    if signal_data:
        signal_data = _override_msg_type(content, signal_data)
    elif content:
        # LLM returned None but content might be an obvious result
        override = _detect_result_from_text(content)
        if override:
            signal_data = override

    if signal_data:
        msg_type = signal_data.get("msg_type", "signal")

        if msg_type == "result":
            # This is a trade RESULT — resolve the trader's most recent pending call
            _resolve_trader_call(
                author=author,
                ticker=signal_data.get("ticker"),
                outcome=signal_data.get("result_outcome", "loss"),
                r_value=signal_data.get("result_r"),
                note=signal_data.get("result_note", ""),
            )
            log.info("[DISCORD] Resolved %s call for %s: %s (%s)",
                     signal_data.get("ticker", "?"), author,
                     signal_data.get("result_outcome"), signal_data.get("result_note"))
        else:
            # This is a NEW SIGNAL
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

            # Generate agent discussions
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
