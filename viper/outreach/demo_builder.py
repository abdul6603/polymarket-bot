"""Demo Builder — generates custom chatbot demo HTML for each prospect.

Reads the generic niche template from ~/chatbot-demos/, scrapes the
prospect's website for real business data, then produces a fully
customized demo page with accurate QA_DATA, team info, hours, etc.

PERMANENT RULE — 8 required data categories:
  1. Practice info (name, phone, address, email)
  2. Hours (full week, CLOSED on days off)
  3. Services (all detected)
  4. Insurance/Payment (plans + payment methods)
  5. Team (doctors/agents)
  6. New Patient info
  7. Emergency info
  8. Lead Capture (booking/appointment)

Quality gate: 7 test questions must ALL pass before Gate 2.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

REPO_DIR = Path.home() / "chatbot-demos"

# Template slugs by niche — STRICT mapping, NO fallback
_TEMPLATE_MAP = {
    "dental": "dental-demo",
    "real_estate": "realestate-demo",
    "commercial_re": "commercial-re-demo",
}

# Niche aliases — normalize sloppy input to canonical niche
_NICHE_ALIASES = {
    "dental": "dental",
    "dentist": "dental",
    "real_estate": "real_estate",
    "real estate": "real_estate",
    "realestate": "real_estate",
    "real-estate": "real_estate",
    "re": "real_estate",
    "realtor": "real_estate",
    "realty": "real_estate",
    "residential": "real_estate",
    "residential real estate": "real_estate",
    "commercial_re": "commercial_re",
    "commercial re": "commercial_re",
    "commercial": "commercial_re",
    "commercial real estate": "commercial_re",
    "cre": "commercial_re",
}

# Cross-contamination blocklists — if ANY of these appear in the wrong
# niche's output, the build MUST be aborted
_DENTAL_ONLY_MARKERS = [
    "Insurance Check",           # dental feature card
    "dental emergencies",        # dental feature card
    "dental insurance",          # dental QA
    "DOCTOR_DATA",               # dental JS variable
    "Dental Assistant",          # dental hero badge
    "dental needs",              # dental QA phrasing
    "root canal",                # purely dental
    "cleaning cost",             # dental cleaning
    "first tooth",               # pediatric dental
    "treating kids",             # pediatric dental
    "dental team",               # dental QA
]
_RESIDENTIAL_RE_ONLY_MARKERS = [
    "Browse Listings",           # RE feature card
    "Schedule Showings",         # RE feature card
    "Home Valuation",            # RE feature card
    "AGENT_DATA",                # RE JS variable
    "AI-Powered Real Estate Assistant",  # RE hero badge (exact)
    "buying process",            # RE QA
    "pre-qualified",             # RE mortgage QA
    "open house",                # RE QA
    "investment properties",     # RE QA
    "dream home",
    "first-time buyers",
    "home inspection",
    "closing costs",
    "mortgage",
]

_COMMERCIAL_RE_ONLY_MARKERS = [
    "Tenant Representation",     # CRE feature card
    "Property Management",       # CRE feature card
    "Asset & Investment",        # CRE feature card
    "BROKER_DATA",               # CRE JS variable
    "Commercial RE Assistant",   # CRE hero badge
    "tenant rep",                # CRE QA
    "flex space",                # CRE QA
    "sq ft managed",             # CRE QA
]

# Placeholder names/phones in generic templates
_DENTAL_PLACEHOLDERS = {
    "name": "Demo Dental Practice",
    "phone": "555-123-4567",
    "tagline": "Your Neighborhood Dental Office",
    "address": "123 Main Street, Suite 200",
}

_REALESTATE_PLACEHOLDERS = {
    "name": "Demo Realty Group",
    "phone": "(555) 987-6543",
    "tagline": "Your Trusted Real Estate Partner",
    "address": "456 Main Street",
}

_COMMERCIAL_RE_PLACEHOLDERS = {
    "name": "Demo CRE Group",
    "phone": "(555) 987-6543",
    "tagline": "Your Trusted Commercial Real Estate Partner",
    "address": "",
}

# Days of the week — used for full-week hours formatting
_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


# ── Public API ──────────────────────────────────────────────────────

def build_demo_html(
    business_name: str,
    niche: str,
    website: str,
    prospect_data: dict,
) -> str:
    """Build a custom chatbot demo HTML page.

    CRITICAL: niche MUST be resolved BEFORE template selection.
    NEVER fall back to dental. Unknown niche = hard error.

    Returns:
        The customized HTML string ready to deploy.

    Raises:
        ValueError: if niche is unknown or cross-contamination detected.
    """
    # 1. STRICT niche resolution — no silent fallback
    canonical_niche = _NICHE_ALIASES.get(niche.lower().strip() if niche else "")
    if not canonical_niche:
        raise ValueError(
            f"[DEMO_BUILDER] UNKNOWN NICHE '{niche}' for {business_name}. "
            f"Valid: {list(_NICHE_ALIASES.keys())}. ABORTING — will NOT default to dental."
        )

    # 2. Load the CORRECT template for this niche
    template_slug = _TEMPLATE_MAP[canonical_niche]
    template_path = REPO_DIR / template_slug / "index.html"
    if not template_path.exists():
        raise FileNotFoundError(f"Template not found: {template_path}")
    template = template_path.read_text()

    log.info("[DEMO_BUILDER] Niche=%s → template=%s for %s",
             canonical_niche, template_slug, business_name)

    # 3. Scrape for real business data
    scraped = _scrape_for_demo(website)

    # 4. Merge all data sources (scraped > prospect_data > defaults)
    data = _merge_data(scraped, prospect_data, business_name)

    # 5. Apply niche-specific customization — NEVER cross-niche
    if canonical_niche == "commercial_re":
        html = _customize_commercial_re(template, data)
    elif canonical_niche == "real_estate":
        html = _customize_realestate(template, data)
    elif canonical_niche == "dental":
        html = _customize_dental(template, data)
    else:
        raise ValueError(f"No customizer for niche: {canonical_niche}")

    # 6. Upgrade lead capture form (dental=6, RE=8, CRE=7 fields)
    html = _upgrade_lead_capture_form(html, niche=canonical_niche)

    # 7. MANDATORY cross-contamination check — abort if wrong niche content
    contamination = _check_niche_contamination(html, canonical_niche)
    if contamination:
        msg = (f"[DEMO_BUILDER] CROSS-CONTAMINATION detected for {business_name} "
               f"(niche={canonical_niche}): {contamination}")
        log.error(msg)
        raise ValueError(msg)

    log.info("[DEMO_BUILDER] Built demo for %s (niche=%s, team=%d, services=%d, quality=%d)",
             business_name, canonical_niche, len(data["team"]), len(data["services"]),
             _calc_data_quality(data))
    return html


def _check_niche_contamination(html: str, niche: str) -> list[str]:
    """Check that the built HTML does NOT contain content from the wrong niche.

    Each niche checks against BOTH other niches' markers.
    Returns list of contamination findings (empty = clean).
    """
    findings: list[str] = []
    html_lower = html.lower()

    if niche == "dental":
        for marker in _RESIDENTIAL_RE_ONLY_MARKERS:
            if marker.lower() in html_lower:
                findings.append(f"dental demo contains residential RE marker: '{marker}'")
        for marker in _COMMERCIAL_RE_ONLY_MARKERS:
            if marker.lower() in html_lower:
                findings.append(f"dental demo contains commercial RE marker: '{marker}'")
    elif niche == "real_estate":
        for marker in _DENTAL_ONLY_MARKERS:
            if marker.lower() in html_lower:
                findings.append(f"RE demo contains dental marker: '{marker}'")
        for marker in _COMMERCIAL_RE_ONLY_MARKERS:
            if marker.lower() in html_lower:
                findings.append(f"RE demo contains commercial RE marker: '{marker}'")
    elif niche == "commercial_re":
        for marker in _DENTAL_ONLY_MARKERS:
            if marker.lower() in html_lower:
                findings.append(f"CRE demo contains dental marker: '{marker}'")
        for marker in _RESIDENTIAL_RE_ONLY_MARKERS:
            if marker.lower() in html_lower:
                findings.append(f"CRE demo contains residential RE marker: '{marker}'")

    return findings


def run_quality_gate(html: str, niche: str = "auto") -> tuple[bool, list[str]]:
    """Run 7 mandatory test questions against the built QA_DATA.

    Dental questions:
      1. What are your hours?
      2. Do you accept my insurance?
      3. What services do you offer?
      4. How do I book an appointment?
      5. Are you accepting new patients?
      6. Do you handle dental emergencies?
      7. Where are you located?

    Real estate questions:
      1. What areas do you cover?
      2. I want to schedule a showing
      3. How does the buying process work with your team?
      4. Can you help me sell my home?
      5. Who is the agent I'd be working with?
      6. What types of properties do you specialize in?
      7. What's your contact info?

    Returns (all_pass, list_of_failures).
    """
    m = re.search(r"var QA_DATA = (\[.*?\]);", html, re.DOTALL)
    if not m:
        return False, ["QA_DATA not found in HTML"]

    try:
        qa = json.loads(m.group(1))
    except json.JSONDecodeError:
        return False, ["QA_DATA JSON parse failed"]

    phone_m = re.search(r'var PHONE = "(.+?)";', html)
    phone = phone_m.group(1) if phone_m else ""
    name_m = re.search(r'var BUSINESS_NAME = "(.+?)";', html)
    biz_name = name_m.group(1) if name_m else ""

    # Auto-detect niche from template markers
    if niche == "auto":
        if "BROKER_DATA" in html:
            niche = "commercial_re"
        elif "AGENT_DATA" in html:
            niche = "real_estate"
        else:
            niche = "dental"

    failures: list[str] = []

    def find_answer(question: str) -> dict | None:
        q_lower = question.lower()
        best = None
        best_score = 0
        for entry in qa:
            score = 0
            for kw in entry.get("kw", []):
                if kw.lower() in q_lower:
                    score += 1
            if score > best_score:
                best_score = score
                best = entry
        return best if best_score > 0 else None

    def _check_no_placeholder(ans: dict, label: str) -> None:
        """Check answer doesn't contain placeholder data."""
        a = ans["a"]
        if "555-123-4567" in a and phone != "555-123-4567":
            failures.append(f"{label}: answer still contains placeholder phone 555-123-4567")
        if "(555) 987-6543" in a and phone != "(555) 987-6543":
            failures.append(f"{label}: answer still contains placeholder phone (555) 987-6543")
        if "Demo Dental" in a and "Demo Dental" not in biz_name:
            failures.append(f"{label}: answer still contains 'Demo Dental'")
        if "Demo Realty" in a and "Demo Realty" not in biz_name:
            failures.append(f"{label}: answer still contains 'Demo Realty'")
        if "Demo CRE" in a and "Demo CRE" not in biz_name:
            failures.append(f"{label}: answer still contains 'Demo CRE'")

    if niche == "commercial_re":
        # CRE Test 1: Areas/Markets
        ans = find_answer("What areas or markets do you cover?")
        if not ans:
            failures.append("areas: no QA entry matched")
        else:
            _check_no_placeholder(ans, "areas")

        # CRE Test 2: Tenant Rep
        ans = find_answer("How does tenant representation work?")
        if not ans:
            failures.append("tenant_rep: no QA entry matched")
        else:
            _check_no_placeholder(ans, "tenant_rep")

        # CRE Test 3: Property Management
        ans = find_answer("What property management services do you offer?")
        if not ans:
            failures.append("property_mgmt: no QA entry matched")
        else:
            _check_no_placeholder(ans, "property_mgmt")

        # CRE Test 4: Property Types
        ans = find_answer("What types of commercial properties do you handle?")
        if not ans:
            failures.append("property_types: no QA entry matched")
        else:
            _check_no_placeholder(ans, "property_types")

        # CRE Test 5: Team/Brokers
        ans = find_answer("Who are the brokers on your team?")
        if not ans:
            failures.append("team: no QA entry matched")
        else:
            _check_no_placeholder(ans, "team")

        # CRE Test 6: Investment Consulting
        ans = find_answer("Can you help with investment analysis?")
        if not ans:
            failures.append("investment: no QA entry matched")
        else:
            _check_no_placeholder(ans, "investment")

        # CRE Test 7: Contact Info
        ans = find_answer("What is your contact info?")
        if not ans:
            failures.append("contact: no QA entry matched")
        else:
            _check_no_placeholder(ans, "contact")
            if phone and phone not in ans["a"]:
                failures.append(f"contact: answer missing real phone ({phone})")

    elif niche == "real_estate":
        # RE Test 1: Areas
        ans = find_answer("What areas do you cover?")
        if not ans:
            failures.append("areas: no QA entry matched")
        else:
            _check_no_placeholder(ans, "areas")

        # RE Test 2: Showings
        ans = find_answer("I want to schedule a showing")
        if not ans:
            failures.append("showing: no QA entry matched")
        else:
            _check_no_placeholder(ans, "showing")
            if phone and phone not in ans["a"]:
                failures.append(f"showing: answer missing real phone ({phone})")

        # RE Test 3: Buying process
        ans = find_answer("How does the buying process work with your team?")
        if not ans:
            failures.append("buying: no QA entry matched")
        else:
            _check_no_placeholder(ans, "buying")

        # RE Test 4: Selling
        ans = find_answer("Can you help me sell my home?")
        if not ans:
            failures.append("selling: no QA entry matched")
        else:
            _check_no_placeholder(ans, "selling")

        # RE Test 5: Agent
        ans = find_answer("Who is the agent I'd be working with?")
        if not ans:
            failures.append("agent: no QA entry matched")
        else:
            _check_no_placeholder(ans, "agent")

        # RE Test 6: Specialties
        ans = find_answer("What types of properties do you specialize in?")
        if not ans:
            failures.append("specialties: no QA entry matched")
        else:
            _check_no_placeholder(ans, "specialties")

        # RE Test 7: Contact info
        ans = find_answer("What's your contact info?")
        if not ans:
            failures.append("contact: no QA entry matched")
        else:
            _check_no_placeholder(ans, "contact")
            if phone and phone not in ans["a"]:
                failures.append(f"contact: answer missing real phone ({phone})")

    else:
        # Dental Test 1: Hours
        ans = find_answer("What are your hours?")
        if not ans:
            failures.append("hours: no QA entry matched")
        else:
            _check_no_placeholder(ans, "hours")

        # Dental Test 2: Insurance
        ans = find_answer("Do you accept my insurance?")
        if not ans:
            failures.append("insurance: no QA entry matched")
        else:
            _check_no_placeholder(ans, "insurance")

        # Dental Test 3: Services
        ans = find_answer("What services do you offer?")
        if not ans:
            failures.append("services: no QA entry matched")
        else:
            _check_no_placeholder(ans, "services")

        # Dental Test 4: Booking
        ans = find_answer("How do I book an appointment?")
        if not ans:
            failures.append("booking: no QA entry matched")
        elif phone and phone not in ans["a"]:
            failures.append(f"booking: answer missing real phone ({phone})")

        # Dental Test 5: New patients
        ans = find_answer("Are you accepting new patients?")
        if not ans:
            failures.append("new_patient: no QA entry matched")
        elif phone and phone not in ans["a"]:
            failures.append(f"new_patient: answer missing real phone ({phone})")

        # Dental Test 6: Emergency
        ans = find_answer("Do you handle dental emergencies?")
        if not ans:
            ans = find_answer("Do you handle emergencies?")
        if not ans:
            failures.append("emergency: no QA entry matched")
        elif phone and phone not in ans["a"]:
            failures.append(f"emergency: answer missing real phone ({phone})")

        # Dental Test 7: Location
        ans = find_answer("Where are you located?")
        if not ans:
            failures.append("location: no QA entry matched")
        elif "123 Main Street" in ans["a"] and biz_name != "Demo Dental Practice":
            failures.append("location: answer still contains placeholder address")

    all_pass = len(failures) == 0
    return all_pass, failures


