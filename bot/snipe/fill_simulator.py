"""Pre-trade fill simulation — estimates expected fill from CLOB spread.

Checks the current CLOB orderbook before placing a GTC LIMIT order to predict:
- Expected fill price (best ask for BUY orders)
- Slippage vs mid price
- Whether the order would fill immediately or rest on book

Used for logging and scoring — does NOT block execution.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from bot.snipe import clob_book

log = logging.getLogger("garves.snipe")


@dataclass
class FillEstimate:
    """Estimated fill for a potential order."""
    expected_price: float      # What we expect to pay per share
    mid_price: float           # Current mid price
    slippage_pct: float        # Expected slippage vs mid (%)
    would_fill: bool           # Would fill immediately at our limit price
    detail: str                # Human-readable summary


def estimate_fill(
    token_id: str,
    limit_price: float,
    shares: int,
) -> FillEstimate:
    """Estimate fill quality before placing a BUY LIMIT order.

    For our GTC BUY at limit_price:
    - If best_ask <= limit_price: fills immediately at best_ask
    - If best_ask > limit_price: rests on book as a bid
    """
    book = clob_book.get_orderbook(token_id)

    if not book:
        return FillEstimate(
            expected_price=limit_price,
            mid_price=0.0,
            slippage_pct=0.0,
            would_fill=False,
            detail="No CLOB book data",
        )

    best_bid = book["best_bid"]
    best_ask = book["best_ask"]
    spread = book["spread"]

    if best_bid <= 0 or best_ask <= 0:
        return FillEstimate(
            expected_price=limit_price,
            mid_price=0.0,
            slippage_pct=0.0,
            would_fill=False,
            detail="Empty orderbook",
        )

    mid = (best_bid + best_ask) / 2
    would_fill = best_ask <= limit_price
    expected_price = best_ask if would_fill else limit_price
    slippage_pct = ((expected_price - mid) / mid * 100) if mid > 0 else 0.0

    detail = (
        f"bid=${best_bid:.3f} ask=${best_ask:.3f} mid=${mid:.3f} "
        f"spread=${spread:.4f} | "
        f"{'FILL' if would_fill else 'REST'} @ ${expected_price:.3f} "
        f"(slip={slippage_pct:+.2f}%)"
    )

    return FillEstimate(
        expected_price=round(expected_price, 4),
        mid_price=round(mid, 4),
        slippage_pct=round(slippage_pct, 3),
        would_fill=would_fill,
        detail=detail,
    )
