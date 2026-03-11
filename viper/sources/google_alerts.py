"""Google Alerts RSS source — parses RSS feeds from Google Alerts.

Jordan sets up alerts at https://alerts.google.com with RSS delivery.
Feed URLs are stored in GOOGLE_ALERT_FEEDS env var (comma-separated)
or in data/google_alert_feeds.json.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import feedparser

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent.parent / "data"
FEEDS_FILE = DATA_DIR / "google_alert_feeds.json"

SKILL_KEYWORDS = {
    "coding": [
        "python", "bot", "scraper", "scraping", "api",
        "automation", "chatbot", "ai", "developer", "backend",
        "telegram", "discord", "whatsapp", "flask", "django",
        "n8n", "zapier", "make.com", "appointment", "booking",
        "virtual assistant", "ai agent", "lead capture",
    ],
    "content": [
        "seo", "content", "copywriting", "writer", "writing",
        "marketing", "blog", "newsletter",
    ],
}


@dataclass
class GoogleAlertLead:
    title: str = ""
    url: str = ""
    description: str = ""
    published: str = ""
    feed_query: str = ""
    matched_skills: list[str] = field(default_factory=list)
    category: str = ""
    job_id: str = ""


def _clean_html(text: str) -> str:
    """Strip HTML tags."""
    return re.sub(r"<[^>]+>", "", text).strip()


def _get_feed_urls() -> list[str]:
    """Get RSS feed URLs from env var or config file."""
    feeds = []

    # From env var (comma-separated)
    env_feeds = os.getenv("GOOGLE_ALERT_FEEDS", "")
    if env_feeds:
        feeds.extend(f.strip() for f in env_feeds.split(",") if f.strip())

    # From config file
    if FEEDS_FILE.exists():
        try:
            data = json.loads(FEEDS_FILE.read_text())
            if isinstance(data, list):
                feeds.extend(data)
            elif isinstance(data, dict):
                feeds.extend(data.get("feeds", []))
        except Exception:
            pass

    return list(set(feeds))  # Deduplicate


def _unwrap_google_url(url: str) -> str:
    """Extract the real destination URL from a Google Alerts redirect.

    Google Alerts wraps URLs as:
      https://www.google.com/url?rct=j&sa=t&url=REAL_URL&ct=...&cd=...&usg=...
    The ct/cd/usg params change between fetches, breaking dedup.
    """
    parsed = urlparse(url)
    if parsed.hostname and "google.com" in parsed.hostname and parsed.path == "/url":
        qs = parse_qs(parsed.query)
        inner = qs.get("url", [""])[0]
        if inner:
            return inner
    return url


def _classify(text: str) -> tuple[str, list[str]]:
    """Classify text and return (category, matched_skills)."""
    lower = text.lower()
    coding = [k for k in SKILL_KEYWORDS["coding"] if k in lower]
    content = [k for k in SKILL_KEYWORDS["content"] if k in lower]

    if coding and content:
        return "mixed", coding + content
    elif coding:
        return "coding", coding
    elif content:
        return "content", content
    return "other", []


def scan_google_alerts() -> list[GoogleAlertLead]:
    """Parse all configured Google Alert RSS feeds for leads."""
    feed_urls = _get_feed_urls()
    if not feed_urls:
        log.info("[GALERTS] No Google Alert feeds configured")
        return []

    leads: list[GoogleAlertLead] = []
    seen_urls: set[str] = set()

    for feed_url in feed_urls:
        try:
            feed = feedparser.parse(feed_url)

            if feed.bozo and not feed.entries:
                log.warning("[GALERTS] Failed to parse feed: %s", feed_url[:80])
                continue

            feed_title = feed.feed.get("title", "Google Alert")

            for entry in feed.entries:
                raw_url = entry.get("link", "")
                if not raw_url:
                    continue

                # Unwrap Google redirect URLs to get the real destination
                url = _unwrap_google_url(raw_url)

                if url in seen_urls:
                    continue
                seen_urls.add(url)

                title = _clean_html(entry.get("title", ""))
                description = _clean_html(entry.get("summary", ""))
                published = entry.get("published", "")

                # Classify
                full_text = f"{title} {description}"
                category, matched = _classify(full_text)

                # Stable hash from the unwrapped URL (MD5, not hash())
                job_id = f"ga_{hashlib.md5(url.encode()).hexdigest()[:8]}"

                leads.append(GoogleAlertLead(
                    title=title[:200],
                    url=url,
                    description=description[:500],
                    published=published,
                    feed_query=feed_title,
                    matched_skills=matched,
                    category=category,
                    job_id=job_id,
                ))

        except Exception as e:
            log.error("[GALERTS] Error parsing feed %s: %s", feed_url[:60], str(e)[:200])

    log.info("[GALERTS] Found %d leads from %d feeds", len(leads), len(feed_urls))
    return leads
