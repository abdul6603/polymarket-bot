"""Viper Job Hunter — scans all inbound sources for freelance gigs.

Runs on a 30-min loop, classifies and scores jobs, deduplicates,
and sends top matches to Jordan on Telegram with bid suggestions.

Sources:
  - Hacker News "Who's Hiring" + "Freelancer?" threads (Algolia API, free)
  - Google Alerts RSS (8+ configured feeds)
  - Reddit (r/forhire, r/freelance, etc.) — requires PRAW creds
  - Indie Hackers (Firebase API)
  - Product Hunt RSS
  - n8n Community Jobs (Discourse RSS)
  - Make.com Hire a Pro (Discourse RSS)
  - RemoteOK JSON API
  - We Work Remotely RSS
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from viper.sources.remoteok import scan_remoteok
from viper.sources.weworkremotely import scan_weworkremotely
from viper.sources.hackernews import scan_hackernews
from viper.sources.google_alerts import scan_google_alerts
from viper.sources.reddit import scan_reddit
from viper.telegram_alerts import send_job_alert, send_summary
from viper.lead_writer import write_leads

# Optional sources — only on Pro (graceful skip on Air)
# Indie Hackers DISABLED (Mar 12 2026) — zero valid leads, all builder posts.
# Re-enable after buyer-vs-builder intent filter is built.
# try:
#     from viper.sources.indiehackers import scan_indiehackers
# except ImportError:
#     scan_indiehackers = None
try:
    from viper.sources.producthunt import scan_producthunt
except ImportError:
    scan_producthunt = None  # type: ignore[assignment]
try:
    from viper.sources.n8n_community import scan_n8n_community
except ImportError:
    scan_n8n_community = None  # type: ignore[assignment]
try:
    from viper.sources.make_community import scan_make_community
except ImportError:
    scan_make_community = None  # type: ignore[assignment]

log = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")

DATA_DIR = Path(__file__).parent.parent / "data"
SEEN_JOBS_FILE = DATA_DIR / "viper_seen_jobs.json"
JOB_LOG_FILE = DATA_DIR / "viper_job_log.jsonl"

MIN_ALERT_SCORE = 70  # 7.0/10 = send to Jordan's TG
MAX_ALERTS_PER_CYCLE = 10

# --- Full-time / salary job filter ---
FULLTIME_SIGNALS = [
    "full-time", "full time", "fulltime", "fte",
    "/yr", "/year", "per year", "annual salary", "annually",
    "k/yr", "k per year", "k/year", "per annum",
    "benefits package", "401k", "401(k)", "equity",
    "stock options", "health insurance",
    "paid time off", "pto", "vacation days",
    "permanent position", "permanent role",
    "w-2", "on-site", "onsite", "hybrid role",
    "relocation", "visa sponsor",
]

_SALARY_RE = re.compile(
    r"\$\d{2,3}[,.]?\d{0,3}\s*k|\$\d{3},\d{3}|\$\d{2,3}k?\s*[-–]\s*\$?\d{2,3}k?\s*/\s*y",
    re.IGNORECASE,
)

# --- Garbage lead filter (Pipeline 2 quality gate) ---

_COMPETITOR_RE = re.compile(
    r"\[for\s+hire\]|\[seeking\s+work\]|"
    r"\bhire\s+me\b|\bi\s+offer\b|\bavailable\s+for\b|"
    r"\bi\s+am\s+a\s+freelanc|\bmy\s+services\b|\bi\s+can\s+build\b",
    re.IGNORECASE,
)

# Builder/showcase posts — people sharing what they built, not buying
_BUILDER_RE = re.compile(
    r"\bshow\s+hn\b|\bmy\s+project\b|\bi\s+built\b|\bi\s+made\b|"
    r"\bjust\s+launched\b|\bjust\s+shipped\b|\bcheck\s+out\s+my\b|"
    r"\bopen[\s-]?source\b|\bside\s+project\b|\bweekend\s+project\b|"
    r"\bi\s+created\b|\bi\s+developed\b|\bhere['']?s\s+my\b|"
    r"\bmy\s+(?:new\s+)?(?:app|tool|saas|startup|product|repo|library)\b",
    re.IGNORECASE,
)

_NEWS_DOMAINS = frozenset([
    # Major tech/news
    "techcrunch.com", "itpro.com", "wired.com", "theverge.com",
    "zdnet.com", "venturebeat.com", "mashable.com", "cnet.com",
    "reuters.com", "bloomberg.com", "forbes.com", "medium.com",
    "hackernoon.com", "dev.to", "wikipedia.org", "github.com",
    "stackoverflow.com", "arstechnica.com", "engadget.com",
    "thenextweb.com", "gizmodo.com", "businessinsider.com",
    "cnbc.com", "bbc.com", "nytimes.com",
    # Industry/trade press (leak through Google Alerts)
    "cxtoday.com", "scmr.com", "newarkadvocate.com", "delawareonline.com",
    "prnewswire.com", "globenewswire.com", "businesswire.com",
    "prweb.com", "marketwatch.com", "yahoo.com", "msn.com",
    "techradar.com", "infoworld.com", "computerworld.com",
    "theregister.com", "siliconangle.com", "digiday.com",
    "adweek.com", "martech.org", "searchengineland.com",
    "searchenginejournal.com", "socialmediatoday.com",
    "eweek.com", "informationweek.com", "sdxcentral.com",
    "fiercetelecom.com", "lightreading.com",
])

_BIG_COMPANIES = [
    "google", "meta", "facebook", "amazon", "apple", "microsoft",
    "salesforce", "oracle", "ibm", "netflix", "uber", "lyft",
    "airbnb", "stripe", "shopify", "slack", "twilio", "palantir",
    "snowflake", "datadog", "cloudflare", "atlassian", "adobe",
    "intel", "nvidia", "amd", "cisco", "vmware", "dell",
]

_HIRING_INTENT_RE = re.compile(
    r"\[hiring\]|\bneed\s+someone\b|\bbudget\s*\$|\blooking\s+to\s+hire\b|"
    r"\bhiring\s+a\b|\bwant\s+to\s+hire\b|\bseeking\s+a\s+(developer|freelancer|contractor)\b",
    re.IGNORECASE,
)

# Detect "company launches/announces/unveils AI product" articles — NOT hiring
_AI_LAUNCH_RE = re.compile(
    r"\b(?:launch(?:es|ed|ing)?|announc(?:es|ed|ing)?|unveil(?:s|ed|ing)?|"
    r"introduc(?:es|ed|ing)?|releas(?:es|ed|ing)?|roll(?:s|ed|ing)?\s*out|"
    r"debut(?:s|ed|ing)?|deploy(?:s|ed|ing)?|integrat(?:es|ed|ing)?)\b"
    r".*?\b(?:ai|artificial\s+intelligence|chatbot|virtual\s+assistant|"
    r"machine\s+learning|ml|llm|generative|copilot|platform|tool|solution)\b",
    re.IGNORECASE,
)


def _is_garbage_lead(job: dict) -> tuple[bool, str]:
    """Return (True, reason) if this lead is garbage and should be filtered.

    Called on EVERY job after the full-time filter, before scoring.
    """
    title = job.get("title", "")
    description = job.get("description", "")
    url = job.get("url", "")
    source = job.get("source", "")
    text = f"{title} {description}".lower()

    # 1. Competitor detection — people offering services, not hiring
    if _COMPETITOR_RE.search(f"{title} {description}"):
        return True, "competitor/self-promo"

    # 2. Builder/showcase filter — Indie Hackers, HN, GitHub
    #    "I built X" / "my project" / "Show HN" / open-source shares = builders, not buyers
    if source in ("IndieHackers", "HackerNews", "ProductHunt"):
        if _BUILDER_RE.search(f"{title} {description}"):
            if not _HIRING_INTENT_RE.search(f"{title} {description}"):
                return True, "builder showcase, not a buyer"

    # 3. News domain blocklist — GoogleAlerts only
    if source == "GoogleAlerts":
        url_lower = url.lower()
        for domain in _NEWS_DOMAINS:
            if domain in url_lower:
                return True, f"news domain: {domain}"

    # 4. Big company filter
    for company in _BIG_COMPANIES:
        if f"@ {company}" in text or f"@{company}" in text:
            return True, f"big company: {company}"
        if title.lower().startswith(f"{company} "):
            return True, f"big company: {company}"

    # 5. ProductHunt filter — reject unless explicit hiring intent
    if source == "ProductHunt":
        if not _HIRING_INTENT_RE.search(f"{title} {description}"):
            return True, "ProductHunt launch, no hiring intent"

    # 6. "Company launching AI" article filter — all sources
    #    Articles about companies releasing AI products are NOT hiring leads.
    #    Check title first (strongest signal), then title+description.
    if _AI_LAUNCH_RE.search(title):
        if not _HIRING_INTENT_RE.search(f"{title} {description}"):
            return True, "AI product launch article, not hiring"

    # 7. Google Alerts intent filter — reject unless hiring intent
    #    "chatbot"/"automation" alone is NOT enough — news articles about AI products
    #    contain those words. Require hiring intent OR freelance-specific phrases.
    if source == "GoogleAlerts":
        if not _HIRING_INTENT_RE.search(f"{title} {description}"):
            freelance_kws = [
                "freelance", "contractor", "looking for a developer",
                "need a developer", "gig", "project budget",
            ]
            if not any(kw in text for kw in freelance_kws):
                return True, "GoogleAlerts: no hiring intent"

    # 8. HN freelancer thread filter — "Seeking freelancer?" = people offering, not hiring
    if source == "HackerNews" and job.get("thread_type") == "freelancer":
        if not _HIRING_INTENT_RE.search(f"{title} {description}"):
            # In "Seeking freelancer?" threads, most posts are freelancers advertising
            offering_signals = [
                "available", "i specialize", "my portfolio", "i build",
                "i develop", "my rate", "open to", "looking for work",
                "seeking opportunities", "i'm a ",
            ]
            if any(sig in text for sig in offering_signals):
                return True, "HN freelancer thread: offering services, not hiring"

    return False, ""


def _is_fulltime_job(title: str, description: str, budget: str, source: str) -> bool:
    """Return True if this looks like a full-time/salary position, not freelance."""
    text = f"{title} {description} {budget}".lower()

    if _SALARY_RE.search(text):
        return True

    hit_count = sum(1 for sig in FULLTIME_SIGNALS if sig in text)
    if hit_count >= 2:
        return True

    if source in ("RemoteOK", "WeWorkRemotely", "HackerNews"):
        freelance_signals = [
            "freelance", "contract", "project", "gig",
            "part-time", "part time", "fixed price",
            "hourly", "per hour", "one-time",
        ]
        if not any(fs in text for fs in freelance_signals):
            return True

    return False


def _job_hash(source: str, job_id: str) -> str:
    return hashlib.md5(f"{source}:{job_id}".encode()).hexdigest()[:16]


def _load_seen() -> dict[str, float]:
    if not SEEN_JOBS_FILE.exists():
        return {}
    try:
        data = json.loads(SEEN_JOBS_FILE.read_text())
        now = time.time()
        # Keep: permanent entries (v < 0 = Jordan BID/SKIP'd) + entries < 7 days old
        return {k: v for k, v in data.items() if v < 0 or now - v < 604800}
    except Exception:
        return {}


def _save_seen(seen: dict[str, float]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SEEN_JOBS_FILE.write_text(json.dumps(seen, indent=2))


def _log_job(job: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(JOB_LOG_FILE, "a") as f:
        f.write(json.dumps(job) + "\n")


def _parse_budget_value(budget_str: str) -> float:
    if not budget_str:
        return 0
    nums = re.findall(r"[\d,]+\.?\d*", budget_str.replace(",", ""))
    if nums:
        try:
            return float(nums[-1])
        except ValueError:
            pass
    return 0


def _suggest_bid(budget_min: float, budget_max: float, category: str, bid_count: int) -> str:
    if budget_max <= 0 and budget_min <= 0:
        return "~$50-100"

    mid = (budget_min + budget_max) / 2 if budget_max > 0 else budget_min

    if bid_count <= 5:
        suggested = mid * 0.85
    elif bid_count <= 15:
        suggested = mid * 0.75
    else:
        suggested = mid * 0.65

    suggested = max(suggested, 25)
    return f"${suggested:.0f}"


def _suggest_delivery(budget_max: float, category: str, description: str) -> int:
    if budget_max <= 50:
        return 2
    elif budget_max <= 150:
        return 3
    elif budget_max <= 500:
        return 5
    elif budget_max <= 1500:
        return 7
    else:
        return 10


def _score_job(
    category: str,
    matched_skills: list[str],
    budget_min: float = 0,
    budget_max: float = 0,
    budget_str: str = "",
    bid_count: int = 0,
) -> int:
    """Score a job 0-100 based on fit, budget, and competition."""
    score = 0

    # Skill match (0-40 pts)
    score += min(len(matched_skills) * 10, 40)

    # Budget in USD (0-25 pts)
    budget_val = budget_max or budget_min or _parse_budget_value(budget_str)
    if budget_val >= 500:
        score += 25
    elif budget_val >= 200:
        score += 20
    elif budget_val >= 100:
        score += 15
    elif budget_val >= 50:
        score += 10
    elif budget_val > 0:
        score += 5

    # Low competition (0-20 pts)
    if bid_count == 0:
        score += 20
    elif bid_count <= 5:
        score += 15
    elif bid_count <= 15:
        score += 10
    elif bid_count <= 30:
        score += 5

    # Category bonus (0-15 pts)
    if category == "coding":
        score += 15
    elif category == "mixed":
        score += 10
    elif category == "content":
        score += 8

    return min(score, 100)


def run_scan() -> dict:
    """Execute one full scan cycle across all sources."""
    log.info("[JOB_HUNTER] Starting scan cycle...")
    seen = _load_seen()
    now = time.time()
    total_scanned = 0
    new_matches = 0
    alerts_sent = 0
    source_counts: dict[str, int] = {}

    all_jobs: list[dict] = []

    # --- Hacker News "Who's Hiring" ---
    try:
        hn_jobs = scan_hackernews()
        source_counts["HackerNews"] = len(hn_jobs)
        for hj in hn_jobs:
            total_scanned += 1
            h = _job_hash("hn", hj.comment_id)
            if h in seen:
                continue
            score = _score_job(
                category=hj.category,
                matched_skills=hj.matched_skills,
            )
            # HN jobs are usually high quality — bonus
            score = min(score + 10, 100)
            all_jobs.append({
                "source": "HackerNews",
                "title": hj.title,
                "description": hj.text,
                "url": hj.url,
                "category": hj.category,
                "skills": hj.matched_skills,
                "budget": "",
                "budget_usd_min": 0,
                "budget_usd_max": 0,
                "bid_count": None,
                "score": score,
                "hash": h,
                "suggested_bid": "Apply",
                "suggested_delivery_days": 0,
                "client_country": "",
                "thread_type": hj.thread_type,
            })
    except Exception as e:
        log.error("[JOB_HUNTER] HN scan failed: %s", str(e)[:200])

    # --- Google Alerts RSS ---
    try:
        ga_jobs = scan_google_alerts()
        source_counts["GoogleAlerts"] = len(ga_jobs)
        for gj in ga_jobs:
            total_scanned += 1
            h = _job_hash("galerts", gj.job_id)
            if h in seen:
                continue
            score = _score_job(
                category=gj.category,
                matched_skills=gj.matched_skills,
            )
            # Google Alerts are high-intent signals — bonus
            score = min(score + 5, 100)
            all_jobs.append({
                "source": "GoogleAlerts",
                "title": gj.title,
                "description": gj.description,
                "url": gj.url,
                "category": gj.category,
                "skills": gj.matched_skills,
                "budget": "",
                "budget_usd_min": 0,
                "budget_usd_max": 0,
                "bid_count": None,
                "score": score,
                "hash": h,
                "suggested_bid": "Apply",
                "suggested_delivery_days": 0,
                "client_country": "",
                "feed_query": gj.feed_query,
            })
    except Exception as e:
        log.error("[JOB_HUNTER] Google Alerts scan failed: %s", str(e)[:200])

    # --- Reddit (graceful skip if no creds) ---
    try:
        reddit_jobs = scan_reddit()
        source_counts["Reddit"] = len(reddit_jobs)
        for rj in reddit_jobs:
            total_scanned += 1
            h = _job_hash("reddit", rj.job_id)
            if h in seen:
                continue
            budget_val = _parse_budget_value(rj.budget_hint)
            score = _score_job(
                category=rj.category,
                matched_skills=rj.matched_skills,
                budget_min=budget_val,
                budget_str=rj.budget_hint,
            )
            all_jobs.append({
                "source": "Reddit",
                "title": rj.title,
                "description": rj.body,
                "url": rj.url,
                "category": rj.category,
                "skills": rj.matched_skills,
                "budget": rj.budget_hint,
                "budget_usd_min": budget_val,
                "budget_usd_max": 0,
                "bid_count": None,
                "score": score,
                "hash": h,
                "suggested_bid": _suggest_bid(budget_val, 0, rj.category, 0),
                "suggested_delivery_days": _suggest_delivery(budget_val, rj.category, rj.body),
                "client_country": "",
                "subreddit": rj.subreddit,
            })
    except Exception as e:
        log.error("[JOB_HUNTER] Reddit scan failed: %s", str(e)[:200])

    # --- Indie Hackers — DISABLED (Mar 12 2026) ---
    # Zero valid leads, all builder/showcase posts. Re-enable only after
    # a buyer-vs-builder intent filter is built.
    # See: _BUILDER_RE in _is_garbage_lead() for the filter pattern.

    # --- Product Hunt (optional — Pro only) ---
    if scan_producthunt is not None:
        try:
            ph_jobs = scan_producthunt()
            source_counts["ProductHunt"] = len(ph_jobs)
            for pj in ph_jobs:
                total_scanned += 1
                h = _job_hash("producthunt", pj.job_id)
                if h in seen:
                    continue
                score = _score_job(
                    category=pj.category,
                    matched_skills=pj.matched_skills,
                )
                all_jobs.append({
                    "source": "ProductHunt",
                    "title": pj.title,
                    "description": pj.description,
                    "url": pj.url,
                    "category": pj.category,
                    "skills": pj.matched_skills,
                    "budget": "",
                    "budget_usd_min": 0,
                    "budget_usd_max": 0,
                    "bid_count": None,
                    "score": score,
                    "hash": h,
                    "suggested_bid": "Apply",
                    "suggested_delivery_days": 0,
                    "client_country": "",
                })
        except Exception as e:
            log.error("[JOB_HUNTER] Product Hunt scan failed: %s", str(e)[:200])

    # --- n8n Community Jobs (optional — Pro only) ---
    if scan_n8n_community is not None:
        try:
            n8n_jobs = scan_n8n_community()
            source_counts["n8n"] = len(n8n_jobs)
            for nj in n8n_jobs:
                total_scanned += 1
                h = _job_hash("n8n", nj.job_id)
                if h in seen:
                    continue
                score = _score_job(
                    category=nj.category,
                    matched_skills=nj.matched_skills,
                )
                # n8n community = high-intent automation leads
                score = min(score + 5, 100)
                all_jobs.append({
                    "source": "n8n_community",
                    "title": nj.title,
                    "description": nj.description,
                    "url": nj.url,
                    "category": nj.category,
                    "skills": nj.matched_skills,
                    "budget": "",
                    "budget_usd_min": 0,
                    "budget_usd_max": 0,
                    "bid_count": None,
                    "score": score,
                    "hash": h,
                    "suggested_bid": "Apply",
                    "suggested_delivery_days": 0,
                    "client_country": "",
                })
        except Exception as e:
            log.error("[JOB_HUNTER] n8n scan failed: %s", str(e)[:200])

    # --- Make.com Hire a Pro (optional — Pro only) ---
    if scan_make_community is not None:
        try:
            make_jobs = scan_make_community()
            source_counts["Make"] = len(make_jobs)
            for mj in make_jobs:
                total_scanned += 1
                h = _job_hash("make", mj.job_id)
                if h in seen:
                    continue
                score = _score_job(
                    category=mj.category,
                    matched_skills=mj.matched_skills,
                )
                score = min(score + 5, 100)
                all_jobs.append({
                    "source": "Make_community",
                    "title": mj.title,
                    "description": mj.description,
                    "url": mj.url,
                    "category": mj.category,
                    "skills": mj.matched_skills,
                    "budget": "",
                    "budget_usd_min": 0,
                    "budget_usd_max": 0,
                    "bid_count": None,
                    "score": score,
                    "hash": h,
                    "suggested_bid": "Apply",
                    "suggested_delivery_days": 0,
                    "client_country": "",
                })
        except Exception as e:
            log.error("[JOB_HUNTER] Make.com scan failed: %s", str(e)[:200])

    # --- RemoteOK ---
    try:
        rok_jobs = scan_remoteok()
        source_counts["RemoteOK"] = len(rok_jobs)
        for rk in rok_jobs:
            total_scanned += 1
            h = _job_hash("remoteok", rk.job_id)
            if h in seen:
                continue
            score = _score_job(
                category=rk.category,
                matched_skills=rk.matched_skills,
                budget_min=rk.salary_min / 12 if rk.salary_min else 0,
                budget_max=rk.salary_max / 12 if rk.salary_max else 0,
            )
            salary_str = ""
            if rk.salary_min and rk.salary_max:
                salary_str = f"${rk.salary_min:,}-${rk.salary_max:,}/yr"
            all_jobs.append({
                "source": "RemoteOK",
                "title": f"{rk.title} @ {rk.company}" if rk.company else rk.title,
                "description": rk.description,
                "url": rk.url,
                "category": rk.category,
                "skills": rk.matched_skills,
                "budget": salary_str,
                "budget_usd_min": 0,
                "budget_usd_max": 0,
                "bid_count": None,
                "score": score,
                "hash": h,
                "suggested_bid": "Apply",
                "suggested_delivery_days": 0,
                "client_country": "",
            })
    except Exception as e:
        log.error("[JOB_HUNTER] RemoteOK scan failed: %s", str(e)[:200])

    # --- We Work Remotely ---
    try:
        wwr_jobs = scan_weworkremotely()
        source_counts["WeWorkRemotely"] = len(wwr_jobs)
        for wj in wwr_jobs:
            total_scanned += 1
            h = _job_hash("wwr", wj.job_id)
            if h in seen:
                continue
            score = _score_job(
                category=wj.category,
                matched_skills=wj.matched_skills,
            )
            all_jobs.append({
                "source": "WeWorkRemotely",
                "title": f"{wj.title} @ {wj.company}" if wj.company else wj.title,
                "description": wj.description,
                "url": wj.url,
                "category": wj.category,
                "skills": wj.matched_skills,
                "budget": "",
                "budget_usd_min": 0,
                "budget_usd_max": 0,
                "bid_count": None,
                "score": score,
                "hash": h,
                "suggested_bid": "Apply",
                "suggested_delivery_days": 0,
                "client_country": "",
            })
    except Exception as e:
        log.error("[JOB_HUNTER] WWR scan failed: %s", str(e)[:200])

    # Log source counts
    log.info("[JOB_HUNTER] Source scan results: %s", source_counts)

    # Filter out full-time / salary positions
    freelance_jobs = []
    fulltime_killed = 0
    for job in all_jobs:
        if _is_fulltime_job(
            job["title"], job.get("description", ""),
            job.get("budget", ""), job["source"],
        ):
            fulltime_killed += 1
            continue
        freelance_jobs.append(job)

    if fulltime_killed:
        log.info("[JOB_HUNTER] Filtered %d full-time/salary positions", fulltime_killed)

    # Filter out garbage leads (news, competitors, big companies, etc.)
    clean_jobs = []
    garbage_killed = 0
    for job in freelance_jobs:
        is_garbage, reason = _is_garbage_lead(job)
        if is_garbage:
            garbage_killed += 1
            log.debug("[JOB_HUNTER] Garbage filtered: %s — %s", job["title"][:60], reason)
            continue
        clean_jobs.append(job)

    if garbage_killed:
        log.info("[JOB_HUNTER] Filtered %d garbage leads", garbage_killed)
    freelance_jobs = clean_jobs

    # Sort by score descending
    freelance_jobs.sort(key=lambda j: j["score"], reverse=True)

    # Write to viper_leads.json
    try:
        new_leads = write_leads(freelance_jobs)
        log.info("[JOB_HUNTER] Wrote %d new leads to viper_leads.json", new_leads)
    except Exception as e:
        log.error("[JOB_HUNTER] Lead writer failed: %s", str(e)[:200])

    for job in freelance_jobs:
        if job["score"] < MIN_ALERT_SCORE:
            continue
        if alerts_sent >= MAX_ALERTS_PER_CYCLE:
            break

        new_matches += 1

        days = job.get("suggested_delivery_days", 0)
        delivery_str = f"{days} days" if days else ""
        bid_display = job.get("suggested_bid", "")

        sent = send_job_alert(
            title=job["title"],
            source=job["source"],
            category=job["category"],
            skills=job["skills"],
            budget=job.get("budget", ""),
            url=job["url"],
            score=job["score"],
            bid_count=job.get("bid_count"),
            description=job.get("description", ""),
            suggested_bid=bid_display,
            suggested_delivery=delivery_str,
            client_country=job.get("client_country", ""),
            job_hash=job["hash"],
        )
        if sent:
            alerts_sent += 1

        seen[job["hash"]] = now

        job["alerted_at"] = datetime.now(ET).isoformat()
        _log_job(job)

        time.sleep(0.5)

    _save_seen(seen)

    if alerts_sent > 0:
        send_summary(total_scanned, new_matches, alerts_sent)

    result = {
        "total_scanned": total_scanned,
        "new_matches": new_matches,
        "alerts_sent": alerts_sent,
        "source_counts": source_counts,
        "timestamp": datetime.now(ET).isoformat(),
    }
    log.info(
        "[JOB_HUNTER] Cycle done: scanned=%d matches=%d alerts=%d",
        total_scanned, new_matches, alerts_sent,
    )
    return result


def run_loop(interval_minutes: int = 30) -> None:
    """Run scanner in a loop with configurable interval."""
    from viper.drip_runner import run_drip_cycle
    from viper.inbound.rss_poller import poll_all_feeds

    log.info("[JOB_HUNTER] Starting loop (interval=%d min)", interval_minutes)
    while True:
        try:
            run_scan()
        except Exception as e:
            log.exception("[JOB_HUNTER] Cycle error: %s", str(e)[:200])

        # Check for due follow-up emails (drip sequences)
        try:
            drip_count = run_drip_cycle()
            if drip_count:
                log.info("[JOB_HUNTER] Drip cycle: %d follow-ups sent to TG", drip_count)
        except Exception as e:
            log.exception("[JOB_HUNTER] Drip cycle error: %s", str(e)[:200])

        # Poll inbound RSS feeds (Google Alerts)
        try:
            inbound = poll_all_feeds()
            if inbound.get("hot", 0) or inbound.get("warm", 0):
                log.info("[JOB_HUNTER] Inbound: %d hot, %d warm leads", inbound["hot"], inbound["warm"])
        except Exception as e:
            log.exception("[JOB_HUNTER] Inbound poll error: %s", str(e)[:200])

        time.sleep(interval_minutes * 60)
