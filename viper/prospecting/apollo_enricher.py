"""Apollo.io email enrichment — find decision-maker emails by domain.

Replaces the dead Hunter.io import. Free tier: 10K credits/month.
Budget guard: only call for score >= 7.0 AND no email from scraping.

Cost: $0 (free tier).
Jordan task: Sign up at https://app.apollo.io/ + API key.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path

import requests

log = logging.getLogger(__name__)

_API_URL = "https://api.apollo.io/api/v1/mixed_people/api_search"
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
    email_status: str = ""  # "verified", "guessed", etc.
    first_name: str = ""
    last_name: str = ""
    title: str = ""
    linkedin_url: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def enrich_email(
    domain: str,
    company_name: str = "",
    limit: int = 3,
) -> list[ApolloContact]:
    """Find contact emails for a domain via Apollo.io.

    Args:
        domain: Company website domain (e.g., "acmedental.com").
        company_name: Optional company name for better matching.
        limit: Max contacts to return (default 3).

    Returns:
        List of ApolloContact with email, name, title.
    """
    api_key = _load_api_key()

    if not api_key:
        log.debug("[APOLLO] No API key, skipping enrichment")
        return []

    if not domain:
        return []

    # Clean domain
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

    payload = {
        "q_organization_domains": domain,
        "page": 1,
        "per_page": limit,
        "person_seniorities": ["owner", "founder", "c_suite", "vp", "director", "manager"],
    }

    if company_name:
        payload["q_organization_name"] = company_name

    try:
        resp = requests.post(_API_URL, json=payload, headers=headers, timeout=_TIMEOUT)
        if resp.status_code != 200:
            log.error("[APOLLO] API %d for %s: %s", resp.status_code, domain, resp.text[:200])
            return []

        data = resp.json()
        people = data.get("people", [])

        contacts: list[ApolloContact] = []
        for person in people[:limit]:
            email = person.get("email", "")
            if not email:
                continue

            contact = ApolloContact(
                email=email,
                email_status=person.get("email_status", ""),
                first_name=person.get("first_name", ""),
                last_name=person.get("last_name", ""),
                title=person.get("title", ""),
                linkedin_url=person.get("linkedin_url", ""),
            )
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
