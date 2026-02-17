"""Soren Opportunity Scout — actively hunts brand deals, affiliates, and trending content.

Three scanning layers:
  1. Brand/Deal Scouting — searches for brands seeking dark motivation / stoic influencers
  2. Trend Capitalizer — finds viral moments Soren can jump on for content
  3. Affiliate/Sponsorship Finder — identifies programs in Soren's niche

Saves scored opportunities to data/soren_opportunities.json.
Top opportunities get pushed to Shelby's task queue with [VIPER] prefix.
"""
from __future__ import annotations

import json
import hashlib
import logging
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

from bot.http_session import get_session

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
SOREN_OPPS_FILE = DATA_DIR / "soren_opportunities.json"

# ─── Data Model ──────────────────────────────────────────────────────

@dataclass
class SorenOpportunity:
    id: str = ""
    type: str = ""           # brand_deal, affiliate, trending_content, collab, ad_revenue
    title: str = ""
    description: str = ""
    source: str = ""         # tavily, reddit, manual
    url: str = ""
    estimated_value: str = ""  # "$50-200/post", "$100-500/mo"
    fit_score: int = 0       # 0-100 how well it matches Soren's brand
    urgency: str = "low"     # low, medium, high
    action: str = ""         # what Jordan/Soren needs to do
    category: str = ""       # fitness, mindset, stoic, books, supplements, apps
    timestamp: float = 0.0
    raw_data: dict = field(default_factory=dict)


def _make_id(source: str, title: str) -> str:
    raw = f"{source}:{title}".lower().strip()
    return hashlib.md5(raw.encode()).hexdigest()[:16]


# ─── Brand Keyword Config ────────────────────────────────────────────

# Soren's brand DNA — used for fit scoring
_BRAND_KEYWORDS = {
    "core": ["motivation", "discipline", "stoic", "warrior", "grind", "lone wolf",
             "dark", "mindset", "self improvement", "mental strength", "resilience",
             "hustle", "focus", "dark motivation", "sigma", "masculinity"],
    "fitness": ["gym", "workout", "bodybuilding", "fitness", "supplement", "protein",
                "creatine", "pre-workout", "athletic", "training", "lifting"],
    "mindset": ["meditation", "journaling", "habits", "productivity", "stoicism",
                "marcus aurelius", "philosophy", "morning routine", "cold shower",
                "dopamine", "no fap", "self discipline"],
    "content": ["tiktok", "instagram", "reels", "short form", "viral", "content creator",
                "influencer", "ugc", "brand deal", "sponsorship", "affiliate"],
}

# Tavily queries for each scan layer
_BRAND_QUERIES = [
    "brands looking for dark motivation influencers 2026",
    "fitness supplement brand ambassador program small creators",
    "stoic mindset app sponsorship influencer partnership",
    "self improvement brand deals micro influencer",
]

_TREND_QUERIES = [
    "viral motivational content tiktok trending today",
    "stoic quotes trending social media this week",
    "underdog comeback story viral 2026",
    "dark motivation trend tiktok instagram reels",
]

_AFFILIATE_QUERIES = [
    "best affiliate programs fitness supplements 2026",
    "self improvement book affiliate program commission",
    "meditation app affiliate partnership creators",
    "journaling productivity app influencer program",
]

# Reddit subs for Soren opportunities
_SOREN_SUBS = ["influencermarketing", "UGCcreators", "Entrepreneur", "content_marketing"]


# ─── Scanners ────────────────────────────────────────────────────────

def _scan_tavily_opportunities(api_key: str, queries: list[str], opp_type: str) -> list[SorenOpportunity]:
    """Run Tavily queries and extract Soren opportunities."""
    if not api_key:
        return []

    session = get_session()
    opps: list[SorenOpportunity] = []

    for query in queries[:2]:  # Max 2 per type to save credits
        try:
            resp = session.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": api_key,
                    "query": query,
                    "search_depth": "basic",
                    "max_results": 5,
                    "include_answer": True,
                },
                timeout=15,
            )
            if resp.status_code != 200:
                continue

            data = resp.json()
            for r in data.get("results", []):
                title = r.get("title", "")
                content = r.get("content", "")[:500]
                url = r.get("url", "")

                if not title:
                    continue

                # Score brand fit
                fit = _score_brand_fit(title + " " + content)
                if fit < 10:
                    continue  # Not relevant enough

                # Determine category
                category = _categorize_opportunity(title + " " + content)
                urgency = "high" if any(w in title.lower() for w in ["now", "limited", "deadline", "apply"]) else "medium"

                opps.append(SorenOpportunity(
                    id=_make_id("tavily", title),
                    type=opp_type,
                    title=title[:200],
                    description=content[:400],
                    source="tavily",
                    url=url,
                    estimated_value=_estimate_value(opp_type, fit),
                    fit_score=fit,
                    urgency=urgency,
                    action=_suggest_action(opp_type),
                    category=category,
                    timestamp=time.time(),
                ))

        except Exception:
            log.exception("Tavily soren scout failed for: %s", query[:40])

    return opps


