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
_UPDOWN_RE = re.compile(r"(bitcoin|ethereum|solana|btc|eth|sol|xrp)\s+(up or down)", re.IGNORECASE)

# Broader crypto price market filter — blocks ALL "price of X above/below/between $Y" markets
# These are markets where GPT-4o guesses with no real data (6/10 losses came from these)
_CRYPTO_PRICE_RE = re.compile(
    r"(price\s+of\s+)?(bitcoin|ethereum|solana|xrp|bnb|cardano|ada|dogecoin|doge|"
    r"avalanche|avax|polkadot|dot|polygon|matic|chainlink|link|litecoin|ltc|"
    r"btc|eth|sol|crypto)\s*"
    r"(be\s+)?(above|below|between|over|under|higher|lower|reach|hit|exceed|"
    r"break|surpass|fall|drop|rise|close)",
    re.IGNORECASE,
)
# Also catch "$X,XXX" price target patterns in crypto context
_CRYPTO_PRICE_TARGET_RE = re.compile(
    r"(bitcoin|ethereum|solana|xrp|bnb|btc|eth|sol|crypto).*\$[\d,]+",
    re.IGNORECASE,
)

# Sports detection patterns — regex for high-confidence sports identification
_SPORTS_RE = re.compile(
    r"(spread:\s|o/u\s?\d|over/under|moneyline|"
    r"\bvs\.?\b|"  # "vs" or "vs."
    r"(gators|cardinals|tar heels|wolverines|wildcats|bulldogs|hawks|eagles|"
    r"tigers|bears|lions|panthers|falcons|rams|cowboys|packers|chiefs|"
    r"warriors|lakers|celtics|nets|knicks|heat|bucks|suns|clippers|"
    r"cavaliers|mavericks|nuggets|76ers|grizzlies|pelicans|rockets|"
    r"yankees|dodgers|red sox|braves|astros|padres|mets|phillies|"
    r"ravens|steelers|bengals|browns|bills|dolphins|patriots|jets|"
    r"commanders|giants|saints|buccaneers|49ers|seahawks|chargers|raiders|"
    r"broncos|texans|colts|jaguars|titans|vikings|"
    r"revolutionaries|wolfpack|badgers|buckeyes|billikens|"
    r"redhawks|minutemen|flashes|hokies|hurricanes|mustangs|"
    r"red raiders|sun devils|boilermakers|hoosiers|crimson tide|"
    r"seminoles|blue devils|demon deacons|yellow jackets|"
    r"jayhawks|longhorns|sooners|cyclones|mountaineers|"
    r"razorbacks|volunteers|commodores|gamecocks|aggies|"
    r"ducks|beavers|huskies|cougars|bruins|trojans|"
    r"golden flashes|fighting irish|spartans|hawkeyes|"
    r"terrapins|nittany lions|scarlet knights|"
    r"esports|dota 2|counter-strike|league of legends|valorant))",
    re.IGNORECASE,
)

# Category keywords
_CATEGORY_KEYWORDS = {
    "politics": ["election", "president", "congress", "senate", "vote", "democrat", "republican",
                  "trump", "biden", "governor", "political", "party", "cabinet", "impeach",
                  "nominee", "nomination", "fed chair"],
    "sports": ["nba", "nfl", "mlb", "nhl", "soccer", "football", "basketball", "baseball",
               "hockey", "tennis", "ufc", "mma", "boxing", "super bowl", "world cup",
               "championship", "playoffs", "match", "game", "score", "olympics", "fifa",
               "ice hockey", "gold medal", "ncaa", "college basketball", "college football",
               "premier league", "la liga", "serie a", "bundesliga", "ligue 1",
               "formula 1", "f1", "grand prix", "pga", "lpga", "atp", "wta"],
    "crypto_event": ["bitcoin", "ethereum", "crypto", "btc", "eth", "sol", "token", "blockchain",
                     "defi", "nft", "halving", "etf", "sec", "price will"],
    "culture": ["oscar", "grammy", "emmy", "movie", "film", "music", "celebrity", "tiktok",
                "youtube", "twitch", "viral", "ai", "spacex", "nasa", "weather", "elon musk",
                "tweet", "openai", "google", "apple", "microsoft", "amazon", "meta",
                "earthquake", "hurricane", "war", "conflict", "ceasefire"],
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
    time_left_hours: float = 0.0


def _categorize_market(question: str) -> str:
    """Classify market into category. Sports regex checked FIRST for accuracy."""
    # Sports regex catches "vs.", "Spread:", "O/U", team mascots, esports
    if _SPORTS_RE.search(question):
        return "sports"
    q = question.lower()
    for cat, keywords in _CATEGORY_KEYWORDS.items():
        if any(kw in q for kw in keywords):
            return cat
    return "other"


def _is_updown_price_market(question: str) -> bool:
    """Return True if this is ANY crypto price prediction market.

    Blocks: Up/Down (Garves territory), price above/below/between $X,
    and crypto + price target patterns. GPT-4o has no real data for these.
    """
    if _UPDOWN_RE.search(question):
        return True
    if _CRYPTO_PRICE_RE.search(question):
        return True
    if _CRYPTO_PRICE_TARGET_RE.search(question):
        return True
    return False


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

                    # Skip ALL crypto price markets (Up/Down + price target + above/below)
                    if _is_updown_price_market(question):
                        log.debug("Blocked crypto price market: %s", question[:80])
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

                    # Skip low-liquidity markets (wide spreads, slippage risk)
                    if liquidity < cfg.min_liquidity:
                        continue

                    # Skip markets that resolve too far out OR too soon
                    m_end_date = m.get("endDate", m.get("end_date_iso", ""))
                    if m_end_date:
                        try:
                            end_dt = datetime.fromisoformat(m_end_date.replace("Z", "+00:00"))
                            time_left_h = (end_dt - datetime.now(timezone.utc)).total_seconds() / 3600
                            # Too soon — no time for edge to play out
                            if time_left_h < cfg.min_hours:
                                continue
                            # Too far out
                            if cfg.max_days > 0 and end_dt > datetime.now(timezone.utc) + timedelta(days=cfg.max_days):
                                continue
                        except (ValueError, TypeError):
                            continue  # Skip unparseable dates
                    else:
                        continue  # No end date = skip (can't assess timing)

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

                    # Compute time left in hours
                    time_left_h = 0.0
                    if m_end_date:
                        try:
                            end_dt = datetime.fromisoformat(m_end_date.replace("Z", "+00:00"))
                            time_left_h = max(0.0, (end_dt - datetime.now(timezone.utc)).total_seconds() / 3600)
                        except (ValueError, TypeError):
                            pass

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
                        time_left_hours=time_left_h,
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
