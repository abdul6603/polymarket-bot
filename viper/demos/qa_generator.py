"""Q&A Generator — template banks + LLM-enhanced personalization."""
from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

from viper.demos.scraper import ScrapedBusiness

log = logging.getLogger(__name__)

# Add shared to path for llm_client
sys.path.insert(0, str(Path.home() / "shared"))


@dataclass
class QAPair:
    """Single question-answer pair with matching keywords."""
    question: str
    answer: str
    keywords: list[str] = field(default_factory=list)
    category: str = "general"

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Template banks — always included, populated with scraped data
# ---------------------------------------------------------------------------

def _dental_templates(biz: ScrapedBusiness) -> list[QAPair]:
    """20 base Q&As for dental offices."""
    name = biz.name or "our office"
    phone = biz.phone or "our front desk"
    hours = biz.hours or "during regular business hours"
    services_str = ", ".join(biz.services[:8]) if biz.services else "general and cosmetic dentistry"
    insurance_str = ", ".join(biz.insurance_plans[:6]) if biz.insurance_plans else "most major dental insurance plans"
    team_str = ", ".join(biz.team_members[:4]) if biz.team_members else "our experienced dental team"

    return [
        QAPair(
            question="What are your hours?",
            answer=f"We're open {hours}. Call us at {phone} if you need to confirm availability.",
            keywords=["hours", "open", "close", "time", "schedule", "when"],
            category="hours",
        ),
        QAPair(
            question="How do I book an appointment?",
            answer=f"You can call us at {phone} or use our online booking. We'll find a time that works for you!",
            keywords=["appointment", "book", "schedule", "visit", "come in"],
            category="booking",
        ),
        QAPair(
            question="Do you accept my insurance?",
            answer=f"We accept {insurance_str}. Not sure if yours is covered? Call us at {phone} and we'll verify your benefits for free.",
            keywords=["insurance", "accept", "cover", "plan", "delta", "cigna", "aetna", "metlife"],
            category="insurance",
        ),
        QAPair(
            question="What services do you offer?",
            answer=f"At {name}, we offer {services_str}. We're here for all your dental needs!",
            keywords=["services", "offer", "provide", "do you do", "treatments", "procedures"],
            category="services",
        ),
        QAPair(
            question="Are you accepting new patients?",
            answer=f"Yes! {name} is always welcoming new patients. Call {phone} to get started — we'll make your first visit easy and comfortable.",
            keywords=["new patient", "accepting", "first time", "join", "sign up", "register"],
            category="new_patient",
        ),
        QAPair(
            question="What should I expect at my first visit?",
            answer="Your first visit includes a comprehensive exam, X-rays, and a cleaning. We'll discuss your dental health and create a personalized treatment plan. Plan for about 60-90 minutes.",
            keywords=["first visit", "first appointment", "expect", "what happens", "new patient visit"],
            category="new_patient",
        ),
        QAPair(
            question="Do you handle dental emergencies?",
            answer=f"Yes, we handle dental emergencies! If you're in pain or had an accident, call {phone} immediately. We'll get you in as soon as possible.",
            keywords=["emergency", "urgent", "pain", "toothache", "broken", "knocked out", "accident"],
            category="emergency",
        ),
        QAPair(
            question="How much does a cleaning cost?",
            answer=f"Cleaning costs vary based on your insurance coverage. Most plans cover preventive cleanings at 100%. Call {phone} for a cost estimate with your specific insurance.",
            keywords=["cost", "price", "how much", "cleaning", "fee", "charge", "expensive"],
            category="pricing",
        ),
        QAPair(
            question="Do you offer teeth whitening?",
            answer=f"Yes! We offer professional teeth whitening that delivers dramatic results safely. Ask about our options at your next visit or call {phone} to learn more.",
            keywords=["whitening", "whiten", "white", "bright", "bleach", "stain"],
            category="cosmetic",
        ),
        QAPair(
            question="Do you see children?",
            answer=f"Absolutely! We love treating kids at {name}. We recommend bringing children in for their first visit by age 1 or when their first tooth appears.",
            keywords=["children", "kids", "child", "pediatric", "baby", "toddler", "son", "daughter"],
            category="pediatric",
        ),
        QAPair(
            question="What payment options do you have?",
            answer=f"We accept most insurance plans, cash, credit cards, and offer financing options for larger treatments. Call {phone} to discuss payment plans.",
            keywords=["payment", "pay", "finance", "credit", "cash", "payment plan", "afford"],
            category="billing",
        ),
        QAPair(
            question="Where are you located?",
            answer=f"{name} is located at {biz.address or 'a convenient location in the area'}. We have easy parking available for patients.",
            keywords=["location", "located", "where", "address", "directions", "find you", "parking"],
            category="location",
        ),
        QAPair(
            question="Do you offer Invisalign?",
            answer=f"Yes! We're proud to offer Invisalign clear aligners at {name}. Schedule a consultation to see if Invisalign is right for you.",
            keywords=["invisalign", "braces", "straighten", "alignment", "crooked", "orthodont"],
            category="orthodontics",
        ),
        QAPair(
            question="Who are your dentists?",
            answer=f"Our team includes {team_str}. Each member is dedicated to providing exceptional care in a comfortable environment.",
            keywords=["dentist", "doctor", "who", "team", "staff", "provider", "dr"],
            category="team",
        ),
        QAPair(
            question="Do you offer sedation dentistry?",
            answer="Yes, we offer sedation options for patients who feel anxious about dental visits. We want every visit to be comfortable and stress-free.",
            keywords=["sedation", "anxious", "nervous", "afraid", "fear", "anxiety", "sleep", "nitrous"],
            category="comfort",
        ),
        QAPair(
            question="What about dental implants?",
            answer=f"We offer dental implants as a permanent solution for missing teeth. Call {phone} to schedule a consultation and learn if implants are right for you.",
            keywords=["implant", "missing tooth", "replace", "permanent"],
            category="implants",
        ),
        QAPair(
            question="Do you take walk-ins?",
            answer=f"We prefer appointments so we can give you dedicated time, but we do our best to accommodate walk-ins and emergencies. Call {phone} to check availability.",
            keywords=["walk-in", "walk in", "without appointment", "drop in", "no appointment"],
            category="booking",
        ),
        QAPair(
            question="Can I see you on weekends?",
            answer=f"Please call us at {phone} to check our weekend availability. We understand busy schedules and try to offer convenient appointment times.",
            keywords=["weekend", "saturday", "sunday", "after hours", "evening"],
            category="hours",
        ),
        QAPair(
            question="Do you do root canals?",
            answer=f"Yes, we perform root canal therapy at {name}. It's a routine procedure and we ensure your comfort throughout. Don't worry — modern root canals are much easier than you might think!",
            keywords=["root canal", "endodontic", "nerve", "infected tooth"],
            category="services",
        ),
        QAPair(
            question="What COVID precautions do you take?",
            answer=f"Patient safety is our top priority. We follow all CDC and ADA guidelines including enhanced sanitization, air filtration, and screening protocols.",
            keywords=["covid", "safety", "precaution", "sanitize", "clean", "protocol", "safe"],
            category="safety",
        ),
    ]


