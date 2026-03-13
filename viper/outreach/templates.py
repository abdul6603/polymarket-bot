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

import random
import re

_DEMO_BASE = "https://darkcode-ai.github.io/chatbot-demos/"

# ── Contact name formatting ──

_MEDICAL_NICHES = {"dental", "dentist", "chiropractor", "orthodontist", "doctor"}

# Single-letter initials like "B.", "M.", "J."
_INITIAL_RE = re.compile(r'^[A-Z]\.$')


def _possessive(name: str) -> str:
    """Grammatically correct possessive: 'Associates' -> "Associates'" not "Associates's"."""
    name = name.strip()
    if name.lower().endswith("s"):
        return f"{name}'"
    return f"{name}'s"


def _short_business_name(name: str) -> str:
    """Shorten compound business names for subjects and headings.

    Rules:
    - "Nathan Riel - The Riel Estate Team - Keller Williams Realty"
      → "The Riel Estate Team"  (middle segment, most specific)
    - "John J. Dean Jr. - Engel & Volkers Boston" → "Engel & Volkers Boston"
    - "Darcy Bento, South Boston Realtor - Bento Real Estate Group"
      → "Bento Real Estate Group"
    - Short names stay as-is.

    Strategy: if name has " - " separators, pick the best segment.
    If name has ", " separator, pick the business part (after comma) if it
    looks like a business, otherwise keep the person part.
    """
    name = name.strip()
    if len(name) <= 40:
        return name

    # Split on " - " or " – "
    segments = [s.strip() for s in name.replace(" – ", " - ").split(" - ") if s.strip()]
    if len(segments) >= 3:
        # 3+ segments: middle is usually the team/brand name
        return segments[1]
    if len(segments) == 2:
        # 2 segments: prefer the one that looks like a business (not a person)
        # If first segment is a person name (short, no LLC/Inc/Team/Group), use second
        first, second = segments
        biz_words = ["team", "group", "realty", "real estate", "dental",
                     "associates", "company", "inc", "llc", "partners"]
        if any(w in second.lower() for w in biz_words):
            return second
        if any(w in first.lower() for w in biz_words):
            return first
        return second  # default to second segment

    # Try comma split
    if ", " in name:
        parts = [p.strip() for p in name.split(", ", 1)]
        if len(parts) == 2 and len(parts[1]) > 10:
            return parts[0]  # "Darcy Bento, South Boston Realtor" → "Darcy Bento"

    # Fallback: truncate
    return name[:40]


def format_greeting_name(raw_name: str, niche: str = "") -> str:
    """Format a contact name for email greetings.

    Rules:
    1. Strip everything after first comma (credentials like CRE, DMD, MBA)
    2. Remove single-letter initials (B., M., J.)
    3. Medical niches (dental, chiropractor) → "Dr. [Last Name]"
    4. All other niches → first name only
    5. Never include middle initials or designations

    Examples:
        "B. John Dill, CRE, FRICS"  + real_estate → "John"
        "Dr. Paulomi Naik, DMD"     + dental      → "Dr. Naik"
        "Nicole M. Blanchard"       + real_estate  → "Nicole"
        "Darcy Bento"               + dental       → "Dr. Bento"
    """
    if not raw_name or not raw_name.strip():
        return ""

    # 1. Strip after first comma (credentials)
    name = raw_name.split(",")[0].strip()

    # 2. Split into parts, track and remove "Dr." prefix
    parts = name.split()
    has_dr = False
    clean = []
    for p in parts:
        if p.lower() in ("dr.", "dr"):
            has_dr = True
            continue
        if _INITIAL_RE.match(p):
            continue
        clean.append(p)

    if not clean:
        return ""

    # 3. Determine niche type
    niche_lower = niche.lower().strip() if niche else ""
    is_medical = niche_lower in _MEDICAL_NICHES

    # 4. Format based on niche
    if is_medical:
        # Dr. [Last Name]
        return f"Dr. {clean[-1]}"
    else:
        # First name only
        return clean[0]

