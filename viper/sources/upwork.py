"""Upwork job scanner — scrapes Upwork public search API."""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field

import requests

log = logging.getLogger(__name__)

# Search queries matching our skills
SEARCH_QUERIES = [
    "python bot",
    "python scraper",
    "python automation",
    "telegram bot",
    "web scraping",
    "seo article writing",
    "seo content strategy",
]

UPWORK_SEARCH_URL = "https://www.upwork.com/ab/jobs/search/url"

SKILL_KEYWORDS = {
    "coding": [
        "python", "bot", "scraper", "scraping", "automation", "api",
        "script", "telegram", "discord", "flask", "selenium", "n8n",
        "developer", "backend", "data pipeline",
    ],
    "content": [
        "seo", "article", "blog", "content", "copywriting", "writer",
        "writing", "ghostwriter", "marketing", "newsletter",
    ],
}


@dataclass
class UpworkJob:
    title: str = ""
    description: str = ""
    url: str = ""
    published: str = ""
    budget_hint: str = ""
    job_id: str = ""
    matched_skills: list[str] = field(default_factory=list)
    category: str = ""


def _extract_budget(text: str) -> str:
    patterns = [
        r"\$\d[\d,]*(?:\.\d{2})?",
        r"Budget:\s*\$?\d[\d,]*",
        r"Hourly Range:\s*\$\d+-\$\d+",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            return m.group(0)
    return ""


def _classify(title: str, desc: str) -> tuple[str, list[str]]:
    text = f"{title} {desc}".lower()
    coding_hits = [k for k in SKILL_KEYWORDS["coding"] if k in text]
    content_hits = [k for k in SKILL_KEYWORDS["content"] if k in text]

    if coding_hits and content_hits:
        return "mixed", coding_hits + content_hits
    elif coding_hits:
        return "coding", coding_hits
    elif content_hits:
        return "content", content_hits
    return "", []


def _clean_html(text: str) -> str:
    """Strip HTML tags."""
    return re.sub(r"<[^>]+>", "", text).strip()


def scan_upwork() -> list[UpworkJob]:
    """Scrape Upwork search results for matching freelance jobs."""
    jobs: list[UpworkJob] = []
    seen_urls: set[str] = set()

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml",
    }

    for query in SEARCH_QUERIES:
        try:
            resp = requests.get(
                "https://www.upwork.com/search/jobs/",
                params={"q": query, "sort": "recency", "per_page": "20"},
                headers=headers,
                timeout=15,
            )

            if resp.status_code != 200:
                log.warning("[UPWORK] HTTP %d for query '%s'", resp.status_code, query)
                continue

            # Extract job data from HTML using regex (lightweight, no BS4 needed)
            html = resp.text

            # Find job titles and links
            title_pattern = r'<a[^>]*href="(/jobs/[^"]*)"[^>]*>([^<]+)</a>'
            matches = re.findall(title_pattern, html)

            for link, title in matches[:15]:
                url = f"https://www.upwork.com{link}"
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                title = _clean_html(title).strip()
                category, matched = _classify(title, "")
                if not category:
                    continue

                job_id = link.split("~")[-1] if "~" in link else link[-16:]

                jobs.append(UpworkJob(
                    title=title,
                    description="",
                    url=url,
                    budget_hint="",
                    job_id=job_id,
                    matched_skills=matched,
                    category=category,
                ))

            time.sleep(3)  # Respect rate limits

        except Exception as e:
            log.error("[UPWORK] Error for query '%s': %s", query, str(e)[:200])
            continue

    log.info("[UPWORK] Found %d matching jobs from %d queries", len(jobs), len(SEARCH_QUERIES))
    return jobs
