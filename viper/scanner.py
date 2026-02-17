"""Market Intelligence Scanner — Tavily, Polymarket activity, Reddit predictions.

Viper scans real-time data sources every 5 minutes and produces IntelItems
that get matched to active Polymarket markets and fed to Hawk for trading.
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

from bot.http_session import get_session
from viper.intel import IntelItem, make_intel_id

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"

# ─── Tavily (real-time web search) ────────────────────────────────────

def scan_tavily(api_key: str, queries: list[str] | None = None) -> list[IntelItem]:
    """Use Tavily API for real-time news search on prediction-market-relevant topics."""
    if not api_key:
        log.warning("No Tavily API key — skipping Tavily scan")
        return []

    default_queries = [
        "breaking news politics today",
        "sports results scores today",
        "crypto regulation news today",
        "prediction market polymarket trending",
        "major event happening today breaking",
        "election polls latest results",
    ]
    queries = queries or default_queries
    session = get_session()
    items: list[IntelItem] = []

    for query in queries:
        try:
            resp = session.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": api_key,
                    "query": query,
                    "search_depth": "basic",
                    "max_results": 8,
                    "include_answer": True,
                },
                timeout=15,
            )
            if resp.status_code != 200:
                log.warning("Tavily returned %d for query: %s", resp.status_code, query[:40])
                continue

            data = resp.json()
            results = data.get("results", [])

            for r in results:
                title = r.get("title", "")
                content = r.get("content", "")[:600]
                url = r.get("url", "")

                if not title:
                    continue

                # Extract relevance tags from content
                tags = _extract_tags(title + " " + content)
                category = _categorize_intel(title + " " + content)
                sentiment = _estimate_sentiment(title + " " + content)

                items.append(IntelItem(
                    id=make_intel_id("tavily", title),
                    source="tavily",
                    headline=title[:300],
                    summary=content[:600],
                    url=url,
                    relevance_tags=tags,
                    sentiment=sentiment,
                    confidence=0.7,
                    timestamp=time.time(),
                    category=category,
                ))

        except Exception:
            log.exception("Tavily search failed for: %s", query[:40])

    log.info("Tavily scan: %d intel items from %d queries", len(items), len(queries))
    return items


# ─── Polymarket Activity (volume spikes, new markets) ────────────────

def scan_polymarket_activity(clob_host: str = "https://clob.polymarket.com") -> list[IntelItem]:
    """Detect high-volume markets, price movements, and new trending markets."""
    session = get_session()
    items: list[IntelItem] = []

    try:
        # Get markets sorted by volume (most active)
        resp = session.get(
            f"{clob_host}/markets",
            params={"limit": 50, "order": "volume24hr", "ascending": "false"},
            timeout=15,
        )
        if resp.status_code != 200:
            log.warning("Polymarket API returned %d", resp.status_code)
            return items

        data = resp.json()
        markets = data.get("data", [])

        for m in markets:
            question = m.get("question", "")
            volume = float(m.get("volume", 0) or 0)
            volume_24h = float(m.get("volume24hr", 0) or 0)
            cid = m.get("condition_id", "")

            if not question or not m.get("accepting_orders"):
                continue

            # Flag high-activity markets
            if volume_24h > 50000:
                activity_level = "extremely_high"
            elif volume_24h > 10000:
                activity_level = "high"
            elif volume_24h > 5000:
                activity_level = "moderate"
            else:
                continue  # Skip low-activity

            # Get current prices
            tokens = m.get("tokens", [])
            yes_price = 0.5
            for t in tokens:
                if (t.get("outcome") or "").lower() in ("yes", "up"):
                    try:
                        yes_price = float(t.get("price", 0.5))
                    except (ValueError, TypeError):
                        pass

            tags = _extract_tags(question)
            category = _categorize_intel(question)

            items.append(IntelItem(
                id=make_intel_id("polymarket", f"{cid}_{int(volume_24h)}"),
                source="polymarket_activity",
                headline=f"[{activity_level.upper()}] {question[:200]}",
                summary=(
                    f"24h Volume: ${volume_24h:,.0f} | Total Volume: ${volume:,.0f} | "
                    f"YES Price: {yes_price:.2f} ({yes_price*100:.0f}%)"
                ),
                url=f"https://polymarket.com/event/{cid}",
                relevance_tags=tags + [activity_level],
                sentiment=0.0,  # neutral — just activity data
                confidence=0.9,
                timestamp=time.time(),
                matched_markets=[cid],
                category=category,
                raw_data={
                    "condition_id": cid,
                    "volume_24h": volume_24h,
                    "yes_price": yes_price,
                    "activity_level": activity_level,
                },
            ))

    except Exception:
        log.exception("Polymarket activity scan failed")

    log.info("Polymarket activity scan: %d high-activity markets", len(items))
    return items


# ─── Reddit Predictions ──────────────────────────────────────────────

_PREDICTION_SUBS = ["polymarket", "sportsbook", "politics", "wallstreetbets", "cryptocurrency"]

def scan_reddit_predictions(subreddits: list[str] | None = None) -> list[IntelItem]:
    """Scan prediction-relevant Reddit subs for market-moving discussions."""
    subs = subreddits or _PREDICTION_SUBS
    session = get_session()
    items: list[IntelItem] = []

    for sub in subs:
        try:
            resp = session.get(
                f"https://www.reddit.com/r/{sub}/hot.json",
                params={"limit": 15},
                headers={"User-Agent": "Viper-Intel/2.0"},
                timeout=10,
            )
            if resp.status_code != 200:
                log.warning("Reddit r/%s returned %d", sub, resp.status_code)
                continue

            data = resp.json()
            posts = data.get("data", {}).get("children", [])

            for post in posts:
                p = post.get("data", {})
                title = p.get("title", "")
                body = (p.get("selftext") or "")[:400]
                score = p.get("score", 0)
                num_comments = p.get("num_comments", 0)

                # Only care about popular/active posts
                if score < 50 and num_comments < 20:
                    continue

                combined = title + " " + body
                tags = _extract_tags(combined)
                category = _categorize_intel(combined)
                sentiment = _estimate_sentiment(combined)

                items.append(IntelItem(
                    id=make_intel_id("reddit", title),
                    source=f"reddit/r/{sub}",
                    headline=title[:300],
                    summary=body[:400] if body else f"Score: {score}, Comments: {num_comments}",
                    url=f"https://reddit.com{p.get('permalink', '')}",
                    relevance_tags=tags,
                    sentiment=sentiment,
                    confidence=0.5 + min(0.3, score / 1000),  # higher score = more confidence
                    timestamp=time.time(),
                    category=category,
                    raw_data={"score": score, "comments": num_comments, "sub": sub},
                ))

        except Exception:
            log.exception("Failed to scan r/%s", sub)

    log.info("Reddit scan: %d intel items from %d subs", len(items), len(subs))
    return items


# ─── Combined Scanner ─────────────────────────────────────────────────

def scan_all(tavily_key: str, clob_host: str = "https://clob.polymarket.com") -> list[IntelItem]:
    """Run ALL intelligence scanners, deduplicate, return combined feed."""
    all_items: list[IntelItem] = []
    seen_ids: set[str] = set()

    # 1. Tavily — real-time news
    for item in scan_tavily(tavily_key):
        if item.id not in seen_ids:
            seen_ids.add(item.id)
            all_items.append(item)

    # 2. Polymarket activity — volume spikes
    for item in scan_polymarket_activity(clob_host):
        if item.id not in seen_ids:
            seen_ids.add(item.id)
            all_items.append(item)

    # 3. Reddit predictions
    for item in scan_reddit_predictions():
        if item.id not in seen_ids:
            seen_ids.add(item.id)
            all_items.append(item)

    log.info("Total intel items: %d (Tavily + Polymarket + Reddit)", len(all_items))
    return all_items


# ─── Helpers ──────────────────────────────────────────────────────────

_TAG_KEYWORDS = {
    "politics": ["trump", "biden", "election", "congress", "senate", "republican", "democrat",
                 "president", "vote", "ballot", "governor", "cabinet", "impeach", "supreme court",
                 "policy", "legislation", "partisan"],
    "sports": ["nba", "nfl", "mlb", "nhl", "soccer", "football", "basketball", "baseball",
               "hockey", "tennis", "ufc", "mma", "boxing", "super bowl", "world cup",
               "championship", "playoffs", "match", "game", "score", "winner", "finals"],
    "crypto": ["bitcoin", "ethereum", "crypto", "btc", "eth", "sol", "blockchain", "defi",
               "nft", "halving", "etf", "sec", "regulation", "token", "mining"],
    "culture": ["oscar", "grammy", "emmy", "movie", "film", "music", "celebrity", "ai",
                "spacex", "nasa", "weather", "viral", "tiktok", "youtube"],
    "economy": ["fed", "interest rate", "inflation", "gdp", "recession", "jobs", "unemployment",
                "market", "stock", "tariff", "trade war", "sanctions"],
}


def _extract_tags(text: str) -> list[str]:
    """Extract relevance tags from text for market matching."""
    text_lower = text.lower()
    tags = []
    for category, keywords in _TAG_KEYWORDS.items():
        for kw in keywords:
            if kw in text_lower:
                tags.append(kw)
    # Deduplicate while preserving order
    seen = set()
    result = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            result.append(t)
    return result[:15]  # cap at 15 tags


def _categorize_intel(text: str) -> str:
    """Categorize intel item."""
    text_lower = text.lower()
    scores = {}
    for category, keywords in _TAG_KEYWORDS.items():
        scores[category] = sum(1 for kw in keywords if kw in text_lower)
    if not scores or max(scores.values()) == 0:
        return "other"
    return max(scores, key=scores.get)


_POSITIVE_WORDS = {"win", "surge", "rally", "gain", "up", "rise", "bull", "success", "victory",
                   "strong", "growth", "record", "high", "beat", "positive", "approved", "passed"}
_NEGATIVE_WORDS = {"lose", "crash", "fall", "drop", "down", "bear", "fail", "loss", "defeat",
                   "weak", "decline", "low", "miss", "negative", "rejected", "denied", "scandal"}


def _estimate_sentiment(text: str) -> float:
    """Quick sentiment estimate from -1 to 1."""
    words = set(re.findall(r'\b\w+\b', text.lower()))
    pos = len(words & _POSITIVE_WORDS)
    neg = len(words & _NEGATIVE_WORDS)
    total = pos + neg
    if total == 0:
        return 0.0
    return round((pos - neg) / total, 2)
