"""Garves — 24/7 Crypto News Feed.

Fetches real-time crypto news from RSS feeds (CoinTelegraph + CoinDesk).
Analyzes headlines for bullish/bearish sentiment.
Returns a NewsSignal used as a voting indicator in the ensemble.

No API keys required — pure RSS parsing.
"""
from __future__ import annotations

import logging
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Optional

import requests

log = logging.getLogger(__name__)

# ── RSS Feed Sources ──
FEEDS = [
    "https://cointelegraph.com/rss",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
]

# ── Sentiment Keywords ──
BULLISH_KEYWORDS = [
    "surge", "soar", "rally", "bull", "breakout", "all-time high", "ath",
    "pump", "moon", "bullish", "gains", "recovery", "uptrend", "buy",
    "adoption", "etf approved", "etf approval", "institutional", "inflow",
    "record high", "positive", "growth", "upgrade", "milestone",
    "accumulation", "whale buy", "support holds",
]

BEARISH_KEYWORDS = [
    "crash", "plunge", "dump", "bear", "selloff", "sell-off", "liquidat",
    "hack", "exploit", "rug pull", "scam", "fraud", "ban", "restrict",
    "regulation crackdown", "sec sues", "sec charges", "fud", "fear",
    "outflow", "decline", "drop", "correction", "bearish", "warning",
    "bankrupt", "insolven", "collapse", "downtrend", "resistance fails",
]

# Asset-specific keywords (map headline → which asset it's about)
ASSET_KEYWORDS = {
    "bitcoin": ["bitcoin", "btc", "satoshi"],
    "ethereum": ["ethereum", "eth", "vitalik", "erc-20", "erc20"],
    "solana": ["solana", "sol"],
}

CACHE_TTL = 300  # 5 minutes
MAX_HEADLINE_AGE = 3600  # 1 hour — only consider recent news


@dataclass
class NewsSignal:
    """Aggregated news sentiment for a specific asset."""
    sentiment: float      # -1.0 (very bearish) to +1.0 (very bullish)
    headline_count: int   # how many relevant headlines found
    top_headline: str     # most impactful headline
    asset: str            # which asset this is about


class CryptoNewsFeed:
    """Fetches and analyzes crypto news from RSS feeds."""

    def __init__(self):
        self._cache: dict[str, list[dict]] = {}
        self._cache_time: float = 0

    def get_sentiment(self, asset: str = "bitcoin") -> Optional[NewsSignal]:
        """Get current news sentiment for an asset.

        Returns None if no relevant news found or feed unavailable.
        """
        headlines = self._get_headlines()
        if not headlines:
            return None

        # Filter to asset-specific headlines (deduplicate by title to prevent double-counting)
        asset_keys = ASSET_KEYWORDS.get(asset, [asset])
        relevant = []
        seen_titles: set[str] = set()
        for h in headlines:
            title_lower = h["title"].lower()
            if title_lower in seen_titles:
                continue
            if any(k in title_lower for k in asset_keys):
                relevant.append(h)
                seen_titles.add(title_lower)

        # Also include general crypto/market headlines
        general_keys = ["crypto", "market", "defi", "web3", "blockchain"]
        for h in headlines:
            title_lower = h["title"].lower()
            if title_lower in seen_titles:
                continue
            if any(k in title_lower for k in general_keys):
                relevant.append(h)
                seen_titles.add(title_lower)

        if not relevant:
            return None

        # Score each headline
        total_score = 0.0
        top_headline = ""
        top_score = 0.0

        for h in relevant:
            score = self._score_headline(h["title"])
            total_score += score
            if abs(score) > abs(top_score):
                top_score = score
                top_headline = h["title"]

        if not relevant:
            return None

        avg_sentiment = total_score / len(relevant)
        # Clamp to [-1, 1]
        avg_sentiment = max(-1.0, min(1.0, avg_sentiment))

        # Only return a signal if sentiment is meaningful
        if abs(avg_sentiment) < 0.05:
            return None

        return NewsSignal(
            sentiment=avg_sentiment,
            headline_count=len(relevant),
            top_headline=top_headline[:100],
            asset=asset,
        )

    def _score_headline(self, title: str) -> float:
        """Score a single headline. Positive = bullish, negative = bearish."""
        title_lower = title.lower()
        bull_hits = sum(1 for k in BULLISH_KEYWORDS if k in title_lower)
        bear_hits = sum(1 for k in BEARISH_KEYWORDS if k in title_lower)

        if bull_hits == 0 and bear_hits == 0:
            return 0.0

        total = bull_hits + bear_hits
        # Score between -1 and +1
        return (bull_hits - bear_hits) / total

    def _get_headlines(self) -> list[dict]:
        """Fetch headlines from RSS feeds (cached 5 min)."""
        now = time.time()
        if now - self._cache_time < CACHE_TTL and self._cache.get("headlines"):
            return self._cache["headlines"]

        headlines = []
        for feed_url in FEEDS:
            try:
                resp = requests.get(feed_url, timeout=10, headers={
                    "User-Agent": "Garves/2.0 CryptoBot"
                })
                if resp.status_code != 200:
                    continue

                root = ET.fromstring(resp.content)
                # Standard RSS format: channel > item > title + pubDate
                for item in root.iter("item"):
                    title_el = item.find("title")
                    if title_el is None or not title_el.text:
                        continue
                    headlines.append({
                        "title": title_el.text.strip(),
                        "source": feed_url.split("/")[2],
                    })

            except Exception as e:
                log.debug("[NEWS] Feed error (%s): %s", feed_url[:30], str(e)[:80])
                continue

        # Keep only the most recent headlines (RSS feeds are sorted newest-first)
        headlines = headlines[:30]

        self._cache["headlines"] = headlines
        self._cache_time = now

        if headlines:
            log.info("[NEWS] Fetched %d headlines from %d feeds", len(headlines), len(FEEDS))

        return headlines


# ── Module singleton ──
_feed: CryptoNewsFeed | None = None


def get_news_feed() -> CryptoNewsFeed:
    """Get or create the global news feed instance."""
    global _feed
    if _feed is None:
        _feed = CryptoNewsFeed()
    return _feed
