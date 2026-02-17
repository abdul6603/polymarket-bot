"""Market Scanner — crawl ALL Polymarket markets, exclude crypto Up/Down."""
from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from hawk.config import HawkConfig
from bot.http_session import get_session

log = logging.getLogger(__name__)

# Keywords that indicate Garves's territory — exclude these
_UPDOWN_RE = re.compile(r"(bitcoin|ethereum|solana|btc|eth|sol)\s+(up or down)", re.IGNORECASE)

# Category keywords
_CATEGORY_KEYWORDS = {
    "politics": ["election", "president", "congress", "senate", "vote", "democrat", "republican",
                  "trump", "biden", "governor", "political", "party", "cabinet", "impeach"],
    "sports": ["nba", "nfl", "mlb", "nhl", "soccer", "football", "basketball", "baseball",
               "hockey", "tennis", "ufc", "mma", "boxing", "super bowl", "world cup",
               "championship", "playoffs", "match", "game", "score"],
    "crypto_event": ["bitcoin", "ethereum", "crypto", "btc", "eth", "sol", "token", "blockchain",
                     "defi", "nft", "halving", "etf", "sec"],
    "culture": ["oscar", "grammy", "emmy", "movie", "film", "music", "celebrity", "tiktok",
                "youtube", "twitch", "viral", "ai", "spacex", "nasa", "weather"],
}


@dataclass
class HawkMarket:
    condition_id: str
    question: str
    category: str
    volume: float
    liquidity: float
    tokens: list[dict[str, Any]] = field(default_factory=list)
    end_date: str = ""
    accepting_orders: bool = True


def _categorize_market(question: str) -> str:
    """Keyword-classify a market question into a category."""
    q = question.lower()
    for cat, keywords in _CATEGORY_KEYWORDS.items():
        if any(kw in q for kw in keywords):
            return cat
    return "other"


def _is_updown_price_market(question: str) -> bool:
    """Return True if this is a crypto Up/Down price market (Garves's territory)."""
    return bool(_UPDOWN_RE.search(question))


def _filter_markets(markets: list[HawkMarket], cfg: HawkConfig) -> list[HawkMarket]:
    """Filter by volume, accepting_orders, not closed."""
    return [
        m for m in markets
        if m.volume >= cfg.min_volume
        and m.accepting_orders
    ]


def scan_all_markets(cfg: HawkConfig) -> list[HawkMarket]:
    """Crawl CLOB API with cursor pagination. Exclude crypto Up/Down markets."""
    session = get_session()
    all_markets: list[HawkMarket] = []
    seen_ids: set[str] = set()

    cursor = ""
    pages = 0
    max_pages = 50

    while pages < max_pages:
        params: dict[str, Any] = {"limit": 100}
        if cursor:
            params["next_cursor"] = cursor

        try:
            resp = session.get(
                f"{cfg.clob_host}/markets",
                params=params,
                timeout=15,
            )
            if resp.status_code != 200:
                log.warning("CLOB API returned %d", resp.status_code)
                break

            data = resp.json()
        except Exception:
            log.exception("Failed to fetch markets page %d", pages)
            break

        markets_data = data.get("data", [])
        if not markets_data:
            break

        for m in markets_data:
            cid = m.get("condition_id", "")
            question = m.get("question", "")

            if cid in seen_ids:
                continue
            seen_ids.add(cid)

            # Skip Garves's territory
            if _is_updown_price_market(question):
                continue

            # Skip closed/inactive markets
            if not m.get("accepting_orders") or m.get("closed"):
                continue
            if not m.get("active", True):
                continue

            volume = float(m.get("volume", 0) or 0)
            liquidity = float(m.get("liquidity", 0) or 0)

            market = HawkMarket(
                condition_id=cid,
                question=question,
                category=_categorize_market(question),
                volume=volume,
                liquidity=liquidity,
                tokens=m.get("tokens", []),
                end_date=m.get("end_date_iso", ""),
                accepting_orders=True,
            )
            all_markets.append(market)

        cursor = data.get("next_cursor", "")
        if not cursor or cursor == "LTE=":
            break
        pages += 1

    log.info("Scanned %d pages, found %d non-crypto markets", pages + 1, len(all_markets))

    # Filter by volume/liquidity
    filtered = _filter_markets(all_markets, cfg)
    log.info("After filtering: %d markets (min_volume=%d)", len(filtered), cfg.min_volume)

    return filtered
