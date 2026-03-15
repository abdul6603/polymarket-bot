"""Viper Inbound RSS Poller — monitors 25 Google Alert feeds for buyer intent.

Runs every 5 minutes as part of Viper's loop. High-intent matches (score 50+)
trigger immediate Telegram alerts to Jordan via Shelby.

VIPER-Q scoring from spec:
  Industry Fit (0-20), Budget Signals (0-20), Project Specificity (0-15),
  Decision-Maker (0-15), Timeline Urgency (0-15), Engagement Quality (0-10),
  Tech Adoption (0-5). Negative deductions for job seekers/spam.
"""
from __future__ import annotations

import json
import logging
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

_TZ_ET = ZoneInfo("America/New_York")
_DATA_DIR = Path.home() / "polymarket-bot" / "data"
_FEEDS_FILE = Path.home() / "polymarket-bot" / "viper" / "inbound" / "rss_feeds.json"
_SEEN_FILE = _DATA_DIR / "inbound_seen.json"
_INBOUND_LOG = _DATA_DIR / "inbound_leads.jsonl"

_POLL_TIMEOUT = 15  # seconds per feed

# ── Buyer Intent Keywords (HIGH weight) ─────────────────────────────

_BUYER_KEYWORDS = {
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

# ── Job Seeker Keywords (NEGATIVE) ──────────────────────────────────

_JOB_SEEKER_KEYWORDS = [
    "hiring", "job", "position", "salary", "remote work", "freelance",
    "full-time", "part-time", "hourly rate", "join our team",
    "add to our team", "dedicated resource", "daily standups",
    "report to our manager", "resume", "apply now",
]

_SPAM_KEYWORDS = [
    "free trial", "limited time offer", "click here", "unsubscribe",
    "sponsored", "advertisement", "affiliate",
]

# ── Industry Fit ────────────────────────────────────────────────────

_NICHE_KEYWORDS = {
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

_NICHE_SCORES = {"dental": 20, "real_estate": 20, "hvac": 20, "legal": 20, "med_spa": 18}


def _detect_niche(text: str) -> tuple[str, int]:
    lower = text.lower()
    for niche, keywords in _NICHE_KEYWORDS.items():
        for kw in keywords:
            if kw in lower:
                return niche, _NICHE_SCORES.get(niche, 10)
    if any(w in lower for w in ["small business", "business owner", "my business"]):
        return "general", 10
    return "unknown", 0


# ── VIPER-Q Scoring ─────────────────────────────────────────────────

def score_lead(title: str, snippet: str) -> dict:
    """Score an inbound lead using VIPER-Q 100-point model."""
    text = (title + " " + snippet).lower()
    score = 0
    matched_signals = []

    # Industry Fit (0-20)
    niche, niche_score = _detect_niche(text)
    score += niche_score
    if niche_score > 0:
        matched_signals.append(f"niche:{niche}({niche_score})")

    # Buyer Intent / Budget Signals (0-20)
    intent_score = 0
    for kw, pts in _BUYER_KEYWORDS.items():
        if kw in text:
            intent_score += pts
            matched_signals.append(f"intent:{kw}")
    intent_score = min(intent_score, 20)
    score += intent_score

    # Project Specificity (0-15)
    spec_score = 0
    if any(w in text for w in ["build me", "need a chatbot built", "want to automate",
                                "deliverable", "fixed price", "turnkey", "end-to-end"]):
        spec_score += 12
        matched_signals.append("project_specific")
    elif any(w in text for w in ["automate", "chatbot", "bot", "ai assistant"]):
        spec_score += 5
    score += min(spec_score, 15)

    # Decision-Maker signals (0-15)
    dm_score = 0
    if any(w in text for w in ["owner", "ceo", "founder", "i own", "my practice",
                                "my business", "my firm", "my company"]):
        dm_score += 15
        matched_signals.append("decision_maker")
    elif any(w in text for w in ["manager", "director"]):
        dm_score += 10
    score += min(dm_score, 15)

    # Timeline Urgency (0-15)
    urg_score = 0
    if any(w in text for w in ["asap", "immediately", "urgent", "this week", "right now"]):
        urg_score += 15
        matched_signals.append("urgent")
    elif any(w in text for w in ["soon", "this month", "within"]):
        urg_score += 8
    score += min(urg_score, 15)

    # Tech Adoption (0-5)
    tech_score = 0
    if any(w in text for w in ["crm", "automation", "zapier", "n8n", "make.com"]):
        tech_score += 5
        matched_signals.append("tech_aware")
    elif any(w in text for w in ["website", "online", "digital"]):
        tech_score += 2
    score += min(tech_score, 5)

    # Negative deductions
    deductions = 0
    if any(w in text for w in _JOB_SEEKER_KEYWORDS):
        deductions += 20
        matched_signals.append("JOB_SEEKER(-20)")
    if any(w in text for w in _SPAM_KEYWORDS):
        deductions += 10
        matched_signals.append("SPAM(-10)")
    if any(w in text for w in ["student", "academic", "research paper", "thesis"]):
        deductions += 15
        matched_signals.append("ACADEMIC(-15)")
    if "free" in text and "no budget" in text:
        deductions += 10
        matched_signals.append("NO_BUDGET(-10)")

    final_score = max(0, min(100, score - deductions))

    # Classification
    if final_score >= 75:
        classification = "HOT"
    elif final_score >= 50:
        classification = "WARM"
    elif final_score >= 30:
        classification = "LUKEWARM"
    elif final_score >= 10:
        classification = "LOW"
    else:
        classification = "DISQUALIFIED"

    return {
        "score": final_score,
        "classification": classification,
        "niche": niche,
        "signals": matched_signals,
        "raw_score": score,
        "deductions": deductions,
    }


# ── RSS Parsing ─────────────────────────────────────────────────────

def _fetch_feed(url: str) -> list[dict]:
    """Fetch and parse a Google Alerts RSS feed. Returns list of entries."""
    try:
        req = Request(url, headers={"User-Agent": "Viper-Inbound/1.0"})
        with urlopen(req, timeout=_POLL_TIMEOUT) as resp:
            xml_data = resp.read()
        root = ET.fromstring(xml_data)

        entries = []
        # Atom feed format (Google Alerts uses Atom)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        for entry in root.findall("atom:entry", ns):
            title_el = entry.find("atom:title", ns)
            link_el = entry.find("atom:link", ns)
            content_el = entry.find("atom:content", ns)
            id_el = entry.find("atom:id", ns)
            published_el = entry.find("atom:published", ns)

            entries.append({
                "id": id_el.text if id_el is not None else "",
                "title": title_el.text if title_el is not None else "",
                "url": link_el.get("href", "") if link_el is not None else "",
                "snippet": (content_el.text or "")[:500] if content_el is not None else "",
                "published": published_el.text if published_el is not None else "",
            })
        return entries
    except (URLError, ET.ParseError, OSError) as e:
        log.debug("Feed fetch failed for %s: %s", url[:60], e)
        return []


# ── Seen tracking ───────────────────────────────────────────────────

def _load_seen() -> set:
    if _SEEN_FILE.exists():
        try:
            return set(json.loads(_SEEN_FILE.read_text()))
        except Exception:
            log.warning("Corrupted seen file at %s — starting fresh", _SEEN_FILE)
            return set()
    return set()


def _save_seen(seen: set) -> None:
    # Cap at 25K to prevent unbounded growth (covers ~3 days of feeds)
    items = list(seen)
    if len(items) > 25000:
        items = items[-25000:]
    _SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    _SEEN_FILE.write_text(json.dumps(items))


def _log_lead(entry: dict, result: dict, feed_keyword: str) -> None:
    """Append to inbound leads JSONL log."""
    record = {
        "ts": datetime.now(_TZ_ET).isoformat(),
        "title": entry.get("title", ""),
        "url": entry.get("url", ""),
        "source": "google_alert",
        "feed_keyword": feed_keyword,
        "score": result["score"],
        "classification": result["classification"],
        "niche": result["niche"],
        "signals": result["signals"],
    }
    with open(_INBOUND_LOG, "a") as f:
        f.write(json.dumps(record) + "\n")


# ── Telegram Alert ──────────────────────────────────────────────────

def _send_tg_alert(entry: dict, result: dict) -> None:
    """Send high-intent lead alert to Jordan via Shelby TG."""
    try:
        from viper.tg_router import send as tg_send
    except ImportError:
        log.warning("tg_router not available — skipping TG alert")
        return

    emoji = {"HOT": "🔥", "WARM": "🟡"}.get(result["classification"], "📋")

    text = (
        f"{emoji} <b>Viper Inbound — {result['classification']}</b>\n\n"
        f"Source: Google Alert\n"
        f"Post: {entry.get('title', 'N/A')}\n"
        f"URL: {entry.get('url', 'N/A')}\n"
        f"Niche: {result['niche'].replace('_', ' ').title()}\n"
        f"Score: {result['score']}/100\n"
        f"Signals: {', '.join(result['signals'][:5])}\n\n"
        f"→ Reply <b>BID</b> or <b>SKIP</b>"
    )

    try:
        tg_send(text, channel="INBOUND")
        log.info("TG alert sent for: %s (score %d)", entry.get("title", "")[:40], result["score"])
    except Exception as e:
        log.error("TG alert failed: %s", e)


# ── Main Poll Loop ──────────────────────────────────────────────────

def poll_all_feeds() -> dict:
    """Poll all 25 RSS feeds, score new entries, alert on high-intent.

    Returns summary dict with counts.
    """
    if not _FEEDS_FILE.exists():
        log.warning("No RSS feeds configured at %s", _FEEDS_FILE)
        return {"polled": 0, "new": 0, "hot": 0, "warm": 0}

    feeds_data = json.loads(_FEEDS_FILE.read_text())
    feeds = feeds_data.get("feeds", [])
    if not feeds:
        return {"polled": 0, "new": 0, "hot": 0, "warm": 0}

    seen = _load_seen()
    stats = {"polled": 0, "new": 0, "hot": 0, "warm": 0, "archived": 0}

    # Fetch all feeds in parallel (10 threads)
    feed_results = {}
    valid_feeds = [(f.get("url", ""), f.get("keyword", "")) for f in feeds if f.get("url")]
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(_fetch_feed, url): (url, kw) for url, kw in valid_feeds}
        for future in as_completed(futures):
            url, kw = futures[future]
            try:
                feed_results[(url, kw)] = future.result()
            except Exception:
                feed_results[(url, kw)] = []

    for (url, keyword), entries in feed_results.items():
        stats["polled"] += 1

        for entry in entries:
            entry_id = entry.get("id") or entry.get("url", "")
            if not entry_id or entry_id in seen:
                continue

            seen.add(entry_id)
            stats["new"] += 1

            # Score it
            result = score_lead(entry.get("title", ""), entry.get("snippet", ""))

            # Log all leads
            _log_lead(entry, result, keyword)

            # Alert on HOT and WARM
            if result["classification"] == "HOT":
                stats["hot"] += 1
                _send_tg_alert(entry, result)
            elif result["classification"] == "WARM":
                stats["warm"] += 1
                _send_tg_alert(entry, result)
            else:
                stats["archived"] += 1

    _save_seen(seen)

    if stats["new"] > 0:
        log.info(
            "Inbound poll: %d feeds, %d new, %d hot, %d warm, %d archived",
            stats["polled"], stats["new"], stats["hot"], stats["warm"], stats["archived"],
        )

    return stats
