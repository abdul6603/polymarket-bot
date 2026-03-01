"""Reddit job scanner — scrapes r/forhire, r/slavelabour, r/freelance."""
from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field

import praw

log = logging.getLogger(__name__)

SUBREDDITS = ["forhire", "slavelabour", "freelance"]
HIRING_PATTERNS = re.compile(
    r"\[hiring\]|\[paid\]|looking\s+for|need\s+a?\s*(developer|coder|writer|scraper|bot)",
    re.IGNORECASE,
)
SKIP_KEYWORDS = [
    "wordpress", "elementor", "shopify", "wix", "php", "laravel",
    "graphic design", "logo", "video editing", "ios", "swift", "kotlin",
]

SKILL_KEYWORDS = {
    "coding": [
        "python", "bot", "scraper", "scraping", "api",
        "script", "telegram", "discord", "flask", "django", "selenium",
        "web scraping", "data pipeline", "backend", "developer", "coder",
        "programming", "software", "n8n", "zapier", "airtable",
    ],
    "content": [
        "seo", "article", "blog", "content", "copywriting", "writer",
        "writing", "ghostwriter", "editor", "proofreading", "marketing",
        "social media", "newsletter", "email",
    ],
}
MAX_POSTS_PER_SUB = 30
POST_AGE_LIMIT = 86400 * 2  # 2 days


@dataclass
class RedditJob:
    title: str = ""
    body: str = ""
    url: str = ""
    subreddit: str = ""
    author: str = ""
    created_utc: float = 0.0
    score: int = 0
    job_id: str = ""
    budget_hint: str = ""
    matched_skills: list[str] = field(default_factory=list)
    category: str = ""  # coding | content | mixed


def _extract_budget(text: str) -> str:
    """Try to pull a budget hint from post text."""
    patterns = [
        r"\$\d[\d,]*(?:\.\d{2})?",
        r"\d+\s*(?:usd|USD|dollars)",
        r"budget[:\s]+\$?\d[\d,]*",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            return m.group(0)
    return ""


def _classify_post(title: str, body: str) -> tuple[str, list[str]]:
    """Classify post as coding/content/mixed and return matched skills."""
    text = f"{title} {body}".lower()

    for skip in SKIP_KEYWORDS:
        if skip in text:
            return "", []

    coding_hits = [k for k in SKILL_KEYWORDS["coding"] if k in text]
    content_hits = [k for k in SKILL_KEYWORDS["content"] if k in text]

    if coding_hits and content_hits:
        return "mixed", coding_hits + content_hits
    elif coding_hits:
        return "coding", coding_hits
    elif content_hits:
        return "content", content_hits
    return "", []


def scan_reddit() -> list[RedditJob]:
    """Scan hiring subreddits for freelance jobs. Returns list of RedditJob."""
    client_id = os.getenv("REDDIT_CLIENT_ID", "")
    client_secret = os.getenv("REDDIT_CLIENT_SECRET", "")
    user_agent = os.getenv("REDDIT_USER_AGENT", "ViperJobHunter/1.0")

    if not client_id or not client_secret:
        log.warning("[REDDIT] Missing REDDIT_CLIENT_ID or REDDIT_CLIENT_SECRET")
        return []

    try:
        reddit = praw.Reddit(
            client_id=client_id,
            client_secret=client_secret,
            user_agent=user_agent,
        )
    except Exception as e:
        log.error("[REDDIT] Failed to init PRAW: %s", str(e)[:200])
        return []

    jobs: list[RedditJob] = []
    now = time.time()

    for sub_name in SUBREDDITS:
        try:
            subreddit = reddit.subreddit(sub_name)
            for post in subreddit.new(limit=MAX_POSTS_PER_SUB):
                # Skip old posts
                if now - post.created_utc > POST_AGE_LIMIT:
                    continue

                title = post.title or ""
                body = post.selftext or ""
                full_text = f"{title} {body}"

                # Only hiring posts
                if not HIRING_PATTERNS.search(full_text):
                    continue

                category, matched = _classify_post(title, body)
                if not category:
                    continue

                budget = _extract_budget(full_text)

                jobs.append(RedditJob(
                    title=title.strip(),
                    body=body[:500].strip(),
                    url=f"https://reddit.com{post.permalink}",
                    subreddit=sub_name,
                    author=str(post.author or ""),
                    created_utc=post.created_utc,
                    score=post.score,
                    job_id=post.id,
                    budget_hint=budget,
                    matched_skills=matched,
                    category=category,
                ))

            time.sleep(2)  # Rate limit between subreddits

        except Exception as e:
            log.error("[REDDIT] Error scanning r/%s: %s", sub_name, str(e)[:200])
            continue

    log.info("[REDDIT] Found %d matching jobs across %d subreddits", len(jobs), len(SUBREDDITS))
    return jobs