def _real_estate_templates(biz: ScrapedBusiness) -> list[QAPair]:
    """20 base Q&As for real estate agencies."""
    name = biz.name or "our agency"
    phone = biz.phone or "our office"
    areas = ", ".join(biz.areas_served[:5]) if biz.areas_served else "the local area"
    team_str = ", ".join(biz.team_members[:4]) if biz.team_members else "our experienced agents"
    services_str = ", ".join(biz.services[:6]) if biz.services else "buying and selling residential properties"

    return [
        QAPair(
            question="What areas do you serve?",
            answer=f"We serve {areas} and surrounding communities. Our agents have deep local knowledge of these markets.",
            keywords=["area", "serve", "where", "location", "neighborhood", "town", "city", "region"],
            category="areas",
        ),
        QAPair(
            question="How do I start the home buying process?",
            answer=f"Start with a free consultation! Call {phone} — we'll discuss your budget, preferences, and timeline. We also recommend getting pre-approved for a mortgage first.",
            keywords=["buy", "buying", "purchase", "start", "begin", "process", "first time", "how to"],
            category="buying",
        ),
        QAPair(
            question="How do I sell my home?",
            answer=f"Contact us at {phone} for a free home valuation. We'll analyze the market, suggest pricing, and create a marketing plan to sell your home quickly and at the best price.",
            keywords=["sell", "selling", "list", "listing", "put on market", "home value"],
            category="selling",
        ),
        QAPair(
            question="What's the market like right now?",
            answer=f"The market in {areas} is always evolving. Contact us at {phone} for a current market analysis specific to your area and price range.",
            keywords=["market", "conditions", "hot", "cold", "prices", "trends", "forecast"],
            category="market",
        ),
        QAPair(
            question="Do you help with financing?",
            answer=f"While we're not a lender, we have trusted mortgage partners we can refer you to. Getting pre-approved is the first step — call {phone} and we'll connect you.",
            keywords=["financing", "mortgage", "loan", "pre-approved", "down payment", "afford"],
            category="financing",
        ),
        QAPair(
            question="Can I schedule a showing?",
            answer=f"Absolutely! Call {phone} or let me know which property you're interested in, and we'll arrange a private showing at a time that works for you.",
            keywords=["showing", "tour", "visit", "see", "view", "look at", "open house"],
            category="showing",
        ),
        QAPair(
            question="Who are your agents?",
            answer=f"Our team includes {team_str}. Each agent brings expertise and dedication to helping you achieve your real estate goals.",
            keywords=["agent", "realtor", "who", "team", "staff", "broker"],
            category="team",
        ),
        QAPair(
            question="Do you handle rentals?",
            answer=f"Yes, {name} can help with rental properties too. Whether you're looking to rent or need a property managed, call {phone} to discuss your needs.",
            keywords=["rental", "rent", "lease", "apartment", "tenant", "landlord"],
            category="rentals",
        ),
        QAPair(
            question="What's my home worth?",
            answer=f"We offer free, no-obligation home valuations! Call {phone} or share your address and we'll provide a comparative market analysis based on recent sales in your area.",
            keywords=["worth", "value", "valuation", "estimate", "cma", "appraisal", "price my home"],
            category="valuation",
        ),
        QAPair(
            question="Do you work with first-time buyers?",
            answer=f"We love working with first-time buyers! Our agents will guide you through every step — from pre-approval to closing. Call {phone} to start your journey.",
            keywords=["first time", "first-time", "never bought", "new buyer", "beginner"],
            category="buying",
        ),
        QAPair(
            question="What are the closing costs?",
            answer="Closing costs typically range from 2-5% of the purchase price. They include lender fees, title insurance, inspections, and more. We'll walk you through every cost before you sign.",
            keywords=["closing cost", "fees", "how much", "additional cost", "hidden cost"],
            category="costs",
        ),
        QAPair(
            question="How long does it take to buy a home?",
            answer="The typical process takes 30-60 days from offer acceptance to closing. Finding the right home can take a few weeks to a few months depending on the market and your criteria.",
            keywords=["how long", "timeline", "time", "duration", "weeks", "months"],
            category="timeline",
        ),
        QAPair(
            question="Do you handle commercial properties?",
            answer=f"Yes, {name} handles commercial real estate including office, retail, and investment properties. Call {phone} to discuss your commercial needs.",
            keywords=["commercial", "business", "office", "retail", "investment", "industrial"],
            category="commercial",
        ),
        QAPair(
            question="What services do you offer?",
            answer=f"We offer {services_str}. Our full-service approach means we handle everything from listing to closing.",
            keywords=["services", "offer", "provide", "help with", "what do you do"],
            category="services",
        ),
        QAPair(
            question="Do you have any open houses coming up?",
            answer=f"We regularly host open houses! Call {phone} or check our website for upcoming dates and addresses. We can also set up alerts for open houses in your preferred areas.",
            keywords=["open house", "upcoming", "this weekend", "events"],
            category="events",
        ),
        QAPair(
            question="What's the commission rate?",
            answer=f"Commission rates are negotiable and discussed upfront. Call {phone} and we'll explain our fee structure — no surprises, full transparency.",
            keywords=["commission", "rate", "percentage", "fee", "charge", "cost to sell"],
            category="pricing",
        ),
        QAPair(
            question="Can you help me find an investment property?",
            answer=f"Absolutely! We work with investors to find properties with strong ROI potential. Call {phone} to discuss your investment goals and budget.",
            keywords=["investment", "invest", "rental property", "roi", "income property", "flip"],
            category="investment",
        ),
        QAPair(
            question="Do you offer virtual tours?",
            answer=f"Yes! We offer virtual tours and video walkthroughs for many of our listings. Contact us at {phone} for a virtual showing of any property.",
            keywords=["virtual", "video", "online tour", "remote", "3d tour"],
            category="technology",
        ),
        QAPair(
            question="What should I know about the local schools?",
            answer=f"Great schools are a big part of the {areas} area. Our agents can provide detailed information about school districts, ratings, and proximity for any property.",
            keywords=["school", "education", "district", "rating", "kids", "family"],
            category="community",
        ),
        QAPair(
            question="How do I contact you?",
            answer=f"You can reach {name} at {phone}" + (f" or email {biz.email}" if biz.email else "") + ". We're here to help with all your real estate needs!",
            keywords=["contact", "reach", "call", "phone", "email", "get in touch"],
            category="contact",
        ),
    ]


