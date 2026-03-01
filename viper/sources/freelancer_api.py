"""Freelancer.com job scanner — uses public search API."""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field

import requests

log = logging.getLogger(__name__)

SEARCH_QUERIES = [
    "python bot",
    "web scraping",
    "python automation",
    "telegram bot",
    "seo article writing",
    "api integration python",
    "data pipeline",
]

FREELANCER_SEARCH_URL = "https://www.freelancer.com/api/projects/0.1/projects/active/"

# Only accept clients from these regions
ALLOWED_COUNTRIES = {
    # US & Canada
    "us", "ca",
    # Europe
    "gb", "uk", "de", "fr", "nl", "se", "no", "dk", "fi", "ch", "at",
    "be", "ie", "es", "it", "pt", "pl", "cz", "ro", "hu", "hr", "bg",
    "sk", "si", "lt", "lv", "ee", "lu", "mt", "cy", "gr", "is",
    # Middle East
    "ae", "sa", "qa", "kw", "bh", "om", "jo", "lb", "il", "tr",
    # Australia/NZ (bonus — good clients)
    "au", "nz",
}

SKILL_KEYWORDS = {
    "coding": [
        "python", "bot", "scraper", "scraping", "api",
        "script", "telegram", "discord", "flask", "selenium",
        "backend", "data pipeline", "n8n",
    ],
    "content": [
        "seo", "article", "blog", "content", "copywriting", "writer",
        "writing", "ghostwriter", "marketing",
    ],
}

# Jobs containing these keywords get SKIPPED — not our niche
SKIP_KEYWORDS = [
    "wordpress", "elementor", "shopify", "wix", "squarespace",
    "graphic design", "logo design", "photoshop", "illustrator",
    "video editing", "animation", "3d model", "unity", "unreal",
    "ios", "swift", "kotlin", "android app", "react native",
    "php", "laravel", "drupal", "joomla", "magento",
]

# Freelancer currency_id → (code, rate_to_usd)
CURRENCY_MAP = {
    1: ("USD", 1.0),
    2: ("GBP", 1.27),
    3: ("EUR", 1.08),
    4: ("AUD", 0.64),
    5: ("HKD", 0.13),
    6: ("SGD", 0.75),
    7: ("NZD", 0.58),
    8: ("CAD", 0.72),
    9: ("INR", 0.012),
    10: ("SEK", 0.096),
    11: ("JPY", 0.0067),
    12: ("CNY", 0.14),
    13: ("PHP", 0.018),
    14: ("MYR", 0.22),
    15: ("THB", 0.029),
    16: ("ZAR", 0.055),
    17: ("BRL", 0.17),
    18: ("PLN", 0.25),
    19: ("ARS", 0.001),
    20: ("ILS", 0.28),
    21: ("MXN", 0.058),
}


@dataclass
class FreelancerJob:
    title: str = ""
    description: str = ""
    url: str = ""
    budget_min_usd: float = 0.0
    budget_max_usd: float = 0.0
    budget_min_raw: float = 0.0
    budget_max_raw: float = 0.0
    currency_code: str = "USD"
    bid_count: int = 0
    job_id: str = ""
    matched_skills: list[str] = field(default_factory=list)
    category: str = ""
    job_type: str = ""  # fixed | hourly
    client_country: str = ""  # 2-letter code


def _classify(title: str, desc: str) -> tuple[str, list[str]]:
    text = f"{title} {desc}".lower()

    # Skip non-matching niches
    for skip in SKIP_KEYWORDS:
        if skip in text:
            return "", []

    coding_hits = [k for k in SKILL_KEYWORDS["coding"] if k in text]
    content_hits = [k for k in SKILL_KEYWORDS["content"] if k in text]

    # "automation" alone is too broad — require another coding keyword too
    if coding_hits == ["automation"]:
        return "", []

    if coding_hits and content_hits:
        return "mixed", coding_hits + content_hits
    elif coding_hits:
        return "coding", coding_hits
    elif content_hits:
        return "content", content_hits
    return "", []


