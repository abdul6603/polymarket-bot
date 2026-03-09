"""Output prospects as JSON + terminal summary table."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from viper.prospecting.maps_scraper import MapsListing
from viper.prospecting.chatbot_detector import ChatbotDetectionResult
from viper.prospecting.local_scorer import ProspectScore
from viper.demos.scraper import ScrapedBusiness

log = logging.getLogger(__name__)

_DATA_DIR = Path.home() / "polymarket-bot" / "data" / "prospects"
_TZ = ZoneInfo("America/New_York")


@dataclass
class LocalProspect:
    """Outreach-ready prospect record."""
    business_name: str = ""
    contact_name: str = ""
    website: str = ""
    phone: str = ""
    email: str = ""
    contact_form_url: str = ""
    address: str = ""
    has_chatbot: bool = False
    chatbot_name: str = ""
    google_rating: float = 0.0
    review_count: int = 0
    maps_url: str = ""
    score: float = 0.0
    score_breakdown: dict = field(default_factory=dict)
    outreach_priority: str = "LOW"
    pitch_angle: str = ""
    scraped_at: str = ""
    scrape_quality: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def build_prospect(
    listing: MapsListing,
    scraped: ScrapedBusiness | None,
    chatbot: ChatbotDetectionResult | None,
    score: ProspectScore,
) -> LocalProspect:
    """Assemble a LocalProspect from pipeline components."""
    now = datetime.now(_TZ).isoformat(timespec="seconds")
    return LocalProspect(
        business_name=listing.business_name,
        contact_name=(scraped.team_members[0] if scraped and scraped.team_members else ""),
        website=listing.website_url or (scraped.url if scraped else ""),
        phone=listing.phone or (scraped.phone if scraped else ""),
        email=scraped.email if scraped else "",
        contact_form_url=scraped.contact_form_url if scraped else "",
        address=listing.address or (scraped.address if scraped else ""),
        has_chatbot=chatbot.has_chatbot if chatbot else False,
        chatbot_name=chatbot.chatbot_name if chatbot else "",
        google_rating=listing.rating,
        review_count=listing.review_count,
        maps_url=listing.maps_url,
        score=score.total,
        score_breakdown=score.breakdown,
        outreach_priority=score.priority,
        pitch_angle=score.pitch_angle,
        scraped_at=now,
        scrape_quality=scraped.quality_score if scraped else 0,
    )


def write_prospects(
    prospects: list[LocalProspect],
    niche: str,
    city: str,
) -> Path:
    """Write prospects list to JSON file. Returns the file path."""
    _DATA_DIR.mkdir(parents=True, exist_ok=True)

    slug_niche = niche.lower().replace(" ", "-")
    slug_city = city.lower().replace(" ", "-").replace(",", "")
    date_str = datetime.now(_TZ).strftime("%Y-%m-%d")
    filename = f"{slug_niche}_{slug_city}_{date_str}.json"

    out_path = _DATA_DIR / filename
    payload = [p.to_dict() for p in prospects]
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    log.info("Wrote %d prospects to %s", len(prospects), out_path)
    return out_path


def print_summary(prospects: list[LocalProspect], top_n: int = 20) -> None:
    """Print a ranked table of top prospects to terminal."""
    if not prospects:
        print("\n  No prospects found.\n")
        return

    top = prospects[:top_n]
    print(f"\n{'='*100}")
    print(f"  TOP {len(top)} PROSPECTS (sorted by score)")
    print(f"{'='*100}")
    print(
        f"  {'#':>2}  {'Score':>5}  {'Pri':>4}  {'Chatbot':>12}  "
        f"{'Rating':>6}  {'Phone':>14}  {'Name':<35}"
    )
    print(f"  {'-'*2}  {'-'*5}  {'-'*4}  {'-'*12}  {'-'*6}  {'-'*14}  {'-'*35}")

    for i, p in enumerate(top, 1):
        chatbot_col = p.chatbot_name if p.has_chatbot else "None"
        rating_col = f"{p.google_rating:.1f}/{p.review_count}" if p.review_count else "—"
        phone_col = p.phone or "—"
        name_col = p.business_name[:35]
        print(
            f"  {i:>2}  {p.score:>5.1f}  {p.outreach_priority:>4}  {chatbot_col:>12}  "
            f"{rating_col:>6}  {phone_col:>14}  {name_col:<35}"
        )

    print(f"{'='*100}")

    # Priority breakdown
    high = sum(1 for p in top if p.outreach_priority == "HIGH")
    med = sum(1 for p in top if p.outreach_priority == "MEDIUM")
    low = sum(1 for p in top if p.outreach_priority == "LOW")
    print(f"  Priority: {high} HIGH, {med} MEDIUM, {low} LOW")

    # Chatbot stats
    with_bot = sum(1 for p in top if p.has_chatbot)
    print(f"  Chatbots: {with_bot}/{len(top)} already have one")
    print()