def _scan_reddit_opportunities() -> list[SorenOpportunity]:
    """Scan Reddit for brand deal posts, collab requests, trend signals."""
    session = get_session()
    opps: list[SorenOpportunity] = []

    for sub in _SOREN_SUBS:
        try:
            resp = session.get(
                f"https://www.reddit.com/r/{sub}/hot.json",
                params={"limit": 10},
                headers={"User-Agent": "Viper-SorenScout/1.0"},
                timeout=10,
            )
            if resp.status_code != 200:
                continue

            posts = resp.json().get("data", {}).get("children", [])
            for post in posts:
                p = post.get("data", {})
                title = p.get("title", "")
                body = (p.get("selftext") or "")[:400]
                score = p.get("score", 0)
                url = f"https://reddit.com{p.get('permalink', '')}"

                if score < 10:
                    continue

                combined = title + " " + body
                fit = _score_brand_fit(combined)
                if fit < 8:
                    continue

                # Determine type from content
                opp_type = "brand_deal"
                lower = combined.lower()
                if any(w in lower for w in ["affiliate", "commission", "referral"]):
                    opp_type = "affiliate"
                elif any(w in lower for w in ["trend", "viral", "going viral", "blowing up"]):
                    opp_type = "trending_content"
                elif any(w in lower for w in ["collab", "collaboration", "looking for creator"]):
                    opp_type = "collab"

                category = _categorize_opportunity(combined)

                opps.append(SorenOpportunity(
                    id=_make_id("reddit", title),
                    type=opp_type,
                    title=title[:200],
                    description=body[:400] if body else f"r/{sub} | Score: {score}",
                    source=f"reddit/r/{sub}",
                    url=url,
                    estimated_value=_estimate_value(opp_type, fit),
                    fit_score=fit,
                    urgency="medium" if score > 50 else "low",
                    action=_suggest_action(opp_type),
                    category=category,
                    timestamp=time.time(),
                    raw_data={"score": score, "sub": sub},
                ))

        except Exception:
            log.exception("Reddit soren scout failed for r/%s", sub)

    return opps


# ─── Combined Scanner ────────────────────────────────────────────────

def scout_soren_opportunities(tavily_key: str) -> list[dict]:
    """Run all Soren opportunity scanners and return scored, deduped results.

    Budget: 6 Tavily queries per scout run (2 per layer x 3 layers).
    Runs every 6th Viper cycle (~30 min).
    """
    all_opps: list[SorenOpportunity] = []
    seen_ids: set[str] = set()

    def _dedup_add(items: list[SorenOpportunity]):
        for item in items:
            if item.id not in seen_ids:
                seen_ids.add(item.id)
                all_opps.append(item)

    # Layer 1: Brand deals
    _dedup_add(_scan_tavily_opportunities(tavily_key, _BRAND_QUERIES, "brand_deal"))

    # Layer 2: Trending content
    _dedup_add(_scan_tavily_opportunities(tavily_key, _TREND_QUERIES, "trending_content"))

    # Layer 3: Affiliate programs
    _dedup_add(_scan_tavily_opportunities(tavily_key, _AFFILIATE_QUERIES, "affiliate"))

    # Layer 4: Reddit (free)
    _dedup_add(_scan_reddit_opportunities())

    # Sort by fit score
    all_opps.sort(key=lambda o: o.fit_score, reverse=True)

    log.info("Soren scout: %d opportunities found (fit >= 15)", len(all_opps))

    # Convert to dicts and save
    opp_dicts = [asdict(o) for o in all_opps[:50]]
    _save_soren_opportunities(opp_dicts)

    # Submit top opportunities to brand channel for Soren assessment
    try:
        from shared.brand_channel import submit_opportunity
        for opp in opp_dicts[:10]:
            if opp.get("fit_score", 0) >= 15:
                submit_opportunity(opp)
    except Exception:
        log.exception("Brand channel submission failed")

    return opp_dicts


def _save_soren_opportunities(opps: list[dict]) -> None:
    """Save Soren opportunities to disk, merging with existing unexpired ones."""
    DATA_DIR.mkdir(exist_ok=True)

    # Load existing to merge (keep unexpired)
    existing = []
    if SOREN_OPPS_FILE.exists():
        try:
            data = json.loads(SOREN_OPPS_FILE.read_text())
            existing = data.get("opportunities", [])
        except Exception:
            pass

    # Merge: new ones replace old with same ID, keep old unexpired (24h)
    now = time.time()
    by_id = {}
    for o in existing:
        if now - o.get("timestamp", 0) < 86400:  # 24h TTL
            by_id[o.get("id", "")] = o
    for o in opps:
        by_id[o.get("id", "")] = o  # New overwrites old

    merged = sorted(by_id.values(), key=lambda x: x.get("fit_score", 0), reverse=True)[:50]

    try:
        SOREN_OPPS_FILE.write_text(json.dumps({
            "opportunities": merged,
            "count": len(merged),
            "updated": now,
            "types": _count_by_type(merged),
        }, indent=2))
    except Exception:
        log.exception("Failed to save Soren opportunities")


