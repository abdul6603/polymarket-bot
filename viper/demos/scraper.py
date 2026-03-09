"""Website scraper — extracts business data for demo personalization."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, asdict
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}
_TIMEOUT = 15
_SUBPAGE_KEYWORDS = ["about", "services", "contact", "faq", "team", "staff",
                     "insurance", "listings", "agents", "hours", "meet"]


@dataclass
class ScrapedBusiness:
    """Structured business data extracted from a website."""
    url: str = ""
    name: str = ""
    phone: str = ""
    email: str = ""
    address: str = ""
    hours: str = ""
    services: list[str] = field(default_factory=list)
    team_members: list[str] = field(default_factory=list)
    faq_entries: list[dict] = field(default_factory=list)
    tagline: str = ""
    description: str = ""
    brand_color: str = "#2563eb"
    niche: str = "general"
    # Dental-specific
    insurance_plans: list[str] = field(default_factory=list)
    # Real estate-specific
    listings_sample: list[dict] = field(default_factory=list)
    areas_served: list[str] = field(default_factory=list)
    # Scrape quality
    pages_scraped: int = 0
    text_chars: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def quality_score(self) -> int:
        """0-100 score of how much data we got."""
        score = 0
        if self.name: score += 15
        if self.phone: score += 15
        if self.email: score += 10
        if self.address: score += 10
        if self.hours: score += 10
        if self.services: score += 15
        if self.team_members: score += 5
        if self.description: score += 10
        if self.insurance_plans or self.areas_served: score += 10
        return min(score, 100)


def scrape_business(url: str, niche: str = "auto") -> ScrapedBusiness:
    """Scrape a business website and extract structured data."""
    biz = ScrapedBusiness(url=url)

    # Fetch homepage
    homepage_soup = _fetch_page(url)
    if homepage_soup is None:
        log.warning("Could not fetch homepage: %s", url)
        return biz

    homepage_text = homepage_soup.get_text(" ", strip=True)
    biz.text_chars = len(homepage_text)
    biz.pages_scraped = 1

    if biz.text_chars < 200:
        log.warning("Page has very little text (%d chars) — may be JS-rendered", biz.text_chars)

    # Auto-detect niche
    if niche == "auto":
        niche = _detect_niche(homepage_text)
    biz.niche = niche

    # Extract from homepage
    _extract_name(homepage_soup, biz)
    _extract_contact(homepage_text, biz)
    _extract_brand_color(homepage_soup, biz)
    _extract_tagline(homepage_soup, biz)
    _extract_description(homepage_soup, biz)

    # Discover and scrape subpages
    subpage_urls = _discover_subpages(homepage_soup, url)
    all_text = homepage_text

    for sub_url in subpage_urls[:6]:
        sub_soup = _fetch_page(sub_url)
        if sub_soup is None:
            continue
        biz.pages_scraped += 1
        sub_text = sub_soup.get_text(" ", strip=True)
        all_text += " " + sub_text
        biz.text_chars += len(sub_text)

        # Extract contact from subpages too
        _extract_contact(sub_text, biz)
        _extract_faq(sub_soup, biz)
        _extract_team(sub_soup, sub_text, biz)

    # Extract niche-specific data from all text
    _extract_services(all_text, biz)
    _extract_hours(all_text, biz)

    if niche == "dental":
        _extract_insurance(all_text, biz)
    elif niche == "real_estate":
        _extract_areas(all_text, biz)

    log.info("Scraped %s: quality=%d, pages=%d, chars=%d",
             biz.name or url, biz.quality_score, biz.pages_scraped, biz.text_chars)
    return biz


def _fetch_page(url: str) -> BeautifulSoup | None:
    """Fetch and parse a single page."""
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT, allow_redirects=True)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        log.debug("Failed to fetch %s: %s", url, e)
        return None


def _detect_niche(text: str) -> str:
    """Detect business niche from page text."""
    text_lower = text.lower()
    dental_kw = ["dentist", "dental", "orthodont", "cleaning", "filling",
                 "crown", "implant", "root canal", "hygienist", "tooth"]
    re_kw = ["real estate", "realtor", "listing", "property", "home for sale",
             "buyer", "seller", "mls", "open house", "mortgage"]

    dental_hits = sum(1 for kw in dental_kw if kw in text_lower)
    re_hits = sum(1 for kw in re_kw if kw in text_lower)

    if dental_hits >= 3:
        return "dental"
    if re_hits >= 3:
        return "real_estate"
    return "general"


def _extract_name(soup: BeautifulSoup, biz: ScrapedBusiness) -> None:
    """Extract business name from title, og:site_name, or h1."""
    # og:site_name
    og = soup.find("meta", property="og:site_name")
    if og and og.get("content"):
        biz.name = og["content"].strip()
        return

    # <title> tag — strip common suffixes
    if soup.title and soup.title.string:
        raw = soup.title.string.strip()
        for sep in [" | ", " - ", " — ", " :: "]:
            if sep in raw:
                raw = raw.split(sep)[0].strip()
                break
        if len(raw) < 80:
            biz.name = raw
            return

    # First <h1>
    h1 = soup.find("h1")
    if h1:
        biz.name = h1.get_text(strip=True)[:80]


def _extract_contact(text: str, biz: ScrapedBusiness) -> None:
    """Extract phone and email from text."""
    if not biz.phone:
        phone_match = re.search(r'\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}', text)
        if phone_match:
            biz.phone = phone_match.group().strip()

    if not biz.email:
        email_match = re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text)
        if email_match:
            candidate = email_match.group()
            # Skip common non-business emails
            if not any(d in candidate.lower() for d in ["example.com", "sentry.io", "wixpress"]):
                biz.email = candidate


def _extract_brand_color(soup: BeautifulSoup, biz: ScrapedBusiness) -> None:
    """Extract brand color from theme-color meta or CSS."""
    theme = soup.find("meta", attrs={"name": "theme-color"})
    if theme and theme.get("content"):
        color = theme["content"].strip()
        if re.match(r'^#[0-9a-fA-F]{3,8}$', color):
            biz.brand_color = color
            return

    # Check inline styles for primary colors
    for style in soup.find_all("style"):
        if style.string:
            match = re.search(r'--(?:primary|brand|main)[\w-]*:\s*(#[0-9a-fA-F]{3,8})', style.string)
            if match:
                biz.brand_color = match.group(1)
                return


def _extract_tagline(soup: BeautifulSoup, biz: ScrapedBusiness) -> None:
    """Extract tagline from og:description or first prominent text."""
    og = soup.find("meta", property="og:description")
    if og and og.get("content"):
        desc = og["content"].strip()
        if len(desc) < 200:
            biz.tagline = desc
            return

    meta_desc = soup.find("meta", attrs={"name": "description"})
    if meta_desc and meta_desc.get("content"):
        desc = meta_desc["content"].strip()
        if len(desc) < 200:
            biz.tagline = desc


def _extract_description(soup: BeautifulSoup, biz: ScrapedBusiness) -> None:
    """Extract longer description from about-like sections."""
    for tag in soup.find_all(["p", "div"], limit=20):
        text = tag.get_text(strip=True)
        if 50 < len(text) < 500 and not biz.description:
            biz.description = text
            break


def _discover_subpages(soup: BeautifulSoup, base_url: str) -> list[str]:
    """Find relevant subpage links."""
    base_domain = urlparse(base_url).netloc
    found = []
    seen = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        full_url = urljoin(base_url, href)
        parsed = urlparse(full_url)

        if parsed.netloc != base_domain:
            continue
        if full_url in seen or full_url.rstrip("/") == base_url.rstrip("/"):
            continue

        link_text = (a.get_text(strip=True) + " " + parsed.path).lower()
        if any(kw in link_text for kw in _SUBPAGE_KEYWORDS):
            seen.add(full_url)
            found.append(full_url)

    return found


def _extract_services(text: str, biz: ScrapedBusiness) -> None:
    """Extract service offerings from page text."""
    if biz.niche == "dental":
        dental_services = [
            "General Dentistry", "Cosmetic Dentistry", "Teeth Whitening",
            "Dental Implants", "Orthodontics", "Root Canal", "Crowns",
            "Veneers", "Pediatric Dentistry", "Emergency Dental Care",
            "Dentures", "Bridges", "Oral Surgery", "Periodontics",
            "Teeth Cleaning", "Fillings", "Invisalign", "Sedation Dentistry",
        ]
        text_lower = text.lower()
        for svc in dental_services:
            if svc.lower() in text_lower and svc not in biz.services:
                biz.services.append(svc)
    elif biz.niche == "real_estate":
        re_services = [
            "Buyer Representation", "Seller Representation", "Property Management",
            "Market Analysis", "Home Staging", "Relocation Services",
            "Investment Properties", "New Construction", "Luxury Homes",
            "First-Time Buyers", "Commercial Real Estate", "Rental Properties",
        ]
        text_lower = text.lower()
        for svc in re_services:
            if svc.lower() in text_lower and svc not in biz.services:
                biz.services.append(svc)


def _extract_hours(text: str, biz: ScrapedBusiness) -> None:
    """Extract business hours from text."""
    if biz.hours:
        return
    patterns = [
        r'(?:Monday|Mon)[\s\-–toThru]+(?:Friday|Fri)[:\s]+(\d{1,2}(?::\d{2})?\s*(?:AM|PM|am|pm)\s*[-–to]+\s*\d{1,2}(?::\d{2})?\s*(?:AM|PM|am|pm))',
        r'(?:Hours|Office Hours|Business Hours)[:\s]+([\w\s:,\-–]+(?:AM|PM|am|pm))',
        r'(\d{1,2}(?::\d{2})?\s*(?:AM|am)\s*[-–]\s*\d{1,2}(?::\d{2})?\s*(?:PM|pm))',
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            biz.hours = match.group(0).strip()[:100]
            return


def _extract_faq(soup: BeautifulSoup, biz: ScrapedBusiness) -> None:
    """Extract FAQ entries from structured FAQ sections."""
    # Look for FAQ schema
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            import json
            data = json.loads(script.string or "")
            if isinstance(data, dict) and data.get("@type") == "FAQPage":
                for item in data.get("mainEntity", []):
                    q = item.get("name", "")
                    a = item.get("acceptedAnswer", {}).get("text", "")
                    if q and a:
                        biz.faq_entries.append({"q": q, "a": a})
        except Exception:
            continue

    # Look for accordion/FAQ patterns
    for dt in soup.find_all(["dt", "summary"]):
        q = dt.get_text(strip=True)
        dd = dt.find_next_sibling(["dd", "div", "p"])
        if dd and q:
            a = dd.get_text(strip=True)
            if len(q) > 10 and len(a) > 10:
                biz.faq_entries.append({"q": q, "a": a[:300]})


def _extract_team(soup: BeautifulSoup, text: str, biz: ScrapedBusiness) -> None:
    """Extract team member names."""
    # Look for common team patterns: "Dr. Name" or "Name, Title"
    dr_pattern = re.findall(r'Dr\.?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2}', text)
    for name in dr_pattern:
        if name not in biz.team_members:
            biz.team_members.append(name)

    # Look for team cards with names in headings
    for heading in soup.find_all(["h3", "h4"], limit=20):
        name = heading.get_text(strip=True)
        if 5 < len(name) < 40 and not any(c.isdigit() for c in name):
            parent_class = " ".join(heading.parent.get("class", []))
            if any(kw in parent_class.lower() for kw in ["team", "staff", "doctor", "agent"]):
                if name not in biz.team_members:
                    biz.team_members.append(name)


def _extract_insurance(text: str, biz: ScrapedBusiness) -> None:
    """Extract accepted insurance plans (dental)."""
    insurance_names = [
        "Delta Dental", "Cigna", "Aetna", "MetLife", "Guardian",
        "United Healthcare", "Humana", "BlueCross", "Blue Cross",
        "Anthem", "GEHA", "Principal", "Ameritas", "DentaQuest",
        "Northeast Delta Dental", "Medicaid",
    ]
    text_lower = text.lower()
    for ins in insurance_names:
        if ins.lower() in text_lower and ins not in biz.insurance_plans:
            biz.insurance_plans.append(ins)


def _extract_areas(text: str, biz: ScrapedBusiness) -> None:
    """Extract areas served (real estate)."""
    # Look for NH towns/cities commonly near Dover
    nh_areas = [
        "Dover", "Portsmouth", "Rochester", "Durham", "Newmarket",
        "Barrington", "Somersworth", "Madbury", "Lee", "Exeter",
        "Hampton", "Rye", "Kittery", "York", "Berwick",
        "Rollinsford", "Strafford", "Farmington",
    ]
    for area in nh_areas:
        if area in text and area not in biz.areas_served:
            biz.areas_served.append(area)
