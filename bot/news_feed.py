"""Garves — 24/7 Crypto News Feed with LLM Sentiment Analysis.

Fetches real-time crypto news from RSS feeds (CoinTelegraph + CoinDesk).
Uses local LLM (Qwen 3B via shared layer) for sentiment analysis with
keyword fallback if LLM is unavailable or slow.

No API keys required for RSS — LLM uses shared intelligence layer.
"""
from __future__ import annotations

import logging
import sys
import time
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from bot.http_session import get_session

log = logging.getLogger(__name__)

# ── Shared Intelligence Layer for LLM sentiment ──
_USE_LLM = False
_llm_call = None
try:
    sys.path.insert(0, str(Path.home() / "shared"))
    from llm_client import llm_call as _shared_llm_call
    _llm_call = _shared_llm_call
    _USE_LLM = True
except ImportError:
    pass

# ── RSS Feed Sources ──
FEEDS = [
    "https://cointelegraph.com/rss",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
]

# ── Keyword Fallback Sentiment ──
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
    "xrp": ["xrp", "ripple", "xrpl"],
}

CACHE_TTL = 300  # 5 minutes
LLM_CACHE_TTL = 600  # 10 minutes for LLM results (more expensive)
MAX_HEADLINE_AGE = 3600  # 1 hour — only consider recent news
LLM_TIMEOUT = 5.0  # Max seconds to wait for LLM response


@dataclass
class NewsSignal:
    """Aggregated news sentiment for a specific asset."""
    sentiment: float      # -1.0 (very bearish) to +1.0 (very bullish)
    headline_count: int   # how many relevant headlines found
    top_headline: str     # most impactful headline
    asset: str            # which asset this is about


