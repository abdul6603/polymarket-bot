"""RemoteOK job scanner — free JSON API, high-quality remote jobs."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import requests

log = logging.getLogger(__name__)

REMOTEOK_API = "https://remoteok.com/api"

SKILL_KEYWORDS = {
    "coding": [
        "python", "bot", "scraper", "scraping", "api",
        "script", "telegram", "discord", "flask", "django", "selenium",
        "backend", "data pipeline", "n8n", "automation", "devops",
    ],
    "content": [
        "seo", "article", "blog", "content", "copywriting", "writer",
        "writing", "marketing", "newsletter",
    ],
}

SKIP_TAGS = {
    "wordpress", "php", "laravel", "ios", "swift", "kotlin",
    "react native", "unity", "graphic design", "video",
    "shopify", "drupal", "java", "c++", "rust", "go",
}


@dataclass
class RemoteOKJob:
    title: str = ""
    company: str = ""
    description: str = ""
    url: str = ""
    salary_min: int = 0
    salary_max: int = 0
    tags: list[str] = field(default_factory=list)
    job_id: str = ""
    matched_skills: list[str] = field(default_factory=list)
    category: str = ""
    date: str = ""


def scan_remoteok() -> list[RemoteOKJob]:
    """Fetch latest jobs from RemoteOK JSON API."""
    jobs: list[RemoteOKJob] = []

    try:
        resp = requests.get(
            REMOTEOK_API,
            headers={"User-Agent": "ViperJobHunter/1.0"},
            timeout=15,
        )
        if resp.status_code != 200:
            log.warning("[REMOTEOK] HTTP %d", resp.status_code)
            return []

        data = resp.json()
        # First item is metadata, skip it
        listings = data[1:] if len(data) > 1 else []

        for item in listings[:50]:
            title = item.get("position", "")
            company = item.get("company", "")
            desc = item.get("description", "")[:500]
            tags = [t.lower() for t in item.get("tags", [])]
            url = item.get("url", "")
            job_id = str(item.get("id", ""))
            date = item.get("date", "")
            salary_min = item.get("salary_min", 0) or 0
            salary_max = item.get("salary_max", 0) or 0

            # Skip non-matching tags
            if any(skip in tags for skip in SKIP_TAGS):
                continue

            text = f"{title} {desc} {' '.join(tags)}".lower()

            coding_hits = [k for k in SKILL_KEYWORDS["coding"] if k in text]
            content_hits = [k for k in SKILL_KEYWORDS["content"] if k in text]

            if coding_hits and content_hits:
                category, matched = "mixed", coding_hits + content_hits
            elif coding_hits:
                category, matched = "coding", coding_hits
            elif content_hits:
                category, matched = "content", content_hits
            else:
                continue

            if url and not url.startswith("http"):
                url = f"https://remoteok.com{url}"

            jobs.append(RemoteOKJob(
                title=title.strip(),
                company=company.strip(),
                description=desc.strip(),
                url=url,
                salary_min=salary_min,
                salary_max=salary_max,
                tags=tags,
                job_id=job_id,
                matched_skills=matched,
                category=category,
                date=date,
            ))

    except Exception as e:
        log.error("[REMOTEOK] Error: %s", str(e)[:200])

    log.info("[REMOTEOK] Found %d matching jobs", len(jobs))
    return jobs