def _to_usd(amount: float, currency_id: int) -> float:
    """Convert any Freelancer currency to USD."""
    _, rate = CURRENCY_MAP.get(currency_id, ("???", 1.0))
    return round(amount * rate, 2)


def _currency_code(currency_id: int) -> str:
    code, _ = CURRENCY_MAP.get(currency_id, ("???", 1.0))
    return code


def scan_freelancer() -> list[FreelancerJob]:
    """Search Freelancer.com for matching active projects."""
    jobs: list[FreelancerJob] = []
    seen_ids: set[str] = set()

    for query in SEARCH_QUERIES:
        try:
            resp = requests.get(
                FREELANCER_SEARCH_URL,
                params={
                    "query": query,
                    "compact": "true",
                    "limit": 15,
                    "sort_field": "time_submitted",
                    "project_types[]": ["fixed", "hourly"],
                    "owners[]": "true",
                    "owner_details": "true",
                },
                headers={"User-Agent": "ViperJobHunter/1.0"},
                timeout=15,
            )

            if resp.status_code != 200:
                log.warning("[FREELANCER] HTTP %d for query '%s'", resp.status_code, query)
                continue

            data = resp.json()
            projects = data.get("result", {}).get("projects", [])

            for proj in projects:
                pid = str(proj.get("id", ""))
                if not pid or pid in seen_ids:
                    continue
                seen_ids.add(pid)

                title = proj.get("title", "")
                desc = proj.get("preview_description", "")
                budget = proj.get("budget", {})
                bid_count = proj.get("bid_stats", {}).get("bid_count", 0)

                if bid_count > 30:
                    continue

                # Filter by client country — block India explicitly
                owner = proj.get("owner", {})
                location = owner.get("location", {})
                country_code = location.get("country", {}).get("code", "").lower()
                if country_code == "in":
                    continue
                if country_code and country_code not in ALLOWED_COUNTRIES:
                    continue

                category, matched = _classify(title, desc)
                if not category:
                    continue

                currency_id = budget.get("currency_id", 1)
                raw_min = budget.get("minimum", 0)
                raw_max = budget.get("maximum", 0)
                usd_min = _to_usd(raw_min, currency_id)
                usd_max = _to_usd(raw_max, currency_id)

                # Skip jobs under $20 USD
                if usd_max > 0 and usd_max < 20:
                    continue

                seo_url = proj.get("seo_url", "")
                url = f"https://www.freelancer.com/projects/{seo_url}" if seo_url else ""

                job_type = proj.get("type", "fixed")

                jobs.append(FreelancerJob(
                    title=title.strip(),
                    description=desc[:500].strip(),
                    url=url,
                    budget_min_usd=usd_min,
                    budget_max_usd=usd_max,
                    budget_min_raw=raw_min,
                    budget_max_raw=raw_max,
                    currency_code=_currency_code(currency_id),
                    bid_count=bid_count,
                    job_id=pid,
                    matched_skills=matched,
                    category=category,
                    job_type=job_type,
                    client_country=country_code.upper(),
                ))

            time.sleep(2)

        except Exception as e:
            log.error("[FREELANCER] Error for query '%s': %s", query, str(e)[:200])
            continue

    log.info("[FREELANCER] Found %d matching jobs from %d queries", len(jobs), len(SEARCH_QUERIES))
    return jobs


def fetch_full_job(project_id: str) -> dict | None:
    """Fetch complete job details from Freelancer API by project ID."""
    try:
        resp = requests.get(
            f"https://www.freelancer.com/api/projects/0.1/projects/{project_id}/",
            params={"full_description": "true"},
            headers={"User-Agent": "ViperJobHunter/1.0"},
            timeout=15,
        )
        if resp.status_code == 200:
            return resp.json().get("result", {})
        log.warning("[FREELANCER] fetch_full_job HTTP %d for %s", resp.status_code, project_id)
        return None
    except Exception as e:
        log.error("[FREELANCER] fetch_full_job error: %s", str(e)[:200])
        return None