class CryptoNewsFeed:
    """Fetches and analyzes crypto news from RSS feeds with LLM sentiment."""

    def __init__(self):
        self._cache: dict[str, list[dict]] = {}
        self._cache_time: float = 0
        # LLM sentiment cache: asset -> (NewsSignal, timestamp)
        self._llm_cache: dict[str, tuple[NewsSignal | None, float]] = {}

    def get_sentiment(self, asset: str = "bitcoin") -> Optional[NewsSignal]:
        """Get current news sentiment for an asset.

        Uses LLM sentiment analysis if available, falls back to keyword matching.
        Returns None if no relevant news found or feed unavailable.
        """
        headlines = self._get_headlines()
        if not headlines:
            return None

        # Filter to asset-relevant headlines
        relevant = self._filter_relevant(headlines, asset)
        if not relevant:
            return None

        # Try LLM sentiment first (cached 10 min)
        if _USE_LLM and _llm_call:
            llm_result = self._get_llm_sentiment(asset, relevant)
            if llm_result is not None:
                return llm_result

        # Fallback: keyword-based sentiment
        return self._keyword_sentiment(asset, relevant)

    def _filter_relevant(self, headlines: list[dict], asset: str) -> list[dict]:
        """Filter headlines to asset-specific and general crypto news."""
        asset_keys = ASSET_KEYWORDS.get(asset, [asset])
        relevant = []
        seen_titles: set[str] = set()

        # Asset-specific headlines
        for h in headlines:
            title_lower = h["title"].lower()
            if title_lower in seen_titles:
                continue
            if any(k in title_lower for k in asset_keys):
                relevant.append(h)
                seen_titles.add(title_lower)

        # General crypto/market headlines
        general_keys = ["crypto", "market", "defi", "web3", "blockchain"]
        for h in headlines:
            title_lower = h["title"].lower()
            if title_lower in seen_titles:
                continue
            if any(k in title_lower for k in general_keys):
                relevant.append(h)
                seen_titles.add(title_lower)

        return relevant

    def _get_llm_sentiment(self, asset: str, relevant: list[dict]) -> Optional[NewsSignal]:
        """Use local LLM (Qwen 3B) for sentiment analysis.

        Batches all headlines into a single prompt for efficiency.
        Falls back to keyword method on any failure.
        """
        # Check LLM cache
        now = time.time()
        cached = self._llm_cache.get(asset)
        if cached and (now - cached[1]) < LLM_CACHE_TTL:
            return cached[0]

        try:
            # Build headline list for the prompt
            headline_list = "\n".join(
                f"{i+1}. {h['title'][:120]}"
                for i, h in enumerate(relevant[:30])
            )

            t0 = time.time()
            result = _llm_call(
                system=(
                    "You are a crypto market sentiment analyzer. "
                    "Rate the overall sentiment of these headlines for the given asset. "
                    "Reply with ONLY a number from -1.0 (very bearish) to +1.0 (very bullish). "
                    "0.0 = neutral. Consider: regulatory news, adoption, price action, "
                    "institutional flows, hacks/exploits, market structure."
                ),
                user=(
                    f"Asset: {asset.upper()}\n"
                    f"Headlines ({len(relevant)}):\n{headline_list}\n\n"
                    f"Overall sentiment score (-1.0 to +1.0):"
                ),
                agent="garves",
                task_type="fast",  # Use fast model (Qwen 3B)
                max_tokens=10,
                temperature=0.1,
            )
            elapsed = time.time() - t0

            if elapsed > LLM_TIMEOUT:
                log.debug("[NEWS/LLM] Too slow (%.1fs), falling back to keywords", elapsed)
                return None

            if result:
                try:
                    sentiment = float(result.strip())
                    sentiment = max(-1.0, min(1.0, sentiment))

                    if abs(sentiment) < 0.05:
                        self._llm_cache[asset] = (None, now)
                        return None

                    # Find top headline (most relevant to asset)
                    top = relevant[0]["title"] if relevant else ""

                    signal = NewsSignal(
                        sentiment=sentiment,
                        headline_count=len(relevant),
                        top_headline=top[:100],
                        asset=asset,
                    )
                    self._llm_cache[asset] = (signal, now)
                    log.info("[NEWS/LLM] %s: sentiment=%.2f (%d headlines, %.1fs) — %s",
                             asset.upper(), sentiment, len(relevant), elapsed, top[:60])
                    return signal
                except (ValueError, TypeError):
                    log.debug("[NEWS/LLM] Failed to parse LLM response: %s", result[:50])
        except Exception as e:
            log.debug("[NEWS/LLM] LLM call failed: %s", str(e)[:80])

        return None

    def _keyword_sentiment(self, asset: str, relevant: list[dict]) -> Optional[NewsSignal]:
        """Fallback: keyword-based sentiment scoring."""
        total_score = 0.0
        top_headline = ""
        top_score = 0.0

        for h in relevant:
            score = self._score_headline(h["title"])
            total_score += score
            if abs(score) > abs(top_score):
                top_score = score
                top_headline = h["title"]

        avg_sentiment = total_score / len(relevant)
        avg_sentiment = max(-1.0, min(1.0, avg_sentiment))

        if abs(avg_sentiment) < 0.05:
            return None

        return NewsSignal(
            sentiment=avg_sentiment,
            headline_count=len(relevant),
            top_headline=top_headline[:100],
            asset=asset,
        )

    def _score_headline(self, title: str) -> float:
        """Score a single headline via keywords. Positive = bullish, negative = bearish."""
        title_lower = title.lower()
        bull_hits = sum(1 for k in BULLISH_KEYWORDS if k in title_lower)
        bear_hits = sum(1 for k in BEARISH_KEYWORDS if k in title_lower)

        if bull_hits == 0 and bear_hits == 0:
            return 0.0

        total = bull_hits + bear_hits
        return (bull_hits - bear_hits) / total

    def _get_headlines(self) -> list[dict]:
        """Fetch headlines from RSS feeds (cached 5 min)."""
        now = time.time()
        if now - self._cache_time < CACHE_TTL and self._cache.get("headlines"):
            return self._cache["headlines"]

        headlines = []
        for feed_url in FEEDS:
            try:
                session = get_session()
                resp = session.get(feed_url, timeout=10, headers={
                    "User-Agent": "Garves/2.0 CryptoBot"
                })
                if resp.status_code != 200:
                    continue

                root = ElementTree.fromstring(resp.content)
                for item in root.iter("item"):
                    title_el = item.find("title")
                    if title_el is None or not title_el.text:
                        continue

                    pub_el = item.find("pubDate")
                    if pub_el is not None and pub_el.text:
                        try:
                            from email.utils import parsedate_to_datetime
                            pub_dt = parsedate_to_datetime(pub_el.text)
                            age_seconds = now - pub_dt.timestamp()
                            if age_seconds > MAX_HEADLINE_AGE:
                                continue
                        except Exception:
                            pass

                    headlines.append({
                        "title": title_el.text.strip(),
                        "source": feed_url.split("/")[2],
                    })

            except Exception as e:
                log.debug("[NEWS] Feed error (%s): %s", feed_url[:30], str(e)[:80])
                continue

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