# ---------------------------------------------------------------------------
# LLM enhancement — generates additional personalized Q&As
# ---------------------------------------------------------------------------

def _llm_enhance(biz: ScrapedBusiness, existing_count: int) -> list[QAPair]:
    """Use local Qwen to generate additional personalized Q&As."""
    try:
        from llm_client import llm_call
    except ImportError:
        log.warning("llm_client not available, skipping LLM enhancement")
        return []

    scraped_summary = (
        f"Business: {biz.name}\n"
        f"Niche: {biz.niche}\n"
        f"Phone: {biz.phone}\n"
        f"Address: {biz.address}\n"
        f"Hours: {biz.hours}\n"
        f"Services: {', '.join(biz.services[:10])}\n"
        f"Team: {', '.join(biz.team_members[:5])}\n"
        f"Description: {biz.description[:300]}\n"
        f"Tagline: {biz.tagline}\n"
    )
    if biz.niche == "dental":
        scraped_summary += f"Insurance: {', '.join(biz.insurance_plans[:8])}\n"
    elif biz.niche == "real_estate":
        scraped_summary += f"Areas: {', '.join(biz.areas_served[:8])}\n"

    for faq in biz.faq_entries[:5]:
        scraped_summary += f"FAQ: Q: {faq['q'][:80]} A: {faq['a'][:120]}\n"

    system_prompt = (
        "You are a chatbot Q&A generator for a business website. "
        "Generate realistic customer questions and helpful answers. "
        "Output ONLY a JSON array of objects with keys: question, answer, keywords (array of strings), category (string). "
        "No markdown, no explanation, just valid JSON."
    )

    niche_label = "dental office" if biz.niche == "dental" else "real estate agency"
    user_prompt = (
        f"Here is data scraped from a {niche_label} website:\n\n"
        f"{scraped_summary}\n\n"
        f"Generate 15 additional Q&A pairs that a website visitor might ask. "
        f"Make answers specific to this business using the scraped data. "
        f"Include the business name and phone number in answers where appropriate. "
        f"Cover questions not already in the {existing_count} template Q&As."
    )

    try:
        response = llm_call(
            system=system_prompt,
            user=user_prompt,
            agent="viper",
            task_type="writing",
            max_tokens=3000,
            temperature=0.7,
        )

        # Parse JSON from response
        text = response.strip()
        # Handle potential markdown wrapping
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        text = text.strip()

        items = json.loads(text)
        pairs = []
        for item in items:
            if isinstance(item, dict) and "question" in item and "answer" in item:
                pairs.append(QAPair(
                    question=item["question"],
                    answer=item["answer"],
                    keywords=item.get("keywords", []),
                    category=item.get("category", "general"),
                ))
        log.info("LLM generated %d additional Q&A pairs", len(pairs))
        return pairs

    except json.JSONDecodeError:
        log.warning("LLM returned invalid JSON, skipping enhancement")
        return []
    except Exception as e:
        log.warning("LLM enhancement failed: %s", e)
        return []


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------

