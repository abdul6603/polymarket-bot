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
from viper.prospecting.local_scorer import score_prospect, score_prospect_v3
from viper.prospecting.prospect_writer import build_prospect, LocalProspect
from viper.prospecting.site_auditor import audit_site, crawl_and_audit, format_findings_for_email
from viper.prospecting.tech_fingerprinter import fingerprint_tech_stack
from viper.prospecting.pagespeed_auditor import audit_pagespeed
from viper.prospecting.gbp_enricher import enrich_from_gbp
from viper.prospecting.apollo_enricher import enrich_email as apollo_enrich_email, extract_domain
from viper.demos.scraper import scrape_business, ScrapedBusiness
from viper.outreach.outreach_engine import run_outreach
from viper.tg_router import send as tg_send

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("mass_scan")

ET = timezone(timedelta(hours=-5))

# ── Configuration ────────────────────────────────────────────────────────

NICHES = ["HVAC contractor", "personal injury lawyer", "med spa"]
CITIES = ["Portland ME", "Boston MA", "Manchester NH", "Nashua NH", "Concord NH", "Worcester MA", "Providence RI"]
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
    """Send via tg_router on OUTREACH channel."""
    return tg_send(text, channel="OUTREACH")


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

    # Step 2 — Enrich + detect + score (V3 pipeline)
    prospects: list[LocalProspect] = []

    for i, listing in enumerate(listings, 1):
        scraped: ScrapedBusiness | None = None
        chatbot: ChatbotDetectionResult | None = None
        tech_stack_data: dict | None = None
        pagespeed_mobile_data: dict | None = None
        pagespeed_desktop_data: dict | None = None
        gbp_data_dict: dict | None = None
        apollo_contacts_list: list | None = None

        if listing.website_url:
            try:
                scraped = scrape_business(listing.website_url)
            except Exception as e:
                log.debug("Scrape failed for %s: %s", listing.website_url, e)

            # Chatbot detection
            raw_html = ""
            if scraped and scraped.raw_html:
                raw_html = scraped.raw_html
                chatbot = detect_chatbot(raw_html)
            elif listing.website_url:
                from viper.demos.scraper import _fetch_raw_html
                raw_html = _fetch_raw_html(listing.website_url) or ""
                if raw_html:
                    chatbot = detect_chatbot(raw_html)

            # Tech stack fingerprinting (V3)
            if raw_html:
                try:
                    ts_result = fingerprint_tech_stack(listing.website_url, raw_html)
                    tech_stack_data = ts_result.to_dict()
                except Exception as e:
                    log.debug("Tech fingerprint failed for %s: %s", listing.website_url, e)

            # PageSpeed audit (V3) — mobile + desktop
            try:
                ps_mobile = audit_pagespeed(listing.website_url, "mobile")
                if not ps_mobile.error:
                    pagespeed_mobile_data = ps_mobile.to_dict()
                ps_desktop = audit_pagespeed(listing.website_url, "desktop")
                if not ps_desktop.error:
                    pagespeed_desktop_data = ps_desktop.to_dict()
            except Exception as e:
                log.debug("PageSpeed failed for %s: %s", listing.website_url, e)

            time.sleep(SITE_DELAY)

        # GBP enrichment (V3) — budget guard: pre-score >= 6.0
        pre_score = score_prospect(listing, scraped, chatbot)
        if pre_score.total >= 6.0:
            try:
                gbp_result = enrich_from_gbp(listing.business_name, listing.address)
                if not gbp_result.error:
                    gbp_data_dict = gbp_result.to_dict()
            except Exception as e:
                log.debug("GBP enrich failed for %s: %s", listing.business_name, e)

        # Apollo email enrichment (V3) — budget guard: score >= 7.0 AND no email
        if pre_score.total >= 7.0 and scraped and not scraped.email and listing.website_url:
            domain = extract_domain(listing.website_url)
            if domain:
                try:
                    contacts = apollo_enrich_email(domain, listing.business_name, limit=3)
                    if contacts:
                        apollo_contacts_list = [c.to_dict() for c in contacts]
                        # Use best contact's email
                        best = contacts[0]
                        scraped.email = best.email
                        name = f"{best.first_name} {best.last_name}".strip()
                        if name and not scraped.team_members:
                            scraped.team_members.append(name)
                except Exception as e:
                    log.debug("Apollo enrich failed for %s: %s", listing.business_name, e)

        # Score with V3 (8 dimensions) using enrichment data
        score = score_prospect_v3(
            listing, scraped, chatbot,
            tech_stack=tech_stack_data,
            pagespeed=pagespeed_mobile_data,
            gbp=gbp_data_dict,
        )
        prospect = build_prospect(
            listing, scraped, chatbot, score,
            tech_stack=tech_stack_data,
            pagespeed_mobile=pagespeed_mobile_data,
            pagespeed_desktop=pagespeed_desktop_data,
            gbp_data=gbp_data_dict,
            apollo_contacts=apollo_contacts_list,
        )
        prospects.append(prospect)

    # Sort by score descending
    prospects.sort(key=lambda p: p.score, reverse=True)

    # Site audit — full crawl via Cloudflare if available, else local fallback
    for p in prospects:
        crawl, findings = crawl_and_audit(p)
        p.audit_findings = findings
        if crawl and not crawl.error:
            # Override chatbot detection with crawl data (more thorough)
            if crawl.has_chatbot and p.chatbot_confidence != "DETECTED":
                p.chatbot_confidence = "DETECTED"
                p.chatbot_name = crawl.chatbot_name

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