# ── Tagline cleanup ───────────────────────────────────────────────

def _clean_tagline(raw: str) -> str:
    """Reject garbage taglines (phone numbers, addresses, CTA text)."""
    if not raw:
        return ""
    # Too long = probably scraped junk
    if len(raw) > 80:
        return ""
    # Contains phone number
    if re.search(r'\d{3}[-.\s]?\d{3}[-.\s]?\d{4}', raw):
        return ""
    # Contains street address
    if re.search(r'\d+\s+\w+\s+(st|street|ave|avenue|rd|road|blvd|dr|ln|ct|way|place|pl)\b', raw, re.I):
        return ""
    # Contains CTA / scheduling junk
    if re.search(r'schedule|appointment|book now|call us|contact us|click here', raw, re.I):
        return ""
    # Contains zip code
    if re.search(r'\b\d{5}(-\d{4})?\b', raw):
        return ""
    return raw.strip()


# ── Data merging ────────────────────────────────────────────────────

def _merge_data(scraped, prospect_data: dict, business_name: str) -> dict:
    """Merge scraped data + prospect_data into a single dict.

    Priority: scraped > prospect_data > sensible defaults.
    """
    s = scraped  # May be None

    phone_raw = (
        (s.phone if s else "")
        or prospect_data.get("phone", "")
        or ""
    )
    phone = _format_phone(phone_raw) if phone_raw else ""

    address = (
        (s.address if s else "")
        or prospect_data.get("address", "")
        or ""
    )

    email = (
        prospect_data.get("email", "")
        or (s.email if s else "")
        or ""
    )

    team = (s.team_members if s else []) or []
    hours_raw = (s.hours if s else "") or prospect_data.get("hours", "") or ""
    services = (s.services if s else []) or []
    insurance = (s.insurance_plans if s else []) or []
    payment_methods = (s.payment_methods if s else []) or []
    tagline = _clean_tagline(
        (s.tagline if s else "")
        or (s.description if s else "")
        or prospect_data.get("tagline", "")
        or ""
    )
    brand_color = (s.brand_color if s else "") or ""
    new_patient_info = (s.new_patient_info if s else "") or ""
    accepting_new = s.accepting_new_patients if s else True
    emergency_info = (s.emergency_info if s else "") or ""
    faq_entries = (s.faq_entries if s else []) or []

    # RE-specific
    areas_served = (s.areas_served if s else []) or []
    re_specialties = (s.re_specialties if s else []) or []
    credentials = (s.credentials if s else "") or ""
    languages = (s.languages if s else []) or []
    buying_process = (s.buying_process if s else "") or ""
    selling_process = (s.selling_process if s else "") or ""

    # Commercial RE-specific
    portfolio_sqft = prospect_data.get("portfolio_sqft", "")
    property_types = prospect_data.get("property_types", [])
    firm_history = prospect_data.get("firm_history", "")

    return {
        "name": business_name,
        "phone": phone,
        "address": address,
        "email": email,
        "team": team,
        "hours_raw": hours_raw,
        "services": services,
        "insurance": insurance,
        "payment_methods": payment_methods,
        "tagline": tagline,
        "brand_color": brand_color,
        "new_patient_info": new_patient_info,
        "accepting_new_patients": accepting_new,
        "emergency_info": emergency_info,
        "faq_entries": faq_entries,
        # RE-specific
        "areas_served": areas_served,
        "re_specialties": re_specialties,
        "credentials": credentials,
        "languages": languages,
        "buying_process": buying_process,
        "selling_process": selling_process,
        # Commercial RE-specific
        "portfolio_sqft": portfolio_sqft,
        "property_types": property_types,
        "firm_history": firm_history,
    }


def _calc_data_quality(data: dict) -> int:
    """0-100 score of how complete the merged data is."""
    score = 0
    if data["name"]:
        score += 15
    if data["phone"]:
        score += 15
    if data["email"]:
        score += 10
    if data["address"]:
        score += 10
    if data["hours_raw"]:
        score += 10
    if data["services"]:
        score += 15
    if data["team"]:
        score += 5
    if data["insurance"] or data["payment_methods"]:
        score += 10
    if data["tagline"]:
        score += 10
    return min(score, 100)


# ── Phone formatting ───────────────────────────────────────────────

def _format_phone(phone: str) -> str:
    """Normalize any phone to (XXX) XXX-XXXX format."""
    digits = re.sub(r'\D', '', phone)
    if len(digits) == 11 and digits[0] == '1':
        digits = digits[1:]
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    return phone


# ── Hours formatting ───────────────────────────────────────────────

