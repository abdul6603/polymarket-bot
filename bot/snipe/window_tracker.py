"""Window Tracker — discovers 5m markets and tracks window open prices.

Responsibilities:
- Accept discovered 5m markets from the taker loop (BTC, ETH, SOL, XRP)
- Parse window start/end times from market questions
- Capture asset open price at window start from PriceCache
- Track remaining time for each active window
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from bot.config import Config
from bot.price_cache import PriceCache

log = logging.getLogger("garves.snipe")
ET = ZoneInfo("America/New_York")

# Regex to parse 5m window times: "10:00PM-10:05PM ET"
_RANGE_RE = re.compile(
    r"(\d{1,2}):(\d{2})(AM|PM)-(\d{1,2}):(\d{2})(AM|PM)\s+ET",
    re.IGNORECASE,
)

_DATE_RE = re.compile(
    r"(January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+(\d{1,2})",
)


@dataclass
class Window:
    """A single 5m trading window."""
    market_id: str
    question: str
    asset: str             # "bitcoin", "ethereum", "solana", "xrp"
    up_token_id: str
    down_token_id: str
    start_ts: float       # Unix timestamp of window start
    end_ts: float          # Unix timestamp of window end
    open_price: float      # Asset price at window start
    traded: bool = False   # Whether we've already traded this window


class WindowTracker:
    """Discovers and tracks 5m windows across all crypto assets."""

    def __init__(self, cfg: Config, price_cache: PriceCache):
        self._cfg = cfg
        self._cache = price_cache
        self._active: dict[str, Window] = {}  # market_id -> Window

    def get_active_window(self) -> Window | None:
        """Return the best untouched window to trade, or None."""
        now = time.time()
        best = None
        for w in self._active.values():
            if w.traded:
                continue
            remaining = w.end_ts - now
            if remaining <= 0 or remaining > 300:
                continue
            if best is None or remaining < (best.end_ts - now):
                best = w
        return best

    def all_active_windows(self) -> list:
        """Return all active windows for multi-asset scanning."""
        return list(self._active.values())

    def update(self, markets_5m: list) -> None:
        """Update window state from discovered 5m markets.

        Args:
            markets_5m: List of DiscoveredMarket objects for BTC 5m.
        """
        now = time.time()

        # Clean expired windows (keep 120s for resolution checking)
        expired = [mid for mid, w in self._active.items() if w.end_ts < now - 120]
        for mid in expired:
            del self._active[mid]

        # Add new windows
        for dm in markets_5m:
            mid = dm.market_id
            if mid in self._active:
                continue

            # Parse tokens
            tokens = dm.raw.get("tokens", [])
            up_tid = down_tid = ""
            for t in tokens:
                outcome = (t.get("outcome") or "").lower()
                tid = t.get("token_id", "")
                if outcome in ("up", "yes"):
                    up_tid = tid
                elif outcome in ("down", "no"):
                    down_tid = tid

            if not up_tid or not down_tid:
                continue

            # Parse window start/end times
            start_ts, end_ts = self._parse_window_times(dm.question)
            if not start_ts or not end_ts:
                continue

            # Get asset open price at window start
            open_price = self._get_open_price(dm.asset, start_ts)
            if open_price <= 0:
                log.debug("[SNIPE] No open price for window %s (%s)", mid[:12], dm.asset)
                continue

            window = Window(
                market_id=mid,
                question=dm.question,
                asset=dm.asset,
                up_token_id=up_tid,
                down_token_id=down_tid,
                start_ts=start_ts,
                end_ts=end_ts,
                open_price=open_price,
            )
            self._active[mid] = window
            remaining = end_ts - now
            log.info(
                "[SNIPE] Window tracked: %s | %s open=$%.2f | T-%.0fs | %s",
                mid[:12], dm.asset.upper(), open_price, remaining, dm.question[:60],
            )

    def _parse_window_times(self, question: str) -> tuple[float, float]:
        """Parse start and end timestamps from market question."""
        date_match = _DATE_RE.search(question)
        range_match = _RANGE_RE.search(question)
        if not date_match or not range_match:
            return 0.0, 0.0

        month_str, day_str = date_match.groups()
        h1, m1, ap1, h2, m2, ap2 = range_match.groups()

        now = datetime.now(ET)
        try:
            month_num = datetime.strptime(month_str, "%B").month
        except ValueError:
            return 0.0, 0.0

        year = now.year
        if now.month - month_num > 6:
            year += 1
        elif now.month - month_num < -6:
            year -= 1

        start_hour = int(h1) % 12 + (12 if ap1.upper() == "PM" else 0)
        start_min = int(m1)
        end_hour = int(h2) % 12 + (12 if ap2.upper() == "PM" else 0)
        end_min = int(m2)

        try:
            start_dt = datetime(
                year, month_num, int(day_str),
                start_hour % 24, start_min, tzinfo=ET,
            )
            end_dt = datetime(
                year, month_num, int(day_str),
                end_hour % 24, end_min, tzinfo=ET,
            )
            if end_dt <= start_dt:
                from datetime import timedelta
                end_dt += timedelta(days=1)
        except ValueError:
            return 0.0, 0.0

        return start_dt.timestamp(), end_dt.timestamp()

    def _get_open_price(self, asset: str, start_ts: float) -> float:
        """Get asset price at window start from PriceCache."""
        start_minute = int(start_ts // 60) * 60
        candles = self._cache.get_candles(asset, 300)

        # Exact match: candle at window start minute
        for c in candles:
            if abs(c.timestamp - start_minute) < 60:
                return c.open

        # Fallback: if window just started (< 2 min ago), use current price
        now = time.time()
        if now - start_ts < 120:
            price = self._cache.get_price(asset)
            if price:
                return price

        # Fallback: closest candle within 5 min
        if candles:
            closest = min(candles, key=lambda c: abs(c.timestamp - start_minute))
            if abs(closest.timestamp - start_minute) < 300:
                return closest.close

        return 0.0

    def mark_traded(self, market_id: str) -> None:
        """Mark a window as traded (no more entries)."""
        if market_id in self._active:
            self._active[market_id].traded = True

    def get_window(self, market_id: str) -> Window | None:
        """Get a specific window by market_id."""
        return self._active.get(market_id)

    def get_status(self) -> dict:
        """Dashboard-friendly status."""
        now = time.time()
        windows = []
        for w in self._active.values():
            remaining = w.end_ts - now
            if remaining > -30:
                windows.append({
                    "market_id": w.market_id[:12],
                    "asset": w.asset,
                    "open_price": w.open_price,
                    "remaining_s": round(max(0, remaining)),
                    "traded": w.traded,
                    "question": w.question[:60],
                })
        return {
            "active_windows": len(windows),
            "windows": windows,
        }
