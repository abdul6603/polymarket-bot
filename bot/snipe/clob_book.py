"""Bridge to read Polymarket CLOB orderbook data from ws_feed (thread-safe).

The MarketFeed runs in the asyncio event loop and updates _orderbooks on
every "book" WS event. The snipe engine runs in a separate sync thread.
Python's GIL makes dict reads thread-safe, so we read snapshots directly.

Usage:
    # In Garves main (once during init):
    from bot.snipe import clob_book
    clob_book.set_feed(self.feed)

    # In snipe engine (every tick):
    book = clob_book.get_orderbook(token_id)
"""
from __future__ import annotations

_feed_ref = None  # MarketFeed instance, set once by Garves main


def set_feed(feed) -> None:
    """Set the MarketFeed reference. Called once from Garves main."""
    global _feed_ref
    _feed_ref = feed


def get_orderbook(token_id: str) -> dict | None:
    """Get latest OrderbookSnapshot for a token as a dict.

    Returns dict with: buy_pressure, sell_pressure, best_bid, best_ask, spread.
    Returns None if no data or feed not set.
    """
    if _feed_ref is None:
        return None
    books = _feed_ref.latest_orderbook
    snap = books.get(token_id)
    if snap is None:
        return None
    return {
        "buy_pressure": snap.buy_pressure,
        "sell_pressure": snap.sell_pressure,
        "best_bid": snap.best_bid,
        "best_ask": snap.best_ask,
        "spread": snap.spread,
    }


def get_spread(token_id: str) -> float | None:
    """Get current spread for a token. Returns None if no data."""
    book = get_orderbook(token_id)
    return book["spread"] if book else None


def get_mid_price(token_id: str) -> float | None:
    """Get mid price (avg of best bid + best ask). Returns None if no data."""
    book = get_orderbook(token_id)
    if not book or book["best_bid"] <= 0 or book["best_ask"] <= 0:
        return None
    return (book["best_bid"] + book["best_ask"]) / 2
