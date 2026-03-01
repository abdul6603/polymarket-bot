"""Market Scanner — fetch ALL active events from Gamma API and group by event slug."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from arbiter.config import ArbiterConfig
from bot.http_session import get_session

log = logging.getLogger(__name__)


@dataclass
class BracketMarket:
    condition_id: str
    question: str
    group_label: str          # "<58,000", "58-62K", "78-79F"
    yes_price: float
    no_price: float
    yes_token_id: str
    no_token_id: str
    volume: float
    liquidity: float
    end_date: str


@dataclass
class EventGroup:
    event_slug: str
    event_title: str
    markets: list[BracketMarket] = field(default_factory=list)
    total_yes_sum: float = 0.0
    deviation_pct: float = 0.0

    def compute_deviation(self) -> None:
        """Compute total YES sum and deviation from 1.00."""
        self.total_yes_sum = sum(m.yes_price for m in self.markets)
        self.deviation_pct = abs(self.total_yes_sum - 1.0) * 100


def _parse_json_field(raw) -> list:
    """Parse a field that may be a JSON string or already a list."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
    return []


def scan_all_events(cfg: ArbiterConfig) -> list[EventGroup]:
    """Scan active Polymarket events via Gamma API, group markets by event.

    Returns EventGroups where each group has 3+ bracket markets.
    """
    session = get_session()
    groups: dict[str, EventGroup] = {}

    offset = 0
    page_size = 50
    max_events = 500

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
                event_slug = event.get("slug", "")
                event_title = event.get("title", "")
                raw_markets = event.get("markets", [])

                if not event_slug or len(raw_markets) < 3:
                    continue

                group = groups.get(event_slug)
                if not group:
                    group = EventGroup(
                        event_slug=event_slug,
                        event_title=event_title,
                    )
                    groups[event_slug] = group

                for m in raw_markets:
                    cid = m.get("conditionId", m.get("condition_id", ""))
                    question = m.get("question", "")
                    if not cid or not question:
                        continue

                    if m.get("closed") or not m.get("active", True):
                        continue
                    if m.get("acceptingOrders") is False:
                        continue

                    volume = float(m.get("volume", 0) or 0)
                    liquidity = float(m.get("liquidity", 0) or 0)

                    if volume < cfg.min_volume:
                        continue

                    raw_outcomes = _parse_json_field(m.get("outcomes", []))
                    raw_prices = _parse_json_field(m.get("outcomePrices", []))
                    raw_token_ids = _parse_json_field(m.get("clobTokenIds", []))

                    if len(raw_outcomes) < 2 or len(raw_prices) < 2:
                        continue

                    try:
                        yes_price = float(raw_prices[0])
                        no_price = float(raw_prices[1])
                    except (ValueError, TypeError, IndexError):
                        continue

                    yes_token = raw_token_ids[0] if len(raw_token_ids) > 0 else ""
                    no_token = raw_token_ids[1] if len(raw_token_ids) > 1 else ""

                    # Group label: use groupItemTitle or question prefix
                    group_label = m.get("groupItemTitle", "") or question[:50]

                    bracket = BracketMarket(
                        condition_id=cid,
                        question=question,
                        group_label=group_label,
                        yes_price=yes_price,
                        no_price=no_price,
                        yes_token_id=yes_token,
                        no_token_id=no_token,
                        volume=volume,
                        liquidity=liquidity,
                        end_date=m.get("endDate", m.get("end_date_iso", "")),
                    )
                    group.markets.append(bracket)

            offset += page_size
            if len(events) < page_size:
                break

        except Exception:
            log.exception("Failed to fetch Gamma events page offset=%d", offset)
            break

    # Filter: only keep groups with 3+ markets (bracket-style)
    result = []
    for group in groups.values():
        if len(group.markets) >= 3:
            group.compute_deviation()
            result.append(group)

    log.info("Arbiter scan: %d bracket groups from %d events (offset=%d)",
             len(result), len(groups), offset)
    return result
