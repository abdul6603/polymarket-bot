"""Market Scanner — scan active Polymarket markets via Gamma API, exclude crypto Up/Down."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any

from hawk.config import HawkConfig
from bot.http_session import get_session

log = logging.getLogger(__name__)

# Keywords that indicate Garves's territory — exclude these
_UPDOWN_RE = re.compile(r"(bitcoin|ethereum|solana|btc|eth|sol)\s+(up or down)", re.IGNORECASE)

# Category keywords
_CATEGORY_KEYWORDS = {
    "politics": ["election", "president", "congress", "senate", "vote", "democrat", "republican",
                  "trump", "biden", "governor", "political", "party", "cabinet", "impeach",
                  "nominee", "nomination", "fed chair"],
    "sports": ["nba", "nfl", "mlb", "nhl", "soccer", "football", "basketball", "baseball",
               "hockey", "tennis", "ufc", "mma", "boxing", "super bowl", "world cup",
               "championship", "playoffs", "match", "game", "score", "olympics", "fifa",
               "ice hockey", "gold medal"],
    "crypto_event": ["bitcoin", "ethereum", "crypto", "btc", "eth", "sol", "token", "blockchain",
                     "defi", "nft", "halving", "etf", "sec", "price will"],
    "culture": ["oscar", "grammy", "emmy", "movie", "film", "music", "celebrity", "tiktok",
                "youtube", "twitch", "viral", "ai", "spacex", "nasa", "weather", "elon musk",
                "tweet"],
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
    event_title: str = ""
    market_slug: str = ""
    event_slug: str = ""


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


def scan_all_markets(cfg: HawkConfig) -> list[HawkMarket]:
    """Scan active Polymarket markets via Gamma API.

    Uses the Gamma API (gamma-api.polymarket.com) which returns properly
    sorted active events with volume data, unlike the CLOB /markets endpoint
    which returns stale data.
    """
    session = get_session()
    all_markets: list[HawkMarket] = []
    seen_ids: set[str] = set()

    # Gamma API: get active events sorted by volume
    offset = 0
    page_size = 50
    max_events = 200

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
                log.warning("Gamma API returned %d", resp.status_code)
                break

            events = resp.json()
            if not events:
                break

            for event in events:
                event_title = event.get("title", "")
                markets = event.get("markets", [])

                for m in markets:
                    cid = m.get("conditionId", m.get("condition_id", ""))
                    question = m.get("question", "")

                    if not cid or not question:
                        continue
                    if cid in seen_ids:
                        continue
                    seen_ids.add(cid)

                    # Skip Garves's territory
                    if _is_updown_price_market(question):
                        continue

                    # Skip closed/inactive
                    if m.get("closed") or not m.get("active", True):
                        continue
                    if m.get("acceptingOrders") is False:
                        continue

                    volume = float(m.get("volume", 0) or 0)
                    liquidity = float(m.get("liquidity", 0) or 0)

                    # Skip low-volume markets
                    if volume < cfg.min_volume:
                        continue

                    # Skip markets that resolve too far out
                    m_end_date = m.get("endDate", m.get("end_date_iso", ""))
                    if cfg.max_days > 0 and m_end_date:
                        try:
                            end_dt = datetime.fromisoformat(m_end_date.replace("Z", "+00:00"))
                            cutoff = datetime.now(timezone.utc) + timedelta(days=cfg.max_days)
                            if end_dt > cutoff:
                                continue
                        except (ValueError, TypeError):
                            continue  # Skip unparseable dates

                    # Build tokens list from Gamma format
                    # Gamma returns these as JSON-encoded strings, not arrays
                    tokens = []
                    raw_outcomes = m.get("outcomes", [])
                    raw_prices = m.get("outcomePrices", [])
                    raw_token_ids = m.get("clobTokenIds", [])

                    # Parse JSON strings if needed
                    if isinstance(raw_outcomes, str):
                        try:
                            import json
                            raw_outcomes = json.loads(raw_outcomes)
                        except (json.JSONDecodeError, TypeError):
                            raw_outcomes = []
                    if isinstance(raw_prices, str):
                        try:
                            import json
                            raw_prices = json.loads(raw_prices)
                        except (json.JSONDecodeError, TypeError):
                            raw_prices = []
                    if isinstance(raw_token_ids, str):
                        try:
                            import json
                            raw_token_ids = json.loads(raw_token_ids)
                        except (json.JSONDecodeError, TypeError):
                            raw_token_ids = []

                    if raw_outcomes and raw_prices:
                        for idx, outcome_name in enumerate(raw_outcomes):
                            tok = {
                                "outcome": outcome_name,
                                "price": raw_prices[idx] if idx < len(raw_prices) else "0.5",
                            }
                            if raw_token_ids and idx < len(raw_token_ids):
                                tok["token_id"] = raw_token_ids[idx]
                            tokens.append(tok)

                    market = HawkMarket(
                        condition_id=cid,
                        question=question,
                        category=_categorize_market(question),
                        volume=volume,
                        liquidity=liquidity,
                        tokens=tokens,
                        end_date=m.get("endDate", m.get("end_date_iso", "")),
                        accepting_orders=True,
                        event_title=event_title,
                        market_slug=m.get("slug", ""),
                        event_slug=event.get("slug", ""),
                    )
                    all_markets.append(market)

            offset += page_size

            # If we got fewer events than requested, we've hit the end
            if len(events) < page_size:
                break

        except Exception:
            log.exception("Failed to fetch Gamma events page offset=%d", offset)
            break

    log.info("Gamma scan: %d active markets with volume >= $%d (from %d events)",
             len(all_markets), cfg.min_volume, offset)

    return all_markets
