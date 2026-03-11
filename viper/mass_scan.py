"""Viper Mass Scan — scheduled multi-city, multi-niche prospector.

Scans multiple niches across multiple cities, runs the full pipeline
(Google Maps → enrich → chatbot detect → site audit → score → queue),
compiles a summary report, and sends it to Jordan via Shelby Telegram.

NOTHING auto-sends. All emails queued for Jordan's TG approval.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Allow running from repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from viper.prospecting.maps_scraper import discover_businesses, deduplicate_listings
from viper.prospecting.chatbot_detector import detect_chatbot, ChatbotDetectionResult
from viper.prospecting.local_scorer import score_prospect
from viper.prospecting.prospect_writer import build_prospect, LocalProspect
from viper.prospecting.site_auditor import audit_site, format_findings_for_email
from viper.demos.scraper import scrape_business, ScrapedBusiness
from viper.outreach.outreach_engine import run_outreach

try:
    from viper.sources.hunter import find_emails, extract_domain
except ImportError:
    find_emails = None  # type: ignore[assignment]
    extract_domain = None  # type: ignore[assignment]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("mass_scan")

ET = timezone(timedelta(hours=-5))

# ── Configuration ────────────────────────────────────────────────────────

NICHES = ["dental practice", "real estate"]
CITIES = ["Boston MA", "Cambridge MA", "Worcester MA", "Springfield MA", "Lowell MA"]
MAX_PER_CITY = 25
MIN_SCORE = 7.0
SITE_DELAY = 1.5  # seconds between scrapes
MAPS_DELAY = 2.5  # seconds between Maps scrolls
SCAN_PAUSE = 10   # seconds between city scans (avoid rate limits)

DATA_DIR = Path.home() / "polymarket-bot" / "data"

# ── Telegram ─────────────────────────────────────────────────────────────

_TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
_TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

if not _TG_TOKEN:
    _shelby_env = Path.home() / "shelby" / ".env"
    if _shelby_env.exists():
        for line in _shelby_env.read_text().splitlines():
            if line.startswith("TELEGRAM_BOT_TOKEN="):
                _TG_TOKEN = line.split("=", 1)[1].strip()
            elif line.startswith("TELEGRAM_CHAT_ID="):
                _TG_CHAT_ID = line.split("=", 1)[1].strip()


def _send_tg(text: str) -> bool:
    """Send a Telegram message to Jordan."""
    if not _TG_TOKEN or not _TG_CHAT_ID:
        log.warning("TG credentials not configured — printing instead")
        print(text)
        return False

    import requests
    url = f"https://api.telegram.org/bot{_TG_TOKEN}/sendMessage"
    payload = {
        "chat_id": _TG_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            return True
        log.error("TG API error %d: %s", resp.status_code, resp.text[:200])
        return False
    except Exception as e:
        log.error("TG send failed: %s", e)
        return False


# ── Single City Scan ─────────────────────────────────────────────────────

def scan_city_niche(niche: str, city: str) -> dict:
    """Run the full prospector pipeline for one niche × city combo.

    Returns:
        dict with found, no_chatbot, queued, prospects, outreach_stats
    """
    result = {
        "niche": niche,
        "city": city,
        "found": 0,
        "no_chatbot": 0,
        "qualified": 0,
        "queued": 0,
        "skipped": 0,
        "already_contacted": 0,
        "prospects": [],
        "error": None,
    }

    log.info("Scanning: %s in %s", niche, city)

    # Step 1 — Google Maps discovery
    try:
        listings = discover_businesses(
            niche=niche,
            city=city,
            max_results=MAX_PER_CITY,
            headless=True,
            delay=MAPS_DELAY,
        )
    except RuntimeError as e:
        log.error("CAPTCHA blocked for %s in %s: %s", niche, city, e)
        result["error"] = f"CAPTCHA: {e}"
        return result

    if not listings:
        log.info("No results for %s in %s", niche, city)
        return result

    listings = deduplicate_listings(listings)
    result["found"] = len(listings)
    log.info("Found %d unique businesses for %s in %s", len(listings), niche, city)

    # Step 2 — Enrich + detect + score
    prospects: list[LocalProspect] = []

    for i, listing in enumerate(listings, 1):
        scraped: ScrapedBusiness | None = None
        chatbot: ChatbotDetectionResult | None = None

        if listing.website_url:
            try:
                scraped = scrape_business(listing.website_url)
            except Exception as e:
                log.debug("Scrape failed for %s: %s", listing.website_url, e)

            if scraped and scraped.raw_html:
                chatbot = detect_chatbot(scraped.raw_html)
            elif listing.website_url:
                from viper.demos.scraper import _fetch_raw_html
                raw = _fetch_raw_html(listing.website_url)
                if raw:
                    chatbot = detect_chatbot(raw)

            time.sleep(SITE_DELAY)

        # Hunter.io fallback
        if find_emails and scraped and not scraped.email and listing.website_url:
            domain = extract_domain(listing.website_url)
            if domain:
                hunter_results = find_emails(domain, limit=2)
                if hunter_results:
                    best = hunter_results[0]
                    scraped.email = best["email"]
                    name = f"{best.get('first_name', '')} {best.get('last_name', '')}".strip()
                    if name and not scraped.team_members:
                        scraped.team_members.append(name)

        score = score_prospect(listing, scraped, chatbot)
        prospect = build_prospect(listing, scraped, chatbot, score)
        prospects.append(prospect)

    # Sort by score descending
    prospects.sort(key=lambda p: p.score, reverse=True)

    # Site audit
    for p in prospects:
        p.audit_findings = audit_site(p)

    # Count stats
    result["no_chatbot"] = sum(
        1 for p in prospects if p.chatbot_confidence == "NOT_FOUND"
    )
    qualified = [p for p in prospects if p.score >= MIN_SCORE]
    result["qualified"] = len(qualified)

    # Serialize prospects for JSON output
    for p in prospects:
        pdict = p.to_dict()
        pdict["audit_findings"] = [
            {"issue": f.issue, "email_line": f.email_line}
            for f in getattr(p, "audit_findings", [])
        ]
        result["prospects"].append(pdict)

    # Step 3 — Queue outreach (NOT sending — just queuing for Jordan's TG approval)
    if qualified:
        outreach_stats = run_outreach(
            prospects=prospects,
            niche=niche,
            city=city,
            min_score=MIN_SCORE,
            dry_run=False,
        )
        result["queued"] = outreach_stats.get("queued", 0)
        result["skipped"] = outreach_stats.get("skipped", 0)
        result["already_contacted"] = outreach_stats.get("already_contacted", 0)

    log.info(
        "Done: %s in %s — %d found, %d no chatbot, %d qualified, %d queued",
        niche, city, result["found"], result["no_chatbot"],
        result["qualified"], result["queued"],
    )

    return result


# ── Main ─────────────────────────────────────────────────────────────────

def main() -> int:
    start_time = datetime.now(ET)
    date_str = start_time.strftime("%Y_%m_%d")
    output_file = DATA_DIR / f"mass_scan_{date_str}.json"

    log.info("=== VIPER MASS SCAN — %s ===", start_time.strftime("%B %d, %Y"))
    log.info("Niches: %s", NICHES)
    log.info("Cities: %s", CITIES)
    log.info("Total scans: %d", len(NICHES) * len(CITIES))

    all_results: list[dict] = []
    all_prospects_flat: list[dict] = []

    for niche in NICHES:
        for city in CITIES:
            result = scan_city_niche(niche, city)
            all_results.append(result)
            all_prospects_flat.extend(result["prospects"])

            # Pause between scans to avoid rate limits
            time.sleep(SCAN_PAUSE)

    # ── Save full results to JSON ────────────────────────────────────
    output_payload = {
        "scan_date": start_time.isoformat(),
        "niches": NICHES,
        "cities": CITIES,
        "total_scans": len(all_results),
        "results": all_results,
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(output_payload, indent=2, ensure_ascii=False))
    log.info("Saved results to %s", output_file)

    # ── Compile niche-level stats ────────────────────────────────────
    niche_stats: dict[str, dict] = {}
    for r in all_results:
        niche = r["niche"]
        if niche not in niche_stats:
            niche_stats[niche] = {"found": 0, "no_chatbot": 0, "queued": 0}
        niche_stats[niche]["found"] += r["found"]
        niche_stats[niche]["no_chatbot"] += r["no_chatbot"]
        niche_stats[niche]["queued"] += r["queued"]

    # ── Top 5 prospects across all scans ─────────────────────────────
    scored = sorted(all_prospects_flat, key=lambda p: p.get("score", 0), reverse=True)
    top5 = scored[:5]

    # ── Build summary message ────────────────────────────────────────
    date_display = start_time.strftime("%B %d")
    lines = [f"\U0001f50d <b>VIPER MASS SCAN — {date_display}</b>\n"]

    for niche, stats in niche_stats.items():
        niche_label = niche.replace("dental practice", "Dental").replace("real estate", "Real Estate")
        lines.append(
            f"<b>{niche_label} (MA):</b> {stats['found']} businesses found | "
            f"{stats['no_chatbot']} no chatbot | {stats['queued']} queued for outreach"
        )

    lines.append("")
    lines.append("\U0001f525 <b>TOP 5 PROSPECTS:</b>")
    for i, p in enumerate(top5, 1):
        name = p.get("business_name", "Unknown")[:30]
        city = p.get("address", "")
        # Extract city from address
        city_short = ""
        for c in CITIES:
            city_name = c.split()[0]
            if city_name.lower() in city.lower():
                city_short = city_name
                break
        if not city_short:
            city_short = "MA"

        score = p.get("score", 0)
        findings = p.get("audit_findings", [])
        finding_summary = ", ".join(f["issue"] for f in findings[:3]) if findings else "No issues detected"

        lines.append(f"{i}. {name} — {city_short} — Score {score:.1f} — {finding_summary}")

    lines.append("")
    lines.append("Reply /leads to review all. Reply YES to approve emails.")

    summary = "\n".join(lines)

    # ── Send to Jordan via Telegram ──────────────────────────────────
    log.info("Sending summary to Jordan via Telegram...")
    sent = _send_tg(summary)
    if sent:
        log.info("Summary sent to Jordan")
    else:
        log.warning("TG send failed — summary printed to stdout above")

    # ── Print summary to log too ─────────────────────────────────────
    elapsed = (datetime.now(ET) - start_time).total_seconds()
    log.info("Mass scan complete in %.0f seconds", elapsed)
    log.info("Results saved to %s", output_file)

    # Print plain-text summary for log file
    print("\n" + "=" * 60)
    print(summary.replace("<b>", "").replace("</b>", ""))
    print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
