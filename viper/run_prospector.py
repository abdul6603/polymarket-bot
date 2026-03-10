"""CLI entry point — local business prospector pipeline."""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

# Allow running as `python viper/run_prospector.py` from repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from viper.prospecting.maps_scraper import discover_businesses, deduplicate_listings
from viper.prospecting.chatbot_detector import detect_chatbot, ChatbotDetectionResult
from viper.prospecting.local_scorer import score_prospect
from viper.prospecting.prospect_writer import (
    build_prospect,
    write_prospects,
    print_summary,
)
from viper.demos.scraper import scrape_business, ScrapedBusiness
from viper.sources.hunter import find_emails, extract_domain

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("prospector")

_SITE_DELAY = 1.5  # seconds between website scrapes


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Viper Local Business Prospector — find outreach-ready leads",
    )
    parser.add_argument("niche", help='Business niche, e.g. "dental practice"')
    parser.add_argument("city", help='City + state, e.g. "Dover NH"')
    parser.add_argument("--max", type=int, default=25, help="Max Maps results (default 25)")
    parser.add_argument("--no-enrich", action="store_true", help="Skip website scraping")
    parser.add_argument("--headless", default="true", help="Browser mode (true/false)")
    parser.add_argument("--delay", type=float, default=2.5, help="Seconds between Maps scrolls")
    parser.add_argument(
        "--auto-outreach", action="store_true",
        help="Auto-send cold emails to prospects scored >= 7 via SendGrid",
    )
    parser.add_argument(
        "--outreach-dry-run", action="store_true",
        help="Compose outreach messages but don't actually send (preview mode)",
    )
    parser.add_argument(
        "--demo-slug", default="",
        help="Demo URL slug (e.g., 'belknapdental-com'). Auto-generated if not set.",
    )
    args = parser.parse_args()

    headless = args.headless.lower() != "false"
    has_outreach = args.auto_outreach or args.outreach_dry_run
    total_steps = 6 if has_outreach else 5

    # Step 1 — Google Maps discovery
    print(f"\n[1/{total_steps}] Searching Google Maps: \"{args.niche}\" in {args.city} ...")
    try:
        listings = discover_businesses(
            niche=args.niche,
            city=args.city,
            max_results=args.max,
            headless=headless,
            delay=args.delay,
        )
    except RuntimeError as e:
        print(f"\n  CAPTCHA BLOCKED: {e}")
        return 1

    if not listings:
        print("  No results found on Google Maps.")
        return 0

    print(f"  Found {len(listings)} businesses")

    # Dedup — merge listings sharing same website domain
    listings = deduplicate_listings(listings)
    print(f"  After dedup: {len(listings)} unique practices")

    # Step 2 — Enrich each listing
    prospects = []
    total = len(listings)

    for i, listing in enumerate(listings, 1):
        scraped: ScrapedBusiness | None = None
        chatbot: ChatbotDetectionResult | None = None

        if not args.no_enrich and listing.website_url:
            pct = int(i / total * 100)
            print(f"  [2/{total_steps}] Enriching {i}/{total} ({pct}%) — {listing.business_name[:40]}", end="\r")

            try:
                scraped = scrape_business(listing.website_url)
            except Exception as e:
                log.debug("Scrape failed for %s: %s", listing.website_url, e)

            # Chatbot detection
            if scraped and scraped.raw_html:
                chatbot = detect_chatbot(scraped.raw_html)
            elif listing.website_url:
                # Try raw fetch for chatbot detection even if full scrape failed
                from viper.demos.scraper import _fetch_raw_html
                raw = _fetch_raw_html(listing.website_url)
                if raw:
                    chatbot = detect_chatbot(raw)

            time.sleep(_SITE_DELAY)

        # Hunter.io fallback — if no email found from scraping, try domain lookup
        if scraped and not scraped.email and listing.website_url:
            domain = extract_domain(listing.website_url)
            if domain:
                hunter_results = find_emails(domain, limit=2)
                if hunter_results:
                    best = hunter_results[0]
                    scraped.email = best["email"]
                    # Store contact name from Hunter if we don't have one
                    name = f"{best.get('first_name', '')} {best.get('last_name', '')}".strip()
                    if name and not scraped.team_members:
                        scraped.team_members.append(name)
                    log.info("Hunter.io found email for %s: %s", listing.business_name, scraped.email)

        # Step 3 — Score
        score = score_prospect(listing, scraped, chatbot)

        # Step 4 — Build prospect record
        prospect = build_prospect(listing, scraped, chatbot, score)
        prospects.append(prospect)

    print(f"\n[3/{total_steps}] Scored {len(prospects)} prospects")

    # Sort by score descending
    prospects.sort(key=lambda p: p.score, reverse=True)

    # Step 4 — Write output
    out_path = write_prospects(prospects, args.niche, args.city)
    print(f"[4/{total_steps}] Saved to {out_path}")

    # Step 5 — Terminal summary
    print(f"[5/{total_steps}] Results:")
    print_summary(prospects)

    # Step 6 — Auto-outreach (if enabled)
    if has_outreach:
        print(f"[6/{total_steps}] Auto-outreach...")
        from viper.outreach.outreach_engine import run_outreach
        stats = run_outreach(
            prospects=prospects,
            niche=args.niche,
            city=args.city,
            min_score=7.0,
            demo_slug=args.demo_slug,
            dry_run=args.outreach_dry_run,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