def _format_hours_full_week(hours_raw: str) -> str:
    """Format hours as full week with CLOSED days explicitly marked.

    Input: raw hours string like "Mon-Fri 8am-5pm" or structured text.
    Output: Multi-line full week like:
      Monday: 8:00 AM - 5:00 PM
      Tuesday: 8:00 AM - 5:00 PM
      ...
      Saturday: CLOSED
      Sunday: CLOSED
    """
    if not hours_raw:
        return ""

    hours_lower = hours_raw.lower()
    day_hours: dict[str, str] = {}

    # Try to parse "Mon-Fri 8am-5pm" style
    range_match = re.search(
        r'(mon(?:day)?)\s*[-–to]+\s*(fri(?:day)?|thu(?:rsday)?|sat(?:urday)?)'
        r'[:\s]+(\d{1,2}(?::\d{2})?\s*(?:am|pm)\s*[-–to]+\s*\d{1,2}(?::\d{2})?\s*(?:am|pm))',
        hours_lower,
    )
    if range_match:
        end_day = range_match.group(2)[:3]
        time_range = range_match.group(3).strip()
        time_range = _normalize_time_range(time_range)

        day_map = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
        end_idx = day_map.get(end_day, 4)

        for i, day in enumerate(_DAYS):
            if i <= end_idx:
                day_hours[day] = time_range
            else:
                day_hours[day] = "CLOSED"
    else:
        # Try individual day patterns
        for day in _DAYS:
            short = day[:3].lower()
            pattern = rf'{short}(?:\w*)?[:\s]+(\d{{1,2}}(?::\d{{2}})?\s*(?:am|pm)\s*[-–to]+\s*\d{{1,2}}(?::\d{{2}})?\s*(?:am|pm))'
            m = re.search(pattern, hours_lower)
            if m:
                day_hours[day] = _normalize_time_range(m.group(1))

        # Check for "closed" mentions
        for day in _DAYS:
            short = day[:3].lower()
            if re.search(rf'{short}(?:\w*)?[:\s]+closed', hours_lower):
                day_hours[day] = "CLOSED"

    if not day_hours:
        # Can't parse structured — return raw but trim
        return hours_raw[:150]

    # Fill missing days as CLOSED
    for day in _DAYS:
        if day not in day_hours:
            day_hours[day] = "CLOSED"

    lines = [f"{day}: {day_hours[day]}" for day in _DAYS]
    return "\n".join(lines)


def _normalize_time_range(t: str) -> str:
    """Normalize '8am-5pm' to '8:00 AM - 5:00 PM'."""
    parts = re.split(r'\s*[-–to]+\s*', t.strip(), maxsplit=1)
    if len(parts) == 2:
        return f"{_normalize_time(parts[0])} - {_normalize_time(parts[1])}"
    return t.strip()


def _normalize_time(t: str) -> str:
    """Normalize '8am' or '8:30am' to '8:00 AM' or '8:30 AM'."""
    t = t.strip()
    m = re.match(r'(\d{1,2})(?::(\d{2}))?\s*(am|pm)', t, re.IGNORECASE)
    if m:
        hour = m.group(1)
        minute = m.group(2) or "00"
        ampm = m.group(3).upper()
        return f"{hour}:{minute} {ampm}"
    return t


# ── Dental customization ───────────────────────────────────────────

def _customize_dental(template: str, data: dict) -> str:
    """Customize the generic dental template with real business data."""
    old = _DENTAL_PLACEHOLDERS
    html = template
    name = data["name"]
    phone = data["phone"] or old["phone"]

    # ── HTML replacements ──
    html = re.sub(r"<title>.*?</title>",
                  f"<title>{_html_escape(name)} - AI Assistant Demo</title>", html)
    html = html.replace(f"<h1>{old['name']}</h1>", f"<h1>{_html_escape(_short_business_name(name))}</h1>")
    html = html.replace(f"<p>{old['tagline']}</p>",
                        f"<p>{_html_escape(data['tagline'] or 'Your Trusted Dental Care Provider')}</p>")
    html = html.replace(f"<h4>{old['name']}</h4>", f"<h4>{_html_escape(name)}</h4>")

    # Brand color
    if data["brand_color"] and data["brand_color"] != "#2563eb":
        html = re.sub(r"--brand:\s*#[0-9a-fA-F]{6};", f"--brand: {data['brand_color']};", html)

    # ── JavaScript variable replacements ──
    html = re.sub(r'var BUSINESS_NAME = ".*?";',
                  f'var BUSINESS_NAME = "{_js_escape(name)}";', html)
    html = re.sub(r'var PHONE = ".*?";',
                  f'var PHONE = "{_js_escape(phone)}";', html)

    # Replace DOCTOR_DATA if we have team members
    if data["team"]:
        doctor_js = _build_doctor_data_js(data["team"])
        html = re.sub(r"var DOCTOR_DATA = \[.*?\];", f"var DOCTOR_DATA = {doctor_js};",
                      html, flags=re.DOTALL)

    # ── Build comprehensive QA_DATA ──
    qa = _build_dental_qa(data)
    qa_json = json.dumps(qa, ensure_ascii=False)
    html = re.sub(r"var QA_DATA = \[.*?\];",
                  lambda m: f"var QA_DATA = {qa_json};",
                  html, flags=re.DOTALL)

    # ── QUICK_ACTIONS ──
    quick = [
        {"label": "Book Appointment", "q": "How do I book an appointment?"},
        {"label": "Insurance", "q": "Do you accept my insurance?"},
        {"label": "Services", "q": "What services do you offer?"},
        {"label": "Emergency", "q": "Do you handle dental emergencies?"},
    ]
    quick_json = json.dumps(quick)
    html = re.sub(r"var QUICK_ACTIONS = \[.*?\];",
                  lambda m: f"var QUICK_ACTIONS = {quick_json};",
                  html, flags=re.DOTALL)

    # ── Bulk phone replacement for any remaining references ──
    if phone != old["phone"]:
        html = html.replace(old["phone"], phone)

    return html


