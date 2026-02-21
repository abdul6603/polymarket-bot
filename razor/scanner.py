"""Market Scanner — discover ALL active binary markets via Gamma API."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from razor.config import RazorConfig
from bot.http_session import get_session

log = logging.getLogger(__name__)


@dataclass
class RazorMarket:
    """A binary market with two outcomes and their token IDs."""
    condition_id: str
    question: str
    token_a_id: str
    token_b_id: str
    outcome_a: str
    outcome_b: str
    price_a: float
    price_b: float
    volume: float
    liquidity: float
    event_title: str = ""


def scan_all_markets(cfg: RazorConfig) -> list[RazorMarket]:
    """Scan ALL active binary markets via Gamma API. No category filtering.

    Returns markets with exactly 2 outcomes that have valid clobTokenIds
    and volume > $1000.
    """
    session = get_session()
    markets: list[RazorMarket] = []
    seen_ids: set[str] = set()

    offset = 0
    page_size = 50
    max_events = 500  # Scan deep — we want ALL markets

    while offset < max_events:
        try:
            resp = session.get(
                f"{cfg.gamma_host}/events",
                params={
                    "limit": page_size,
                    "offset": offset,
                    "active": "true",
                    "closed": "false",
                    "order": "volume24hr",
                    "ascending": "false",
                },
                timeout=15,
            )
            if resp.status_code != 200:
                log.warning("Gamma API returned %d at offset %d", resp.status_code, offset)
                break

            events = resp.json()
            if not events:
                break

            for event in events:
                event_title = event.get("title", "")
                for m in event.get("markets", []):
                    cid = m.get("conditionId", m.get("condition_id", ""))
                    question = m.get("question", "")

                    if not cid or not question:
                        continue
                    if cid in seen_ids:
                        continue
                    seen_ids.add(cid)

                    # Must be active and accepting orders
                    if m.get("closed") or not m.get("active", True):
                        continue
                    if m.get("acceptingOrders") is False:
                        continue

                    # Parse outcomes, prices, token IDs (Gamma returns JSON strings)
                    raw_outcomes = m.get("outcomes", [])
                    raw_prices = m.get("outcomePrices", [])
                    raw_token_ids = m.get("clobTokenIds", [])

                    if isinstance(raw_outcomes, str):
                        try:
                            raw_outcomes = json.loads(raw_outcomes)
                        except (json.JSONDecodeError, TypeError):
                            raw_outcomes = []
                    if isinstance(raw_prices, str):
                        try:
                            raw_prices = json.loads(raw_prices)
                        except (json.JSONDecodeError, TypeError):
                            raw_prices = []
                    if isinstance(raw_token_ids, str):
                        try:
                            raw_token_ids = json.loads(raw_token_ids)
                        except (json.JSONDecodeError, TypeError):
                            raw_token_ids = []

                    # Must be binary (exactly 2 outcomes) with token IDs
                    if len(raw_outcomes) != 2 or len(raw_token_ids) != 2:
                        continue
                    if not raw_token_ids[0] or not raw_token_ids[1]:
                        continue

                    # Volume filter — skip illiquid markets
                    volume = float(m.get("volume", 0) or 0)
                    if volume < 1000:
                        continue

                    liquidity = float(m.get("liquidity", 0) or 0)

                    # Parse prices
                    try:
                        price_a = float(raw_prices[0]) if len(raw_prices) > 0 else 0.5
                        price_b = float(raw_prices[1]) if len(raw_prices) > 1 else 0.5
                    except (ValueError, TypeError):
                        price_a, price_b = 0.5, 0.5

                    markets.append(RazorMarket(
                        condition_id=cid,
                        question=question,
                        token_a_id=raw_token_ids[0],
                        token_b_id=raw_token_ids[1],
                        outcome_a=raw_outcomes[0],
                        outcome_b=raw_outcomes[1],
                        price_a=price_a,
                        price_b=price_b,
                        volume=volume,
                        liquidity=liquidity,
                        event_title=event_title,
                    ))

            offset += page_size
            if len(events) < page_size:
                break

        except Exception:
            log.exception("Gamma fetch failed at offset=%d", offset)
            break

    log.info("Razor scan: %d binary markets discovered (from %d event pages)",
             len(markets), offset // page_size)
    return markets
