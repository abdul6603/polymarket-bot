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
            # Try alternative selector
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

        # Parse each card
        cards = page.query_selector_all('a[href*="/maps/place/"]')
        log.info("Found %d raw cards", len(cards))

        for card in cards:
            if len(listings) >= max_results:
                break
            try:
                listing = _parse_card(card, page)
                if listing.business_name and listing.business_name not in seen_names:
                    seen_names.add(listing.business_name)
                    listings.append(listing)
            except Exception as e:
                log.debug("Failed to parse card: %s", e)

        browser.close()

    log.info("Discovered %d businesses for '%s'", len(listings), query)
    return listings


def _handle_consent(page, delay: float) -> None:
    """Click through Google's cookie consent dialog if present."""
    if "consent.google" not in page.url:
        return

    log.info("Cookie consent wall detected — accepting")
    # Try multiple button selectors (text varies by locale)
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

    # Fallback: try the second button in the consent form
    buttons = page.query_selector_all("form button")
    if len(buttons) >= 2:
        buttons[-1].click()
        time.sleep(delay + 1)
        log.info("Consent accepted (fallback), now at: %s", page.url)


def _parse_card(card, page) -> MapsListing:
    """Extract data from a single Maps card element."""
    listing = MapsListing()

    # Business name from aria-label
    label = card.get_attribute("aria-label") or ""
    listing.business_name = label.strip()
    listing.maps_url = card.get_attribute("href") or ""

    # Navigate into the card's parent container to get details
    parent = card.evaluate_handle("el => el.closest('[jsaction]') || el.parentElement")

    # Get all text from the card area
    card_text = parent.evaluate("el => el.innerText") if parent else ""

    # Rating — look for pattern like "4.5" followed by rating indicators
    rating_match = re.search(r'(\d\.\d)\s*\((\d[\d,]*)\)', card_text)
    if rating_match:
        listing.rating = float(rating_match.group(1))
        listing.review_count = int(rating_match.group(2).replace(",", ""))

    # Phone
    phone_match = re.search(r'\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}', card_text)
    if phone_match:
        listing.phone = phone_match.group().strip()

    # Address — typically the line after the category
    lines = [l.strip() for l in card_text.split("\n") if l.strip()]
    for line in lines:
        if re.search(r'\d+\s+\w+\s+(St|Ave|Rd|Dr|Blvd|Ln|Way|Ct|Pl|Hwy)', line):
            listing.address = line
            break

    # Website — click into the listing details to find website link
    # Instead of clicking (slow), look for website button nearby
    try:
        website_btn = parent.query_selector('a[data-value="Website"], a[aria-label*="Website"]')
        if website_btn:
            listing.website_url = website_btn.get_attribute("href") or ""
    except Exception:
        pass

    return listing


def _try_extract_websites(page, listings: list[MapsListing], delay: float) -> None:
    """For listings missing websites, click into each to find the website link."""
    for listing in listings:
        if listing.website_url:
            continue
        try:
            # Find and click the listing by name
            link = page.query_selector(f'a[aria-label="{listing.business_name}"]')
            if not link:
                continue
            link.click()
            time.sleep(delay)

            # Look for website link in the details panel
            website_el = page.query_selector(
                'a[data-item-id="authority"], a[aria-label*="Website"]'
            )
            if website_el:
                href = website_el.get_attribute("href") or ""
                if href and "google.com" not in href:
                    listing.website_url = href

            # Go back to results
            page.keyboard.press("Escape")
            time.sleep(1)
        except Exception as e:
            log.debug("Website extraction failed for %s: %s", listing.business_name, e)
