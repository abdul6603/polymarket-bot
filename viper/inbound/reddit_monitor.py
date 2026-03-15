"""Reddit Inbound Monitor — scans business + industry subreddits for buyer intent.

SEPARATE from viper/sources/reddit.py (which scans r/forhire for freelance jobs).
This monitors subreddits where business OWNERS discuss problems we solve.

Subreddits from spec:
  Business owner: r/smallbusiness, r/Entrepreneur, r/EntrepreneurRideAlong, r/SaaS
  AI/chatbot: r/chatbots, r/AIautomation, r/nocode, r/ChatGPT
  Industry: r/Dentistry, r/dentist, r/realestate, r/RealEstateAgents,
            r/HVAC, r/LawFirm, r/lawyers

Reddit Rules — MANDATORY:
  - 90/10 rule: 90% non-promotional activity
  - Build 25+ karma before ANY promotional activity
  - Never drop links in strict subreddits
  - Share case studies as stories, mention agency ONLY when directly asked

Uses Reddit JSON API (no API key needed for public posts).
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError
from zoneinfo import ZoneInfo

from viper.viper_q import score as viper_q_score, detect_niche as vq_detect_niche

log = logging.getLogger(__name__)

_TZ_ET = ZoneInfo("America/New_York")
_DATA_DIR = Path.home() / "polymarket-bot" / "data"
_SEEN_FILE = _DATA_DIR / "reddit_inbound_seen.json"
_INBOUND_LOG = _DATA_DIR / "inbound_leads.jsonl"

# ── Subreddits to Monitor ───────────────────────────────────────────

BUSINESS_SUBS = [
    "smallbusiness", "Entrepreneur", "EntrepreneurRideAlong", "SaaS",
]

AI_SUBS = [
    "chatbots", "nocode", "ChatGPT",
]

INDUSTRY_SUBS = [
    "Dentistry", "dentist", "realestate", "RealEstateAgents",
    "HVAC", "LawFirm", "lawyers",
]

ALL_SUBS = BUSINESS_SUBS + AI_SUBS + INDUSTRY_SUBS

# ── Buyer Intent Keywords ───────────────────────────────────────────

HIGH_INTENT = [
    "looking for chatbot", "need a chatbot", "chatbot for my business",
    "need automation", "automate my business", "ai for my business",
    "missed calls", "losing leads", "after hours calls",
    "chatbot developer needed", "recommend a chatbot", "who can build",
    "appointment scheduling bot", "booking automation",
    "virtual receptionist", "answering service",
    "patient scheduling", "client intake automation",
    "need help with", "looking for someone to build",
    "can anyone recommend", "does anyone use", "what chatbot",
    "how do i automate", "tired of missing", "no one answers",
]

# Filter OUT job seekers and self-promotion
SKIP_PATTERNS = [
    "i built", "i created", "check out my", "i made",
    "hiring", "job opening", "we're looking to hire",
    "i'm a developer", "available for work", "my portfolio",
    "sponsored", "affiliate link", "discount code",
]

_FETCH_TIMEOUT = 10
_MAX_POSTS = 25  # per subreddit
_POST_AGE_LIMIT = 86400 * 3  # 3 days


# ── Reddit JSON API (no auth needed) ───────────────────────────────

def _fetch_subreddit(sub: str) -> list[dict]:
    """Fetch recent posts from a subreddit using public JSON API."""
    url = f"https://www.reddit.com/r/{sub}/new.json?limit={_MAX_POSTS}"
    headers = {"User-Agent": "Viper-Inbound-Monitor/1.0"}
    try:
        req = Request(url, headers=headers)
        with urlopen(req, timeout=_FETCH_TIMEOUT) as resp:
            data = json.loads(resp.read())
        posts = []
        now = time.time()
        for child in data.get("data", {}).get("children", []):
            post = child.get("data", {})
            age = now - post.get("created_utc", 0)
            if age > _POST_AGE_LIMIT:
                continue
            posts.append({
                "id": post.get("id", ""),
                "title": post.get("title", ""),
                "body": (post.get("selftext", "") or "")[:1000],
                "url": f"https://reddit.com{post.get('permalink', '')}",
                "subreddit": sub,
                "author": post.get("author", ""),
                "created_utc": post.get("created_utc", 0),
                "score": post.get("score", 0),
                "num_comments": post.get("num_comments", 0),
            })
        return posts
    except (URLError, json.JSONDecodeError, OSError) as e:
        log.debug("[REDDIT] Failed to fetch r/%s: %s", sub, str(e)[:100])
        return []


def _has_buyer_intent(title: str, body: str) -> tuple[bool, list[str]]:
    """Check if post has buyer intent. Returns (is_match, matched_keywords)."""
    text = f"{title} {body}".lower()

    # Skip self-promotion and job posts
    for skip in SKIP_PATTERNS:
        if skip in text:
            return False, []

    matched = []
    for kw in HIGH_INTENT:
        if kw in text:
            matched.append(kw)

    return len(matched) > 0, matched


# ── Seen Tracking ───────────────────────────────────────────────────

def _load_seen() -> set:
    if _SEEN_FILE.exists():
        try:
            return set(json.loads(_SEEN_FILE.read_text()))
        except Exception:
            log.warning("Corrupted reddit seen file — starting fresh")
            return set()
    return set()


def _save_seen(seen: set) -> None:
    items = list(seen)
    if len(items) > 10000:
        items = items[-10000:]
    _SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    _SEEN_FILE.write_text(json.dumps(items))


def _log_lead(post: dict, matched: list[str], score: int, classification: str) -> None:
    record = {
        "ts": datetime.now(_TZ_ET).isoformat(),
        "title": post.get("title", ""),
        "url": post.get("url", ""),
        "source": "reddit",
        "subreddit": post.get("subreddit", ""),
        "author": post.get("author", ""),
        "score": score,
        "classification": classification,
        "niche": _detect_niche(post.get("title", "") + " " + post.get("body", "")),
        "signals": matched,
    }
    with open(_INBOUND_LOG, "a") as f:
        f.write(json.dumps(record) + "\n")


def _detect_niche(text: str) -> str:
    lower = text.lower()
    if any(w in lower for w in ["dentist", "dental", "patient", "practice"]):
        return "dental"
    if any(w in lower for w in ["real estate", "realtor", "listing", "showing"]):
        return "real_estate"
    if any(w in lower for w in ["hvac", "heating", "cooling", "plumb"]):
        return "hvac"
    if any(w in lower for w in ["lawyer", "law firm", "attorney", "legal"]):
        return "legal"
    if any(w in lower for w in ["med spa", "medspa", "aestheti"]):
        return "med_spa"
    return "general"


# ── TG Alert ────────────────────────────────────────────────────────

def _send_alert(post: dict, matched: list[str], score: int) -> None:
    try:
        from viper.tg_router import send as tg_send
    except ImportError:
        return

    niche = _detect_niche(post.get("title", "") + " " + post.get("body", ""))
    text = (
        f"🔥 <b>Viper Inbound — Reddit</b>\n\n"
        f"Sub: r/{post.get('subreddit', '?')}\n"
        f"Post: {post.get('title', 'N/A')[:100]}\n"
        f"URL: {post.get('url', 'N/A')}\n"
        f"Author: u/{post.get('author', '?')}\n"
        f"Niche: {niche.replace('_', ' ').title()}\n"
        f"Intent: {', '.join(matched[:3])}\n"
        f"Comments: {post.get('num_comments', 0)}\n\n"
        f"→ Reply <b>BID</b> or <b>SKIP</b>"
    )

    try:
        tg_send(text, channel="INBOUND")
    except Exception as e:
        log.error("[REDDIT] TG alert failed: %s", e)


# ── Main ────────────────────────────────────────────────────────────

def poll_reddit() -> dict:
    """Poll all monitored subreddits for buyer-intent posts.

    Returns summary dict.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    seen = _load_seen()
    stats = {"subs_polled": 0, "new_posts": 0, "matches": 0, "alerts": 0}

    # Fetch all subs in parallel
    sub_results = {}
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(_fetch_subreddit, sub): sub for sub in ALL_SUBS}
        for future in as_completed(futures):
            sub = futures[future]
            try:
                sub_results[sub] = future.result()
            except Exception:
                sub_results[sub] = []

    for sub, posts in sub_results.items():
        stats["subs_polled"] += 1

        for post in posts:
            post_id = post.get("id", "")
            if not post_id or post_id in seen:
                continue

            seen.add(post_id)
            stats["new_posts"] += 1

            is_match, matched = _has_buyer_intent(
                post.get("title", ""),
                post.get("body", ""),
            )

            if is_match:
                stats["matches"] += 1
                # Use unified VIPER-Q scoring
                result = viper_q_score(
                    post.get("title", ""),
                    post.get("body", ""),
                    metadata={"num_comments": post.get("num_comments", 0)},
                )
                if result["score"] >= 50:
                    _log_lead(post, matched, result["score"], result["classification"])
                    _send_alert(post, matched, result["score"])
                    stats["alerts"] += 1
                else:
                    _log_lead(post, matched, result["score"], result["classification"])

    _save_seen(seen)

    if stats["matches"] > 0:
        log.info(
            "[REDDIT] Inbound: %d subs, %d new posts, %d matches, %d alerts",
            stats["subs_polled"], stats["new_posts"], stats["matches"], stats["alerts"],
        )

    return stats
