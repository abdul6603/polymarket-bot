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

# Template slugs by niche
_TEMPLATE_MAP = {
    "dental": "dental-demo",
    "real_estate": "realestate-demo",
}

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

    1. Scrape the prospect's website for detailed business info
    2. Read the generic niche template
    3. Customize with real business data (8 required categories)
    4. Return the full HTML string

    Returns:
        The customized HTML string ready to deploy.
    """
    # 1. Scrape for real business data
    scraped = _scrape_for_demo(website)

    # 2. Read the generic template
    template_slug = _TEMPLATE_MAP.get(niche, "dental-demo")
    template_path = REPO_DIR / template_slug / "index.html"
    if not template_path.exists():
        raise FileNotFoundError(f"Template not found: {template_path}")
    template = template_path.read_text()

    # 3. Merge all data sources (scraped > prospect_data > defaults)
    data = _merge_data(scraped, prospect_data, business_name)

    # 4. Apply customizations based on niche
    if niche == "real_estate":
        html = _customize_realestate(template, data)
    else:
        html = _customize_dental(template, data)

    # 5. Upgrade lead capture form to 6 fields
    html = _upgrade_lead_capture_form(html)

    log.info("[DEMO_BUILDER] Built demo for %s (niche=%s, team=%d, services=%d, quality=%d)",
             business_name, niche, len(data["team"]), len(data["services"]),
             _calc_data_quality(data))
    return html


def run_quality_gate(html: str) -> tuple[bool, list[str]]:
    """Run 7 mandatory test questions against the built QA_DATA.

    Returns (all_pass, list_of_failures).
    Each failure is a string like "hours: answer contains placeholder".
    """
    # Extract QA_DATA from the built HTML
    m = re.search(r"var QA_DATA = (\[.*?\]);", html, re.DOTALL)
    if not m:
        return False, ["QA_DATA not found in HTML"]

    try:
        qa = json.loads(m.group(1))
    except json.JSONDecodeError:
        return False, ["QA_DATA JSON parse failed"]

    # Extract PHONE and BUSINESS_NAME
    phone_m = re.search(r'var PHONE = "(.+?)";', html)
    phone = phone_m.group(1) if phone_m else ""
    name_m = re.search(r'var BUSINESS_NAME = "(.+?)";', html)
    biz_name = name_m.group(1) if name_m else ""

    failures: list[str] = []

    # Build a simple keyword matcher (same logic as the JS findBestMatch)
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

    # Test 1: Hours
    ans = find_answer("What are your hours?")
    if not ans:
        failures.append("hours: no QA entry matched")
    elif "555-123-4567" in ans["a"] and phone != "555-123-4567":
        failures.append("hours: answer still contains placeholder phone")
    elif "Demo Dental" in ans["a"] or "Demo Realty" in ans["a"]:
        failures.append("hours: answer still contains placeholder business name")

    # Test 2: Insurance
    ans = find_answer("Do you accept my insurance?")
    if not ans:
        failures.append("insurance: no QA entry matched")
    elif "555-123-4567" in ans["a"] and phone != "555-123-4567":
        failures.append("insurance: answer still contains placeholder phone")

    # Test 3: Services
    ans = find_answer("What services do you offer?")
    if not ans:
        failures.append("services: no QA entry matched")
    elif "Demo Dental" in ans["a"] or "Demo Realty" in ans["a"]:
        failures.append("services: answer still contains placeholder name")

    # Test 4: Booking
    ans = find_answer("How do I book an appointment?")
    if not ans:
        failures.append("booking: no QA entry matched")
    elif phone and phone not in ans["a"]:
        failures.append(f"booking: answer missing real phone ({phone})")

    # Test 5: New patients
    ans = find_answer("Are you accepting new patients?")
    if not ans:
        failures.append("new_patient: no QA entry matched")
    elif phone and phone not in ans["a"]:
        failures.append(f"new_patient: answer missing real phone ({phone})")

    # Test 6: Emergency
    ans = find_answer("Do you handle dental emergencies?")
    if not ans:
        # Try broader match for real estate
        ans = find_answer("Do you handle emergencies?")
    if not ans:
        failures.append("emergency: no QA entry matched")
    elif phone and phone not in ans["a"]:
        failures.append(f"emergency: answer missing real phone ({phone})")

    # Test 7: Location
    ans = find_answer("Where are you located?")
    if not ans:
        failures.append("location: no QA entry matched")
    elif "123 Main Street" in ans["a"] and biz_name != "Demo Dental Practice":
        failures.append("location: answer still contains placeholder address")

    all_pass = len(failures) == 0
    return all_pass, failures


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
    tagline = (
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
    html = html.replace(f"<h1>{old['name']}</h1>", f"<h1>{_html_escape(name)}</h1>")
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
    html = html.replace(f"<h1>{old['name']}</h1>", f"<h1>{_html_escape(name)}</h1>")
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

    # ── Bulk phone/name replacement ──
    if phone != old["phone"]:
        html = html.replace(old["phone"], phone)
    html = html.replace(old["name"], name)

    return html


def _build_realestate_qa(data: dict) -> list[dict]:
    """Build comprehensive real estate QA_DATA."""
    qa: list[dict] = []
    name = data["name"]
    phone = data["phone"] or "our office"
    phone_cta = f"Call us at {phone}" if data["phone"] else "Contact us"

    # Contact
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
        "kw": ["contact info", "contact information", "how to contact", "reach you", "contact"],
        "cat": "contact",
    })
    qa.append({
        "q": "What is your phone number?",
        "a": f"You can reach us at {phone}. We're happy to help!",
        "kw": ["phone", "phone number", "call you", "telephone", "your number"],
        "cat": "contact",
    })

    # Hours
    hours_formatted = _format_hours_full_week(data["hours_raw"])
    if hours_formatted:
        qa.append({
            "q": "What are your hours?",
            "a": f"Our hours at {name}:\n\n{hours_formatted}\n\n{phone_cta} if you need to confirm availability.",
            "kw": ["hours", "open", "close", "time", "schedule", "when"],
            "cat": "hours",
        })
    else:
        qa.append({
            "q": "What are your hours?",
            "a": f"Please call us at {phone} for our current office hours.",
            "kw": ["hours", "open", "close", "time", "schedule", "when"],
            "cat": "hours",
        })

    # Services
    if data["services"]:
        svc_text = ", ".join(data["services"][:12])
        qa.append({
            "q": "What services do you offer?",
            "a": f"At {name}, we offer {svc_text}.\n\n{phone_cta} to discuss your real estate needs.",
            "kw": ["services", "what do you offer", "provide", "do you do"],
            "cat": "services",
        })
    else:
        qa.append({
            "q": "What services do you offer?",
            "a": f"At {name}, we provide comprehensive real estate services including buyer and seller representation, market analysis, and more.\n\n{phone_cta}.",
            "kw": ["services", "what do you offer", "provide", "do you do"],
            "cat": "services",
        })

    # Insurance/Payment → Commission/fees for RE
    qa.append({
        "q": "Do you accept my insurance?",
        "a": f"Real estate services don't typically involve insurance. {phone_cta} to discuss our commission structure and fees.",
        "kw": ["insurance", "fee", "commission", "cost", "how much"],
        "cat": "insurance",
    })

    # Team
    if data["team"]:
        if len(data["team"]) == 1:
            team_text = f"Our lead agent is {data['team'][0]}."
        else:
            team_list = "\n".join(f"- {t}" for t in data["team"])
            team_text = f"We have {len(data['team'])} experienced agents:\n\n{team_list}"

        qa.append({
            "q": "Who are your agents?",
            "a": f"{team_text}\n\n{phone_cta} to connect with the right agent.",
            "kw": ["agents", "agent", "team", "staff", "who works", "realtors", "realtor", "meet the team"],
            "cat": "team",
        })
    else:
        qa.append({
            "q": "Who are your agents?",
            "a": f"Our experienced team at {name} is ready to help! {phone_cta} to connect with an agent.",
            "kw": ["agents", "agent", "team", "staff", "who works", "realtors", "realtor"],
            "cat": "team",
        })

    # New clients
    qa.append({
        "q": "Are you accepting new patients?",
        "a": f"Absolutely! {name} is always taking on new clients. {phone_cta} to get started — we'd love to help you find your dream home!",
        "kw": ["new patient", "new client", "accepting", "first time", "sign up"],
        "cat": "new_patient",
    })

    # Emergency
    qa.append({
        "q": "Do you handle emergencies?",
        "a": f"If you have an urgent real estate need, call {phone} directly. We're responsive and will get back to you as quickly as possible.",
        "kw": ["emergency", "urgent", "asap", "rush", "immediate"],
        "cat": "emergency",
    })

    # Booking
    qa.append({
        "q": "How do I book an appointment?",
        "a": f"You can schedule a consultation by calling {phone} or leaving your info in the chat! We'll set up a time to discuss your needs.",
        "kw": ["appointment", "book", "schedule", "consultation", "meet"],
        "cat": "booking",
    })

    # Location
    if data["address"]:
        qa.append({
            "q": "Where are you located?",
            "a": f"We're located at {data['address']}. {phone_cta} for directions.",
            "kw": ["location", "located", "where", "address", "directions", "find you"],
            "cat": "location",
        })
    else:
        qa.append({
            "q": "Where are you located?",
            "a": f"Please visit our website or call {phone} for our office location.",
            "kw": ["location", "located", "where", "address", "directions"],
            "cat": "location",
        })

    return qa


# ── Enhanced lead capture form ──────────────────────────────────────

def _upgrade_lead_capture_form(html: str) -> str:
    """Replace the 3-field lead capture form with the enhanced 6-field version.

    New fields: name, phone, email, preferred date/time, new/existing patient, reason.
    """
    # Replace showLeadCaptureForm() function
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
