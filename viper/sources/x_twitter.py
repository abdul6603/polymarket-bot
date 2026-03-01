"""X/Twitter job scanner — searches for freelance gigs posted on X."""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field

import requests

log = logging.getLogger(__name__)

# Search queries for X (via Nitter instances for public access)
NITTER_INSTANCES = [
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
    "https://nitter.net",
]

SEARCH_QUERIES = [
    "hiring python freelance",
    "need python developer",
    "looking for scraper developer",
    "hiring bot developer",
    "freelance seo writer needed",
    "hiring automation developer remote",
    "#hiring python",
    "#freelance python bot",
]

SKILL_KEYWORDS = {
    "coding": [
        "python", "bot", "scraper", "scraping", "api",
        "automation", "telegram", "discord", "flask",
        "backend", "data", "script",
    ],
    "content": [
        "seo", "content", "copywriting", "writer", "writing",
        "marketing", "blog", "article",
    ],
}

SKIP_WORDS = [
    "wordpress", "php", "ios", "swift", "kotlin",
    "react native", "java ", "c++",
]


@dataclass
class XJob:
    title: str = ""
    text: str = ""
    url: str = ""
    author: str = ""
    job_id: str = ""
    matched_skills: list[str] = field(default_factory=list)
    category: str = ""


def _try_nitter_search(query: str) -> list[dict]:
    """Try searching via Nitter instances."""
    for instance in NITTER_INSTANCES:
        try:
            resp = requests.get(
                f"{instance}/search",
                params={"f": "tweets", "q": query},
                headers={"User-Agent": "ViperJobHunter/1.0"},
                timeout=10,
            )
            if resp.status_code == 200:
                html = resp.text
                # Extract tweets
                tweets = re.findall(
                    r'class="tweet-content[^"]*"[^>]*>([^<]+(?:<[^>]+>[^<]*)*)</div>',
                    html, re.DOTALL,
                )
                results = []
                for tweet_html in tweets[:10]:
                    clean = re.sub(r"<[^>]+>", " ", tweet_html).strip()
                    if len(clean) > 30:
                        results.append({"text": clean})
                if results:
                    return results
        except Exception:
            continue
    return []


def scan_x() -> list[XJob]:
    """Search X/Twitter for freelance job posts via public search."""
    jobs: list[XJob] = []
    seen_texts: set[str] = set()

    for query in SEARCH_QUERIES:
        try:
            results = _try_nitter_search(query)

            for item in results:
                text = item.get("text", "")
                text_key = text[:80].lower()
                if text_key in seen_texts:
                    continue
                seen_texts.add(text_key)

                lower = text.lower()

                if any(skip in lower for skip in SKIP_WORDS):
                    continue

                # Must be hiring/looking, not someone looking for work
                if not any(w in lower for w in ["hiring", "looking for", "need a", "need someone", "seeking"]):
                    continue

                coding_hits = [k for k in SKILL_KEYWORDS["coding"] if k in lower]
                content_hits = [k for k in SKILL_KEYWORDS["content"] if k in lower]

                if coding_hits and content_hits:
                    cat, matched = "mixed", coding_hits + content_hits
                elif coding_hits:
                    cat, matched = "coding", coding_hits
                elif content_hits:
                    cat, matched = "content", content_hits
                else:
                    continue

                jobs.append(XJob(
                    title=text[:100].strip(),
                    text=text[:500].strip(),
                    url="",  # Nitter doesn't give direct tweet URLs easily
                    author="",
                    job_id=str(hash(text_key))[:16],
                    matched_skills=matched,
                    category=cat,
                ))

            time.sleep(3)

        except Exception as e:
            log.error("[X] Error for '%s': %s", query, str(e)[:200])
            continue

    log.info("[X] Found %d matching jobs from %d queries", len(jobs), len(SEARCH_QUERIES))
    return jobs
