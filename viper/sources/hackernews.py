"""Hacker News "Who's Hiring" scanner — monthly goldmine threads via Algolia API."""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field

import requests

log = logging.getLogger(__name__)

# Algolia HN Search API — free, no key needed
HN_SEARCH_URL = "https://hn.algolia.com/api/v1/search"

SKILL_KEYWORDS = {
    "coding": [
        "python", "bot", "scraper", "scraping", "api",
        "automation", "flask", "django", "backend", "data pipeline",
        "devops", "scripting", "etl",
    ],
    "content": [
        "seo", "content", "copywriting", "writer", "writing",
        "marketing", "blog", "technical writer",
    ],
}

SKIP_WORDS = [
    "wordpress", "php", "ios", "swift", "kotlin", "java ",
    "c++", "rust ", "golang", "onsite only", "no remote",
]


@dataclass
class HNJob:
    title: str = ""
    text: str = ""
    url: str = ""
    comment_id: str = ""
    parent_id: str = ""
    matched_skills: list[str] = field(default_factory=list)
    category: str = ""
    author: str = ""


def _extract_title(text: str) -> str:
    """Extract company/role from first line of HN hiring comment."""
    first_line = text.split("\n")[0].strip()
    first_line = re.sub(r"<[^>]+>", "", first_line)
    return first_line[:120] if first_line else "HN Hiring Post"


def _extract_url(text: str) -> str:
    """Pull first URL from the comment."""
    m = re.search(r'href="(https?://[^"]+)"', text)
    if m:
        return m.group(1)
    m = re.search(r"(https?://\S+)", text)
    if m:
        return m.group(1)
    return ""


def scan_hackernews() -> list[HNJob]:
    """Search HN for recent 'Who is Hiring' comments matching our skills."""
    jobs: list[HNJob] = []

    try:
        # Find the latest "Who is hiring?" thread
        resp = requests.get(
            HN_SEARCH_URL,
            params={
                "query": "Ask HN: Who is hiring",
                "tags": "story",
                "numericFilters": "created_at_i>%d" % (time.time() - 45 * 86400),
            },
            timeout=15,
        )

        if resp.status_code != 200:
            log.warning("[HN] Search HTTP %d", resp.status_code)
            return []

        stories = resp.json().get("hits", [])
        hiring_story = None
        for s in stories:
            title = (s.get("title") or "").lower()
            if "who is hiring" in title or "who's hiring" in title:
                hiring_story = s
                break

        if not hiring_story:
            log.info("[HN] No recent 'Who is hiring' thread found")
            return []

        story_id = hiring_story.get("objectID", "")
        log.info("[HN] Found hiring thread: %s (id=%s)", hiring_story.get("title", ""), story_id)

        # Fetch comments (children of this story)
        resp2 = requests.get(
            HN_SEARCH_URL,
            params={
                "tags": f"comment,story_{story_id}",
                "hitsPerPage": 100,
            },
            timeout=15,
        )

        if resp2.status_code != 200:
            log.warning("[HN] Comments HTTP %d", resp2.status_code)
            return []

        comments = resp2.json().get("hits", [])

        for comment in comments:
            text = comment.get("comment_text", "") or ""
            if len(text) < 50:
                continue

            clean = re.sub(r"<[^>]+>", " ", text).lower()

            if any(skip in clean for skip in SKIP_WORDS):
                continue

            # Must mention REMOTE
            if "remote" not in clean:
                continue

            coding_hits = [k for k in SKILL_KEYWORDS["coding"] if k in clean]
            content_hits = [k for k in SKILL_KEYWORDS["content"] if k in clean]

            if coding_hits and content_hits:
                cat, matched = "mixed", coding_hits + content_hits
            elif coding_hits:
                cat, matched = "coding", coding_hits
            elif content_hits:
                cat, matched = "content", content_hits
            else:
                continue

            cid = comment.get("objectID", "")
            title = _extract_title(text)
            apply_url = _extract_url(text)
            hn_url = f"https://news.ycombinator.com/item?id={cid}"

            jobs.append(HNJob(
                title=title,
                text=re.sub(r"<[^>]+>", " ", text)[:500].strip(),
                url=apply_url or hn_url,
                comment_id=cid,
                parent_id=story_id,
                matched_skills=matched,
                category=cat,
                author=comment.get("author", ""),
            ))

    except Exception as e:
        log.error("[HN] Error: %s", str(e)[:200])

    log.info("[HN] Found %d matching remote jobs", len(jobs))
    return jobs
