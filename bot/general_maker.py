"""General Market Maker Scanner — finds sports + politics markets with maker-friendly order books.

Scans Polymarket via Gamma API, filters for two-sided books with tight spreads,
and returns markets in the format MakerEngine expects.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bot.http_session import get_session

log = logging.getLogger("general_maker")

DATA_DIR = Path(__file__).parent.parent / "data"

# ── Category classification ──────────────────────────────────────────────

_UPDOWN_RE = re.compile(
    r"(bitcoin|ethereum|solana|btc|eth|sol|xrp)\s+(up or down)", re.IGNORECASE
)
_CRYPTO_PRICE_RE = re.compile(
    r"(price\s+of\s+)?(bitcoin|ethereum|solana|xrp|bnb|cardano|ada|dogecoin|doge|"
    r"avalanche|avax|polkadot|dot|polygon|matic|chainlink|link|litecoin|ltc|"
    r"btc|eth|sol|crypto)\s*"
    r"(be\s+)?(above|below|between|over|under|higher|lower|reach|hit|exceed|"
    r"break|surpass|fall|drop|rise|close)",
    re.IGNORECASE,
)
_ESPORTS_RE = re.compile(
    r"(esports|dota\s*2|counter-?strike|league of legends|valorant|"
    r"overwatch|csgo|cs2|LoL|PARIVISION|MOUZ|TheMongolz|Fnatic|"
    r"Team Vitality|G2 Esports|Cloud9|T1|Gen\.G|NaVi|FaZe Clan|"
    r"\bBO[135]\b|Map \d Winner)",
    re.IGNORECASE,
)
_SPORTS_RE = re.compile(
    r"(spread:\s|o/u\s?\d|over/under|moneyline|total points|total goals|"
    r"\bvs\.?\b|"
    r"nba|nfl|mlb|nhl|ncaa|ufc|mma|boxing|pga|atp|wta|"
    r"premier league|la liga|serie a|bundesliga|champions league|"
    r"super bowl|world cup|playoffs|championship|"
    r"lakers|celtics|warriors|nuggets|cavaliers|knicks|heat|bucks|"
    r"76ers|nets|bulls|pistons|rockets|suns|mavericks|clippers|"
    r"chiefs|eagles|cowboys|49ers|bills|ravens|bengals|dolphins|"
    r"yankees|dodgers|mets|braves|astros|padres|phillies|cubs|"
    r"bruins|rangers|oilers|panthers|avalanche|lightning|"
    r"badgers|wildcats|bulldogs|wolverines|buckeyes|tigers|gators|"
    r"cardinals|seahawks|packers|saints|chargers|broncos|rams|"
    r"trail blazers|blazers|timberwolves|pelicans|hawks|magic|"
    r"raptors|spurs|grizzlies|kings|hornets|wizards|thunder|jazz)",
    re.IGNORECASE,
)

_CATEGORY_KEYWORDS = {
    "politics": [
        "election", "president", "congress", "senate", "vote", "democrat",
        "republican", "trump", "biden", "governor", "political", "party",
        "cabinet", "impeach", "nominee", "nomination", "fed chair",
        "approval rating", "executive order", "tariff", "supreme court",
    ],
    "sports": [
        "nba", "nfl", "mlb", "nhl", "soccer", "football", "basketball",
        "baseball", "hockey", "tennis", "ufc", "mma", "boxing", "super bowl",
        "world cup", "championship", "playoffs", "match", "game", "score",
        "olympics", "formula 1", "f1", "grand prix", "pga",
    ],
}

ALLOWED_CATEGORIES = {"sports", "politics", "crypto"}


def _categorize(question: str) -> str:
    if _SPORTS_RE.search(question):
        return "sports"
    q = question.lower()
    for cat, keywords in _CATEGORY_KEYWORDS.items():
        if any(kw in q for kw in keywords):
            return cat
    return "other"


def _is_crypto(question: str) -> bool:
    if _UPDOWN_RE.search(question):
        return True
    if _CRYPTO_PRICE_RE.search(question):
        return True
    return False


# ── Data structures ──────────────────────────────────────────────────────

@dataclass
class MakerMarket:
    """A market suitable for market making."""
    condition_id: str
    question: str
    category: str
    tokens: list[dict[str, Any]]  # [{"token_id": str, "outcome": str, "price": float}]
    volume_24h: float
    liquidity: float
    spread: float          # best_ask - best_bid on the YES token
    mid_price: float       # (best_bid + best_ask) / 2 on YES token
    best_bid: float
    best_ask: float
    end_date: str
    remaining_s: float
    event_title: str = ""
    market_slug: str = ""


# ── Scanner ──────────────────────────────────────────────────────────────

def scan_maker_markets(
    gamma_host: str = "https://gamma-api.polymarket.com",
    clob_host: str = "https://clob.polymarket.com",
    min_volume: float = 1000.0,
    min_liquidity: float = 500.0,
    min_spread: float = 0.005,
    max_spread: float = 0.15,
    min_price: float = 0.12,
    max_price: float = 0.88,
    min_hours: float = 2.0,
    max_days: float = 14.0,
    max_markets: int = 20,
) -> list[MakerMarket]:
    """Scan Polymarket for sports + politics markets with maker-friendly books.

    Returns up to max_markets sorted by spread opportunity (tightest spreads first).
    """
    session = get_session()
    candidates: list[MakerMarket] = []
    seen_ids: set[str] = set()
    now = datetime.now(timezone.utc)

    # Fetch active events sorted by 24h volume (highest first)
    offset = 0
    page_size = 50
    max_scan = 300  # Don't over-scan — top 300 events by volume is plenty

    while offset < max_scan:
        try:
            resp = session.get(
                f"{gamma_host}/events",
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
                break
            events = resp.json()
            if not events:
                break
        except Exception as e:
            log.warning("[GMAKER] Gamma API error: %s", str(e)[:100])
            break

        for event in events:
            event_title = event.get("title", "")
            markets = event.get("markets", [])

            for m in markets:
                cid = m.get("conditionId", m.get("condition_id", ""))
                question = m.get("question", "")
                if not cid or not question or cid in seen_ids:
                    continue
                seen_ids.add(cid)

                # Category filter
                if _ESPORTS_RE.search(question):
                    continue
                is_crypto_mkt = _is_crypto(question)
                if is_crypto_mkt:
                    cat = "crypto"
                else:
                    cat = _categorize(question)
                    if cat not in ALLOWED_CATEGORIES:
                        continue

                # Volume + liquidity filter
                vol = float(m.get("volume", 0) or 0)
                liq = float(m.get("liquidity", 0) or 0)
                if vol < min_volume or liq < min_liquidity:
                    continue

                # Active + accepting orders
                if not m.get("active", True) or m.get("closed", False):
                    continue
                if not m.get("acceptingOrders", m.get("accepting_orders", True)):
                    continue

                # Time to resolution
                end_str = m.get("endDate", m.get("end_date_iso", ""))
                if not end_str:
                    continue
                try:
                    end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                    remaining = (end_dt - now).total_seconds()
                except (ValueError, TypeError):
                    continue
                # Crypto Up/Down have short TTR (5min-24h); general need 2h+
                min_remaining_s = 180 if is_crypto_mkt else min_hours * 3600
                if remaining < min_remaining_s or remaining > max_days * 86400:
                    continue

                # Parse tokens
                raw_outcomes = m.get("outcomes", [])
                raw_prices = m.get("outcomePrices", [])
                raw_tokens = m.get("clobTokenIds", [])
                if isinstance(raw_outcomes, str):
                    try:
                        raw_outcomes = json.loads(raw_outcomes)
                    except (json.JSONDecodeError, TypeError):
                        continue
                if isinstance(raw_prices, str):
                    try:
                        raw_prices = json.loads(raw_prices)
                    except (json.JSONDecodeError, TypeError):
                        continue
                if isinstance(raw_tokens, str):
                    try:
                        raw_tokens = json.loads(raw_tokens)
                    except (json.JSONDecodeError, TypeError):
                        continue

                if len(raw_outcomes) < 2 or len(raw_tokens) < 2:
                    continue

                # Build token list
                tokens = []
                for i, outcome in enumerate(raw_outcomes):
                    price = float(raw_prices[i]) if i < len(raw_prices) else 0.5
                    tid = raw_tokens[i] if i < len(raw_tokens) else ""
                    if not tid:
                        continue
                    tokens.append({
                        "token_id": tid,
                        "outcome": outcome,
                        "price": price,
                    })

                if len(tokens) < 2:
                    continue

                # Price range filter (YES token)
                yes_price = tokens[0].get("price", 0.5)
                if yes_price < min_price or yes_price > max_price:
                    continue

                candidates.append(MakerMarket(
                    condition_id=cid,
                    question=question,
                    category=cat,
                    tokens=tokens,
                    volume_24h=vol,
                    liquidity=liq,
                    spread=0.0,       # will be filled by book check
                    mid_price=yes_price,
                    best_bid=0.0,
                    best_ask=0.0,
                    end_date=end_str,
                    remaining_s=remaining,
                    event_title=event_title,
                    market_slug=m.get("slug", ""),
                ))

        if len(events) < page_size:
            break
        offset += page_size

    log.info("[GMAKER] Gamma scan: %d candidates from %d events checked", len(candidates), len(seen_ids))

    # Phase 2: Fetch order books and filter by spread
    maker_ready: list[MakerMarket] = []

    for mkt in candidates[:60]:  # Check books for top 60 candidates max (rate limit friendly)
        yes_token = mkt.tokens[0]["token_id"]
        try:
            resp = session.get(
                f"{clob_host}/book",
                params={"token_id": yes_token},
                timeout=8,
            )
            if resp.status_code != 200:
                continue
            book = resp.json()
        except Exception:
            continue

        bids = book.get("bids", [])
        asks = book.get("asks", [])
        if not bids or not asks:
            continue

        # CLOB sorts bids ascending, asks descending — best prices are LAST
        best_bid = float(bids[-1].get("price", 0))
        best_ask = float(asks[-1].get("price", 0))

        if best_bid <= 0 or best_ask <= 0 or best_ask <= best_bid:
            continue

        spread = best_ask - best_bid
        mid = (best_bid + best_ask) / 2.0

        if spread < min_spread or spread > max_spread:
            continue

        # Update with real book data
        mkt.spread = spread
        mkt.mid_price = round(mid, 4)
        mkt.best_bid = best_bid
        mkt.best_ask = best_ask

        maker_ready.append(mkt)

        if len(maker_ready) >= max_markets:
            break

        # Small delay to avoid rate limits
        time.sleep(0.1)

    # Sort by spread (tightest first — most liquid, easiest to fill)
    maker_ready.sort(key=lambda m: m.spread)

    log.info(
        "[GMAKER] Book check: %d maker-ready (crypto=%d, sports=%d, politics=%d)",
        len(maker_ready),
        sum(1 for m in maker_ready if m.category == "crypto"),
        sum(1 for m in maker_ready if m.category == "sports"),
        sum(1 for m in maker_ready if m.category == "politics"),
    )

    return maker_ready


def markets_for_engine(maker_markets: list[MakerMarket]) -> list[dict]:
    """Convert MakerMarket list to the format MakerEngine.tick() expects."""
    result = []
    for mkt in maker_markets:
        result.append({
            "market_id": mkt.condition_id,
            "tokens": mkt.tokens,
            "asset": _short_label(mkt.question),
            "remaining_s": mkt.remaining_s,
            "timeframe": "general",
            "mid_price": mkt.mid_price,
            "book_spread": mkt.spread,
            "category": mkt.category,
        })
    return result


def _short_label(question: str) -> str:
    """Shorten question to a readable label for logs/dashboard."""
    q = question.strip()
    if len(q) <= 35:
        return q
    return q[:32] + "..."


def save_scan_results(markets: list[MakerMarket]) -> None:
    """Save scan results to disk for dashboard."""
    data = {
        "timestamp": time.time(),
        "count": len(markets),
        "markets": [],
    }
    for m in markets:
        data["markets"].append({
            "condition_id": m.condition_id,
            "question": m.question[:80],
            "category": m.category,
            "spread": round(m.spread, 4),
            "mid_price": round(m.mid_price, 4),
            "best_bid": round(m.best_bid, 4),
            "best_ask": round(m.best_ask, 4),
            "volume_24h": round(m.volume_24h, 0),
            "liquidity": round(m.liquidity, 0),
            "remaining_h": round(m.remaining_s / 3600, 1),
            "event_title": m.event_title[:60],
        })
    try:
        (DATA_DIR / "general_maker_scan.json").write_text(json.dumps(data, indent=2))
    except Exception as e:
        log.warning("[GMAKER] Failed to save scan: %s", str(e)[:100])