def _build_dental_qa(data: dict) -> list[dict]:
    """Build comprehensive dental QA_DATA covering all 8 required categories."""
    qa: list[dict] = []
    name = data["name"]
    phone = data["phone"] or "our office"
    phone_cta = f"Call us at {phone}" if data["phone"] else "Contact us"

    # ── Category 1: Practice Info ──
    contact_parts = []
    if data["phone"]:
        contact_parts.append(f"Phone: {phone}")
    if data["address"]:
        contact_parts.append(f"Address: {data['address']}")
    if data["email"]:
        contact_parts.append(f"Email: {data['email']}")
    contact_text = "\n".join(contact_parts) if contact_parts else f"{phone_cta} for details."

    qa.append({
        "q": "What is your contact info?",
        "a": f"Here's how to reach {name}:\n\n{contact_text}",
        "kw": ["contact info", "contact information", "how to contact", "reach you",
               "get in touch", "contact details", "how do i contact", "how do i reach", "contact"],
        "cat": "contact",
    })
    qa.append({
        "q": "What is your phone number?",
        "a": f"You can reach us at {phone}. We're happy to help!",
        "kw": ["phone", "phone number", "your phone", "call you", "telephone", "your number", "number"],
        "cat": "contact",
    })
    if data["email"]:
        qa.append({
            "q": "What is your email?",
            "a": f"You can email us at {data['email']}, or call {phone}.",
            "kw": ["email", "email address", "your email", "e-mail", "mail"],
            "cat": "contact",
        })
    else:
        qa.append({
            "q": "What is your email?",
            "a": f"For email inquiries, visit our website to use our contact form, or call us directly at {phone}.",
            "kw": ["email", "email address", "your email", "e-mail", "mail"],
            "cat": "contact",
        })

    # ── Category 2: Hours (full week) ──
    hours_formatted = _format_hours_full_week(data["hours_raw"])
    if hours_formatted:
        qa.append({
            "q": "What are your hours?",
            "a": f"Our hours at {name}:\n\n{hours_formatted}\n\n{phone_cta} if you need to confirm availability.",
            "kw": ["hours", "open", "close", "time", "schedule", "when", "business hours", "office hours"],
            "cat": "hours",
        })
    else:
        qa.append({
            "q": "What are your hours?",
            "a": f"Please call us at {phone} for our current office hours. We'll be happy to find a time that works for you!",
            "kw": ["hours", "open", "close", "time", "schedule", "when", "business hours", "office hours"],
            "cat": "hours",
        })

    qa.append({
        "q": "Can I see you on weekends?",
        "a": f"Please call us at {phone} to check our weekend availability. We understand busy schedules and try to offer convenient appointment times.",
        "kw": ["weekend", "saturday", "sunday", "after hours", "evening"],
        "cat": "hours",
    })

    # ── Category 3: Services ──
    if data["services"]:
        svc_text = ", ".join(data["services"][:15])
        qa.append({
            "q": "What services do you offer?",
            "a": f"At {name}, we offer {svc_text}. We're here for all your dental needs!\n\n{phone_cta} to schedule a consultation.",
            "kw": ["services", "what do you offer", "provide", "do you do", "treatments", "procedures", "services do you"],
            "cat": "services",
        })
    else:
        qa.append({
            "q": "What services do you offer?",
            "a": f"At {name}, we provide comprehensive dental care including preventive, restorative, and cosmetic services.\n\n{phone_cta} to discuss your specific needs.",
            "kw": ["services", "what do you offer", "provide", "do you do", "treatments", "procedures", "services do you"],
            "cat": "services",
        })

    # Individual service entries for common dental services
    _dental_service_qa = [
        ("Do you offer teeth whitening?", ["whitening", "whiten", "white", "bright", "bleach", "stain"],
         "Teeth Whitening", "cosmetic"),
        ("Do you offer Invisalign?", ["invisalign", "braces", "straighten", "alignment", "crooked", "orthodont"],
         "Invisalign", "orthodontics"),
        ("What about dental implants?", ["implant", "missing tooth", "replace", "permanent"],
         "Dental Implants", "implants"),
        ("Do you do root canals?", ["root canal", "endodontic", "nerve", "infected tooth"],
         "Root Canal", "services"),
        ("Do you offer sedation dentistry?", ["sedation", "anxious", "nervous", "afraid", "fear", "anxiety", "sleep", "nitrous"],
         "Sedation Dentistry", "comfort"),
    ]
    for q, kw, svc_name, cat in _dental_service_qa:
        if any(svc_name.lower() in s.lower() for s in data["services"]):
            qa.append({
                "q": q,
                "a": f"Yes! We offer {svc_name.lower()} at {name}. {phone_cta} to schedule a consultation.",
                "kw": kw, "cat": cat,
            })

    qa.append({
        "q": "Do you see children?",
        "a": f"Absolutely! We love treating kids at {name}. We recommend bringing children in for their first visit by age 1 or when their first tooth appears.",
        "kw": ["children", "kids", "child", "pediatric", "baby", "toddler", "son", "daughter"],
        "cat": "pediatric",
    })

    # ── Category 4: Insurance / Payment ──
    if data["insurance"]:
        plans_text = ", ".join(data["insurance"][:12])
        qa.append({
            "q": "Do you accept my insurance?",
            "a": f"We accept a wide range of dental insurance, including:\n\n{plans_text}.\n\nNot sure if yours is covered? {phone_cta} and we'll verify your benefits for free.",
            "kw": ["insurance", "accept", "cover", "plan", "delta", "cigna", "aetna", "metlife",
                   "bcbs", "blue cross", "anthem", "guardian", "geha", "unum", "principal",
                   "insurance companies", "what insurance", "which insurance", "insurance do you", "accept insurance"],
            "cat": "insurance",
        })
    else:
        qa.append({
            "q": "Do you accept my insurance?",
            "a": f"We accept most major dental insurance plans. {phone_cta} and we'll verify your specific coverage for free!",
            "kw": ["insurance", "accept", "cover", "plan", "delta", "cigna", "aetna", "metlife",
                   "bcbs", "blue cross", "anthem", "guardian", "geha", "unum", "principal",
                   "insurance companies", "what insurance", "which insurance", "insurance do you", "accept insurance"],
            "cat": "insurance",
        })

    qa.append({
        "q": "What insurance do you accept for orthodontics?",
        "a": f"For orthodontic coverage, please call us at {phone} to verify your specific plan. Most major plans provide orthodontic benefits.",
        "kw": ["orthodontic insurance", "ortho insurance", "braces insurance", "braces coverage",
               "invisalign insurance", "orthodontic coverage"],
        "cat": "insurance",
    })
    qa.append({
        "q": "What if my insurance is out-of-network?",
        "a": f"No problem! If you're out-of-network, we'll submit insurance claims on your behalf so you can still get reimbursed. {phone_cta} and we'll help you understand your coverage.",
        "kw": ["out-of-network", "out of network", "not in network", "oon"],
        "cat": "insurance",
    })

    # Payment methods
    if data["payment_methods"]:
        pay_text = ", ".join(data["payment_methods"])
        qa.append({
            "q": "What payment options do you have?",
            "a": f"We accept {pay_text}. {phone_cta} to discuss payment options.",
            "kw": ["payment", "pay", "finance", "financing", "credit", "cash", "payment plan",
                   "afford", "carecredit", "care credit", "out of network", "out-of-network",
                   "offer finance", "offer financing", "finance option", "financing option",
                   "pay for", "how to pay", "financing available"],
            "cat": "billing",
        })
    else:
        qa.append({
            "q": "What payment options do you have?",
            "a": f"We accept most major credit cards and offer flexible payment options. {phone_cta} to discuss the best option for your treatment.",
            "kw": ["payment", "pay", "finance", "financing", "credit", "cash", "payment plan",
                   "afford", "carecredit", "care credit", "offer finance", "offer financing",
                   "pay for", "how to pay", "financing available"],
            "cat": "billing",
        })

    qa.append({
        "q": "How much does a cleaning cost?",
        "a": f"Cleaning costs vary based on your insurance coverage. Most plans cover preventive cleanings at 100%. {phone_cta} for a cost estimate with your specific insurance.",
        "kw": ["cost", "price", "how much", "cleaning", "fee", "charge", "expensive"],
        "cat": "pricing",
    })

    # ── Category 5: Team ──
    if data["team"]:
        if len(data["team"]) == 1:
            team_text = f"Our doctor is {data['team'][0]}."
        else:
            team_list = "\n".join(f"- {t}" for t in data["team"])
            team_text = f"We have {len(data['team'])} doctors on our team:\n\n{team_list}"

        qa.append({
            "q": "Who are your dentists?",
            "a": f"{team_text}\n\n{phone_cta} to schedule with any of our doctors.",
            "kw": ["dentist", "doctor", "who", "team", "staff", "provider", "dr",
                   "doctors", "how many", "meet the team", "docs"],
            "cat": "team",
        })
        qa.append({
            "q": "Who are your doctors?",
            "a": f"{team_text}\n\n{phone_cta} to schedule with any of our doctors.",
            "kw": ["doctors", "doctor", "who", "team", "staff", "provider", "dr",
                   "works there", "how many", "meet the team", "docs"],
            "cat": "doctor",
        })

        # Individual doctor entries
        for member in data["team"]:
            parts = member.split()
            clean = [p.rstrip(",") for p in parts if p not in ("Dr.", "Dr", "DMD", "DDS", "MD")]
            first_name = clean[0].lower() if clean else ""
            last_name = clean[-1].lower() if len(clean) > 1 else ""

            kw = [k for k in [last_name, first_name, f"dr {last_name}"] if k]
            qa.append({
                "q": f"Tell me about {member}",
                "a": f"{member} is part of our team at {name}. {phone_cta} to schedule an appointment.",
                "kw": kw,
                "cat": "doctor",
            })
    else:
        qa.append({
            "q": "Who are your dentists?",
            "a": f"Our experienced dental team at {name} is ready to help! {phone_cta} to schedule an appointment.",
            "kw": ["dentist", "doctor", "who", "team", "staff", "provider", "dr",
                   "doctors", "how many", "meet the team", "docs"],
            "cat": "team",
        })

    # ── Category 6: New Patients ──
    new_patient_text = ""
    if not data["accepting_new_patients"]:
        new_patient_text = f"Unfortunately, we are not accepting new patients at this time. Please {phone_cta.lower()} for waitlist information."
    elif data["new_patient_info"]:
        new_patient_text = f"Yes! {name} is welcoming new patients. {data['new_patient_info']}\n\n{phone_cta} to get started!"
    else:
        new_patient_text = f"Yes! {name} is always welcoming new patients. {phone_cta} to get started — we'll make your first visit easy and comfortable."

    qa.append({
        "q": "Are you accepting new patients?",
        "a": new_patient_text,
        "kw": ["new patient", "accepting", "first time", "join", "sign up", "register"],
        "cat": "new_patient",
    })
    qa.append({
        "q": "What should I expect at my first visit?",
        "a": f"Your first visit includes a comprehensive exam, X-rays, and a cleaning. We'll discuss your dental health and create a personalized treatment plan. Plan for about 60-90 minutes.",
        "kw": ["first visit", "first appointment", "expect", "what happens", "new patient visit"],
        "cat": "new_patient",
    })

    # ── Category 7: Emergency ──
    if data["emergency_info"]:
        emergency_text = f"Yes, we handle dental emergencies! {data['emergency_info']}\n\n{phone_cta} immediately for emergency care."
    else:
        emergency_text = f"Yes, we handle dental emergencies! If you're in pain or had an accident, call {phone} immediately. We'll get you in as soon as possible."

    qa.append({
        "q": "Do you handle dental emergencies?",
        "a": emergency_text,
        "kw": ["emergency", "emergencies", "urgent", "pain", "toothache", "broken", "knocked out", "accident"],
        "cat": "emergency",
    })

    # ── Category 8: Booking / Lead Capture ──
    qa.append({
        "q": "How do I book an appointment?",
        "a": f"You can book by calling us at {phone} or by leaving your info in the chat! We'll find a time that works for you.",
        "kw": ["appointment", "book", "schedule", "visit", "come in"],
        "cat": "booking",
    })
    qa.append({
        "q": "How do I book online?",
        "a": f"You can book online at our website or call us at {phone}. We'll find a time that works for you!",
        "kw": ["book online", "online booking", "online appointment", "website booking", "online schedule"],
        "cat": "booking",
    })
    qa.append({
        "q": "Do you take walk-ins?",
        "a": f"We prefer appointments so we can give you dedicated time, but we do our best to accommodate walk-ins and emergencies. {phone_cta} to check availability.",
        "kw": ["walk-in", "walk in", "without appointment", "drop in", "no appointment"],
        "cat": "booking",
    })

    # ── Location ──
    if data["address"]:
        qa.append({
            "q": "Where are you located?",
            "a": f"We're located at {data['address']}. {phone_cta} for directions.",
            "kw": ["location", "located", "where", "address", "directions", "find you", "parking"],
            "cat": "location",
        })
    else:
        qa.append({
            "q": "Where are you located?",
            "a": f"Please visit our website or call {phone} for our office address and directions.",
            "kw": ["location", "located", "where", "address", "directions", "find you", "parking"],
            "cat": "location",
        })

    # ── Misc standard entries ──
    qa.append({
        "q": "What COVID precautions do you take?",
        "a": "Patient safety is our top priority. We follow all CDC and ADA guidelines including enhanced sanitization, air filtration, and screening protocols.",
        "kw": ["covid", "safety", "precaution", "sanitize", "clean", "protocol", "safe"],
        "cat": "safety",
    })

    # ── Scraped FAQ entries ──
    for faq in data.get("faq_entries", [])[:10]:
        q_text = faq.get("q", "")
        a_text = faq.get("a", "")
        if q_text and a_text:
            # Generate keywords from the question
            words = re.findall(r'[a-z]{3,}', q_text.lower())
            stop_words = {"what", "how", "does", "your", "the", "you", "can", "are", "this", "that", "with", "for", "from"}
            kw = [w for w in words if w not in stop_words][:6]
            if kw:
                qa.append({"q": q_text, "a": a_text, "kw": kw, "cat": "faq"})

    return qa


# ── Real estate customization ──────────────────────────────────────

def _customize_realestate(template: str, data: dict) -> str:
    """Customize the generic real estate template with real business data."""
    old = _REALESTATE_PLACEHOLDERS
    html = template
    name = data["name"]
    phone = data["phone"] or old["phone"]

    # ── HTML replacements ──
    html = re.sub(r"<title>.*?</title>",
                  f"<title>{_html_escape(name)} - AI Assistant Demo</title>", html)
    html = html.replace(f"<h1>{old['name']}</h1>", f"<h1>{_html_escape(_short_business_name(name))}</h1>")
    html = html.replace(f'<div class="tagline">{old["tagline"]}</div>',
                        f'<div class="tagline">{_html_escape(data["tagline"] or "Your Trusted Real Estate Partner")}</div>')
    html = html.replace(f"<h4>{old['name']}</h4>", f"<h4>{_html_escape(name)}</h4>")

    # ── JavaScript variable replacements ──
    html = re.sub(r'var BUSINESS_NAME = ".*?";',
                  f'var BUSINESS_NAME = "{_js_escape(name)}";', html)
    html = re.sub(r'var PHONE = ".*?";',
                  f'var PHONE = "{_js_escape(phone)}";', html)

    # Replace AGENT_DATA if we have team members
    if data["team"]:
        agent_js = _build_agent_data_js(data["team"])
        html = re.sub(r"var AGENT_DATA = \[.*?\];", f"var AGENT_DATA = {agent_js};",
                      html, flags=re.DOTALL)

    # ── Build comprehensive QA_DATA ──
    qa = _build_realestate_qa(data)
    qa_json = json.dumps(qa, ensure_ascii=False)
    html = re.sub(r"var QA_DATA = \[.*?\];",
                  lambda m: f"var QA_DATA = {qa_json};",
                  html, flags=re.DOTALL)

    # ── QUICK_ACTIONS ──
    quick = [
        {"label": "Browse Listings", "q": "What properties do you have available?"},
        {"label": "Schedule Showing", "q": "I want to schedule a showing"},
        {"label": "Sell My Home", "q": "Can you help me sell my home?"},
        {"label": "Free Valuation", "q": "Can you do a market analysis or home valuation?"},
    ]
    quick_json = json.dumps(quick)
    html = re.sub(r"var QUICK_ACTIONS = \[.*?\];",
                  lambda m: f"var QUICK_ACTIONS = {quick_json};",
                  html, flags=re.DOTALL)

    # ── Bulk phone/name replacement ──
    if phone != old["phone"]:
        html = html.replace(old["phone"], phone)
    html = html.replace(old["name"], name)

    return html


