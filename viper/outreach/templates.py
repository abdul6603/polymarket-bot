"""Cold outreach email templates — strict rules.

RULES (Jordan-enforced):
- Subject: NEVER mention DarkCode. Frame as question about THEIR business.
  Pattern: "quick question about [Business Name]'s [specific issue]"
- Body line 1: Specific seo-audit finding from their site.
- Body line 2: Cost of that problem (lost leads, Google penalty, etc.).
- ONE demo link. No video links, no portfolio, no carrd.
- CTA: "If you want a version customized for [Business], I'll build it
  in 24 hours — on me."
- Sign off: Jordan, DarkCode AI

NEVER DO:
- Apologize or hedge ("I know this sounds forward")
- Say "no strings" or "free to try"
- Use "if you're curious"
- Include multiple links
- Open with anything about us ("I built", "I noticed")
- Use generic pain points not from the actual audit
"""
from __future__ import annotations

_DEMO_BASE = "https://darkcode-ai.github.io/chatbot-demos/"

# Verified demo slugs — these return 200 on GitHub Pages
_DEMO_SLUGS = {
    "dental": "dental-demo",
    "real_estate": "realestate-demo",
}


def get_outreach_message(
    niche: str,
    business_name: str,
    demo_url: str,
    contact_name: str = "",
    findings: str = "",
) -> dict[str, str]:
    """Return personalized subject + body for a niche.

    Args:
        findings: Pre-formatted findings string from format_findings_for_email().
                  Each line starts with "- ". First finding becomes the email opener.

    Returns dict with 'subject' and 'body' keys (plain text).
    """
    greeting = f"Hi {contact_name}" if contact_name else "Hi"
    niche_key = resolve_niche_key(niche) if niche not in _NICHE_BODIES else niche

    # Parse findings into individual lines
    finding_lines = []
    if findings:
        finding_lines = [
            line.lstrip("- ").strip()
            for line in findings.strip().splitlines()
            if line.strip()
        ]

    # Build subject from first finding (specific to their site)
    if finding_lines:
        subject = _subject_from_finding(business_name, finding_lines[0])
    else:
        subject = _FALLBACK_SUBJECTS.get(
            niche_key,
            f"Quick question about {business_name}'s website",
        ).format(business_name=business_name)

    # Build body: finding opener → cost → demo → CTA
    opener = _build_opener(finding_lines, business_name, niche_key)
    niche_body = _NICHE_BODIES.get(niche_key, _NICHE_BODIES["general"])
    cost_line = niche_body["cost"].format(business_name=business_name)

    niche_label = _NICHE_LABELS.get(niche_key, "business")
    body = (
        f"{greeting},\n\n"
        f"{opener}\n\n"
        f"{cost_line}\n\n"
        f"I put together a working demo for a similar {niche_label} "
        f"— you can try it here:\n"
        f"{demo_url}\n\n"
        f"If you want a version customized for {business_name}, "
        f"I'll build it in 24 hours — on me.\n\n"
        f"Jordan\n"
        f"DarkCode AI"
    )

    return {"subject": subject, "body": body}


def _subject_from_finding(business_name: str, finding: str) -> str:
    """Generate subject line from the first audit finding.

    Pattern: "Quick question about {Business}'s {specific issue}"
    Never mentions DarkCode.
    """
    # Map common finding patterns to short subject-line pain points
    finding_lower = finding.lower()

    if "chatbot" in finding_lower or "live chat" in finding_lower:
        pain = "after-hours inquiries"
    elif "meta description" in finding_lower:
        pain = "Google search preview"
    elif "viewport" in finding_lower or "mobile" in finding_lower:
        pain = "mobile experience"
    elif "schema" in finding_lower or "structured data" in finding_lower:
        pain = "Google listing"
    elif "alt text" in finding_lower:
        pain = "image SEO"
    elif "faq" in finding_lower:
        pain = "FAQ page"
    elif "contact form" in finding_lower:
        pain = "contact page"
    elif "ssl" in finding_lower or "https" in finding_lower:
        pain = "site security"
    elif "h1" in finding_lower:
        pain = "homepage SEO"
    else:
        pain = "website"

    return f"Quick question about {business_name}'s {pain}"


_NICHE_LABELS: dict[str, str] = {
    "dental": "dental practice",
    "real_estate": "real estate agency",
    "chiropractor": "chiropractic office",
    "auto_repair": "auto shop",
    "general": "business",
}

_FALLBACK_OPENERS: dict[str, str] = {
    "dental": (
        "{business_name}'s website doesn't have a way to handle "
        "patient questions after hours — every unanswered inquiry "
        "is a potential new patient walking to a competitor."
    ),
    "real_estate": (
        "{business_name}'s website doesn't have a way to handle "
        "buyer questions after hours — every unanswered inquiry "
        "is a potential showing lost to the next agent."
    ),
    "chiropractor": (
        "{business_name}'s website doesn't have a way to handle "
        "patient questions after hours — every unanswered inquiry "
        "is someone booking with the next chiropractor they find."
    ),
    "auto_repair": (
        "{business_name}'s website doesn't have a way to handle "
        "customer questions after hours — every missed call is a "
        "$500+ repair job going to the shop down the road."
    ),
    "general": (
        "{business_name}'s website doesn't have a way to handle "
        "visitor questions after hours — every unanswered inquiry "
        "is a potential customer walking to a competitor."
    ),
}


