"""Per-market news fetcher for Hawk analysis enrichment.

Sources (priority order):
1. Viper raw intel (keyword match from viper_intel.json)
2. Atlas news_sentiment (for crypto_event category)
3. Google News RSS (free, entity-keyword search)

Results cached 15 minutes per market to avoid redundant fetches.
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.parse import quote_plus
from xml.etree import ElementTree

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"

# Cache: {condition_id: {"items": [...], "ts": float}}
_cache: dict[str, dict] = {}
_CACHE_TTL = 900  # 15 minutes


def fetch_market_news(
    question: str,
    category: str,
    entities: list[str],
    condition_id: str,
) -> list[dict]:
    """Fetch recent news for a specific market.

    Returns list of {headline, summary, source, url, sentiment, hours_ago}.
    Max 5 items.
    """
    # Check cache
    cached = _cache.get(condition_id)
    if cached and time.time() - cached["ts"] < _CACHE_TTL:
        return cached["items"]

    items: list[dict] = []

    # Source 1: Viper raw intel keyword match
    try:
        viper_items = _match_viper_raw(question, condition_id)
        items.extend(viper_items)
    except Exception:
        log.debug("Viper raw match failed for %s", condition_id[:12])

    # Source 2: Atlas news_sentiment (crypto markets)
    if category == "crypto_event":
        try:
            atlas_items = _load_atlas_news(question)
            items.extend(atlas_items)
        except Exception:
            log.debug("Atlas news load failed for %s", condition_id[:12])

    # Source 3: Google News RSS (if we have <3 items)
    if len(items) < 3:
        try:
            keywords = _extract_keywords(question)
            if keywords:
                rss_items = _google_news_rss(keywords)
                items.extend(rss_items)
        except Exception:
            log.debug("Google News RSS failed for %s", condition_id[:12])

    # Deduplicate by headline
    seen = set()
    unique = []
    for item in items:
        h = item.get("headline", "").lower()[:60]
        if h and h not in seen:
            seen.add(h)
            unique.append(item)

    result = unique[:5]

    # Cache
    _cache[condition_id] = {"items": result, "ts": time.time()}
    return result


def _match_viper_raw(question: str, condition_id: str) -> list[dict]:
    """Match raw Viper intel items by keyword overlap with market question."""
    intel_file = DATA_DIR / "viper_intel.json"
    if not intel_file.exists():
        return []

    try:
        data = json.loads(intel_file.read_text())
        raw_items = data.get("items", [])
    except Exception:
        return []

    if not raw_items:
        return []

    # Extract significant keywords from question
    q_words = set(re.findall(r'\b[a-zA-Z]{4,}\b', question.lower()))
    stop = {"will", "what", "when", "this", "that", "have", "from", "with", "they", "been", "more", "than"}
    q_words -= stop

    matches = []
    now = time.time()
    for item in raw_items:
        # Skip old items (>24h)
        if now - item.get("timestamp", 0) > 86400:
            continue

        text = (item.get("headline", "") + " " + item.get("summary", "")).lower()
        overlap = sum(1 for w in q_words if w in text)
        if overlap >= 2:
            hours_ago = (now - item.get("timestamp", now)) / 3600
            sent = item.get("sentiment", 0)
            matches.append({
                "headline": item.get("headline", "")[:150],
                "summary": item.get("summary", "")[:200],
                "source": item.get("source", "viper"),
                "url": item.get("url", ""),
                "sentiment": "positive" if sent > 0.2 else "negative" if sent < -0.2 else "neutral",
                "hours_ago": hours_ago,
            })

    # Sort by recency
    matches.sort(key=lambda x: x["hours_ago"])
    return matches[:3]


def _load_atlas_news(question: str) -> list[dict]:
    """Load Atlas news_sentiment data for crypto markets."""
    # Check hawk-specific atlas intel first
    hawk_intel_file = DATA_DIR / "hawk_atlas_intel.json"
    if hawk_intel_file.exists():
        try:
            data = json.loads(hawk_intel_file.read_text())
            news = data.get("news_sentiment", [])
            if news:
                items = []
                now = time.time()
                for n in news[:3]:
                    items.append({
                        "headline": n.get("title", n.get("headline", ""))[:150],
                        "summary": n.get("snippet", n.get("summary", ""))[:200],
                        "source": "atlas",
                        "url": n.get("url", ""),
                        "sentiment": "neutral",
                        "hours_ago": (now - n.get("timestamp", now)) / 3600 if n.get("timestamp") else 1,
                    })
                return items
        except Exception:
            pass

    # Fallback: garves market intel (has crypto news)
    garves_intel = DATA_DIR / "market_intel.json"
    if not garves_intel.exists():
        garves_intel = Path.home() / "polymarket-bot" / "data" / "market_intel.json"
    if garves_intel.exists():
        try:
            data = json.loads(garves_intel.read_text())
            news = data.get("news", [])
            items = []
            for n in news[:2]:
                items.append({
                    "headline": n.get("title", "")[:150],
                    "summary": n.get("snippet", "")[:200],
                    "source": "atlas/garves",
                    "url": n.get("url", ""),
                    "sentiment": "neutral",
                    "hours_ago": 1,
                })
            return items
        except Exception:
            pass
    return []


def _extract_keywords(question: str) -> str:
    """Extract search-worthy keywords from market question."""
    # Remove common question words
    q = question.lower()
    remove = ["will", "the", "be", "to", "in", "on", "at", "by", "for", "of", "a", "an",
              "is", "are", "was", "were", "has", "have", "had", "do", "does", "did",
              "before", "after", "above", "below", "this", "that", "these", "those"]
    words = q.split()
    keywords = [w.strip("?.,!") for w in words if w.strip("?.,!") not in remove and len(w) > 2]
    return " ".join(keywords[:6])


def _google_news_rss(query: str) -> list[dict]:
    """Fetch from Google News RSS feed (free, no API key)."""
    url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"
    try:
        req = Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        })
        with urlopen(req, timeout=10) as resp:
            xml_data = resp.read()

        root = ElementTree.fromstring(xml_data)
        items = []
        now = time.time()

        for item_el in root.findall(".//item")[:5]:
            title = item_el.findtext("title", "")
            link = item_el.findtext("link", "")
            pub_date = item_el.findtext("pubDate", "")
            source = item_el.findtext("source", "news")

            # Parse pub date to get hours ago
            hours_ago = 12  # default
            if pub_date:
                try:
                    from email.utils import parsedate_to_datetime
                    dt = parsedate_to_datetime(pub_date)
                    hours_ago = max(0, (now - dt.timestamp()) / 3600)
                except Exception:
                    pass

            if title:
                items.append({
                    "headline": title[:150],
                    "summary": "",
                    "source": source[:30] if source else "google_news",
                    "url": link,
                    "sentiment": "neutral",
                    "hours_ago": round(hours_ago, 1),
                })

        return items
    except Exception:
        log.debug("Google News RSS failed for query: %s", query[:50])
        return []
