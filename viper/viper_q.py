"""VIPER-Q — Unified 100-Point Lead Scoring Model.

Single scoring system for ALL leads (inbound + outbound).
Only leads scoring 50+ reach Jordan's inbox.

Dimensions (from spec):
  Industry Fit    (0-20)  — Dental/RE/HVAC/Legal=20, Med spa=18, Home services=15
  Budget Signals  (0-20)  — Explicit $5K+=20, $2K-$5K=15, asking about pricing=8
  Project Spec    (0-15)  — Detailed requirements=12, specific integrations=12
  Decision-Maker  (0-15)  — Owner/CEO/Founder=15, Director/Manager=10
  Timeline Urgency(0-15)  — ASAP=15, within 1mo=12, 1-3mo=8
  Engagement      (0-10)  — Contact info=5, quick response=3, active LinkedIn=2
  Tech Adoption   (0-5)   — Uses CRM/automation=5, mentions tools=4, has website=2

Negative Deductions:
  Job seeker: -20 | Competitor: -15 | Student/academic: -15
  Staff augmentation: -15 | Spam/bot: -20 | "Free"/"no budget": -10

Score Thresholds:
  75-100 = HOT    → Immediate personal outreach within 5 minutes
  50-74  = WARM   → Queue for outreach within 24 hours
  30-49  = LUKEWARM → Add to nurture email sequence
  10-29  = LOW    → Park, re-evaluate if engagement increases
  0-9    = DISQUALIFIED → Auto-archive, log reason
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# ── Classifications ─────────────────────────────────────────────────

HOT = "HOT"
WARM = "WARM"
LUKEWARM = "LUKEWARM"
LOW = "LOW"
DISQUALIFIED = "DISQUALIFIED"

THRESHOLDS = [(75, HOT), (50, WARM), (30, LUKEWARM), (10, LOW), (0, DISQUALIFIED)]


def classify(score: int) -> str:
    for threshold, label in THRESHOLDS:
        if score >= threshold:
            return label
    return DISQUALIFIED


# ── Niche Registry ──────────────────────────────────────────────────

NICHE_ALIASES = {
    "dentist": "dental", "dental practice": "dental", "dental office": "dental",
    "orthodontist": "dental", "periodontics": "dental",
    "real estate": "real_estate", "real estate agency": "real_estate",
    "realtor": "real_estate", "realty": "real_estate",
    "property management": "real_estate",
    "hvac": "hvac", "hvac company": "hvac", "heating": "hvac",
    "cooling": "hvac", "plumber": "hvac", "plumbing": "hvac",
    "lawyer": "legal", "law firm": "legal", "attorney": "legal", "legal": "legal",
    "med spa": "med_spa", "medspa": "med_spa", "medical spa": "med_spa",
    "aesthetics": "med_spa",
}

NICHE_SCORES = {
    "dental": 20, "real_estate": 20, "hvac": 20, "legal": 20,
    "med_spa": 18, "home_services": 15, "ecommerce": 12,
    "restaurant": 10, "general": 5,
}

NICHE_COLORS = {
    "dental": "#3b82f6", "real_estate": "#10b981", "hvac": "#f59e0b",
    "legal": "#ef4444", "med_spa": "#ec4899", "general": "#64748b",
}

NICHE_KEYWORDS = {
    "dental": ["dentist", "dental", "dental practice", "orthodont", "periodon",
               "patients", "operatories", "hygiene", "dental office"],
    "real_estate": ["real estate", "realtor", "realty", "listings", "mls",
                    "brokerage", "buyer leads", "seller leads", "showings"],
    "hvac": ["hvac", "heating", "cooling", "air conditioning", "furnace",
             "service calls", "dispatching", "ac repair", "plumber"],
    "legal": ["law firm", "lawyer", "attorney", "legal", "case intake",
              "client intake", "paralegal", "billable hours"],
    "med_spa": ["med spa", "medspa", "medical spa", "aesthetics", "botox",
                "dermal filler", "laser treatment"],
}


def normalize_niche(niche: str) -> str:
    n = (niche or "").lower().strip()
    return NICHE_ALIASES.get(n, n)


def detect_niche(text: str) -> tuple[str, int]:
    """Detect niche from text. Returns (niche_key, score)."""
    lower = text.lower()
    for niche, keywords in NICHE_KEYWORDS.items():
        for kw in keywords:
            if kw in lower:
                return niche, NICHE_SCORES.get(niche, 10)
    if any(w in lower for w in ["small business", "business owner", "my business"]):
        return "general", 5
    return "unknown", 0


def niche_color(niche: str) -> str:
    n = normalize_niche(niche)
    return NICHE_COLORS.get(n, "#64748b")


# ── Keyword Dictionaries ───────────────────────────────────────────

_BUYER_INTENT = {
    "need a chatbot": 15, "looking for chatbot": 15, "need chatbot": 15,
    "chatbot for my business": 20, "need automation": 12,
    "automate my business": 15, "ai for my business": 12,
    "missed calls": 10, "losing leads": 12, "after hours calls": 10,
    "chatbot developer needed": 20, "recommend a chatbot": 15,
    "appointment scheduling bot": 15, "booking automation": 12,
    "hire chatbot developer": 20, "chatbot agency": 18,
    "looking for ai": 12, "who can build": 20, "help me": 8,
    "want to automate": 12, "budget": 15, "how much": 10,
    "searching for": 10, "need help with": 10,
    "virtual receptionist": 12, "answering service": 10,
    "patient scheduling": 12, "client intake": 12,
}

_JOB_SEEKER = [
    "hiring", "job", "position", "salary", "remote work", "freelance",
    "full-time", "part-time", "hourly rate", "join our team",
    "add to our team", "dedicated resource", "daily standups",
    "report to our manager", "resume", "apply now",
]

_STAFF_AUG = [
    "need a developer for x months", "add to our team",
    "full-time/part-time", "hourly rate", "dedicated resource",
    "daily standups", "report to our manager",
]

_SPAM = [
    "free trial", "limited time offer", "click here", "unsubscribe",
    "sponsored", "advertisement", "affiliate",
]

_PROJECT_SIGNALS = [
    "build me", "need a chatbot built", "want to automate",
    "deliverable", "fixed price", "turnkey", "end-to-end",
    "automate our", "build me a",
]

_DECISION_MAKER = [
    "owner", "ceo", "founder", "i own", "my practice",
    "my business", "my firm", "my company",
]

_URGENCY = [
    "asap", "immediately", "urgent", "this week", "right now",
]

_TECH = [
    "crm", "automation", "zapier", "n8n", "make.com",
    "servicetitan", "dentrix", "eaglesoft",
]


# ── Main Scoring Function ──────────────────────────────────────────

def score(title: str, body: str = "", metadata: dict | None = None) -> dict:
    """Score any lead using VIPER-Q 100-point model.

    Args:
        title: Lead title / post title / job title
        body: Description / body text / snippet
        metadata: Optional dict with keys like 'budget', 'bid_count',
                  'source', 'niche', 'has_contact_info', 'client_country'

    Returns dict with: score, classification, niche, niche_score,
        dimensions (dict), signals (list), deductions (int)
    """
    meta = metadata or {}
    text = f"{title} {body}".lower()
    signals = []

    # ── 1. Industry Fit (0-20) ──
    niche, niche_pts = detect_niche(text)
    # Override if niche provided in metadata
    if meta.get("niche"):
        niche = normalize_niche(meta["niche"])
        niche_pts = NICHE_SCORES.get(niche, 5)
    if niche_pts > 0:
        signals.append(f"niche:{niche}({niche_pts})")

    # ── 2. Budget Signals (0-20) ──
    budget_pts = 0
    explicit_budget = meta.get("budget", 0) or meta.get("budget_usd_max", 0) or 0
    if explicit_budget >= 5000:
        budget_pts = 20
        signals.append(f"budget:${explicit_budget}")
    elif explicit_budget >= 2000:
        budget_pts = 15
        signals.append(f"budget:${explicit_budget}")
    elif explicit_budget >= 500:
        budget_pts = 10
        signals.append(f"budget:${explicit_budget}")
    # Also check text for buyer intent keywords (additive)
    intent_pts = 0
    for kw, pts in _BUYER_INTENT.items():
        if kw in text:
            intent_pts += pts
            signals.append(f"intent:{kw}")
    budget_pts = min(budget_pts + intent_pts, 20)

    # ── 3. Project Specificity (0-15) ──
    spec_pts = 0
    if any(w in text for w in _PROJECT_SIGNALS):
        spec_pts = 12
        signals.append("project_specific")
    elif any(w in text for w in ["automate", "chatbot", "bot", "ai assistant"]):
        spec_pts = 5
    spec_pts = min(spec_pts, 15)

    # ── 4. Decision-Maker (0-15) ──
    dm_pts = 0
    if any(w in text for w in _DECISION_MAKER):
        dm_pts = 15
        signals.append("decision_maker")
    elif any(w in text for w in ["manager", "director"]):
        dm_pts = 10
    dm_pts = min(dm_pts, 15)

    # ── 5. Timeline Urgency (0-15) ──
    urg_pts = 0
    if any(w in text for w in _URGENCY):
        urg_pts = 15
        signals.append("urgent")
    elif any(w in text for w in ["soon", "this month", "within"]):
        urg_pts = 8
    urg_pts = min(urg_pts, 15)

    # ── 6. Engagement Quality (0-10) ──
    eng_pts = 0
    if meta.get("has_contact_info"):
        eng_pts += 5
        signals.append("has_contact")
    if meta.get("num_comments", 0) > 5:
        eng_pts += 3
    if meta.get("client_country") in ("US", "CA", "GB", "AU"):
        eng_pts += 2
    eng_pts = min(eng_pts, 10)

    # ── 7. Tech Adoption (0-5) ──
    tech_pts = 0
    if any(w in text for w in _TECH):
        tech_pts = 5
        signals.append("tech_aware")
    elif any(w in text for w in ["website", "online", "digital"]):
        tech_pts = 2
    tech_pts = min(tech_pts, 5)

    # ── Raw score ──
    raw = niche_pts + budget_pts + spec_pts + dm_pts + urg_pts + eng_pts + tech_pts

    # ── Negative Deductions ──
    deductions = 0
    if any(w in text for w in _JOB_SEEKER):
        deductions += 20
        signals.append("JOB_SEEKER(-20)")
    if any(w in text for w in _STAFF_AUG):
        deductions += 15
        signals.append("STAFF_AUG(-15)")
    if any(w in text for w in _SPAM):
        deductions += 10
        signals.append("SPAM(-10)")
    if any(w in text for w in ["student", "academic", "research paper", "thesis"]):
        deductions += 15
        signals.append("ACADEMIC(-15)")
    if "free" in text and "no budget" in text:
        deductions += 10
        signals.append("NO_BUDGET(-10)")
    # Competitor detection
    if any(w in text for w in ["we offer chatbot", "our agency", "our platform", "try our"]):
        deductions += 15
        signals.append("COMPETITOR(-15)")

    final = max(0, min(100, raw - deductions))

    return {
        "score": final,
        "classification": classify(final),
        "niche": niche,
        "niche_score": niche_pts,
        "dimensions": {
            "industry_fit": niche_pts,
            "budget_signals": budget_pts,
            "project_specificity": spec_pts,
            "decision_maker": dm_pts,
            "timeline_urgency": urg_pts,
            "engagement_quality": eng_pts,
            "tech_adoption": tech_pts,
        },
        "signals": signals,
        "raw_score": raw,
        "deductions": deductions,
    }


def score_to_10(vq_score: int) -> float:
    """Convert VIPER-Q 0-100 score to legacy 0-10 scale."""
    return round(vq_score / 10, 1)