def _build_opener(finding_lines: list[str], business_name: str, niche_key: str = "general") -> str:
    """Build the email opener from audit findings.

    Line 1 = specific finding from their site.
    Line 2+ = additional findings if available (max 2 extra).
    """
    if not finding_lines:
        template = _FALLBACK_OPENERS.get(niche_key, _FALLBACK_OPENERS["general"])
        return template.format(business_name=business_name)

    # First finding is the main opener
    opener = f"{business_name}'s website: {finding_lines[0]}."

    # Add 1-2 more findings as supporting evidence
    extras = finding_lines[1:3]
    if extras:
        extra_text = ". ".join(extras)
        opener += f" Also — {extra_text}."

    return opener


# ── Niche-specific cost lines ──
# Each niche gets a "cost of the problem" that follows the findings opener.

_NICHE_BODIES: dict[str, dict[str, str]] = {
    "dental": {
        "cost": (
            "Most dental practices lose 10-15 new patient inquiries per month "
            "to unanswered after-hours calls and website questions. At $200-500 "
            "per new patient lifetime value, that adds up fast."
        ),
    },
    "real_estate": {
        "cost": (
            "Buyers browsing listings at 10 PM aren't going to wait until "
            "morning for answers — they move to the next agent. Every "
            "unanswered question is a lost showing."
        ),
    },
    "chiropractor": {
        "cost": (
            "When someone's in pain at night, they're searching for help "
            "right then. If your site can't answer their insurance and "
            "availability questions, they'll book with whoever can."
        ),
    },
    "auto_repair": {
        "cost": (
            "When someone's car breaks down, they need answers now — "
            "not a voicemail. Every call that goes unanswered is a $500+ "
            "repair job going to the shop down the road."
        ),
    },
    "general": {
        "cost": (
            "Every visitor who leaves your site with an unanswered question "
            "is a potential customer you'll never see again. Most businesses "
            "lose 20-30% of leads this way."
        ),
    },
}

# Fallback subjects when no audit findings are available
_FALLBACK_SUBJECTS: dict[str, str] = {
    "dental": "Quick question about {business_name}'s patient inquiries",
    "real_estate": "Quick question about {business_name}'s after-hours leads",
    "chiropractor": "Quick question about {business_name}'s patient intake",
    "auto_repair": "Quick question about {business_name}'s missed calls",
    "general": "Quick question about {business_name}'s website",
}


# Map common niche search terms to template keys
NICHE_MAP: dict[str, str] = {
    "dental practice": "dental",
    "dental office": "dental",
    "dentist": "dental",
    "orthodontist": "dental",
    "pediatric dentist": "dental",
    "real estate": "real_estate",
    "realtor": "real_estate",
    "real estate agent": "real_estate",
    "real estate agency": "real_estate",
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


# ── Forum Reply Templates ──
# For community leads (Make.com, n8n, Reddit, etc.)
# Short, casual, demo-first. No pitch, no pricing.
# Jordan copy-pastes into the forum thread manually.

_FORUM_TEMPLATES: dict[str, str] = {
    "automation": (
        "Hey — I build exactly this type of automation. "
        "Here's a working demo of something similar I put together: {demo_url}\n\n"
        "DM me if you want to talk details."
    ),
    "chatbot": (
        "Hey — I build custom chatbots like this. "
        "Here's a live demo you can try right now: {demo_url}\n\n"
        "DM me if you want to talk details."
    ),
    "general": (
        "Hey — I've built something similar. "
        "Here's a working demo: {demo_url}\n\n"
        "DM me if you want to talk details."
    ),
}

_FORUM_TYPE_KEYWORDS: dict[str, list[str]] = {
    "automation": [
        "automation", "workflow", "n8n", "make.com", "zapier",
        "integrate", "api", "trigger",
    ],
    "chatbot": [
        "chatbot", "chat bot", "assistant", "widget",
        "customer support", "faq bot", "ai bot",
    ],
}


def get_forum_reply(
    post_context: str = "",
    demo_url: str = "https://darkcode-ai.github.io/chatbot-demos/belknapdental-com/",
    reply_type: str = "",
) -> str:
    """Generate a short forum reply for community leads.

    Args:
        post_context: Original forum post text (used for type detection).
        demo_url: Demo link to include.
        reply_type: Force a type ("automation", "chatbot", "general").
                    If empty, auto-detects from post_context.

    Returns:
        Ready-to-paste forum reply string.
    """
    if not reply_type and post_context:
        post_lower = post_context.lower()
        for rtype, keywords in _FORUM_TYPE_KEYWORDS.items():
            if any(kw in post_lower for kw in keywords):
                reply_type = rtype
                break

    if not reply_type:
        reply_type = "general"

    template = _FORUM_TEMPLATES.get(reply_type, _FORUM_TEMPLATES["general"])
    return template.format(demo_url=demo_url)
