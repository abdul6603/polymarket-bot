"""Score local business prospects on a 1-10 scale."""
from __future__ import annotations

from dataclasses import dataclass, field

from viper.prospecting.maps_scraper import MapsListing
from viper.prospecting.chatbot_detector import ChatbotDetectionResult
from viper.demos.scraper import ScrapedBusiness


@dataclass
class ProspectScore:
    """Scored prospect with breakdown and priority."""
    total: float = 0.0
    breakdown: dict = field(default_factory=dict)
    priority: str = "LOW"  # HIGH, MEDIUM, LOW
    pitch_angle: str = ""


def score_prospect(
    listing: MapsListing,
    scraped: ScrapedBusiness | None,
    chatbot: ChatbotDetectionResult | None,
) -> ProspectScore:
    """Score a prospect across 5 dimensions (max 10 points)."""
    result = ProspectScore()
    bd = {}

    # A. Chatbot status (max 4)
    if chatbot is None or chatbot.confidence == "unknown":
        bd["chatbot"] = 2.0  # can't tell — moderate score
    elif chatbot.has_chatbot:
        bd["chatbot"] = 0.0
    else:
        bd["chatbot"] = 4.0

    # B. Website reachability (max 2)
    if scraped and scraped.pages_scraped > 0:
        bd["website"] = 2.0
    elif listing.website_url:
        bd["website"] = 1.0  # has URL but scrape failed
    else:
        bd["website"] = 0.5  # no website at all

    # C. Contact info (max 2)
    has_email = bool(scraped and scraped.email) if scraped else False
    has_contact_form = bool(scraped and scraped.contact_form_url) if scraped else False
    has_phone = bool(listing.phone or (scraped and scraped.phone))

    if has_email:
        bd["contact"] = 2.0
    elif has_contact_form:
        bd["contact"] = 1.0
    elif has_phone:
        bd["contact"] = 0.5
    else:
        bd["contact"] = 0.0

    # D. Business signals (max 1)
    if listing.rating >= 4.0 and listing.review_count >= 20:
        bd["signals"] = 1.0
    else:
        bd["signals"] = 0.0

    # E. Data quality (max 1)
    if scraped and scraped.quality_score >= 60:
        bd["quality"] = 1.0
    else:
        bd["quality"] = 0.0

    total = sum(bd.values())
    result.total = round(total, 1)
    result.breakdown = bd

    # Priority bands
    if total >= 7:
        result.priority = "HIGH"
    elif total >= 4:
        result.priority = "MEDIUM"
    else:
        result.priority = "LOW"

    # Pitch angle
    result.pitch_angle = _build_pitch(listing, scraped, chatbot)

    return result


def _build_pitch(
    listing: MapsListing,
    scraped: ScrapedBusiness | None,
    chatbot: ChatbotDetectionResult | None,
) -> str:
    """Generate a deterministic one-liner pitch angle for Jordan."""
    if not listing.website_url:
        return "No website — pitch website + chatbot bundle"

    if chatbot and chatbot.has_chatbot:
        return f"Has {chatbot.chatbot_name} — pitch upgrade to custom AI"

    if scraped and scraped.pages_scraped > 0:
        return "No chatbot — pitch 24/7 booking automation"

    return "Website unreachable — pitch modern site + chatbot"
