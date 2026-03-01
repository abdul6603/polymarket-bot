"""We Work Remotely — RSS feed scanner for premium remote jobs."""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field

import feedparser

log = logging.getLogger(__name__)

# WWR RSS feeds by category
RSS_FEEDS = [
    "https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-full-stack-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
    "https://weworkremotely.com/categories/remote-copywriting-jobs.rss",
]

SKILL_KEYWORDS = {
    "coding": [
        "python", "bot", "scraper", "scraping", "api",
        "automation", "flask", "django", "backend", "data",
        "devops", "scripting", "pipeline",
    ],
    "content": [
        "seo", "content", "copywriting", "writer", "writing",
        "marketing", "blog", "article",
    ],
}

SKIP_WORDS = [
    "wordpress", "php", "laravel", "ios", "swift", "kotlin",
    "react native", "java ", "c++", "rust ", "golang",
]


@dataclass
class WWRJob:
    title: str = ""
    company: str = ""
    description: str = ""
    url: str = ""
    published: str = ""
    job_id: str = ""
    matched_skills: list[str] = field(default_factory=list)
    category: str = ""


def _clean_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).strip()


def scan_weworkremotely() -> list[WWRJob]:
    """Parse We Work Remotely RSS feeds for matching jobs."""
    jobs: list[WWRJob] = []
    seen_urls: set[str] = set()

    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:15]:
                url = entry.get("link", "")
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)

                title = entry.get("title", "")
                desc = _clean_html(entry.get("description", ""))[:500]
                published = entry.get("published", "")
                company = entry.get("author", "") or ""

                text = f"{title} {desc}".lower()

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

                jobs.append(WWRJob(
                    title=title.strip(),
                    company=company.strip(),
                    description=desc.strip(),
                    url=url,
                    published=published,
                    job_id=url.split("/")[-1] if "/" in url else url[-16:],
                    matched_skills=matched,
                    category=cat,
                ))

            time.sleep(1)

        except Exception as e:
            log.error("[WWR] Error parsing feed: %s", str(e)[:200])
            continue

    log.info("[WWR] Found %d matching jobs from %d feeds", len(jobs), len(RSS_FEEDS))
    return jobs
