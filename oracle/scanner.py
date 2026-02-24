"""Weekly crypto market discovery for Oracle.

Finds all active weekly markets on Polymarket across 3 types:
  - Above/Below:  "Will BTC be above $68,000 on February 28?"
  - Price Range:  "Will BTC be between $66,000 and $68,000 on February 28?"
  - Hit Price:    "Will BTC reach $74,000 February 23-28?"

Slug patterns (Gamma API):
  - above:  {asset}-above-on-{month}-{day}
  - range:  {asset}-price-on-{month}-{day}
  - hit:    what-price-will-{asset}-hit-{month}-{start}-{end}
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from oracle.config import OracleConfig

log = logging.getLogger(__name__)

# Market type constants
TYPE_ABOVE = "above_below"
TYPE_RANGE = "price_range"
TYPE_HIT = "hit_price"


@dataclass
class WeeklyMarket:
    """A single tradeable weekly market."""
    condition_id: str
    question: str
    asset: str               # "bitcoin", "ethereum", "solana", "xrp"
    market_type: str         # TYPE_ABOVE, TYPE_RANGE, TYPE_HIT
    event_slug: str
    event_title: str
    threshold: float | None  # price level (e.g. 68000.0) or None for ranges
    range_low: float | None  # for price range markets
    range_high: float | None
    yes_price: float         # current market YES price (probability)
    no_price: float
    volume: float
    end_date: str            # ISO date string
    active: bool
    tokens: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


def _parse_threshold(question: str) -> float | None:
    """Extract dollar threshold from question text."""
    m = re.search(r"\$([0-9,]+(?:\.\d+)?)", question)
    if m:
        return float(m.group(1).replace(",", ""))
    return None


def _parse_range(question: str) -> tuple[float | None, float | None]:
    """Extract price range from 'between $X and $Y' questions."""
    m = re.search(r"between\s+\$([0-9,]+)\s+and\s+\$([0-9,]+)", question, re.IGNORECASE)
    if m:
        return float(m.group(1).replace(",", "")), float(m.group(2).replace(",", ""))
    # "less than $X"
    m = re.search(r"less than\s+\$([0-9,]+)", question, re.IGNORECASE)
    if m:
        return 0.0, float(m.group(1).replace(",", ""))
    # "greater than $X"
    m = re.search(r"greater than\s+\$([0-9,]+)", question, re.IGNORECASE)
    if m:
        return float(m.group(1).replace(",", "")), float("inf")
    return None, None


def _parse_prices(market: dict) -> tuple[float, float]:
    """Extract YES/NO prices from Gamma market data."""
    raw = market.get("outcomePrices", "[]")
    if isinstance(raw, str):
        import json
        try:
            prices = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return 0.5, 0.5
    else:
        prices = raw
    if len(prices) >= 2:
        return float(prices[0]), float(prices[1])
    return 0.5, 0.5


def _week_dates() -> list[dict[str, Any]]:
    """Generate date parameters for current and next week markets.

    Returns list of dicts with keys: month, day, start_day, end_day, label.
    """
    now = datetime.now(timezone.utc)
    today = now.date()
    results = []

    # Find next Saturday (weekly resolution day) — scan forward up to 14 days
    for offset in range(0, 15):
        candidate = today + timedelta(days=offset)
        if candidate.weekday() == 5:  # Saturday
            month = candidate.strftime("%B").lower()
            day = candidate.day
            # Week range: previous Sunday to this Saturday
            week_start = candidate - timedelta(days=6)
            results.append({
                "month": month,
                "day": day,
                "start_day": week_start.day,
                "end_day": day,
                "label": f"{month.title()} {week_start.day}-{day}",
                "end_date": candidate.isoformat(),
            })
            if len(results) >= 2:
                break

    return results


def scan_weekly_markets(cfg: OracleConfig) -> list[WeeklyMarket]:
    """Discover all active weekly crypto markets from Polymarket Gamma API."""
    markets: list[WeeklyMarket] = []
    seen_cids: set[str] = set()
    session = requests.Session()

    weeks = _week_dates()
    log.info("Scanning weeks: %s", [w["label"] for w in weeks])

    for week in weeks:
        month = week["month"]
        day = week["day"]
        start_day = week["start_day"]
        end_day = week["end_day"]
        end_date = week["end_date"]

        for asset in cfg.assets:
            # --- Type 1: Above/Below ---
            slug = f"{asset}-above-on-{month}-{day}"
            _fetch_event_markets(
                session, cfg, slug, asset, TYPE_ABOVE, end_date, markets, seen_cids,
            )

            # --- Type 2: Price Range ---
            slug = f"{asset}-price-on-{month}-{day}"
            _fetch_event_markets(
                session, cfg, slug, asset, TYPE_RANGE, end_date, markets, seen_cids,
            )

            # --- Type 3: Hit Price ---
            slug = f"what-price-will-{asset}-hit-{month}-{start_day}-{end_day}"
            _fetch_event_markets(
                session, cfg, slug, asset, TYPE_HIT, end_date, markets, seen_cids,
            )

    # Log summary
    by_type: dict[str, int] = {}
    for m in markets:
        key = f"{m.asset}/{m.market_type}"
        by_type[key] = by_type.get(key, 0) + 1
    summary = ", ".join(f"{k}: {v}" for k, v in sorted(by_type.items()))
    log.info("Weekly markets found — %s (total: %d)", summary or "none", len(markets))

    return markets


def _fetch_event_markets(
    session: requests.Session,
    cfg: OracleConfig,
    slug: str,
    asset: str,
    market_type: str,
    end_date: str,
    out: list[WeeklyMarket],
    seen: set[str],
) -> None:
    """Fetch markets for a single event slug and append to output list."""
    try:
        resp = session.get(
            f"{cfg.gamma_host}/events",
            params={"slug": slug},
            timeout=8,
        )
        if resp.status_code != 200:
            return
        events = resp.json()
        if not events:
            return
    except Exception:
        return

    ev = events[0]
    event_title = ev.get("title", "")

    for m in ev.get("markets", []):
        cid = m.get("conditionId", "")
        if not cid or cid in seen:
            continue
        if m.get("closed"):
            continue

        question = m.get("question", "")
        yes_price, no_price = _parse_prices(m)
        volume = float(m.get("volume", 0) or 0)

        # Parse threshold / range based on market type
        threshold = None
        range_low = None
        range_high = None
        if market_type == TYPE_ABOVE:
            threshold = _parse_threshold(question)
        elif market_type == TYPE_RANGE:
            range_low, range_high = _parse_range(question)
        elif market_type == TYPE_HIT:
            threshold = _parse_threshold(question)

        # Get CLOB tokens for execution
        tokens = []
        try:
            clob_resp = session.get(f"{cfg.clob_host}/markets/{cid}", timeout=5)
            if clob_resp.status_code == 200:
                clob_data = clob_resp.json()
                tokens = clob_data.get("tokens", [])
                if not clob_data.get("active"):
                    continue
        except Exception:
            pass

        seen.add(cid)
        out.append(WeeklyMarket(
            condition_id=cid,
            question=question,
            asset=asset,
            market_type=market_type,
            event_slug=slug,
            event_title=event_title,
            threshold=threshold,
            range_low=range_low,
            range_high=range_high,
            yes_price=yes_price,
            no_price=no_price,
            volume=volume,
            end_date=end_date,
            active=True,
            tokens=tokens,
            raw=m,
        ))


# ── Kalshi Crypto Market Scanner (V9) ──

# Kalshi crypto event ticker prefixes
_KALSHI_CRYPTO_PREFIXES = {"KXBTC", "KXETH", "KXSOL", "KXDOGE", "KXADA", "KXBNB", "KXLINK"}

# Map Kalshi crypto asset names to Oracle asset names
_KALSHI_ASSET_MAP = {
    "KXBTC": "bitcoin",
    "KXETH": "ethereum",
    "KXSOL": "solana",
    "KXDOGE": "dogecoin",
    "KXADA": "cardano",
    "KXBNB": "bnb",
    "KXLINK": "chainlink",
}


def scan_kalshi_crypto_markets(cfg: OracleConfig) -> list[WeeklyMarket]:
    """Scan Kalshi for crypto weekly markets alongside Polymarket.

    Kalshi crypto patterns:
    - "Will Bitcoin be above $X on [date]?"
    - "BTC price range [date]"
    - Event tickers like: KXBTC-*, KXETH-*

    Uses unauthenticated read-only endpoint (same as hawk/kalshi.py).
    """
    if not cfg.kalshi_enabled:
        return []

    try:
        from hawk.kalshi import _fetch_all_markets, _get_kalshi_price
    except ImportError:
        log.warning("[KALSHI] hawk.kalshi not available for Oracle scanning")
        return []

    all_markets = _fetch_all_markets()
    if not all_markets:
        return []

    results: list[WeeklyMarket] = []
    seen_tickers: set[str] = set()
    now = datetime.now(timezone.utc)

    for m in all_markets:
        ticker = m.get("ticker", "")
        status = m.get("status", "")

        if status != "open":
            continue
        if ticker in seen_tickers:
            continue

        # Only crypto markets — check ticker prefix
        ticker_prefix = ticker.split("-")[0] if "-" in ticker else ticker
        if ticker_prefix not in _KALSHI_CRYPTO_PREFIXES:
            continue

        # Only assets Oracle tracks
        asset = _KALSHI_ASSET_MAP.get(ticker_prefix)
        if not asset or asset not in cfg.assets:
            continue

        seen_tickers.add(ticker)

        title = m.get("title", "")
        subtitle = m.get("subtitle", "")
        question = f"{title} {subtitle}".strip() if subtitle else title

        # Time filter: must resolve within 14 days
        end_date_str = m.get("close_time") or m.get("expiration_time", "")
        if not end_date_str:
            continue
        try:
            end_dt = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
            if end_dt < now:
                continue
            if end_dt > now + timedelta(days=14):
                continue
        except (ValueError, TypeError):
            continue

        # Price normalization: cents → 0.00-1.00
        yes_price = _get_kalshi_price(m)
        if yes_price is None:
            continue
        no_price = round(1.0 - yes_price, 4)

        # Determine market type from question text
        q_lower = question.lower()
        if "above" in q_lower or "below" in q_lower:
            market_type = TYPE_ABOVE
        elif "between" in q_lower or "range" in q_lower:
            market_type = TYPE_RANGE
        elif "hit" in q_lower or "reach" in q_lower:
            market_type = TYPE_HIT
        else:
            market_type = TYPE_ABOVE  # default

        # Parse thresholds
        threshold = _parse_threshold(question)
        range_low, range_high = None, None
        if market_type == TYPE_RANGE:
            range_low, range_high = _parse_range(question)

        volume = float(m.get("volume", 0) or 0)

        # Build synthetic tokens for execution routing
        tokens = [
            {"outcome": "Yes", "price": str(yes_price), "token_id": f"kalshi_{ticker}_yes"},
            {"outcome": "No", "price": str(no_price), "token_id": f"kalshi_{ticker}_no"},
        ]

        results.append(WeeklyMarket(
            condition_id=f"kalshi_{ticker}",
            question=question,
            asset=asset,
            market_type=market_type,
            event_slug=m.get("event_ticker", ""),
            event_title=m.get("event_ticker", ""),
            threshold=threshold,
            range_low=range_low,
            range_high=range_high,
            yes_price=yes_price,
            no_price=no_price,
            volume=volume,
            end_date=end_date_str,
            active=True,
            tokens=tokens,
            raw=m,
        ))

    log.info("[KALSHI] Found %d crypto markets for Oracle (%d raw Kalshi markets)",
             len(results), len(all_markets))
    return results


def filter_tradeable(markets: list[WeeklyMarket], min_edge: float = 0.08) -> list[WeeklyMarket]:
    """Filter out markets that are too obvious (>95% or <5%) to trade."""
    tradeable = []
    for m in markets:
        # Skip extreme markets — no edge when market is 99% or 1%
        if m.yes_price > 0.95 or m.yes_price < 0.05:
            continue
        tradeable.append(m)
    return tradeable