# Verified demo slugs — these return 200 on GitHub Pages
_DEMO_SLUGS = {
    "dental": "dental-demo",
    "real_estate": "realestate-demo",
    "commercial_re": "commercial-re-demo",
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
    formatted_name = format_greeting_name(contact_name, niche)
    greeting = f"Hi {formatted_name}" if formatted_name else "Hi team"
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
        pain = _FALLBACK_PAIN.get(niche_key, "website")
        subject = f"Quick question about {_possessive(_short_business_name(business_name))} {pain}"

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

    return f"Quick question about {_possessive(_short_business_name(business_name))} {pain}"


_NICHE_LABELS: dict[str, str] = {
    "dental": "dental practice",
    "real_estate": "real estate agency",
    "commercial_re": "commercial real estate firm",
    "chiropractor": "chiropractic office",
    "auto_repair": "auto shop",
    "general": "business",
}

_FALLBACK_OPENERS: dict[str, str] = {
    "dental": (
        "Your website doesn't have a way to handle patient questions "
        "after hours — every unanswered inquiry is a potential new "
        "patient walking to a competitor."
    ),
    "real_estate": (
        "Your website doesn't have a way to handle buyer questions "
        "after hours — every unanswered inquiry is a potential "
        "showing lost to the next agent."
    ),
    "commercial_re": (
        "Your website doesn't have a way to handle tenant and "
        "investor questions after hours — every unanswered inquiry "
        "is a potential lease or deal walking to the next broker."
    ),
    "chiropractor": (
        "Your website doesn't have a way to handle patient questions "
        "after hours — every unanswered inquiry is someone booking "
        "with the next chiropractor they find."
    ),
    "auto_repair": (
        "Your website doesn't have a way to handle customer questions "
        "after hours — every missed call is a $500+ repair job going "
        "to the shop down the road."
    ),
    "general": (
        "Your website doesn't have a way to handle visitor questions "
        "after hours — every unanswered inquiry is a potential "
        "customer walking to a competitor."
    ),
}


# Humanized opener phrases keyed by finding type.
# Each list has 5-6 variants that sound like Jordan personally visited the site.
# {biz} = business name, {finding} = the raw finding text.
_OPENER_VARIANTS: dict[str, list[str]] = {
    "chatbot": [
        "Spent a few minutes on your site — nothing catching visitors after hours.",
        "Checked out your site — looks like you don't have live chat set up yet.",
        "I pulled up your site on my phone and tried asking a question after 5 PM. No way to get an answer.",
        "I was on your website last night and noticed there's no chatbot or live chat handling after-hours questions.",
        "Looked at your site earlier — visitors with questions outside office hours have nowhere to go.",
        "Went through your website and there's nothing handling visitor questions when the office is closed.",
    ],
    "meta_description": [
        "I Googled your practice and the search preview is just random text pulled from the site — no clear description.",
        "Searched for you on Google and the snippet under your name looks auto-generated. No meta description set.",
        "Looked you up on Google — the preview text doesn't say what you actually do. Missing a meta description.",
        "Pulled up your site in Google search results and the description is generic page text, not a real pitch.",
        "Googled your business and the two-line preview doesn't do you justice — no meta description telling people what you offer.",
    ],
    "viewport": [
        "I opened your site on my phone and it doesn't resize properly — no mobile viewport tag.",
        "Pulled up your website on mobile and the layout is broken. Missing a viewport meta tag.",
        "Checked your site on my phone — it loads the desktop version and you have to pinch-zoom everything.",
        "I visited your website from my iPhone and it's not mobile-friendly. Google penalizes that in rankings.",
        "Looked at your site on mobile — it's not optimized for phones, which is where most people are searching.",
    ],
    "schema": [
        "I checked your site and there's no structured data — your hours, reviews, and address won't show up as rich results on Google.",
        "Looked at your source code and there's no schema markup. You're missing out on those enhanced Google search listings.",
        "Pulled you up on Google — no rich results showing hours or ratings. Your site is missing schema markup.",
        "Checked your website and there's no structured data markup — Google can't display your business info in search results.",
        "Looked at your site and noticed no schema markup. That means no star ratings, hours, or address showing up in Google.",
    ],
    "contact_form": [
        "I tried to reach out through your website and couldn't find a contact form anywhere.",
        "Went through your site looking for a way to send a message — the contact form is either missing or buried deep.",
        "Spent a few minutes on your site trying to find a contact form. Gave up after three clicks.",
        "Checked your website and there's no easy way for visitors to reach you without picking up the phone.",
        "Looked through your site and the contact form is missing — visitors who don't want to call have no way to reach out.",
    ],
    "alt_text": [
        "I looked at your website and most of the images are missing alt text — that hurts both SEO and accessibility.",
        "Checked your site and a big chunk of images have no descriptions. Google can't index what it can't read.",
        "Went through your website — a lot of images are missing alt tags, which means Google is ignoring them entirely.",
        "Pulled up your site and noticed the images aren't tagged with descriptions. That's free SEO left on the table.",
        "Looked at your website source — most images have no alt text. Screen readers can't describe them either.",
    ],
    "faq": [
        "I found your FAQ page and it's got questions that a chatbot could answer instantly, 24/7.",
        "Checked your FAQ — all those questions are exactly what a chat assistant handles automatically.",
        "Looked at your FAQ page and every question there is something an AI assistant could field after hours.",
        "Went through your FAQ and counted the questions — all of them could be automated with a chatbot.",
        "Saw your FAQ section and thought: every single one of these could get an instant answer from a chat assistant.",
    ],
    "ssl": [
        "I visited your website and Chrome flagged it as 'Not Secure' — no SSL certificate.",
        "Pulled up your site and the browser shows a security warning. The site isn't running HTTPS.",
        "Checked your website and it's not using SSL — visitors see a 'Not Secure' warning, which kills trust.",
        "I went to your site and noticed it's still on HTTP. Google ranks HTTPS sites higher and browsers warn visitors.",
        "Looked at your website — no SSL certificate. That 'Not Secure' label in the browser scares people off.",
    ],
    "h1": [
        "I looked at your homepage and the H1 heading is missing — Google uses that to understand what the page is about.",
        "Checked your site and there's no main heading on the homepage. Search engines need that to rank you properly.",
        "Pulled up your homepage source and the H1 tag is missing. That's one of the first things Google looks at.",
        "Went through your website and noticed the homepage doesn't have a proper H1 heading for SEO.",
        "Looked at your site — no H1 on the homepage. That's a quick SEO fix that helps Google understand your business.",
    ],
}

# Catch-all for finding types not in the map above
_GENERIC_OPENERS = [
    "I was looking at your website and spotted something: {finding}.",
    "Spent a few minutes on your site and noticed {finding}.",
    "Checked out your website and one thing stood out — {finding}.",
    "Pulled up your site earlier and saw that {finding}.",
    "I went through your website and found that {finding}.",
    "Looked at your site and noticed {finding}.",
]


def _classify_finding(finding: str) -> str:
    """Map a finding string to a variant key."""
    lower = finding.lower()
    if "chatbot" in lower or "live chat" in lower or "after-hours" in lower:
        return "chatbot"
    if "meta description" in lower:
        return "meta_description"
    if "viewport" in lower or "mobile" in lower:
        return "viewport"
    if "schema" in lower or "structured data" in lower:
        return "schema"
    if "contact form" in lower:
        return "contact_form"
    if "alt text" in lower:
        return "alt_text"
    if "faq" in lower:
        return "faq"
    if "ssl" in lower or "https" in lower or "not secure" in lower:
        return "ssl"
    if "h1" in lower:
        return "h1"
    return "generic"


def _build_opener(finding_lines: list[str], business_name: str, niche_key: str = "general") -> str:
    """Build the email opener from the ONE strongest audit finding.

    Randomly selects from 5-6 human-phrased variants per finding type.
    Uses "your site" phrasing — sounds like Jordan personally visited.
    """
    if not finding_lines:
        return _FALLBACK_OPENERS.get(niche_key, _FALLBACK_OPENERS["general"])

    finding = finding_lines[0]
    ftype = _classify_finding(finding)

    variants = _OPENER_VARIANTS.get(ftype, _GENERIC_OPENERS)
    template = random.choice(variants)

    return template.format(finding=finding)


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
    "commercial_re": {
        "cost": (
            "Tenants and investors researching spaces after hours won't wait "
            "until morning — they'll call the next broker. Every unanswered "
            "inquiry is a lease or sale walking out the door."
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
_FALLBACK_PAIN: dict[str, str] = {
    "dental": "patient inquiries",
    "real_estate": "after-hours leads",
    "commercial_re": "tenant inquiries",
    "chiropractor": "patient intake",
    "auto_repair": "missed calls",
    "general": "website",
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
    "commercial real estate": "commercial_re",
    "commercial": "commercial_re",
    "cre": "commercial_re",
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