def _build_realestate_qa(data: dict) -> list[dict]:
    """Build comprehensive real estate QA_DATA covering all required categories."""
    qa: list[dict] = []
    name = data["name"]
    phone = data["phone"] or "our office"
    phone_cta = f"Call us at {phone}" if data["phone"] else "Contact us"

    # ── Contact Info ──
    contact_parts = []
    if data["phone"]:
        contact_parts.append(f"Phone: {phone}")
    if data["address"]:
        contact_parts.append(f"Address: {data['address']}")
    if data["email"]:
        contact_parts.append(f"Email: {data['email']}")
    contact_text = "\n".join(contact_parts) if contact_parts else f"{phone_cta} for details."

    qa.append({
        "q": "What is your contact info?",
        "a": f"Here's how to reach {name}:\n\n{contact_text}",
        "kw": ["contact info", "contact information", "how to contact", "reach you",
               "get in touch", "contact details", "contact"],
        "cat": "contact",
    })
    qa.append({
        "q": "What is your phone number?",
        "a": f"You can reach us at {phone}. We're happy to help!",
        "kw": ["phone", "phone number", "call you", "telephone", "your number", "number"],
        "cat": "contact",
    })
    if data["email"]:
        qa.append({
            "q": "What is your email?",
            "a": f"You can email us at {data['email']}, or call {phone}. We respond within one business day.",
            "kw": ["email", "email address", "your email", "e-mail", "mail"],
            "cat": "contact",
        })

    # ── Hours ──
    hours_formatted = _format_hours_full_week(data["hours_raw"])
    if hours_formatted:
        qa.append({
            "q": "What are your hours?",
            "a": f"Our office hours at {name}:\n\n{hours_formatted}\n\nNeed to meet outside these hours? Our agents are flexible — {phone_cta.lower()} to arrange a time.",
            "kw": ["hours", "open", "close", "time", "schedule", "when", "office hours", "business hours"],
            "cat": "hours",
        })
    else:
        qa.append({
            "q": "What are your hours?",
            "a": f"Our agents are available by appointment and our office is open during regular business hours. {phone_cta} to schedule a time that works for you.",
            "kw": ["hours", "open", "close", "time", "schedule", "when", "office hours", "business hours"],
            "cat": "hours",
        })

    # ── Areas Served ──
    if data.get("areas_served"):
        areas_text = ", ".join(data["areas_served"][:12])
        qa.append({
            "q": "What areas do you cover?",
            "a": f"We serve {areas_text} and surrounding communities. Whether you're buying or selling in any of these areas, our agents know the local market inside and out.\n\n{phone_cta} to discuss properties in your preferred area!",
            "kw": ["areas", "cover", "serve", "neighborhoods", "cities", "towns", "where",
                   "service area", "what areas", "which areas", "communities"],
            "cat": "areas",
        })
    else:
        qa.append({
            "q": "What areas do you cover?",
            "a": f"We serve the local area and surrounding communities. {phone_cta} to discuss properties in your preferred neighborhood!",
            "kw": ["areas", "cover", "serve", "neighborhoods", "cities", "towns", "where",
                   "service area", "what areas", "which areas", "communities"],
            "cat": "areas",
        })

    # ── Specialties / Property Types ──
    if data.get("re_specialties"):
        spec_text = ", ".join(data["re_specialties"][:8])
        qa.append({
            "q": "What types of properties do you specialize in?",
            "a": f"At {name}, we specialize in {spec_text}.\n\nNo matter what type of property you're looking for, we have the expertise to help. {phone_cta}!",
            "kw": ["specialize", "specialty", "specialties", "types of properties", "property types",
                   "what kind", "what type", "focus", "expertise"],
            "cat": "specialties",
        })
    else:
        qa.append({
            "q": "What types of properties do you specialize in?",
            "a": f"At {name}, we work with all property types including single-family homes, condos, townhomes, and investment properties.\n\n{phone_cta} to discuss what you're looking for!",
            "kw": ["specialize", "specialty", "specialties", "types of properties", "property types",
                   "what kind", "what type", "focus", "expertise"],
            "cat": "specialties",
        })

    # ── Services ──
    if data["services"]:
        svc_text = ", ".join(data["services"][:12])
        qa.append({
            "q": "What services do you offer?",
            "a": f"At {name}, we provide {svc_text}.\n\n{phone_cta} to discuss how we can help with your real estate goals.",
            "kw": ["services", "what do you offer", "provide", "do you do"],
            "cat": "services",
        })
    else:
        qa.append({
            "q": "What services do you offer?",
            "a": f"At {name}, we provide comprehensive real estate services including buyer representation, seller representation, market analysis, home valuation, and more.\n\n{phone_cta}.",
            "kw": ["services", "what do you offer", "provide", "do you do"],
            "cat": "services",
        })

    # ── Team / Agents ──
    creds_text = f" ({data['credentials']})" if data.get("credentials") else ""
    lang_text = ""
    if data.get("languages"):
        lang_text = f"\n\nLanguages: English, {', '.join(data['languages'])}"

    if data["team"]:
        if len(data["team"]) == 1:
            team_text = f"Your agent is {data['team'][0]}{creds_text}."
        else:
            team_list = "\n".join(f"- {t}" for t in data["team"])
            team_text = f"We have {len(data['team'])} experienced agents on our team{creds_text}:\n\n{team_list}"

        qa.append({
            "q": "Who is the agent I'd be working with?",
            "a": f"{team_text}{lang_text}\n\n{phone_cta} to connect with the right agent for your needs!",
            "kw": ["agent", "agents", "who", "team", "staff", "realtors", "realtor",
                   "meet the team", "your team", "working with", "who would i", "which agent"],
            "cat": "team",
        })

        # Individual agent entries
        for member in data["team"]:
            parts = member.split()
            first_name = parts[0].lower()
            last_name = parts[-1].lower().rstrip(",") if len(parts) >= 2 else ""

            kw = [k for k in [last_name, first_name] if k]
            qa.append({
                "q": f"Tell me about {member}",
                "a": f"{member} is part of our team at {name}. {phone_cta} to schedule a consultation.",
                "kw": kw,
                "cat": "agent",
            })
    else:
        qa.append({
            "q": "Who is the agent I'd be working with?",
            "a": f"Our experienced team at {name}{creds_text} is ready to help!{lang_text}\n\n{phone_cta} to connect with an agent.",
            "kw": ["agent", "agents", "who", "team", "staff", "realtors", "realtor",
                   "meet the team", "working with", "who would i"],
            "cat": "team",
        })

    # ── Buying Process ──
    if data.get("buying_process"):
        qa.append({
            "q": "How does the buying process work with your team?",
            "a": f"{data['buying_process']}\n\n{phone_cta} to schedule a free buyer consultation!",
            "kw": ["buying process", "buy a home", "buy a house", "how to buy", "steps to buy",
                   "buying steps", "purchase", "homebuying", "buyer consultation"],
            "cat": "buying",
        })
    else:
        qa.append({
            "q": "How does the buying process work with your team?",
            "a": f"The home buying process with {name}:\n\n1. Free buyer consultation to understand your needs\n2. Get pre-qualified with our preferred lenders\n3. Browse listings and tour homes\n4. Make a competitive offer\n5. Home inspection and appraisal\n6. Negotiate and finalize\n7. Closing day — get your keys!\n\nOur agents guide you through every step. {phone_cta} to get started!",
            "kw": ["buying process", "buy a home", "buy a house", "how to buy", "steps to buy",
                   "buying steps", "purchase", "homebuying", "buyer consultation"],
            "cat": "buying",
        })

    # ── Selling Process ──
    if data.get("selling_process"):
        qa.append({
            "q": "Can you help me sell my home?",
            "a": f"Absolutely! {data['selling_process']}\n\n{phone_cta} for a free home valuation!",
            "kw": ["sell", "selling", "list my home", "list my house", "listing agent",
                   "sell my home", "sell my house", "listing process", "how to sell"],
            "cat": "selling",
        })
    else:
        qa.append({
            "q": "Can you help me sell my home?",
            "a": f"Absolutely! Here's how {name} helps you sell:\n\n1. Free comparative market analysis (CMA)\n2. Professional staging and photography\n3. Marketing across MLS and major real estate sites\n4. Open houses and private showings\n5. Expert negotiation for top dollar\n6. Transaction management through closing\n\n{phone_cta} for a free consultation!",
            "kw": ["sell", "selling", "list my home", "list my house", "listing agent",
                   "sell my home", "sell my house", "listing process", "how to sell"],
            "cat": "selling",
        })

    # ── Viewings / Showings ──
    qa.append({
        "q": "I want to schedule a showing",
        "a": f"Scheduling a showing is easy! You can:\n\n1. Call us at {phone}\n2. Leave your info in the chat\n3. Visit our website to browse listings\n\nWe offer flexible scheduling including evenings and weekends. Most showings can be arranged within 24-48 hours!",
        "kw": ["showing", "viewing", "tour", "see the house", "see the property", "visit",
               "schedule a viewing", "book a viewing", "private showing", "walkthrough",
               "open house", "schedule a showing"],
        "cat": "viewing",
    })

    # ── New Clients ──
    qa.append({
        "q": "Are you accepting new clients?",
        "a": f"Absolutely! {name} is always welcoming new clients. Whether you're a first-time buyer, seasoned investor, or looking to sell, we're here to help.\n\n{phone_cta} to get started!",
        "kw": ["new client", "new clients", "accepting", "first time", "sign up",
               "taking on", "work with you"],
        "cat": "new_client",
    })

    # ── Mortgage / Pre-qualification ──
    qa.append({
        "q": "How do I get pre-qualified for a mortgage?",
        "a": f"Getting pre-qualified is a great first step! You'll need proof of income, credit history, and bank statements. We partner with trusted local lenders who can pre-qualify you quickly.\n\n{phone_cta} and we'll connect you with the right lender!",
        "kw": ["pre-qualified", "pre-qualify", "prequalify", "prequalified", "pre-approval",
               "mortgage", "loan", "financing", "lender", "how much can i afford", "afford"],
        "cat": "mortgage",
    })

    # ── Home Valuation ──
    qa.append({
        "q": "Can you do a market analysis or home valuation?",
        "a": f"Absolutely! We offer free Comparative Market Analyses (CMAs). We'll analyze recent sales, current trends, and your home's unique features to give you an accurate value estimate.\n\n{phone_cta} or leave your info for a free report within 24 hours!",
        "kw": ["market analysis", "home valuation", "home value", "worth", "how much is my home",
               "cma", "appraisal", "comparable", "comps", "property value", "estimate"],
        "cat": "valuation",
    })

    # ── First-Time Buyers ──
    qa.append({
        "q": "Do you help first-time homebuyers?",
        "a": f"Yes! We love working with first-time homebuyers. We can help with first-time buyer programs (FHA, USDA, state grants), down payment assistance, and guide you through every step.\n\n{phone_cta} for a free first-time buyer consultation!",
        "kw": ["first-time", "first time", "first home", "never bought", "new buyer",
               "starter home", "fha", "down payment assistance", "first time buyer"],
        "cat": "firsttime",
    })

    # ── Investment ──
    qa.append({
        "q": "Do you handle investment properties?",
        "a": f"Absolutely! We help investors with multi-family properties, rental income analysis, 1031 exchanges, fix-and-flip opportunities, and long-term wealth-building strategies.\n\n{phone_cta} to discuss your investment goals!",
        "kw": ["investment", "invest", "rental property", "rental income", "cap rate",
               "multi-family", "duplex", "triplex", "roi", "1031", "flip", "investor"],
        "cat": "investment",
    })

    # ── Urgent / Emergency ──
    qa.append({
        "q": "Do you handle emergencies?",
        "a": f"If you have an urgent real estate need — a time-sensitive offer, last-minute closing issue, or emergency situation — call {phone} directly. We're responsive and will get back to you as quickly as possible.",
        "kw": ["emergency", "emergencies", "urgent", "asap", "rush", "immediate", "time-sensitive"],
        "cat": "emergency",
    })

    # ── Booking / Consultation ──
    qa.append({
        "q": "How do I book an appointment?",
        "a": f"You can schedule a free consultation by calling {phone} or leaving your info in the chat! We'll set up a time to discuss your buying or selling goals.",
        "kw": ["appointment", "book", "schedule", "consultation", "meet", "come in"],
        "cat": "booking",
    })

    # ── Location ──
    if data["address"]:
        qa.append({
            "q": "Where are you located?",
            "a": f"Our office is located at {data['address']}. {phone_cta} for directions or to schedule an in-office meeting.",
            "kw": ["location", "located", "where", "address", "directions", "find you",
                   "parking", "office"],
            "cat": "location",
        })
    else:
        qa.append({
            "q": "Where are you located?",
            "a": f"Please visit our website or call {phone} for our office location. We can also meet at a location convenient for you!",
            "kw": ["location", "located", "where", "address", "directions", "find you", "office"],
            "cat": "location",
        })

    # ── Neighborhoods / Schools ──
    qa.append({
        "q": "Tell me about neighborhoods and school districts",
        "a": f"We're experts in all local neighborhoods! We can help you find the perfect area based on school ratings, commute times, walkability, safety, and property value trends.\n\nTell us what matters most and we'll recommend the best neighborhoods. {phone_cta}!",
        "kw": ["neighborhood", "neighborhoods", "school", "schools", "school district",
               "area", "community", "safe", "walkable", "commute", "best area"],
        "cat": "neighborhoods",
    })

    # ── Closing Costs ──
    qa.append({
        "q": "What are typical closing costs?",
        "a": f"Closing costs typically run 2-5% of the purchase price and include loan fees, title insurance, inspection, appraisal, and taxes. We'll provide a detailed estimate early so there are no surprises.\n\n{phone_cta} for specifics!",
        "kw": ["closing costs", "closing fees", "how much to close", "settlement", "fees", "closing"],
        "cat": "closing",
    })

    # ── Inspection ──
    qa.append({
        "q": "What does a home inspection cover?",
        "a": f"A professional inspection covers foundation, roof, plumbing, electrical, HVAC, windows, and more. Inspections cost $300-$500 and take 2-3 hours. We never recommend skipping this step.\n\n{phone_cta} for inspector recommendations!",
        "kw": ["inspection", "inspect", "home inspection", "inspector", "structural"],
        "cat": "inspection",
    })

    # ── Scraped FAQ entries ──
    for faq in data.get("faq_entries", [])[:10]:
        q_text = faq.get("q", "")
        a_text = faq.get("a", "")
        if q_text and a_text:
            words = re.findall(r'[a-z]{3,}', q_text.lower())
            stop_words = {"what", "how", "does", "your", "the", "you", "can", "are", "this", "that", "with", "for", "from"}
            kw = [w for w in words if w not in stop_words][:6]
            if kw:
                qa.append({"q": q_text, "a": a_text, "kw": kw, "cat": "faq"})

    return qa


