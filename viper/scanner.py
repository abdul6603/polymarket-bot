"""Web Scanner — scan Reddit, Upwork for freelance gigs and opportunities."""
from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field

from bot.http_session import get_session

log = logging.getLogger(__name__)


@dataclass
class Opportunity:
    id: str = ""
    source: str = ""
    title: str = ""
    description: str = ""
    estimated_value_usd: float = 0.0
    effort_hours: float = 0.0
    urgency: str = "normal"  # low, normal, high, urgent
    confidence: float = 0.5
    url: str = ""
    category: str = ""
    tags: list[str] = field(default_factory=list)


def _make_id(source: str, title: str) -> str:
    """Generate a stable ID for deduplication."""
    raw = f"{source}:{title}".lower().strip()
    return hashlib.md5(raw.encode()).hexdigest()[:12]


# Reddit subreddits for gig hunting
_SUBREDDITS = ["forhire", "slavelabour", "algotrading"]
_REDDIT_KEYWORDS = ["python", "bot", "automation", "ai", "trading", "scraping", "api", "data"]


def scan_reddit(subreddits: list[str] | None = None) -> list[Opportunity]:
    """Scan Reddit JSON API (no auth needed) for freelance gigs."""
    subs = subreddits or _SUBREDDITS
    session = get_session()
    opps: list[Opportunity] = []

    for sub in subs:
        try:
            resp = session.get(
                f"https://www.reddit.com/r/{sub}/new.json",
                params={"limit": 25},
                headers={"User-Agent": "Viper/1.0"},
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
                body = (p.get("selftext") or "")[:500]
                combined = (title + " " + body).lower()

                # Check if it matches our skills
                if not any(kw in combined for kw in _REDDIT_KEYWORDS):
                    continue

                # Estimate value from title/body
                value = _estimate_value(title, body)
                effort = _estimate_effort(title, body)

                opp = Opportunity(
                    id=_make_id("reddit", title),
                    source=f"r/{sub}",
                    title=title[:200],
                    description=body[:500],
                    estimated_value_usd=value,
                    effort_hours=effort,
                    urgency="high" if "urgent" in combined or "asap" in combined else "normal",
                    confidence=0.6,
                    url=f"https://reddit.com{p.get('permalink', '')}",
                    category="freelance",
                    tags=[kw for kw in _REDDIT_KEYWORDS if kw in combined],
                )
                opps.append(opp)

        except Exception:
            log.exception("Failed to scan r/%s", sub)

    log.info("Reddit scan: found %d matching opportunities across %d subs", len(opps), len(subs))
    return opps


def scan_upwork(query: str = "python ai bot") -> list[Opportunity]:
    """Scan Upwork RSS/public listings for relevant gigs."""
    session = get_session()
    opps: list[Opportunity] = []

    try:
        resp = session.get(
            "https://www.upwork.com/ab/feed/jobs/rss",
            params={"q": query, "sort": "recency"},
            headers={"User-Agent": "Viper/1.0"},
            timeout=10,
        )
        if resp.status_code == 200:
            # Parse simple RSS — extract items
            text = resp.text
            items = text.split("<item>")[1:]  # skip header
            for item in items[:20]:
                title = _extract_tag(item, "title")
                desc = _extract_tag(item, "description")[:500]
                link = _extract_tag(item, "link")

                if not title:
                    continue

                value = _estimate_value(title, desc)
                effort = _estimate_effort(title, desc)

                opp = Opportunity(
                    id=_make_id("upwork", title),
                    source="upwork",
                    title=title[:200],
                    description=desc[:500],
                    estimated_value_usd=value,
                    effort_hours=effort,
                    urgency="normal",
                    confidence=0.7,
                    url=link,
                    category="freelance",
                    tags=["upwork"],
                )
                opps.append(opp)
        else:
            log.warning("Upwork RSS returned %d", resp.status_code)
    except Exception:
        log.exception("Failed to scan Upwork")

    log.info("Upwork scan: found %d opportunities", len(opps))
    return opps


def scan_all() -> list[Opportunity]:
    """Run all scanners, deduplicate, return combined."""
    all_opps: list[Opportunity] = []
    seen_ids: set[str] = set()

    for opp in scan_reddit() + scan_upwork():
        if opp.id not in seen_ids:
            seen_ids.add(opp.id)
            all_opps.append(opp)

    log.info("Total unique opportunities: %d", len(all_opps))
    return all_opps


def _extract_tag(xml: str, tag: str) -> str:
    """Extract text content from an XML tag."""
    start = xml.find(f"<{tag}>")
    end = xml.find(f"</{tag}>")
    if start == -1 or end == -1:
        return ""
    # Handle CDATA
    content = xml[start + len(tag) + 2:end]
    if content.startswith("<![CDATA["):
        content = content[9:]
    if content.endswith("]]>"):
        content = content[:-3]
    return content.strip()


def _estimate_value(title: str, body: str) -> float:
    """Rough value estimation from text signals."""
    combined = (title + " " + body).lower()
    # Look for dollar amounts
    import re
    amounts = re.findall(r'\$(\d+(?:,\d+)?(?:\.\d+)?)', combined)
    if amounts:
        try:
            return max(float(a.replace(",", "")) for a in amounts)
        except ValueError:
            pass

    # Keyword-based estimation
    if any(w in combined for w in ["bot", "trading", "automation", "ai"]):
        return 300.0
    if any(w in combined for w in ["scraping", "data", "api"]):
        return 150.0
    return 100.0


def _estimate_effort(title: str, body: str) -> float:
    """Rough effort estimation in hours."""
    combined = (title + " " + body).lower()
    if any(w in combined for w in ["simple", "quick", "small", "easy"]):
        return 4.0
    if any(w in combined for w in ["complex", "full", "enterprise", "large"]):
        return 40.0
    return 12.0
