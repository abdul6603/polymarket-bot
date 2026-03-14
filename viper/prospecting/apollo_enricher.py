"""Apollo.io email enrichment — find decision-maker emails by domain.

Two-step: search for people → enrich to reveal emails.
Free tier: 10K credits/month.
Budget guard: only call for score >= 7.0 AND no email from scraping.

Cost: $0 (free tier).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, asdict
from pathlib import Path

import requests

log = logging.getLogger(__name__)

_SEARCH_URL = "https://api.apollo.io/api/v1/mixed_people/api_search"
_ENRICH_URL = "https://api.apollo.io/api/v1/people/match"
_TIMEOUT = 15


def _load_api_key() -> str:
    """Load Apollo API key from env/.env."""
    key = os.getenv("APOLLO_API_KEY", "")
    if not key:
        env_path = Path.home() / "polymarket-bot" / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("APOLLO_API_KEY="):
                    key = line.split("=", 1)[1].strip()
    return key


@dataclass
class ApolloContact:
    """Contact found via Apollo.io."""
    email: str = ""
    email_status: str = ""
    first_name: str = ""
    last_name: str = ""
    title: str = ""
    linkedin_url: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _enrich_person(headers: dict, person: dict, domain: str) -> ApolloContact | None:
    """Enrich a single person to reveal email via /people/match."""
    first = person.get("first_name", "")
    last = person.get("last_name", "")
    apollo_id = person.get("id", "")

    if not first and not apollo_id:
        return None

    payload = {"reveal_personal_emails": True}
    if apollo_id:
        payload["id"] = apollo_id
    else:
        payload["first_name"] = first
        payload["last_name"] = last
        payload["domain"] = domain

    try:
        resp = requests.post(_ENRICH_URL, json=payload, headers=headers, timeout=_TIMEOUT)
        if resp.status_code != 200:
            log.debug("[APOLLO] Enrich %d for %s %s", resp.status_code, first, last)
            return None

        data = resp.json()
        match = data.get("person", {})
        if not match:
            return None

        email = match.get("email", "")
        if not email:
            return None

        return ApolloContact(
            email=email,
            email_status=match.get("email_status", ""),
            first_name=match.get("first_name", ""),
            last_name=match.get("last_name", ""),
            title=match.get("title", ""),
            linkedin_url=match.get("linkedin_url", ""),
        )
    except Exception as e:
        log.debug("[APOLLO] Enrich error: %s", e)
        return None


def enrich_email(
    domain: str,
    company_name: str = "",
    limit: int = 3,
) -> list[ApolloContact]:
    """Find contact emails for a domain via Apollo.io.

    Step 1: Search for people at the domain.
    Step 2: Enrich top matches to reveal emails.
    """
    api_key = _load_api_key()

    if not api_key:
        log.debug("[APOLLO] No API key, skipping enrichment")
        return []

    if not domain:
        return []

    domain = domain.lower().strip()
    if domain.startswith("http"):
        from urllib.parse import urlparse
        domain = urlparse(domain).netloc or domain
    domain = domain.replace("www.", "")

    headers = {
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
        "x-api-key": api_key,
    }

    # Step 1: Search for people
    payload = {
        "q_organization_domains": domain,
        "page": 1,
        "per_page": limit * 2,  # fetch extra in case some don't have emails
        "person_seniorities": ["owner", "founder", "c_suite", "vp", "director", "manager"],
    }
    if company_name:
        payload["q_organization_name"] = company_name

    try:
        resp = requests.post(_SEARCH_URL, json=payload, headers=headers, timeout=_TIMEOUT)
        if resp.status_code != 200:
            log.error("[APOLLO] Search %d for %s: %s", resp.status_code, domain, resp.text[:200])
            return []

        data = resp.json()
        people = data.get("people", [])

        if not people:
            log.info("[APOLLO] No people found for %s", domain)
            return []

        # Step 2: Enrich each person to reveal email
        contacts: list[ApolloContact] = []
        for person in people:
            if len(contacts) >= limit:
                break
            contact = _enrich_person(headers, person, domain)
            if contact:
                contacts.append(contact)

        log.info("[APOLLO] Found %d contacts for %s", len(contacts), domain)
        return contacts

    except requests.Timeout:
        log.error("[APOLLO] Timeout for %s", domain)
    except Exception as e:
        log.error("[APOLLO] Error for %s: %s", domain, e)

    return []


def extract_domain(url: str) -> str:
    """Extract clean domain from URL. Utility for callers."""
    if not url:
        return ""
    from urllib.parse import urlparse
    parsed = urlparse(url if url.startswith("http") else f"https://{url}")
    domain = parsed.netloc or parsed.path.split("/")[0]
    return domain.replace("www.", "").lower().strip()
