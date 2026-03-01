"""Analyzer — detect cross-market inconsistencies (sum-to-one, monotonic, complement)."""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field

from arbiter.config import ArbiterConfig
from arbiter.scanner import BracketMarket, EventGroup

log = logging.getLogger(__name__)


@dataclass
class ArbLeg:
    condition_id: str
    token_id: str             # Which token to buy/sell
    side: str                 # "BUY" or "SELL"
    price: float
    size_usd: float = 0.0


@dataclass
class ArbOpportunity:
    event_slug: str
    event_title: str
    arb_type: str             # "sum_under", "sum_over", "monotonic", "complement"
    legs: list[ArbLeg] = field(default_factory=list)
    total_cost: float = 0.0
    guaranteed_payout: float = 1.0
    expected_profit_pct: float = 0.0
    deviation_pct: float = 0.0
    timestamp: float = 0.0


def check_sum_to_one(group: EventGroup, cfg: ArbiterConfig) -> ArbOpportunity | None:
    """Check if bracket YES prices sum to != 1.00.

    Sum < 0.97: BUY ALL (buy every YES token, guaranteed $1 payout).
    Sum > 1.03: SELL ALL (sell every YES token, collect > $1, pay $1).
    """
    if len(group.markets) < 3:
        return None

    # Sanity: skip groups where the sum is clearly broken (dead/extreme markets)
    # Real bracket markets should sum to roughly 0.85-1.15; anything outside is noise
    total_yes = group.total_yes_sum
    if total_yes < 0.50 or total_yes > 1.50:
        return None

    # Check minimum liquidity on every leg
    for m in group.markets:
        if m.liquidity < cfg.min_liquidity:
            log.debug("Skipping %s — leg '%s' low liquidity ($%.0f < $%d)",
                      group.event_slug, m.group_label, m.liquidity, cfg.min_liquidity)
            return None

    # Require at least 3 legs with meaningful prices (> $0.01)
    meaningful = sum(1 for m in group.markets if m.yes_price >= 0.02)
    if meaningful < 3:
        return None

    deviation = group.deviation_pct

    if deviation < cfg.min_deviation_pct:
        return None

    # Cap max deviation — huge deviations are structural (many-candidate races), not real arbs
    if deviation > cfg.max_deviation_pct:
        log.debug("Skipping %s — deviation %.1f%% > max %.1f%% (structural, not arb)",
                  group.event_slug, deviation, cfg.max_deviation_pct)
        return None

    if total_yes < 1.0:
        # BUY ALL: sum < 1.00 means buying all brackets costs < $1, one pays $1
        cost_per_share = total_yes
        profit_pct = ((1.0 - cost_per_share) / cost_per_share) * 100

        legs = []
        for m in group.markets:
            legs.append(ArbLeg(
                condition_id=m.condition_id,
                token_id=m.yes_token_id,
                side="BUY",
                price=m.yes_price,
            ))

        opp = ArbOpportunity(
            event_slug=group.event_slug,
            event_title=group.event_title,
            arb_type="sum_under",
            legs=legs,
            total_cost=cost_per_share,
            guaranteed_payout=1.0,
            expected_profit_pct=profit_pct,
            deviation_pct=deviation,
            timestamp=time.time(),
        )
        log.info("ARB FOUND [sum_under]: %s | sum=%.4f | dev=%.1f%% | profit=%.1f%% | %d legs",
                 group.event_title[:60], total_yes, deviation, profit_pct, len(legs))
        return opp

    else:
        # SELL ALL: sum > 1.00 means selling all brackets collects > $1, pays $1
        revenue_per_share = total_yes
        profit_pct = ((revenue_per_share - 1.0) / 1.0) * 100

        legs = []
        for m in group.markets:
            legs.append(ArbLeg(
                condition_id=m.condition_id,
                token_id=m.yes_token_id,
                side="SELL",
                price=m.yes_price,
            ))

        opp = ArbOpportunity(
            event_slug=group.event_slug,
            event_title=group.event_title,
            arb_type="sum_over",
            legs=legs,
            total_cost=1.0,  # Need to hold $1 collateral
            guaranteed_payout=revenue_per_share,
            expected_profit_pct=profit_pct,
            deviation_pct=deviation,
            timestamp=time.time(),
        )
        log.info("ARB FOUND [sum_over]: %s | sum=%.4f | dev=%.1f%% | profit=%.1f%% | %d legs",
                 group.event_title[:60], total_yes, deviation, profit_pct, len(legs))
        return opp


# Regex to extract numeric threshold from bracket labels
_THRESHOLD_RE = re.compile(r"[\$]?([\d,]+(?:\.\d+)?)")


def _parse_threshold(label: str) -> float | None:
    """Try to extract a numeric threshold from a bracket label."""
    match = _THRESHOLD_RE.search(label.replace(",", ""))
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None
    return None


