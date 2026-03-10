"""Google Maps discovery via Playwright — find local businesses."""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from urllib.parse import quote_plus

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

log = logging.getLogger(__name__)


@dataclass
class MapsListing:
    """A single business listing from Google Maps."""
    business_name: str = ""
    website_url: str = ""
    phone: str = ""
    rating: float = 0.0
    review_count: int = 0
    address: str = ""
    maps_url: str = ""


def discover_businesses(
    niche: str,
    city: str,
    max_results: int = 25,
    headless: bool = True,
    delay: float = 2.5,
) -> list[MapsListing]:
    """Search Google Maps and return business listings.

    Raises RuntimeError on CAPTCHA detection.
    """
    query = f"{niche} in {city}"
    url = f"https://www.google.com/maps/search/{quote_plus(query)}"
    log.info("Maps search: %s", url)

    listings: list[MapsListing] = []
    seen_names: set[str] = set()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        page = browser.new_page(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page.set_default_timeout(30_000)

        page.goto(url, wait_until="domcontentloaded")
        time.sleep(delay)

        # Handle Google cookie consent wall (common on non-US IPs)
        _handle_consent(page, delay)

        # CAPTCHA check
        if "sorry" in page.url.lower() or page.query_selector("form#captcha-form"):
            browser.close()
            raise RuntimeError(
                "Google CAPTCHA detected. Try again later or use a different IP."
            )

        # Wait for feed container
        feed = page.query_selector('div[role="feed"]')
        if not feed:
            feed = page.query_selector('div[role="main"]')
        if not feed:
            log.warning("No results feed found for: %s", query)
            browser.close()
            return []

        # Scroll to load more results
        prev_count = 0
        stall_rounds = 0
        for _ in range(20):
            feed.evaluate("el => el.scrollTop = el.scrollHeight")
            time.sleep(delay)

            cards = page.query_selector_all('a[href*="/maps/place/"]')
            if len(cards) >= max_results:
                break
            if len(cards) == prev_count:
                stall_rounds += 1
                if stall_rounds >= 3:
                    break
            else:
                stall_rounds = 0
            prev_count = len(cards)

        # Collect card names and hrefs
        cards = page.query_selector_all('a[href*="/maps/place/"]')
        log.info("Found %d raw cards", len(cards))

        card_data: list[dict] = []
        for card in cards:
            if len(card_data) >= max_results:
                break
            name = (card.get_attribute("aria-label") or "").strip()
            href = card.get_attribute("href") or ""
            if name and name not in seen_names:
                seen_names.add(name)
                card_data.append({"name": name, "href": href})

        # Get the full feed text and parse per-business data
        feed_text = feed.evaluate("el => el.innerText") or ""
        card_sections = _split_feed_by_names(feed_text, [c["name"] for c in card_data])

        # Extract website links from the sidebar
        # When any card is open, the sidebar shows "Visit [name]'s website" links
        # Click the first card to expose these links
        website_map: dict[str, str] = {}
        if cards:
            try:
                cards[0].click()
                time.sleep(delay)
                _extract_sidebar_websites(page, website_map)
            except Exception as e:
                log.debug("Sidebar website extraction failed: %s", e)
            finally:
                try:
                    page.keyboard.press("Escape")
                    time.sleep(0.5)
                except Exception:
                    pass

        # Build listings
        for cd in card_data:
            listing = MapsListing(
                business_name=cd["name"],
                maps_url=cd["href"],
            )

            # Parse rating, phone, address from feed section
            section = card_sections.get(cd["name"], "")
            if section:
                _parse_section(listing, section)

            # Match website from sidebar
            website = website_map.get(cd["name"], "")
            if website:
                listing.website_url = website

            listings.append(listing)

        # For any listings still missing websites, click into them individually
        missing_websites = [l for l in listings if not l.website_url]
        if missing_websites:
            _click_for_websites(page, missing_websites, delay)

        browser.close()

    # Filter out individual doctor listings — only keep practices
    before = len(listings)
    listings = [l for l in listings if not _is_individual_doctor(l.business_name)]
    if len(listings) < before:
        log.info("Filtered %d individual doctor listings", before - len(listings))

    log.info("Discovered %d businesses for '%s'", len(listings), query)
    return listings


def _is_individual_doctor(name: str) -> bool:
    """Return True if the listing is an individual doctor, not a practice."""
    return name.startswith("Dr.") or name.startswith("Dr ")


def deduplicate_listings(listings: list[MapsListing]) -> list[MapsListing]:
    """Merge listings sharing the same website domain — keep the practice, not the individual."""
    from urllib.parse import urlparse

    domain_map: dict[str, MapsListing] = {}
    result: list[MapsListing] = []

    for listing in listings:
        if not listing.website_url:
            result.append(listing)
            continue

        domain = urlparse(listing.website_url).netloc.lower().lstrip("www.")
        if domain not in domain_map:
            domain_map[domain] = listing
            result.append(listing)
        else:
            existing = domain_map[domain]
            is_dr = _is_individual_doctor(listing.business_name)
            existing_is_dr = _is_individual_doctor(existing.business_name)
            if is_dr and not existing_is_dr:
                # Keep existing (practice), drop this one (individual)
                log.info("Dedup: dropped '%s' (shares domain with '%s')",
                         listing.business_name, existing.business_name)
            elif not is_dr and existing_is_dr:
                # Replace existing (individual) with this one (practice)
                log.info("Dedup: replaced '%s' with '%s' (same domain)",
                         existing.business_name, listing.business_name)
                result = [listing if l is existing else l for l in result]
                domain_map[domain] = listing
            else:
                # Both same type — keep first
                log.info("Dedup: dropped '%s' (duplicate domain of '%s')",
                         listing.business_name, existing.business_name)

    return result


def _handle_consent(page, delay: float) -> None:
    """Click through Google's cookie consent dialog if present."""
    if "consent.google" not in page.url:
        return

    log.info("Cookie consent wall detected — accepting")
    for selector in [
        'button:has-text("Accept all")',
        'button:has-text("Alles accepteren")',
        'button:has-text("Tout accepter")',
        'button:has-text("Aceptar todo")',
        'form[action*="consent"] button:nth-of-type(2)',
    ]:
        btn = page.query_selector(selector)
        if btn:
            btn.click()
            time.sleep(delay + 1)
            log.info("Consent accepted, now at: %s", page.url)
            return

    buttons = page.query_selector_all("form button")
    if len(buttons) >= 2:
        buttons[-1].click()
        time.sleep(delay + 1)
        log.info("Consent accepted (fallback), now at: %s", page.url)


def _split_feed_by_names(feed_text: str, names: list[str]) -> dict[str, str]:
    """Split the feed text into per-business sections using known names."""
    sections: dict[str, str] = {}
    for i, name in enumerate(names):
        start = feed_text.find(name)
        if start == -1:
            continue
        # Section goes from after the name to the start of the next name
        content_start = start + len(name)
        if i + 1 < len(names):
            next_start = feed_text.find(names[i + 1], content_start)
            if next_start != -1:
                sections[name] = feed_text[content_start:next_start]
                continue
        # Last card or next name not found — take rest of text (capped)
        sections[name] = feed_text[content_start:content_start + 500]
    return sections


def _parse_section(listing: MapsListing, section: str) -> None:
    """Extract rating, phone, address from a feed text section."""
    # Rating — handle locale: "4.5(329)" or "4,5(329)" or "5.0 (329)" or "5,0(329)"
    rating_match = re.search(r'(\d[.,]\d)\s*\((\d[\d.,]*)\)', section)
    if rating_match:
        listing.rating = float(rating_match.group(1).replace(",", "."))
        listing.review_count = int(
            rating_match.group(2).replace(",", "").replace(".", "")
        )

    # Phone — international format "+1 603-457-2024" or "(603) 457-2024"
    phone_match = re.search(
        r'(?:\+1\s?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}', section
    )
    if phone_match:
        listing.phone = phone_match.group().strip()

    # Address — line containing a street number + street type
    lines = [l.strip() for l in section.split("\n") if l.strip()]
    for line in lines:
        # Strip unicode decoration characters
        clean = re.sub(r'[\ue000-\uf8ff]', '', line).strip()
        # Look for "· 123 Main St" pattern (Maps puts · before address)
        addr_match = re.search(
            r'·?\s*(\d+\s+\w[\w\s]+'
            r'(?:St|Ave|Rd|Dr|Blvd|Ln|Way|Ct|Pl|Hwy|Pkwy|Cir|Ter|Trail|Loop)\.?'
            r'(?:\s+#?\w+)?)',
            clean, re.IGNORECASE,
        )
        if addr_match:
            listing.address = addr_match.group(1).strip()
            break


def _extract_sidebar_websites(page, website_map: dict[str, str]) -> None:
    """Extract website links from the Maps sidebar/detail panel.

    When a card is clicked, the sidebar shows links like:
    "Visit [name]'s website" or "Bezoek de website van [name]"
    These links contain the actual website URLs for all visible businesses.
    """
    # Get all external links in the results panel
    links = page.query_selector_all('div[role="main"] a')
    for link in links:
        href = link.get_attribute("href") or ""
        label = link.get_attribute("aria-label") or ""
        if not href or "google.com" in href or "maps" in href:
            continue

        # Extract business name from aria-label patterns:
        # EN: "Visit Central Family Dental's website"
        # NL: "Bezoek de website van Central Family Dental"
        name = ""
        # English pattern
        m = re.search(r"Visit (.+?)(?:'s website|'s website)", label)
        if m:
            name = m.group(1).strip()
        # Dutch pattern
        if not name:
            m = re.search(r"Bezoek de website van (.+)", label)
            if m:
                name = m.group(1).strip()
        # Generic: "Website: domain.com" — look for data-item-id instead
        if not name:
            item_id = link.get_attribute("data-item-id") or ""
            if item_id == "authority":
                # This is the currently-open card's website
                # We'll handle this via the click-through path
                pass

        if name and href:
            # Clean tracking params from URL
            clean_url = re.sub(r'\?utm_.*$', '', href)
            website_map[name] = clean_url
            log.debug("Sidebar website: %s → %s", name, clean_url)


def _click_for_websites(
    page, listings: list[MapsListing], delay: float,
) -> None:
    """Click into individual cards to extract website URLs from detail panel."""
    for listing in listings:
        try:
            card = page.query_selector(
                f'a[aria-label="{listing.business_name}"]'
            )
            if not card:
                continue
            card.click()
            time.sleep(delay)

            # Look for website in the detail panel
            website_el = page.query_selector(
                'a[data-item-id="authority"]'
            )
            if website_el:
                href = website_el.get_attribute("href") or ""
                if href and "google.com" not in href:
                    listing.website_url = re.sub(r'\?utm_.*$', '', href)
                    log.debug("Click website: %s → %s", listing.business_name, listing.website_url)

            # Phone
            if not listing.phone:
                phone_el = page.query_selector('button[data-item-id*="phone"]')
                if phone_el:
                    phone_text = phone_el.evaluate("el => el.innerText") or ""
                    phone_match = re.search(
                        r'(?:\+1\s?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}',
                        phone_text,
                    )
                    if phone_match:
                        listing.phone = phone_match.group().strip()

            page.keyboard.press("Escape")
            time.sleep(0.5)
        except Exception as e:
            log.debug("Click extraction failed for %s: %s", listing.business_name, e)