def load_soren_opportunities() -> dict:
    """Load Soren opportunities from disk."""
    if not SOREN_OPPS_FILE.exists():
        return {"opportunities": [], "count": 0, "updated": 0, "types": {}}
    try:
        return json.loads(SOREN_OPPS_FILE.read_text())
    except Exception:
        return {"opportunities": [], "count": 0, "updated": 0, "types": {}}


# ─── Scoring & Classification Helpers ────────────────────────────────

def _score_brand_fit(text: str) -> int:
    """Score 0-100 how well this opportunity fits Soren's brand.

    Two-axis scoring:
      1. Niche fit (40%): does it mention Soren's themes (motivation, stoic, fitness)?
      2. Opportunity relevance (60%): is it actually a monetization opportunity?
    """
    text_lower = text.lower()

    # Axis 1: Niche keyword matching
    niche_hits = 0
    niche_total = 0
    for category, keywords in _BRAND_KEYWORDS.items():
        if category == "content":
            continue  # Don't count content keywords for niche fit
        weight = 3 if category == "core" else 2 if category in ("fitness", "mindset") else 1
        for kw in keywords:
            niche_total += weight
            if kw in text_lower:
                niche_hits += weight

    niche_score = (niche_hits / max(niche_total, 1)) * 100

    # Axis 2: Opportunity relevance — is this actually a deal/program/trend?
    opp_keywords = [
        ("brand deal", 15), ("sponsorship", 15), ("affiliate", 15), ("ambassador", 15),
        ("partnership", 12), ("commission", 12), ("collaborate", 10), ("creator program", 15),
        ("influencer", 10), ("ugc", 12), ("brand ambassador", 15), ("paid partnership", 15),
        ("nano creator", 12), ("micro influencer", 10), ("content creator", 10),
        ("apply now", 8), ("sign up", 6), ("earn money", 8), ("monetiz", 10),
        ("trending", 8), ("viral", 8), ("going viral", 10), ("blowing up", 8),
        ("reels", 6), ("tiktok", 6), ("short form", 6),
    ]
    opp_score = 0
    for kw, pts in opp_keywords:
        if kw in text_lower:
            opp_score += pts
    opp_score = min(100, opp_score)

    # Combined: 40% niche, 60% opportunity relevance
    raw = niche_score * 0.4 + opp_score * 0.6

    # Bonus for dead-on Soren matches
    if any(phrase in text_lower for phrase in ["dark motivation", "lone wolf", "sigma", "stoic mindset"]):
        raw = min(100, raw + 25)

    return min(100, max(0, int(raw)))


def _categorize_opportunity(text: str) -> str:
    """Classify opportunity into Soren-relevant category."""
    text_lower = text.lower()
    scores = {}
    for cat, keywords in _BRAND_KEYWORDS.items():
        scores[cat] = sum(1 for kw in keywords if kw in text_lower)
    if not scores or max(scores.values()) == 0:
        return "general"
    return max(scores, key=scores.get)


def _estimate_value(opp_type: str, fit_score: int) -> str:
    """Rough value estimate based on type and brand fit."""
    estimates = {
        "brand_deal": {80: "$200-500/post", 50: "$50-200/post", 0: "$20-50/post"},
        "affiliate": {80: "$200-1000/mo", 50: "$50-200/mo", 0: "$10-50/mo"},
        "trending_content": {80: "High viral potential", 50: "Medium reach", 0: "Low reach"},
        "collab": {80: "$100-300 + exposure", 50: "Exposure trade", 0: "Small collab"},
        "ad_revenue": {80: "$500+/mo", 50: "$100-500/mo", 0: "$10-100/mo"},
    }
    tiers = estimates.get(opp_type, estimates["brand_deal"])
    for threshold in sorted(tiers.keys(), reverse=True):
        if fit_score >= threshold:
            return tiers[threshold]
    return "Unknown"


def _suggest_action(opp_type: str) -> str:
    """Suggest next action for this opportunity type."""
    actions = {
        "brand_deal": "Review brand, DM or apply via link",
        "affiliate": "Sign up for program, create affiliate content",
        "trending_content": "Create content on this trend ASAP",
        "collab": "Reach out to creator for collab",
        "ad_revenue": "Check platform monetization requirements",
    }
    return actions.get(opp_type, "Review and assess")


def _count_by_type(opps: list[dict]) -> dict[str, int]:
    """Count opportunities by type."""
    counts: dict[str, int] = {}
    for o in opps:
        t = o.get("type", "other")
        counts[t] = counts.get(t, 0) + 1
    return counts