# ── Commercial RE customization ───────────────────────────────────

def _customize_commercial_re(template: str, data: dict) -> str:
    """Customize the generic commercial RE template with real business data."""
    old = _COMMERCIAL_RE_PLACEHOLDERS
    html = template
    name = data["name"]
    phone = data["phone"] or old["phone"]

    # ── HTML replacements ──
    html = re.sub(r"<title>.*?</title>",
                  f"<title>{_html_escape(name)} - AI Assistant Demo</title>", html)
    html = html.replace(f"<h1>{old['name']}</h1>", f"<h1>{_html_escape(_short_business_name(name))}</h1>")
    html = html.replace(
        f'<div class="tagline">{old["tagline"]}</div>',
        f'<div class="tagline">{_html_escape(data["tagline"] or "Your Trusted Commercial Real Estate Partner")}</div>')
    html = html.replace(f"<h4>{old['name']}</h4>", f"<h4>{_html_escape(name)}</h4>")

    # ── JavaScript variable replacements ──
    html = re.sub(r'var BUSINESS_NAME = ".*?";',
                  f'var BUSINESS_NAME = "{_js_escape(name)}";', html)
    html = re.sub(r'var PHONE = ".*?";',
                  f'var PHONE = "{_js_escape(phone)}";', html)

    # Replace BROKER_DATA if we have team members
    if data["team"]:
        broker_js = _build_broker_data_js(data["team"])
        html = re.sub(r"var BROKER_DATA = \[.*?\];", f"var BROKER_DATA = {broker_js};",
                      html, flags=re.DOTALL)

    # ── Build comprehensive QA_DATA ──
    qa = _build_commercial_re_qa(data)
    qa_json = json.dumps(qa, ensure_ascii=False)
    html = re.sub(r"var QA_DATA = \[.*?\];",
                  lambda m: f"var QA_DATA = {qa_json};",
                  html, flags=re.DOTALL)

    # ── QUICK_ACTIONS ──
    quick = [
        {"label": "Available Listings", "q": "What commercial properties do you have available?"},
        {"label": "Tenant Rep", "q": "How does tenant representation work?"},
        {"label": "Property Mgmt", "q": "What property management services do you offer?"},
        {"label": "Consulting", "q": "Can you help with investment analysis?"},
    ]
    quick_json = json.dumps(quick)
    html = re.sub(r"var QUICK_ACTIONS = \[.*?\];",
                  lambda m: f"var QUICK_ACTIONS = {quick_json};",
                  html, flags=re.DOTALL)

    # ── Bulk phone/name replacement ──
    if phone != old["phone"]:
        html = html.replace(old["phone"], phone)
    html = html.replace(old["name"], name)

    return html


