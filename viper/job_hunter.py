"""Viper Job Hunter — scans Reddit, Upwork, Freelancer for freelance gigs.

Runs on a schedule, classifies and scores jobs, deduplicates,
and sends top matches to Viper's Telegram bot with bid suggestions.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from viper.sources.reddit import scan_reddit
from viper.sources.upwork import scan_upwork
from viper.sources.freelancer_api import scan_freelancer
from viper.sources.remoteok import scan_remoteok
from viper.sources.weworkremotely import scan_weworkremotely
from viper.sources.hackernews import scan_hackernews
from viper.sources.google_jobs import scan_google_jobs
from viper.sources.x_twitter import scan_x
from viper.telegram_alerts import send_job_alert, send_summary

log = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")

DATA_DIR = Path(__file__).parent.parent / "data"
SEEN_JOBS_FILE = DATA_DIR / "viper_seen_jobs.json"
JOB_LOG_FILE = DATA_DIR / "viper_job_log.jsonl"

MIN_ALERT_SCORE = 65  # Higher bar — every alert must be worth bidding on
MAX_ALERTS_PER_CYCLE = 10


def _job_hash(source: str, job_id: str) -> str:
    return hashlib.md5(f"{source}:{job_id}".encode()).hexdigest()[:16]


def _load_seen() -> dict[str, float]:
    if not SEEN_JOBS_FILE.exists():
        return {}
    try:
        data = json.loads(SEEN_JOBS_FILE.read_text())
        now = time.time()
        return {k: v for k, v in data.items() if now - v < 604800}
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
    import re
    nums = re.findall(r"[\d,]+\.?\d*", budget_str.replace(",", ""))
    if nums:
        try:
            return float(nums[-1])
        except ValueError:
            pass
    return 0


def _suggest_bid(budget_min: float, budget_max: float, category: str, bid_count: int) -> str:
    """Suggest a competitive bid amount based on budget and competition."""
    if budget_max <= 0 and budget_min <= 0:
        return "~$50-100"

    mid = (budget_min + budget_max) / 2 if budget_max > 0 else budget_min

    # Bid strategy: slightly below average to be competitive
    if bid_count <= 5:
        # Low competition — bid closer to budget max
        suggested = mid * 0.85
    elif bid_count <= 15:
        # Medium competition — bid at midpoint
        suggested = mid * 0.75
    else:
        # High competition — bid lower to stand out
        suggested = mid * 0.65

    # Floor at $25
    suggested = max(suggested, 25)

    return f"${suggested:.0f}"


def _suggest_delivery(budget_max: float, category: str, description: str) -> int:
    """Suggest exact delivery days based on project size."""
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

    all_jobs: list[dict] = []

    # --- Reddit ---
    try:
        reddit_jobs = scan_reddit()
        for rj in reddit_jobs:
            total_scanned += 1
            h = _job_hash("reddit", rj.job_id)
            if h in seen:
                continue

            budget_val = _parse_budget_value(rj.budget_hint)
            score = _score_job(
                category=rj.category,
                matched_skills=rj.matched_skills,
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
                "budget_usd_min": budget_val * 0.5,
                "budget_usd_max": budget_val,
                "bid_count": None,
                "score": score,
                "hash": h,
                "suggested_bid": _suggest_bid(budget_val * 0.5, budget_val, rj.category, 0),
                "suggested_delivery": _suggest_delivery(budget_val, rj.category, rj.body),
                "client_country": "",
            })
    except Exception as e:
        log.error("[JOB_HUNTER] Reddit scan failed: %s", str(e)[:200])

    # --- Upwork ---
    try:
        upwork_jobs = scan_upwork()
        for uj in upwork_jobs:
            total_scanned += 1
            h = _job_hash("upwork", uj.job_id)
            if h in seen:
                continue

            budget_val = _parse_budget_value(uj.budget_hint)
            score = _score_job(
                category=uj.category,
                matched_skills=uj.matched_skills,
                budget_str=uj.budget_hint,
            )
            all_jobs.append({
                "source": "Upwork",
                "title": uj.title,
                "description": uj.description,
                "url": uj.url,
                "category": uj.category,
                "skills": uj.matched_skills,
                "budget": uj.budget_hint,
                "budget_usd_min": budget_val * 0.5,
                "budget_usd_max": budget_val,
                "bid_count": None,
                "score": score,
                "hash": h,
                "suggested_bid": _suggest_bid(budget_val * 0.5, budget_val, uj.category, 0),
                "suggested_delivery": _suggest_delivery(budget_val, uj.category, uj.description),
                "client_country": "",
            })
    except Exception as e:
        log.error("[JOB_HUNTER] Upwork scan failed: %s", str(e)[:200])

    # --- Freelancer ---
    try:
        fl_jobs = scan_freelancer()
        for fj in fl_jobs:
            total_scanned += 1
            h = _job_hash("freelancer", fj.job_id)
            if h in seen:
                continue

            # Budget is already in USD from the scanner
            budget_str = ""
            if fj.budget_max_usd > 0:
                budget_str = f"${fj.budget_min_usd:.0f}-${fj.budget_max_usd:.0f} USD"
                if fj.currency_code != "USD":
                    budget_str += f" ({fj.currency_code} {fj.budget_min_raw:.0f}-{fj.budget_max_raw:.0f})"
            elif fj.budget_min_usd > 0:
                budget_str = f"${fj.budget_min_usd:.0f} USD"

            score = _score_job(
                category=fj.category,
                matched_skills=fj.matched_skills,
                budget_min=fj.budget_min_usd,
                budget_max=fj.budget_max_usd,
                bid_count=fj.bid_count,
            )

            suggested_bid = _suggest_bid(
                fj.budget_min_usd, fj.budget_max_usd,
                fj.category, fj.bid_count,
            )
            suggested_delivery = _suggest_delivery(
                fj.budget_max_usd, fj.category, fj.description,
            )

            # Bid in their currency, show USD to Jordan
            bid_usd = float(suggested_bid.replace("$", "").replace(",", "")) if suggested_bid.startswith("$") else 50
            from viper.sources.freelancer_api import CURRENCY_MAP
            cur_code = fj.currency_code
            _, rate = CURRENCY_MAP.get({v[0]: k for k, v in CURRENCY_MAP.items()}.get(cur_code, 1), ("USD", 1.0))
            if rate > 0:
                bid_local = round(bid_usd / rate) if cur_code != "USD" else bid_usd
            else:
                bid_local = bid_usd

            all_jobs.append({
                "source": "Freelancer",
                "title": fj.title,
                "description": fj.description,
                "url": fj.url,
                "category": fj.category,
                "skills": fj.matched_skills,
                "budget": budget_str,
                "budget_usd_min": fj.budget_min_usd,
                "budget_usd_max": fj.budget_max_usd,
                "budget_raw_min": fj.budget_min_raw,
                "budget_raw_max": fj.budget_max_raw,
                "currency_code": fj.currency_code,
                "bid_count": fj.bid_count,
                "score": score,
                "hash": h,
                "suggested_bid_usd": bid_usd,
                "suggested_bid_local": bid_local,
                "suggested_bid": suggested_bid,
                "suggested_delivery_days": suggested_delivery,
                "client_country": fj.client_country,
                "job_id": fj.job_id,
            })
    except Exception as e:
        log.error("[JOB_HUNTER] Freelancer scan failed: %s", str(e)[:200])

    # --- RemoteOK ---
    try:
        rok_jobs = scan_remoteok()
        for rk in rok_jobs:
            total_scanned += 1
            h = _job_hash("remoteok", rk.job_id)
            if h in seen:
                continue
            score = _score_job(
                category=rk.category,
                matched_skills=rk.matched_skills,
                budget_min=rk.salary_min / 12 if rk.salary_min else 0,  # Annual → monthly
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

    # --- Hacker News "Who's Hiring" ---
    try:
        hn_jobs = scan_hackernews()
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
            })
    except Exception as e:
        log.error("[JOB_HUNTER] HN scan failed: %s", str(e)[:200])

    # --- Google Jobs ---
    try:
        g_jobs = scan_google_jobs()
        for gj in g_jobs:
            total_scanned += 1
            h = _job_hash("google", gj.job_id)
            if h in seen:
                continue
            score = _score_job(
                category=gj.category,
                matched_skills=gj.matched_skills,
            )
            all_jobs.append({
                "source": "Google",
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
            })
    except Exception as e:
        log.error("[JOB_HUNTER] Google scan failed: %s", str(e)[:200])

    # --- X/Twitter ---
    try:
        x_jobs = scan_x()
        for xj in x_jobs:
            total_scanned += 1
            h = _job_hash("x", xj.job_id)
            if h in seen:
                continue
            score = _score_job(
                category=xj.category,
                matched_skills=xj.matched_skills,
            )
            all_jobs.append({
                "source": "X/Twitter",
                "title": xj.title,
                "description": xj.text,
                "url": xj.url or "Search X for this post",
                "category": xj.category,
                "skills": xj.matched_skills,
                "budget": "",
                "budget_usd_min": 0,
                "budget_usd_max": 0,
                "bid_count": None,
                "score": score,
                "hash": h,
                "suggested_bid": "DM",
                "suggested_delivery_days": 0,
                "client_country": "",
            })
    except Exception as e:
        log.error("[JOB_HUNTER] X scan failed: %s", str(e)[:200])

    # Sort by score descending
    all_jobs.sort(key=lambda j: j["score"], reverse=True)

    for job in all_jobs:
        if job["score"] < MIN_ALERT_SCORE:
            continue
        if alerts_sent >= MAX_ALERTS_PER_CYCLE:
            break

        new_matches += 1

        # Format delivery and bid for display
        days = job.get("suggested_delivery_days", 0)
        delivery_str = f"{days} days" if days else job.get("suggested_delivery", "")
        bid_display = job.get("suggested_bid", "")
        currency = job.get("currency_code", "USD")
        bid_local = job.get("suggested_bid_local", 0)
        bid_usd = job.get("suggested_bid_usd", 0)

        if currency != "USD" and bid_local > 0:
            bid_display = f"{currency} {bid_local:,.0f} (~${bid_usd:.0f} USD)"
        elif bid_usd > 0:
            bid_display = f"${bid_usd:.0f} USD"

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
        "timestamp": datetime.now(ET).isoformat(),
    }
    log.info(
        "[JOB_HUNTER] Cycle done: scanned=%d matches=%d alerts=%d",
        total_scanned, new_matches, alerts_sent,
    )
    return result


def run_loop(interval_minutes: int = 30) -> None:
    """Run scanner in a loop with configurable interval."""
    log.info("[JOB_HUNTER] Starting loop (interval=%d min)", interval_minutes)
    while True:
        try:
            run_scan()
        except Exception as e:
            log.exception("[JOB_HUNTER] Cycle error: %s", str(e)[:200])
        time.sleep(interval_minutes * 60)
