"""Market Intelligence Scanner — Tavily, Polymarket activity, Reddit predictions.

Viper scans real-time data sources every 5 minutes and produces IntelItems
that get matched to active Polymarket markets and fed to Hawk for trading.
"""
from __future__ import annotations

import json
import logging
import re
import sys
import time
from pathlib import Path

from bot.http_session import get_session
from viper.intel import IntelItem, make_intel_id

log = logging.getLogger(__name__)

# ── Shared Intelligence Layer (MLX routing) ──
_USE_SHARED_LLM = False
_shared_llm_call = None
try:
    sys.path.insert(0, str(Path.home() / "shared"))
    from llm_client import llm_call as _llm_call
    _shared_llm_call = _llm_call
    _USE_SHARED_LLM = True
except ImportError:
    pass

DATA_DIR = Path(__file__).parent.parent / "data"

# ─── Hawk Briefing Integration ────────────────────────────────────────

BRIEFING_FILE = DATA_DIR / "hawk_briefing.json"


def _load_hawk_queries() -> list[dict]:
    """Read Hawk briefing and return targeted queries with condition_id linkage.

    Returns list of {"query": str, "condition_id": str, "priority": int}.
    """
    if not BRIEFING_FILE.exists():
        return []
    try:
        briefing = json.loads(BRIEFING_FILE.read_text())
        age = time.time() - briefing.get("generated_at", 0)
        if age > 7200:  # 2 hours stale threshold
            log.info("Hawk briefing stale (%.0f min), skipping targeted queries", age / 60)
            return []

        queries = []
        for market in briefing.get("markets", []):
            cid = market.get("condition_id", "")
            priority = market.get("priority", 99)
            for q in market.get("search_queries", []):
                queries.append({"query": q, "condition_id": cid, "priority": priority})

        # Sort by priority, limit to avoid budget blow
        queries.sort(key=lambda x: x["priority"])
        log.info("Loaded %d targeted queries from Hawk briefing", len(queries))
        return queries
    except Exception:
        log.exception("Failed to load Hawk briefing queries")
        return []


# ─── Tavily (real-time web search) ────────────────────────────────────

_FALLBACK_QUERIES = [
    "Polymarket trending prediction markets today",
    "prediction market news politics sports events today",
]


def scan_tavily(api_key: str, queries: list[str] | None = None, use_briefing: bool = True) -> list[IntelItem]:
    """Use Tavily API for targeted news search driven by Hawk briefing.

    Priority: Hawk briefing queries first (pre-linked to markets),
    then fallback to generic queries only if briefing is empty/stale.
    Budget: max 4 queries per cycle to stay within 12k/month Tavily limit.
    """
    if not api_key:
        log.warning("No Tavily API key — skipping Tavily scan")
        return []

    session = get_session()
    items: list[IntelItem] = []
    max_queries = 4  # Budget: 4 queries/cycle

    # Build query plan: briefing-targeted first, then generic fallback
    query_plan: list[dict] = []  # {"query": str, "condition_id": str|None}

    if use_briefing:
        hawk_queries = _load_hawk_queries()
        # Take top 4 targeted queries (highest priority markets)
        seen_cids: set[str] = set()
        for hq in hawk_queries:
            if len(query_plan) >= max_queries:
                break
            # One query per market to maximize coverage
            cid = hq["condition_id"]
            if cid in seen_cids:
                continue
            seen_cids.add(cid)
            query_plan.append({"query": hq["query"], "condition_id": cid})
    else:
        hawk_queries = []

    # Fallback: if no briefing or <2 targeted queries, add generic ones
    if len(query_plan) < 2:
        fallback = queries or _FALLBACK_QUERIES
        for q in fallback:
            if len(query_plan) >= max_queries:
                break
            query_plan.append({"query": q, "condition_id": None})

    targeted_count = sum(1 for q in query_plan if q["condition_id"])
    log.info("Tavily query plan: %d targeted + %d generic = %d total",
             targeted_count, len(query_plan) - targeted_count, len(query_plan))

    for qp in query_plan:
        query = qp["query"]
        linked_cid = qp["condition_id"]

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

                tags = _extract_tags(title + " " + content)
                category = _categorize_intel(title + " " + content)
                sentiment = _estimate_sentiment(title + " " + content)

                # Pre-link to market if this was a targeted query
                matched = [linked_cid] if linked_cid else []

                items.append(IntelItem(
                    id=make_intel_id("tavily", title),
                    source="tavily",
                    headline=title[:300],
                    summary=content[:600],
                    url=url,
                    relevance_tags=tags,
                    sentiment=sentiment,
                    confidence=0.8 if linked_cid else 0.7,
                    timestamp=time.time(),
                    category=category,
                    matched_markets=matched,
                ))

        except Exception:
            log.exception("Tavily search failed for: %s", query[:40])

    log.info("Tavily scan: %d intel items from %d queries (%d targeted)",
             len(items), len(query_plan), targeted_count)
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

    # Publish each new intel item to the shared event bus
    for item in all_items:
        try:
            from shared.events import publish as bus_publish
            bus_publish(
                agent="viper",
                event_type="opportunity_found",
                data={
                    "source": item.source,
                    "title": item.headline[:200],
                    "estimated_value": 0,
                    "category": item.category,
                    "confidence": item.confidence,
                },
                summary=f"Intel found: {item.headline[:100]}",
            )
        except Exception:
            pass  # Never let bus failure crash Viper

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
    """Categorize intel item — LLM-enhanced with keyword fallback."""
    # Try LLM categorization (fast -> 3B for speed)
    if _USE_SHARED_LLM and _shared_llm_call:
        try:
            result = _shared_llm_call(
                system="You categorize news/intel items. Reply with EXACTLY one word: politics, sports, crypto, culture, economy, or other.",
                user=f"Categorize this: {text[:300]}",
                agent="viper",
                task_type="fast",
                max_tokens=10,
                temperature=0.1,
            )
            if result:
                cat = result.strip().lower().rstrip(".")
                if cat in ("politics", "sports", "crypto", "culture", "economy", "other"):
                    return cat
        except Exception:
            pass

    # Fallback: keyword counting
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
    """Sentiment estimate from -1 to 1 — FinBERT primary, LLM/keyword fallback."""
    # Try FinBERT first (free, local, financial-domain trained)
    try:
        from shared.sentiment import score_headline, sentiment_to_float
        result = score_headline(text[:512])
        if result is not None:
            return sentiment_to_float(result)
    except Exception:
        pass

    # Fallback: LLM sentiment (cloud API cost)
    if _USE_SHARED_LLM and _shared_llm_call:
        try:
            result = _shared_llm_call(
                system="You score sentiment of news items. Reply with ONLY a number from -1.0 (very negative) to 1.0 (very positive). Example: 0.6",
                user=f"Score sentiment: {text[:300]}",
                agent="viper",
                task_type="fast",
                max_tokens=10,
                temperature=0.1,
            )
            if result:
                score = float(result.strip())
                return round(max(-1.0, min(1.0, score)), 2)
        except (ValueError, TypeError, Exception):
            pass

    # Last resort: keyword-based
    words = set(re.findall(r'\b\w+\b', text.lower()))
    pos = len(words & _POSITIVE_WORDS)
    neg = len(words & _NEGATIVE_WORDS)
    total = pos + neg
    if total == 0:
        return 0.0
    return round((pos - neg) / total, 2)