def _build_commercial_re_qa(data: dict) -> list[dict]:
    """Build comprehensive commercial RE QA_DATA."""
    qa: list[dict] = []
    name = data["name"]
    phone = data["phone"] or "our office"
    phone_cta = f"Call us at {phone}" if data["phone"] else "Contact us"

    # ── Contact Info ──
    contact_parts = []
    if data["phone"]:
        contact_parts.append(f"Phone: {phone}")
    if data["address"]:
        contact_parts.append(f"Address: {data['address']}")
    if data["email"]:
        contact_parts.append(f"Email: {data['email']}")
    contact_text = "\n".join(contact_parts) if contact_parts else f"{phone_cta} for details."

    qa.append({
        "q": "What is your contact info?",
        "a": f"Here's how to reach {name}:\n\n{contact_text}",
        "kw": ["contact info", "contact information", "how to contact", "reach you",
               "get in touch", "contact details", "contact"],
        "cat": "contact",
    })
    qa.append({
        "q": "What is your phone number?",
        "a": f"You can reach us at {phone}. We're happy to help!",
        "kw": ["phone", "phone number", "call you", "telephone", "your number", "number"],
        "cat": "contact",
    })
    if data["email"]:
        qa.append({
            "q": "What is your email?",
            "a": f"You can email us at {data['email']}, or call {phone}. We respond within one business day.",
            "kw": ["email", "email address", "your email", "e-mail", "mail"],
            "cat": "contact",
        })

    # ── Hours ──
    hours_formatted = _format_hours_full_week(data["hours_raw"])
    if hours_formatted:
        qa.append({
            "q": "What are your hours?",
            "a": f"Our office hours at {name}:\n\n{hours_formatted}\n\nNeed to meet outside these hours? Our brokers are flexible — {phone_cta.lower()} to arrange a time.",
            "kw": ["hours", "open", "close", "time", "schedule", "when", "office hours", "business hours"],
            "cat": "hours",
        })
    else:
        qa.append({
            "q": "What are your hours?",
            "a": f"Our brokers are available by appointment and our office is open during regular business hours. {phone_cta} to schedule a meeting.",
            "kw": ["hours", "open", "close", "time", "schedule", "when", "office hours", "business hours"],
            "cat": "hours",
        })

    # ── Territory / Areas Served ──
    if data.get("areas_served"):
        areas_text = ", ".join(data["areas_served"][:12])
        qa.append({
            "q": "What areas or markets do you cover?",
            "a": f"We serve {areas_text} and surrounding commercial markets. Our brokers have deep knowledge of local submarkets, vacancy rates, rental comps, and development trends.\n\n{phone_cta} to discuss properties in your target market!",
            "kw": ["areas", "cover", "serve", "markets", "where", "service area", "what areas",
                   "which areas", "territory", "region", "submarket"],
            "cat": "areas",
        })
    else:
        qa.append({
            "q": "What areas or markets do you cover?",
            "a": f"We serve the local area and surrounding commercial markets. {phone_cta} to discuss properties in your target market!",
            "kw": ["areas", "cover", "serve", "markets", "where", "service area", "what areas",
                   "which areas", "territory", "region", "submarket"],
            "cat": "areas",
        })

    # ── Services ──
    if data["services"]:
        svc_text = ", ".join(data["services"][:12])
        qa.append({
            "q": "What services does your firm provide?",
            "a": f"At {name}, we provide {svc_text}.\n\n{phone_cta} to discuss how we can help with your commercial real estate needs.",
            "kw": ["services", "what do you offer", "provide", "do you do", "brokerage", "consulting", "advisory"],
            "cat": "services",
        })
    else:
        qa.append({
            "q": "What services does your firm provide?",
            "a": f"At {name}, we provide comprehensive commercial real estate services including brokerage, tenant representation, property management, asset management, and investment consulting.\n\n{phone_cta}.",
            "kw": ["services", "what do you offer", "provide", "do you do", "brokerage", "consulting", "advisory"],
            "cat": "services",
        })

    # ── Property Types ──
    if data.get("property_types"):
        types_text = ", ".join(data["property_types"][:10])
        qa.append({
            "q": "What types of commercial properties do you handle?",
            "a": f"We handle {types_text}.\n\n{phone_cta} to discuss your specific needs!",
            "kw": ["types", "property types", "what kind", "what type", "office", "retail",
                   "industrial", "warehouse", "flex", "healthcare", "land", "mixed-use", "specialize"],
            "cat": "property_types",
        })
    else:
        qa.append({
            "q": "What types of commercial properties do you handle?",
            "a": f"We handle all major commercial property types including office, retail, industrial, warehouse, flex space, healthcare facilities, land, and mixed-use properties.\n\n{phone_cta}!",
            "kw": ["types", "property types", "what kind", "what type", "office", "retail",
                   "industrial", "warehouse", "flex", "healthcare", "land", "mixed-use", "specialize"],
            "cat": "property_types",
        })

    # ── Team / Brokers ──
    creds_text = f" ({data['credentials']})" if data.get("credentials") else ""
    if data["team"]:
        if len(data["team"]) == 1:
            team_text = f"Our lead broker is {data['team'][0]}{creds_text}."
        else:
            team_list = "\n".join(f"- {t}" for t in data["team"])
            team_text = f"We have {len(data['team'])} experienced professionals on our team{creds_text}:\n\n{team_list}"

        qa.append({
            "q": "Who are the brokers on your team?",
            "a": f"{team_text}\n\n{phone_cta} to connect with the right broker for your needs!",
            "kw": ["brokers", "broker", "team", "staff", "who works", "meet the team",
                   "your team", "who are", "how many", "agents", "agent"],
            "cat": "team",
        })

        for member in data["team"]:
            parts = member.split()
            first_name = parts[0].lower()
            last_name = parts[-1].lower().rstrip(",") if len(parts) >= 2 else ""
            kw = [k for k in [last_name, first_name] if k]
            qa.append({
                "q": f"Tell me about {member}",
                "a": f"{member} is part of our team at {name}. {phone_cta} to schedule a consultation.",
                "kw": kw,
                "cat": "broker",
            })
    else:
        qa.append({
            "q": "Who are the brokers on your team?",
            "a": f"Our experienced brokerage team at {name}{creds_text} is ready to help!\n\n{phone_cta} to connect with a broker.",
            "kw": ["brokers", "broker", "team", "staff", "who works", "meet the team",
                   "your team", "who are", "how many", "agents", "agent"],
            "cat": "team",
        })

    # ── Tenant Representation ──
    qa.append({
        "q": "How does tenant representation work?",
        "a": f"Our tenant representation service at {name} puts your interests first:\n\n1. Needs analysis — we assess your space requirements, budget, and timeline\n2. Market search — we identify all suitable options, including off-market deals\n3. Site tours — we arrange and accompany you on property tours\n4. Financial analysis — we compare total occupancy costs across options\n5. Lease negotiation — we negotiate the best terms on your behalf\n6. Move-in coordination — we manage the transition\n\nTenant rep is typically free to you — the landlord pays our commission. {phone_cta} to get started!",
        "kw": ["tenant rep", "tenant representation", "represent", "find space",
               "lease negotiation", "tenant", "looking for space", "need space", "office search"],
        "cat": "tenant_rep",
    })

    # ── Property Management ──
    portfolio_text = ""
    if data.get("portfolio_sqft"):
        portfolio_text = f"\n\nCurrently managing {data['portfolio_sqft']} sq ft across our portfolio."
    qa.append({
        "q": "What property management services do you offer?",
        "a": f"Our full-service property management at {name} includes:\n\n- Tenant screening and placement\n- Rent collection and financial reporting\n- Maintenance coordination and vendor management\n- Lease administration and renewals\n- Building inspections and compliance\n- Capital improvement planning\n- 24/7 emergency response{portfolio_text}\n\n{phone_cta} to discuss your portfolio!",
        "kw": ["property management", "manage", "management", "pm", "maintain", "maintenance",
               "tenant screening", "rent collection", "building management", "facility"],
        "cat": "property_mgmt",
    })

    # ── Investment Consulting ──
    qa.append({
        "q": "Can you help with investment analysis?",
        "a": f"Absolutely! {name}'s investment consulting services include:\n\n- Cap rate and NOI analysis\n- Cash flow projections\n- Market comparables and trend analysis\n- 1031 exchange guidance\n- Portfolio optimization strategies\n- Due diligence coordination\n- Acquisition and disposition advisory\n\n{phone_cta} to schedule an investment consultation!",
        "kw": ["investment", "invest", "cap rate", "noi", "roi", "1031", "acquisition",
               "disposition", "portfolio", "analysis", "returns", "yield", "consulting"],
        "cat": "investment",
    })

    # ── Available Listings ──
    qa.append({
        "q": "What commercial properties do you have available?",
        "a": f"We have a wide selection of commercial properties available including office space, retail storefronts, industrial facilities, warehouses, flex space, and land for development.\n\nVisit our website to browse all listings, or {phone_cta.lower()} and tell us what you're looking for!",
        "kw": ["listings", "properties", "available", "for sale", "for lease", "browse",
               "search", "what do you have", "current listings", "inventory", "spaces"],
        "cat": "listings",
    })

    # ── Lease vs Buy ──
    qa.append({
        "q": "Should I lease or buy commercial space?",
        "a": f"Great question! Here are the key considerations:\n\nLeasing:\n- Lower upfront costs\n- More flexibility to relocate or resize\n- Landlord handles major maintenance\n- Operating expense, not capital\n\nBuying:\n- Build equity over time\n- Potential tax advantages (depreciation)\n- Control over the property\n- Fixed costs (no rent escalations)\n\nThe right choice depends on your business stage, capital position, and long-term plans. {phone_cta} for a personalized analysis!",
        "kw": ["lease or buy", "buy or lease", "rent or buy", "leasing vs buying",
               "should i lease", "should i buy", "rent vs own", "lease vs purchase"],
        "cat": "lease_buy",
    })

    # ── Firm History ──
    if data.get("firm_history"):
        qa.append({
            "q": "Tell me about your firm's history",
            "a": f"{data['firm_history']}\n\n{phone_cta} to learn more!",
            "kw": ["history", "about", "founded", "background", "firm", "company",
                   "who are you", "tell me about", "how long"],
            "cat": "about",
        })

    # ── Development Advisory ──
    qa.append({
        "q": "Do you handle development projects?",
        "a": f"Yes! {name} provides development advisory services including site selection, feasibility analysis, entitlement support, and project coordination.\n\n{phone_cta} to discuss your development project!",
        "kw": ["development", "develop", "build", "construction", "site selection",
               "feasibility", "entitlement", "zoning"],
        "cat": "development",
    })

    # ── Tour / Showing ──
    qa.append({
        "q": "How do I schedule a property tour?",
        "a": f"Scheduling a property tour is easy! You can:\n\n1. Call us at {phone}\n2. Leave your info in the chat\n3. Visit our website\n\nMost tours can be arranged within 24-48 hours. We'll walk the space with you and discuss layout, lease terms, and building amenities.",
        "kw": ["tour", "showing", "viewing", "see the property", "visit",
               "schedule a tour", "walk through", "site visit"],
        "cat": "tour",
    })

    # ── Booking / Consultation ──
    qa.append({
        "q": "How do I schedule a consultation?",
        "a": f"You can schedule a free consultation by calling {phone} or leaving your info in the chat! We'll set up a time to discuss your commercial real estate needs.",
        "kw": ["appointment", "book", "schedule", "consultation", "meet", "come in"],
        "cat": "booking",
    })

    # ── Location ──
    if data["address"]:
        qa.append({
            "q": "Where are you located?",
            "a": f"Our office is located at {data['address']}. {phone_cta} for directions or to schedule an in-office meeting.",
            "kw": ["location", "located", "where", "address", "directions", "find you",
                   "parking", "office"],
            "cat": "location",
        })
    else:
        qa.append({
            "q": "Where are you located?",
            "a": f"Please visit our website or call {phone} for our office location. We can also meet at a location convenient for you!",
            "kw": ["location", "located", "where", "address", "directions", "find you", "office"],
            "cat": "location",
        })

    # ── Scraped FAQ entries ──
    for faq in data.get("faq_entries", [])[:10]:
        q_text = faq.get("q", "")
        a_text = faq.get("a", "")
        if q_text and a_text:
            words = re.findall(r'[a-z]{3,}', q_text.lower())
            stop_words = {"what", "how", "does", "your", "the", "you", "can", "are", "this", "that", "with", "for", "from"}
            kw = [w for w in words if w not in stop_words][:6]
            if kw:
                qa.append({"q": q_text, "a": a_text, "kw": kw, "cat": "faq"})

    return qa


def _upgrade_lead_form_commercial_re(html: str) -> str:
    """Commercial RE: 7-field lead capture form already in template."""
    # The commercial RE template already has the 7-field form built in
    return html


# ── Enhanced lead capture form ──────────────────────────────────────

def _upgrade_lead_capture_form(html: str, niche: str = "dental") -> str:
    """Replace the 3-field lead capture form with the enhanced version.

    Dental: name, phone, email, preferred date/time, new/existing patient, reason.
    Real estate: name, phone, email, buying/selling/both, budget, timeline,
                 preferred areas, renting or own.
    """
    if niche == "commercial_re":
        return _upgrade_lead_form_commercial_re(html)
    if niche == "real_estate":
        return _upgrade_lead_form_re(html)
    return _upgrade_lead_form_dental(html)


