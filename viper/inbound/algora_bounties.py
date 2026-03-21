"""Viper Inbound Algora Bounty Scanner — monitors algora.io for new code bounties.

Scrapes the Algora bounties page every 30 minutes. New bounties $100+
trigger Telegram alerts to Jordan via Shelby with bounty details.

Filters for languages we can solve: TypeScript, JavaScript, Python, Go, Rust.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

_TZ_ET = ZoneInfo("America/New_York")
_DATA_DIR = Path.home() / "polymarket-bot" / "data"
_SEEN_FILE = _DATA_DIR / "algora_seen.json"
_BOUNTY_LOG = _DATA_DIR / "algora_bounties.jsonl"

_ALGORA_URL = "https://algora.io/bounties"
_FETCH_TIMEOUT = 20

# Languages we can realistically solve
_TARGET_LANGS = {"typescript", "javascript", "python", "go", "rust", "ruby", "php"}

# Minimum bounty amount to alert on
_MIN_BOUNTY_USD = 100


def _fetch_bounties_page() -> str | None:
    """Fetch the Algora bounties HTML page."""
    try:
        req = Request(_ALGORA_URL, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Viper-Bounty/1.0",
            "Accept": "text/html",
        })
        with urlopen(req, timeout=_FETCH_TIMEOUT) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (URLError, OSError) as e:
        log.warning("[ALGORA] Fetch failed: %s", e)
        return None


def _parse_bounties(html: str) -> list[dict]:
    """Parse bounty cards from the Algora bounties page.

    Algora HTML structure per bounty:
      <a href="https://github.com/org/repo/issues/123">
        <li class="flex items-center ...">
          <div class="flex-shrink-0 ...">avatar</div>
          <div class="flex-grow ...">
            <div class="flex items-center text-sm">
              <span class="font-semibold ...">OrgName</span>
              <span class="text-muted-foreground ...">#123</span>
              <span class="... text-success ...">$500</span>
              <span class="text-foreground">Issue title</span>
            </div>
          </div>
        </li>
      </a>
    """
    soup = BeautifulSoup(html, "html.parser")
    bounties = []

    for link in soup.find_all("a", href=True):
        href = link["href"]

        gh_match = re.search(r"github\.com/([^/]+/[^/]+)/issues/(\d+)", href)
        if not gh_match:
            continue

        repo = gh_match.group(1)
        issue_num = gh_match.group(2)
        bounty_id = f"{repo}#{issue_num}"

        # Parse structured spans inside the link
        amount = 0
        title = ""
        org_name = ""

        # Find the amount span (has text-success class and $ sign)
        amount_span = link.find("span", class_=lambda c: c and "text-success" in c)
        if amount_span:
            amount_text = amount_span.get_text(strip=True)
            amount_match = re.search(r"\$\s*([\d,]+)", amount_text)
            if amount_match:
                amount = int(amount_match.group(1).replace(",", ""))

        # Find the title span (has text-foreground class)
        title_span = link.find("span", class_=lambda c: c and "text-foreground" in c)
        if title_span:
            title = title_span.get_text(strip=True)

        # Find the org name span (has font-semibold class)
        org_span = link.find("span", class_=lambda c: c and "font-semibold" in c)
        if org_span:
            org_name = org_span.get_text(strip=True)

        if not title:
            title = f"{org_name or repo} #{issue_num}"

        # Language not shown in the list view — leave blank
        lang = ""

        if amount > 0:
            bounties.append({
                "id": bounty_id,
                "repo": repo,
                "issue": issue_num,
                "title": title[:200],
                "amount": amount,
                "language": lang,
                "url": f"https://github.com/{repo}/issues/{issue_num}",
                "algora_url": href,
            })

    # Deduplicate by bounty_id (same issue can appear multiple times)
    seen_ids = set()
    unique = []
    for b in bounties:
        if b["id"] not in seen_ids:
            seen_ids.add(b["id"])
            unique.append(b)

    return unique


# ── Seen tracking ───────────────────────────────────────────────────

def _load_seen() -> set:
    if _SEEN_FILE.exists():
        try:
            return set(json.loads(_SEEN_FILE.read_text()))
        except Exception:
            return set()
    return set()


def _save_seen(seen: set) -> None:
    items = list(seen)
    if len(items) > 2000:
        items = items[-2000:]
    _SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    _SEEN_FILE.write_text(json.dumps(items))


def _log_bounty(bounty: dict) -> None:
    """Append to algora bounties JSONL log."""
    record = {
        "ts": datetime.now(_TZ_ET).isoformat(),
        **bounty,
    }
    _BOUNTY_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(_BOUNTY_LOG, "a") as f:
        f.write(json.dumps(record) + "\n")


# ── Telegram Alert ──────────────────────────────────────────────────

def _send_tg_alert(bounty: dict) -> None:
    """Send new bounty alert to Jordan via Shelby TG."""
    try:
        from viper.tg_router import send as tg_send
    except ImportError:
        log.warning("tg_router not available — skipping TG alert")
        return

    lang = bounty.get("language", "Unknown")
    can_do = lang.lower() in _TARGET_LANGS
    fit_emoji = "✅" if can_do else "⚠️"

    text = (
        f"💎 <b>NEW BOUNTY</b>\n\n"
        f"<b>${bounty['amount']:,}</b> — {bounty['title']}\n\n"
        f"Repo: {bounty['repo']}\n"
        f"Language: {lang} {fit_emoji}\n"
        f"GitHub: {bounty['url']}\n\n"
        f"{'We can solve this.' if can_do else 'Outside our core stack.'}"
    )

    try:
        tg_send(text, channel="INBOUND")
        log.info("[ALGORA] TG alert sent: $%d — %s", bounty["amount"], bounty["title"][:40])
    except Exception as e:
        log.error("[ALGORA] TG alert failed: %s", e)


# ── Main Poll ───────────────────────────────────────────────────────

def poll_algora() -> dict:
    """Scrape Algora bounties page, alert on new bounties $100+.

    Returns summary dict with counts.
    """
    stats = {"scraped": 0, "new": 0, "alerted": 0, "skipped": 0}

    html = _fetch_bounties_page()
    if not html:
        return stats

    bounties = _parse_bounties(html)
    stats["scraped"] = len(bounties)

    if not bounties:
        log.debug("[ALGORA] No bounties parsed from page")
        return stats

    seen = _load_seen()

    for bounty in bounties:
        if bounty["id"] in seen:
            continue

        seen.add(bounty["id"])
        stats["new"] += 1

        # Log all new bounties
        _log_bounty(bounty)

        # Only alert on bounties worth our time
        if bounty["amount"] < _MIN_BOUNTY_USD:
            stats["skipped"] += 1
            continue

        _send_tg_alert(bounty)
        stats["alerted"] += 1

    _save_seen(seen)

    log.info(
        "[ALGORA] Poll: %d bounties, %d new, %d alerted, %d skipped (< $%d)",
        stats["scraped"], stats["new"], stats["alerted"], stats["skipped"], _MIN_BOUNTY_USD,
    )

    return stats
