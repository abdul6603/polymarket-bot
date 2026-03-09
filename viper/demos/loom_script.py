"""Loom Script Generator — personalized 60-90s video scripts."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from viper.demos.scraper import ScrapedBusiness
from viper.demos.qa_generator import QAPair

log = logging.getLogger(__name__)

sys.path.insert(0, str(Path.home() / "shared"))


def generate_loom_script(biz: ScrapedBusiness, qa_pairs: list[QAPair],
                         demo_url: str) -> str:
    """Generate a personalized Loom recording script."""
    name = biz.name or "this business"
    niche = biz.niche
    phone = biz.phone or ""

    # Pick 3 best demo questions to walk through
    priority_cats = {
        "dental": ["booking", "insurance", "emergency"],
        "real_estate": ["buying", "selling", "showing"],
    }
    cats = priority_cats.get(niche, ["services", "hours", "contact"])

    demo_questions = []
    for cat in cats:
        for qa in qa_pairs:
            if qa.category == cat:
                demo_questions.append(qa)
                break
    # Fill remaining from top questions
    if len(demo_questions) < 3:
        for qa in qa_pairs[:5]:
            if qa not in demo_questions:
                demo_questions.append(qa)
            if len(demo_questions) >= 3:
                break

    q1 = demo_questions[0] if len(demo_questions) > 0 else None
    q2 = demo_questions[1] if len(demo_questions) > 1 else None
    q3 = demo_questions[2] if len(demo_questions) > 2 else None

    # Try LLM-generated script first
    llm_script = _llm_script(biz, demo_questions, demo_url)
    if llm_script:
        return llm_script

    # Fallback: template script
    if niche == "dental":
        value_hook = (
            "Right now, when someone visits your website at 11 PM with a toothache, "
            "they see a contact form. With this chatbot, they get instant answers about "
            "emergency care, can check if their insurance is accepted, and you capture "
            "their contact info — all while your team is asleep."
        )
        full_version = (
            "The full version connects directly to your scheduling system, "
            "verifies insurance in real-time, and sends appointment confirmations. "
            "It pays for itself with the first after-hours patient it captures."
        )
    elif niche == "real_estate":
        value_hook = (
            "Right now, when a buyer visits your website at midnight browsing listings, "
            "they see a contact form and leave. With this chatbot, they get instant answers "
            "about properties, get qualified on budget and timeline, and you wake up to "
            "a warm lead with full contact info."
        )
        full_version = (
            "The full version connects to your MLS feed for live listings, "
            "schedules showings directly on your calendar, and qualifies leads "
            "with your custom criteria. It's like having a showing agent who never sleeps."
        )
    else:
        value_hook = (
            "Right now, after-hours visitors just see a contact form. "
            "With this chatbot, they get instant answers and you capture their info automatically."
        )
        full_version = (
            "The full version connects to your booking system, "
            "handles payments, and sends confirmations automatically."
        )

    script = f"""# Loom Recording Script for {name}
## Duration: 60-90 seconds

---

### [0:00 - 0:15] HOOK
*Open {demo_url} in browser, chat widget visible*

"Hey! I built something specifically for {name}. It's an AI chatbot trained on your actual business data — your services, your hours, your team. Let me show you what it can do."

---

### [0:15 - 0:50] DEMO WALKTHROUGH
*Click the chat bubble to open the widget*

"When a visitor lands on your site, they see this chat assistant."

*Click the first quick-action button{f' ("{q1.question}")' if q1 else ''}*

"Watch — it knows your specific information."
{f'{chr(10)}*Show the response: "{q1.answer[:80]}..."*' if q1 else ''}

*Type: "{q2.question if q2 else 'What services do you offer?'}"*

"Every answer is personalized to {name}."
{f'{chr(10)}*Show the response*' if q2 else ''}

*Type something the chatbot does not know*

"And when it can't answer — instead of losing that visitor, it captures their name, phone, and email."

---

### [0:50 - 1:10] VALUE PROPOSITION

"{value_hook}"

---

### [1:10 - 1:30] CLOSE + CTA

"Everything you just saw was built from your publicly available website data. {full_version}"

"I'd love to set this up for {name}. Would you be open to a quick chat?"

---

## Recording Tips
- Record in a quiet space
- Use screen + face cam (bottom-right)
- Keep energy conversational, NOT salesy
- Demo URL: {demo_url}
- Share Loom link + demo URL together in your outreach message
"""
    return script


def _llm_script(biz: ScrapedBusiness, demo_qs: list[QAPair], demo_url: str) -> str | None:
    """Try to generate script via LLM."""
    try:
        from llm_client import llm_call
    except ImportError:
        return None

    qs_text = ""
    for i, qa in enumerate(demo_qs[:3], 1):
        qs_text += f"  Q{i}: {qa.question} -> A{i}: {qa.answer[:100]}\n"

    niche_label = "dental office" if biz.niche == "dental" else "real estate agency"

    try:
        response = llm_call(
            system=(
                "You write personalized 60-90 second Loom video scripts for cold outreach. "
                "Format: markdown with timestamp sections [0:00-0:15], [0:15-0:50], etc. "
                "Include stage directions in *italics*. Tone: friendly, not salesy. "
                "Output ONLY the script, no preamble."
            ),
            user=(
                f"Write a Loom script for a chatbot demo built for {biz.name} ({niche_label}).\n"
                f"Demo URL: {demo_url}\n"
                f"Phone: {biz.phone}\n"
                f"Services: {', '.join(biz.services[:6])}\n"
                f"Demo questions to walk through:\n{qs_text}\n"
                f"Structure:\n"
                f"[0:00-0:15] Hook — introduce the demo, mention their business by name\n"
                f"[0:15-0:50] Demo walkthrough — click buttons, type questions, show responses\n"
                f"[0:50-1:10] Value prop — paint the after-hours scenario\n"
                f"[1:10-1:30] Close — this was from public data, full version does more, CTA\n"
            ),
            agent="viper",
            task_type="writing",
            max_tokens=1500,
            temperature=0.7,
        )
        if response and len(response) > 100:
            # Add recording tips footer
            response += f"\n\n---\n\n## Recording Tips\n- Record in a quiet space\n- Use screen + face cam (bottom-right)\n- Keep energy conversational, NOT salesy\n- Demo URL: {demo_url}\n"
            return response
    except Exception as e:
        log.warning("LLM script generation failed: %s", e)

    return None