def _upgrade_lead_form_dental(html: str) -> str:
    """Dental: 6-field lead capture form."""
    old_form_fn = (
        '  function showLeadCaptureForm() {\n'
        '    var body = document.getElementById("chatBody");\n'
        '    var form = document.createElement("div");\n'
        '    form.className = "lead-form";\n'
        '    form.innerHTML =\n'
        '      "<p>Leave your info and we will get back to you:</p>" +\n'
        '      "<input type=\\"text\\" id=\\"leadName\\" placeholder=\\"Your name\\">" +\n'
        '      "<input type=\\"tel\\" id=\\"leadPhone\\" placeholder=\\"Phone number\\">" +\n'
        '      "<input type=\\"email\\" id=\\"leadEmail\\" placeholder=\\"Email address\\">" +\n'
        '      "<button onclick=\\"submitLead()\\" type=\\"button\\">Send</button>";\n'
        '    body.appendChild(form);\n'
        '    body.scrollTop = body.scrollHeight;\n'
        '  }'
    )

    new_form_fn = (
        '  function showLeadCaptureForm() {\n'
        '    var body = document.getElementById("chatBody");\n'
        '    var form = document.createElement("div");\n'
        '    form.className = "lead-form";\n'
        '    form.innerHTML =\n'
        '      "<p>Leave your info and we will get back to you:</p>" +\n'
        '      "<input type=\\"text\\" id=\\"leadName\\" placeholder=\\"Your name\\">" +\n'
        '      "<input type=\\"tel\\" id=\\"leadPhone\\" placeholder=\\"Phone number\\">" +\n'
        '      "<input type=\\"email\\" id=\\"leadEmail\\" placeholder=\\"Email address\\">" +\n'
        '      "<input type=\\"text\\" id=\\"leadDate\\" placeholder=\\"Preferred date/time\\">" +\n'
        '      "<select id=\\"leadPatientType\\"><option value=\\"\\">New or existing patient?</option><option value=\\"new\\">New Patient</option><option value=\\"existing\\">Existing Patient</option></select>" +\n'
        '      "<textarea id=\\"leadReason\\" placeholder=\\"Reason for your visit\\"></textarea>" +\n'
        '      "<button onclick=\\"submitLead()\\" type=\\"button\\">Send</button>";\n'
        '    body.appendChild(form);\n'
        '    body.scrollTop = body.scrollHeight;\n'
        '  }'
    )
    html = html.replace(old_form_fn, new_form_fn)

    # Also upgrade the doctor-specific lead capture form
    old_doc_form = (
        '      "<input type=\\"text\\" id=\\"leadName\\" placeholder=\\"Your name\\">" +\n'
        '      "<input type=\\"tel\\" id=\\"leadPhone\\" placeholder=\\"Phone number\\">" +\n'
        '      "<input type=\\"email\\" id=\\"leadEmail\\" placeholder=\\"Email address\\">" +\n'
        '      "<textarea id=\\"leadNote\\" placeholder=\\"Your question or concern\\">"'
    )
    new_doc_form = (
        '      "<input type=\\"text\\" id=\\"leadName\\" placeholder=\\"Your name\\">" +\n'
        '      "<input type=\\"tel\\" id=\\"leadPhone\\" placeholder=\\"Phone number\\">" +\n'
        '      "<input type=\\"email\\" id=\\"leadEmail\\" placeholder=\\"Email address\\">" +\n'
        '      "<input type=\\"text\\" id=\\"leadDate\\" placeholder=\\"Preferred date/time\\">" +\n'
        '      "<select id=\\"leadPatientType\\"><option value=\\"\\">New or existing patient?</option><option value=\\"new\\">New Patient</option><option value=\\"existing\\">Existing Patient</option></select>" +\n'
        '      "<textarea id=\\"leadNote\\" placeholder=\\"Your question or concern\\">"'
    )
    html = html.replace(old_doc_form, new_doc_form)

    # Add CSS for the select element (insert before .lead-form button)
    old_css = ".lead-form button {"
    new_css = (
        ".lead-form select {\n"
        "  width: 100%;\n"
        "  padding: 8px;\n"
        "  margin: 4px 0;\n"
        "  border: 1px solid #ddd;\n"
        "  border-radius: 4px;\n"
        "  font-size: 13px;\n"
        "  background: #fff;\n"
        "}\n"
        ".lead-form button {"
    )
    html = html.replace(old_css, new_css, 1)

    return html


def _upgrade_lead_form_re(html: str) -> str:
    """Real estate: 8-field lead capture form."""
    # Replace the generic 3-field showLeadCaptureForm
    old_form_fn = (
        '  function showLeadCaptureForm() {\n'
        '    var body = document.getElementById("chatBody");\n'
        '    var form = document.createElement("div");\n'
        '    form.className = "lead-form";\n'
        '    form.innerHTML =\n'
        '      "<p>Want us to reach out? Leave your info:</p>" +\n'
        '      "<input type=\\"text\\" id=\\"leadName\\" placeholder=\\"Your name\\">" +\n'
        '      "<input type=\\"tel\\" id=\\"leadPhone\\" placeholder=\\"Phone number\\">" +\n'
        '      "<input type=\\"email\\" id=\\"leadEmail\\" placeholder=\\"Email address\\">" +\n'
        '      "<button onclick=\\"submitLead()\\" type=\\"button\\">Send</button>";\n'
        '    body.appendChild(form);\n'
        '    body.scrollTop = body.scrollHeight;\n'
        '  }'
    )

    new_form_fn = (
        '  function showLeadCaptureForm() {\n'
        '    var body = document.getElementById("chatBody");\n'
        '    var form = document.createElement("div");\n'
        '    form.className = "lead-form";\n'
        '    form.innerHTML =\n'
        '      "<p>Want us to reach out? Leave your info:</p>" +\n'
        '      "<input type=\\"text\\" id=\\"leadName\\" placeholder=\\"Your full name\\">" +\n'
        '      "<input type=\\"tel\\" id=\\"leadPhone\\" placeholder=\\"Phone number\\">" +\n'
        '      "<input type=\\"email\\" id=\\"leadEmail\\" placeholder=\\"Email address\\">" +\n'
        '      "<select id=\\"leadIntent\\"><option value=\\"\\">Are you buying, selling, or both?</option><option value=\\"buying\\">Buying</option><option value=\\"selling\\">Selling</option><option value=\\"both\\">Both</option><option value=\\"renting\\">Renting</option><option value=\\"investing\\">Investing</option></select>" +\n'
        '      "<input type=\\"text\\" id=\\"leadBudget\\" placeholder=\\"Budget range (e.g. $300K-$500K)\\">" +\n'
        '      "<input type=\\"text\\" id=\\"leadTimeline\\" placeholder=\\"Timeline (e.g. 3-6 months)\\">" +\n'
        '      "<input type=\\"text\\" id=\\"leadAreas\\" placeholder=\\"Preferred areas/neighborhoods\\">" +\n'
        '      "<select id=\\"leadCurrentStatus\\"><option value=\\"\\">Currently renting or own?</option><option value=\\"renting\\">Renting</option><option value=\\"own\\">Own</option><option value=\\"other\\">Other</option></select>" +\n'
        '      "<button onclick=\\"submitLead()\\" type=\\"button\\">Send</button>";\n'
        '    body.appendChild(form);\n'
        '    body.scrollTop = body.scrollHeight;\n'
        '  }'
    )
    html = html.replace(old_form_fn, new_form_fn)

    # Also upgrade the agent-specific lead capture form
    old_agent_form = (
        '      "<input type=\\"text\\" id=\\"leadName\\" placeholder=\\"Your name\\">" +\n'
        '      "<input type=\\"tel\\" id=\\"leadPhone\\" placeholder=\\"Phone number\\">" +\n'
        '      "<input type=\\"email\\" id=\\"leadEmail\\" placeholder=\\"Email address\\">" +\n'
        '      "<textarea id=\\"leadNote\\" placeholder=\\"What are you looking for?\\">"'
    )
    new_agent_form = (
        '      "<input type=\\"text\\" id=\\"leadName\\" placeholder=\\"Your full name\\">" +\n'
        '      "<input type=\\"tel\\" id=\\"leadPhone\\" placeholder=\\"Phone number\\">" +\n'
        '      "<input type=\\"email\\" id=\\"leadEmail\\" placeholder=\\"Email address\\">" +\n'
        '      "<select id=\\"leadIntent\\"><option value=\\"\\">Are you buying, selling, or both?</option><option value=\\"buying\\">Buying</option><option value=\\"selling\\">Selling</option><option value=\\"both\\">Both</option></select>" +\n'
        '      "<input type=\\"text\\" id=\\"leadBudget\\" placeholder=\\"Budget range\\">" +\n'
        '      "<input type=\\"text\\" id=\\"leadTimeline\\" placeholder=\\"Timeline (e.g. 3-6 months)\\">" +\n'
        '      "<textarea id=\\"leadNote\\" placeholder=\\"What are you looking for?\\">"'
    )
    html = html.replace(old_agent_form, new_agent_form)

    # Add CSS for select elements
    old_css = ".lead-form button {"
    new_css = (
        ".lead-form select {\n"
        "  width: 100%;\n"
        "  padding: 8px;\n"
        "  margin: 4px 0;\n"
        "  border: 1px solid #ddd;\n"
        "  border-radius: 4px;\n"
        "  font-size: 13px;\n"
        "  background: #fff;\n"
        "}\n"
        ".lead-form button {"
    )
    html = html.replace(old_css, new_css, 1)

    return html


# ── Scraping ────────────────────────────────────────────────────────

def _scrape_for_demo(website: str):
    """Scrape the business website for demo-relevant data."""
    if not website:
        return None
    try:
        from viper.demos.scraper import scrape_business
        return scrape_business(website)
    except Exception as e:
        log.warning("[DEMO_BUILDER] Scrape failed for %s: %s", website, e)
        return None


# ── JavaScript helpers ──────────────────────────────────────────────

def _js_escape(s: str) -> str:
    """Escape a string for safe embedding in JavaScript."""
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("'", "\\'").replace("\n", "\\n")


def _html_escape(s: str) -> str:
    """Basic HTML escaping."""
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
             .replace('"', "&quot;"))


def _build_doctor_data_js(team: list[str]) -> str:
    """Build the DOCTOR_DATA JavaScript array from team member names."""
    docs = []
    for member in team:
        parts = member.split()
        clean = [p.rstrip(",") for p in parts if p not in ("DMD", "DDS", "MD", "PhD")]
        last_name = clean[-1].lower() if len(clean) >= 2 else clean[0].lower() if clean else ""
        specialty = "General Dentist"

        docs.append(f'{{name: "{_js_escape(member)}", '
                    f'lastName: "{last_name}", '
                    f'specialty: "{specialty}"}}')

    return "[" + ", ".join(docs) + "]"


def _build_agent_data_js(team: list[str]) -> str:
    """Build the AGENT_DATA JavaScript array from team member names."""
    agents = []
    for member in team:
        parts = member.split()
        last_name = parts[-1].lower().rstrip(",") if len(parts) >= 2 else parts[0].lower()

        agents.append(f'{{name: "{_js_escape(member)}", '
                      f'lastName: "{last_name}", '
                      f'specialty: "Real Estate Agent"}}')

    return "[" + ", ".join(agents) + "]"


def _build_broker_data_js(team: list[str]) -> str:
    """Build the BROKER_DATA JavaScript array from team member names."""
    brokers = []
    for member in team:
        parts = member.split()
        last_name = parts[-1].lower().rstrip(",") if len(parts) >= 2 else parts[0].lower()

        brokers.append(f'{{name: "{_js_escape(member)}", '
                      f'lastName: "{last_name}", '
                      f'specialty: "Commercial Real Estate Broker"}}')

    return "[" + ", ".join(brokers) + "]"