def check_monotonic(group: EventGroup, cfg: ArbiterConfig) -> list[ArbOpportunity]:
    """Check monotonic consistency for threshold-style events.

    For "above $X" style: P(above $100K) <= P(above $95K) <= P(above $90K).
    Violations create pair-wise arb opportunities.
    """
    # Try to parse thresholds from group labels
    labeled: list[tuple[float, BracketMarket]] = []
    for m in group.markets:
        threshold = _parse_threshold(m.group_label)
        if threshold is not None:
            labeled.append((threshold, m))

    if len(labeled) < 2:
        return []

    # Sort by threshold ascending
    labeled.sort(key=lambda x: x[0])

    # Check if these are "above $X" style (higher threshold = lower probability)
    above_style = any("above" in m.question.lower() or "over" in m.question.lower()
                      for _, m in labeled)

    opportunities = []
    for i in range(len(labeled) - 1):
        thresh_low, mkt_low = labeled[i]
        thresh_high, mkt_high = labeled[i + 1]

        if above_style:
            # P(above higher threshold) should be <= P(above lower threshold)
            dev_raw = mkt_high.yes_price - mkt_low.yes_price
            if dev_raw > cfg.max_deviation_pct / 100:
                continue  # Too large — likely structural, not a real arb
            if mkt_high.yes_price > mkt_low.yes_price + (cfg.min_deviation_pct / 100):
                dev = (mkt_high.yes_price - mkt_low.yes_price) * 100
                profit_pct = dev  # Simplified

                opp = ArbOpportunity(
                    event_slug=group.event_slug,
                    event_title=group.event_title,
                    arb_type="monotonic",
                    legs=[
                        ArbLeg(
                            condition_id=mkt_low.condition_id,
                            token_id=mkt_low.yes_token_id,
                            side="BUY",
                            price=mkt_low.yes_price,
                        ),
                        ArbLeg(
                            condition_id=mkt_high.condition_id,
                            token_id=mkt_high.yes_token_id,
                            side="SELL",
                            price=mkt_high.yes_price,
                        ),
                    ],
                    total_cost=mkt_low.yes_price,
                    guaranteed_payout=mkt_high.yes_price,
                    expected_profit_pct=profit_pct,
                    deviation_pct=dev,
                    timestamp=time.time(),
                )
                opportunities.append(opp)
                log.info("ARB FOUND [monotonic]: %s | $%.0f (%.2f) vs $%.0f (%.2f) | dev=%.1f%%",
                         group.event_title[:60], thresh_low, mkt_low.yes_price,
                         thresh_high, mkt_high.yes_price, dev)

    return opportunities


def check_complement(group: EventGroup, cfg: ArbiterConfig) -> list[ArbOpportunity]:
    """Check YES + NO complement for each market in the group.

    If YES + NO < 0.97: buy both.
    If YES + NO > 1.03: sell both (rare).
    """
    opportunities = []
    for m in group.markets:
        complement_sum = m.yes_price + m.no_price
        deviation = abs(complement_sum - 1.0) * 100

        if deviation < cfg.min_deviation_pct:
            continue
        if m.liquidity < cfg.min_liquidity:
            continue

        if complement_sum < 1.0:
            cost = complement_sum
            profit_pct = ((1.0 - cost) / cost) * 100

            opp = ArbOpportunity(
                event_slug=group.event_slug,
                event_title=group.event_title,
                arb_type="complement",
                legs=[
                    ArbLeg(
                        condition_id=m.condition_id,
                        token_id=m.yes_token_id,
                        side="BUY",
                        price=m.yes_price,
                    ),
                    ArbLeg(
                        condition_id=m.condition_id,
                        token_id=m.no_token_id,
                        side="BUY",
                        price=m.no_price,
                    ),
                ],
                total_cost=cost,
                guaranteed_payout=1.0,
                expected_profit_pct=profit_pct,
                deviation_pct=deviation,
                timestamp=time.time(),
            )
            opportunities.append(opp)
            log.info("ARB FOUND [complement]: %s | YES=%.2f + NO=%.2f = %.4f | profit=%.1f%%",
                     m.question[:60], m.yes_price, m.no_price, complement_sum, profit_pct)

        elif complement_sum > 1.0:
            revenue = complement_sum
            profit_pct = ((revenue - 1.0) / 1.0) * 100

            opp = ArbOpportunity(
                event_slug=group.event_slug,
                event_title=group.event_title,
                arb_type="complement",
                legs=[
                    ArbLeg(
                        condition_id=m.condition_id,
                        token_id=m.yes_token_id,
                        side="SELL",
                        price=m.yes_price,
                    ),
                    ArbLeg(
                        condition_id=m.condition_id,
                        token_id=m.no_token_id,
                        side="SELL",
                        price=m.no_price,
                    ),
                ],
                total_cost=1.0,
                guaranteed_payout=revenue,
                expected_profit_pct=profit_pct,
                deviation_pct=deviation,
                timestamp=time.time(),
            )
            opportunities.append(opp)
            log.info("ARB FOUND [complement_over]: %s | YES=%.2f + NO=%.2f = %.4f | profit=%.1f%%",
                     m.question[:60], m.yes_price, m.no_price, complement_sum, profit_pct)

    return opportunities
