"""Viper Inbound F5Bot Poller — scrapes F5Bot dashboard for Reddit/HN keyword alerts.

F5Bot monitors Reddit + Hacker News for keywords in real-time (free).
This module logs into f5bot.com, scrapes recent hits from the dashboard,
scores them with VIPER-Q, and sends HOT/WARM leads to Jordan via TG.

Runs every 10 minutes as part of Viper's inbound loop.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

_TZ_ET = ZoneInfo("America/New_York")
_DATA_DIR = Path.home() / "polymarket-bot" / "data"
_SEEN_FILE = _DATA_DIR / "f5bot_seen.json"
_INBOUND_LOG = _DATA_DIR / "inbound_leads.jsonl"
_SESSION_FILE = _DATA_DIR / "f5bot_session.json"

_LOGIN_URL = "https://f5bot.com/login"
_LOGIN_POST_URL = "https://f5bot.com/login-post"
_DASH_URL = "https://f5bot.com/dash"
_HISTORY_URL = "https://f5bot.com/history"
_MAX_SEEN = 5000


# ── Session Management ──────────────────────────────────────────────

def _get_credentials() -> tuple[str, str]:
    """Load F5Bot credentials from env."""
    email = os.getenv("F5BOT_EMAIL", "")
    password = os.getenv("F5BOT_PASSWORD", "")
    if not email or not password:
        # Try loading from .env file directly
        env_path = Path.home() / "polymarket-bot" / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("F5BOT_EMAIL="):
                    email = line.split("=", 1)[1].strip()
                elif line.startswith("F5BOT_PASSWORD="):
                    password = line.split("=", 1)[1].strip()
    return email, password


def _load_session() -> dict:
    """Load saved session cookies."""
    if _SESSION_FILE.exists():
        try:
            data = json.loads(_SESSION_FILE.read_text())
            # Check if session is less than 24h old
            saved_at = data.get("saved_at", 0)
            if time.time() - saved_at < 86400:
                return data.get("cookies", {})
        except Exception:
            pass
    return {}


def _save_session(cookies: dict) -> None:
    """Save session cookies for reuse."""
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _SESSION_FILE.write_text(json.dumps({
        "cookies": cookies,
        "saved_at": time.time(),
    }))


def _login() -> requests.Session | None:
    """Login to F5Bot and return authenticated session."""
    email, password = _get_credentials()
    if not email or not password:
        log.warning("[F5BOT] No credentials configured (F5BOT_EMAIL/F5BOT_PASSWORD)")
        return None

    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

    # Try saved session first
    saved_cookies = _load_session()
    if saved_cookies:
        session.cookies.update(saved_cookies)
        try:
            resp = session.get(_DASH_URL, timeout=15, allow_redirects=False)
            if resp.status_code == 200 and "Logout" in resp.text:
                log.debug("[F5BOT] Reused saved session")
                return session
        except Exception:
            pass

    # Fresh login
    try:
        # Get login page + CSRF token
        login_page = session.get(_LOGIN_URL, timeout=15)
        csrf_token = ""
        soup = BeautifulSoup(login_page.text, "html.parser")
        csrf_input = soup.find("input", {"name": "csrf"})
        if csrf_input:
            csrf_token = csrf_input.get("value", "")

        # POST to /login-post (F5Bot's actual endpoint)
        resp = session.post("https://f5bot.com/login-post", data={
            "csrf": csrf_token,
            "email": email,
            "password": password,
        }, timeout=15, allow_redirects=True)

        if resp.status_code == 200 and "Logout" in resp.text:
            _save_session(dict(session.cookies))
            log.info("[F5BOT] Login successful")
            return session
        else:
            log.warning("[F5BOT] Login failed (status=%d)", resp.status_code)
            return None

    except Exception as e:
        log.error("[F5BOT] Login error: %s", str(e)[:200])
        return None


# ── Dashboard Scraping ──────────────────────────────────────────────

def _get_alert_ids(session: requests.Session) -> list[dict]:
    """Get all alert IDs and keywords from dashboard."""
    try:
        resp = session.get(_DASH_URL, timeout=15)
        if resp.status_code != 200:
            log.warning("[F5BOT] Dashboard fetch failed: HTTP %d", resp.status_code)
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        alerts = []
        table = soup.find("table")
        if not table:
            return []

        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 4:
                continue
            keyword = cells[0].get_text(strip=True)
            # Extract alert_id from edit link
            edit_link = row.find("a", href=lambda h: h and "alert_id=" in h)
            if edit_link:
                href = edit_link["href"]
                import urllib.parse
                params = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
                alert_id = params.get("alert_id", [""])[0]
                if alert_id:
                    alerts.append({"id": alert_id, "keyword": keyword})

        return alerts
    except Exception as e:
        log.error("[F5BOT] Dashboard error: %s", str(e)[:200])
        return []


def _scrape_history(session: requests.Session, alert_id: str, keyword: str) -> list[dict]:
    """Scrape hit history for a single alert."""
    try:
        resp = session.get(f"{_HISTORY_URL}?id={alert_id}", timeout=15)
        if resp.status_code != 200:
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        hits = []
        table = soup.find("table")
        if not table:
            return []

        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 5:
                continue

            # Columns: Keyword, Flags, Site, Title, Context, Timestamp
            site = cells[2].get_text(strip=True) if len(cells) > 2 else ""
            title = cells[3].get_text(strip=True) if len(cells) > 3 else ""
            context = cells[4].get_text(strip=True) if len(cells) > 4 else ""

            # Find the reddit/hn link
            link = row.find("a", href=lambda h: h and ("reddit.com" in h or "ycombinator.com" in h))
            if not link:
                continue

            url = link["href"]
            source = "reddit" if "reddit.com" in url else "hackernews"

            hits.append({
                "title": title[:200],
                "url": url,
                "source": f"f5bot_{source}",
                "keyword": keyword,
                "context": context[:300],
                "site": site,
            })

        return hits
    except Exception as e:
        log.error("[F5BOT] History scrape error for %s: %s", alert_id, str(e)[:200])
        return []


def _scrape_all_hits(session: requests.Session) -> list[dict]:
    """Scrape all keyword hit histories from F5Bot."""
    alerts = _get_alert_ids(session)
    if not alerts:
        log.info("[F5BOT] No alerts configured")
        return []

    all_hits = []
    for alert in alerts:
        hits = _scrape_history(session, alert["id"], alert["keyword"])
        all_hits.extend(hits)

    log.info("[F5BOT] Scraped %d total hits across %d keywords", len(all_hits), len(alerts))
    return all_hits


# ── Seen Tracking ───────────────────────────────────────────────────

def _load_seen() -> set:
    if _SEEN_FILE.exists():
        try:
            return set(json.loads(_SEEN_FILE.read_text()))
        except Exception:
            return set()
    return set()


def _save_seen(seen: set) -> None:
    items = list(seen)
    if len(items) > _MAX_SEEN:
        items = items[-_MAX_SEEN:]
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _SEEN_FILE.write_text(json.dumps(items))


def _log_lead(hit: dict, result: dict) -> None:
    """Append to inbound leads JSONL log."""
    record = {
        "ts": datetime.now(_TZ_ET).isoformat(),
        "title": hit.get("title", ""),
        "url": hit.get("url", ""),
        "source": hit.get("source", "f5bot"),
        "feed_keyword": hit.get("keyword", ""),
        "score": result["score"],
        "classification": result["classification"],
        "niche": result["niche"],
        "signals": result["signals"],
    }
    with open(_INBOUND_LOG, "a") as f:
        f.write(json.dumps(record) + "\n")


# ── TG Alert ────────────────────────────────────────────────────────

def _send_tg_alert(hit: dict, result: dict) -> None:
    """Send high-intent lead alert to Jordan via TG."""
    try:
        from viper.tg_router import send as tg_send
    except ImportError:
        log.warning("[F5BOT] tg_router not available — skipping TG alert")
        return

    emoji = {"HOT": "\U0001f525", "WARM": "\U0001f7e1"}.get(result["classification"], "\U0001f4cb")
    source_label = hit.get("source", "f5bot").replace("f5bot_", "").title()

    text = (
        f"{emoji} <b>Viper Inbound — {result['classification']}</b>\n\n"
        f"Source: F5Bot ({source_label})\n"
        f"Post: {hit.get('title', 'N/A')}\n"
        f"URL: {hit.get('url', 'N/A')}\n"
        f"Keyword: {hit.get('keyword', 'N/A')}\n"
        f"Niche: {result['niche'].replace('_', ' ').title()}\n"
        f"Score: {result['score']}/100\n"
        f"Signals: {', '.join(result['signals'][:5])}\n\n"
        f"\u2192 Reply <b>BID</b> or <b>SKIP</b>"
    )

    try:
        tg_send(text, channel="INBOUND")
        log.info("[F5BOT] TG alert sent: %s (score %d)", hit.get("title", "")[:40], result["score"])
    except Exception as e:
        log.error("[F5BOT] TG alert failed: %s", e)


# ── Main Poll Function ──────────────────────────────────────────────

def poll_f5bot() -> dict:
    """Poll F5Bot dashboard, score new hits, alert on high-intent.

    Returns summary dict with counts.
    """
    stats = {"polled": 0, "new": 0, "hot": 0, "warm": 0, "archived": 0}

    session = _login()
    if not session:
        return stats

    hits = _scrape_all_hits(session)
    if not hits:
        return stats

    stats["polled"] = len(hits)
    seen = _load_seen()

    try:
        from viper.viper_q import score as viper_q_score
    except ImportError:
        log.error("[F5BOT] viper_q not available — cannot score")
        return stats

    for hit in hits:
        url = hit.get("url", "")
        if not url or url in seen:
            continue

        seen.add(url)
        stats["new"] += 1

        # Score with VIPER-Q (title + context for better signal detection)
        snippet = hit.get("context", "") or hit.get("title", "")
        result = viper_q_score(hit.get("title", ""), snippet)

        # Log all leads
        _log_lead(hit, result)

        # Alert on HOT and WARM
        if result["classification"] == "HOT":
            stats["hot"] += 1
            _send_tg_alert(hit, result)
        elif result["classification"] == "WARM":
            stats["warm"] += 1
            _send_tg_alert(hit, result)
        else:
            stats["archived"] += 1

    _save_seen(seen)

    if stats["new"] > 0:
        log.info(
            "[F5BOT] Poll: %d hits, %d new, %d hot, %d warm, %d archived",
            stats["polled"], stats["new"], stats["hot"], stats["warm"], stats["archived"],
        )

    return stats
