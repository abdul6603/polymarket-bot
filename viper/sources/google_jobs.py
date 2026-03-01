"""Google Jobs scanner — scrapes Google search for freelance/contract gigs."""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field

import requests

log = logging.getLogger(__name__)

# Google search queries for freelance work
SEARCH_QUERIES = [
    "python freelance contract remote",
    "python bot developer freelance",
    "web scraping freelance project",
    "seo content writer freelance remote",
    "telegram bot developer hire",
    "python automation freelance",
]

SKILL_KEYWORDS = {
    "coding": [
        "python", "bot", "scraper", "scraping", "api",
        "automation", "flask", "django", "backend", "data",
        "telegram", "script",
    ],
    "content": [
        "seo", "content", "copywriting", "writer", "writing",
        "marketing", "blog", "article",
    ],
}

SKIP_WORDS = [
    "wordpress", "php", "laravel", "ios", "swift", "kotlin",
    "react native", "java developer", "c++ developer",
]


@dataclass
class GoogleJob:
    title: str = ""
    description: str = ""
    url: str = ""
    source: str = ""
    job_id: str = ""
    matched_skills: list[str] = field(default_factory=list)
    category: str = ""


def scan_google_jobs() -> list[GoogleJob]:
    """Search Google for freelance job listings matching our skills."""
    jobs: list[GoogleJob] = []
    seen_urls: set[str] = set()

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }

    for query in SEARCH_QUERIES:
        try:
            resp = requests.get(
                "https://www.google.com/search",
                params={"q": query, "num": "15", "tbs": "qdr:w"},  # last week
                headers=headers,
                timeout=15,
            )

            if resp.status_code != 200:
                log.warning("[GOOGLE] HTTP %d for '%s'", resp.status_code, query)
                continue

            html = resp.text

            # Extract result titles and URLs
            results = re.findall(
                r'<a[^>]*href="/url\?q=(https?://[^&"]+)[^"]*"[^>]*>.*?<h3[^>]*>([^<]+)</h3>',
                html, re.DOTALL,
            )

            if not results:
                # Try alternative pattern
                results = re.findall(
                    r'<a[^>]*href="(https?://(?:www\.)?(?:upwork|freelancer|fiverr|peopleperhour|guru|toptal|contra)[^"]*)"[^>]*>.*?([^<]{10,100})',
                    html, re.DOTALL,
                )

            for url, title in results[:10]:
                url = url.split("&")[0]  # Clean tracking params
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                title = re.sub(r"<[^>]+>", "", title).strip()
                text = f"{title} {query}".lower()

                if any(skip in text for skip in SKIP_WORDS):
                    continue

                coding_hits = [k for k in SKILL_KEYWORDS["coding"] if k in text]
                content_hits = [k for k in SKILL_KEYWORDS["content"] if k in text]

                if coding_hits and content_hits:
                    cat, matched = "mixed", coding_hits + content_hits
                elif coding_hits:
                    cat, matched = "coding", coding_hits
                elif content_hits:
                    cat, matched = "content", content_hits
                else:
                    continue

                jobs.append(GoogleJob(
                    title=title,
                    description="",
                    url=url,
                    source="Google",
                    job_id=url[-32:],
                    matched_skills=matched,
                    category=cat,
                ))

            time.sleep(5)  # Respect Google rate limits

        except Exception as e:
            log.error("[GOOGLE] Error for '%s': %s", query, str(e)[:200])
            continue

    log.info("[GOOGLE] Found %d matching jobs from %d queries", len(jobs), len(SEARCH_QUERIES))
    return jobs