def generate_qa_pairs(biz: ScrapedBusiness) -> list[QAPair]:
    """Generate full Q&A set: templates + LLM enhancement."""
    # Layer 1: Template bank
    if biz.niche == "dental":
        pairs = _dental_templates(biz)
    elif biz.niche == "real_estate":
        pairs = _real_estate_templates(biz)
    else:
        # Minimal general templates
        pairs = _dental_templates(biz)[:10] + _real_estate_templates(biz)[:10]

    template_count = len(pairs)
    log.info("Generated %d template Q&A pairs for %s (%s)", template_count, biz.name, biz.niche)

    # Layer 2: LLM enhancement
    llm_pairs = _llm_enhance(biz, template_count)
    if llm_pairs:
        # Dedup by checking question similarity
        existing_qs = {p.question.lower() for p in pairs}
        for lp in llm_pairs:
            if lp.question.lower() not in existing_qs:
                pairs.append(lp)
                existing_qs.add(lp.question.lower())

    # Ensure all pairs have keywords
    for pair in pairs:
        if not pair.keywords:
            words = pair.question.lower().split()
            pair.keywords = [w for w in words if len(w) > 3 and w not in
                            {"what", "your", "does", "have", "with", "about", "this", "that", "from"}]

    log.info("Total Q&A pairs: %d (templates=%d, llm=%d)",
             len(pairs), template_count, len(pairs) - template_count)
    return pairs
