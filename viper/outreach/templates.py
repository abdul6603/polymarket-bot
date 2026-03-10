"""Niche-personalized cold outreach templates for local business prospecting."""
from __future__ import annotations


def get_outreach_message(
    niche: str,
    business_name: str,
    demo_url: str,
    contact_name: str = "",
) -> dict[str, str]:
    """Return personalized subject + body for a niche.

    Returns dict with 'subject' and 'body' keys (plain text).
    """
    greeting = f"Hi {contact_name}" if contact_name else "Hi there"
    template = _TEMPLATES.get(niche.lower(), _TEMPLATES["general"])
    return {
        "subject": template["subject"].format(business_name=business_name),
        "body": template["body"].format(
            greeting=greeting,
            business_name=business_name,
            demo_url=demo_url,
        ),
    }


_TEMPLATES: dict[str, dict[str, str]] = {
    "dental": {
        "subject": "Quick question for {business_name}",
        "body": (
            "{greeting},\n\n"
            "I noticed {business_name} doesn't have a chat assistant on the website. "
            "I built one that handles the questions your front desk gets "
            "most — insurance, appointment booking, hours, doctor availability.\n\n"
            "Here's a working demo I put together for a practice like yours:\n"
            "{demo_url}\n\n"
            "Reply to this email and I'll build a custom version "
            "for {business_name} within 24 hours — no cost, no commitment.\n\n"
            "Jordan\n"
            "DarkCode AI"
        ),
    },
    "real_estate": {
        "subject": "Quick idea for {business_name}",
        "body": (
            "{greeting},\n\n"
            "Buyers browsing your listings at 11 PM have questions but no one "
            "to ask. I built a chat assistant that handles property details, "
            "showing requests, and neighborhood questions — and captures "
            "their contact info before they move on.\n\n"
            "Here's a working demo:\n"
            "{demo_url}\n\n"
            "Worth a look?\n\n"
            "Jordan\n"
            "DarkCode AI"
        ),
    },
    "chiropractor": {
        "subject": "Quick idea for {business_name}",
        "body": (
            "{greeting},\n\n"
            "Most new patients want to book when the pain hits — not during "
            "office hours. I built a chat assistant that answers insurance "
            "questions, explains treatments, and books appointments around "
            "the clock.\n\n"
            "Here's a working demo for a practice like yours:\n"
            "{demo_url}\n\n"
            "Would this be useful for {business_name}?\n\n"
            "Jordan\n"
            "DarkCode AI"
        ),
    },
    "auto_repair": {
        "subject": "Quick idea for {business_name}",
        "body": (
            "{greeting},\n\n"
            "Car owners Google their problem, find your shop, and then "
            "have to call during business hours. Most don't. I built a "
            "chat assistant that answers service questions, gives estimate "
            "ranges, and books appointments on the spot.\n\n"
            "Here's a working demo:\n"
            "{demo_url}\n\n"
            "Think this could work for {business_name}?\n\n"
            "Jordan\n"
            "DarkCode AI"
        ),
    },
    "general": {
        "subject": "Quick question for {business_name}",
        "body": (
            "{greeting},\n\n"
            "I noticed {business_name} doesn't have a chat assistant on the "
            "website. I built one that handles common questions, books "
            "appointments, and captures visitor info after hours.\n\n"
            "Here's a working demo:\n"
            "{demo_url}\n\n"
            "Worth a quick look?\n\n"
            "Jordan\n"
            "DarkCode AI"
        ),
    },
}

# Map common niche search terms to template keys
NICHE_MAP: dict[str, str] = {
    "dental practice": "dental",
    "dental office": "dental",
    "dentist": "dental",
    "orthodontist": "dental",
    "real estate": "real_estate",
    "realtor": "real_estate",
    "real estate agent": "real_estate",
    "chiropractor": "chiropractor",
    "chiropractic": "chiropractor",
    "auto repair": "auto_repair",
    "auto shop": "auto_repair",
    "mechanic": "auto_repair",
    "car repair": "auto_repair",
}


def resolve_niche_key(niche_query: str) -> str:
    """Map a search query niche to a template key."""
    return NICHE_MAP.get(niche_query.lower(), "general")
