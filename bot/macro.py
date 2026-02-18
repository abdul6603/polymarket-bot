"""FRED Macro Event Detection — CPI/FOMC/Jobs Report days.

On macro event days, crypto swings 5-10%. This module detects those days
and acts as a regime modifier (NOT a voting indicator), requiring stronger
signals before trading.

Also provides DXY trend as a standalone voting indicator (inverse correlation
with crypto).

Free API: api.stlouisfed.org (FRED_API_KEY required for DXY/VIX data).
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from bot.http_session import get_session

log = logging.getLogger(__name__)

_FRED_API_KEY = os.environ.get("FRED_API_KEY", "")
_FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"

# Cache for macro context
_cache: dict[str, tuple["MacroContext | None", float]] = {}
_CACHE_TTL = 3600  # 1 hour — slow-changing data

ET = ZoneInfo("America/New_York")

# ── Hardcoded FOMC 2026 dates (announced by Fed in advance) ──
FOMC_2026 = {
    date(2026, 1, 28), date(2026, 1, 29),
    date(2026, 3, 17), date(2026, 3, 18),
    date(2026, 5, 5), date(2026, 5, 6),
    date(2026, 6, 16), date(2026, 6, 17),
    date(2026, 7, 28), date(2026, 7, 29),
    date(2026, 9, 15), date(2026, 9, 16),
    date(2026, 10, 27), date(2026, 10, 28),
    date(2026, 12, 15), date(2026, 12, 16),
}

# CPI release dates 2026 (typically 2nd or 3rd Tuesday of month, 8:30 AM ET)
CPI_2026 = {
    date(2026, 1, 14), date(2026, 2, 12), date(2026, 3, 11),
    date(2026, 4, 14), date(2026, 5, 13), date(2026, 6, 10),
    date(2026, 7, 14), date(2026, 8, 12), date(2026, 9, 16),
    date(2026, 10, 13), date(2026, 11, 12), date(2026, 12, 9),
}


def _first_friday(year: int, month: int) -> date:
    """Heuristic: Non-Farm Payrolls are released on the first Friday of each month."""
    d = date(year, month, 1)
    # Find first Friday (weekday 4)
    while d.weekday() != 4:
        d += timedelta(days=1)
    return d


def _get_nfp_dates_2026() -> set[date]:
    """Generate first-Friday dates for 2026."""
    return {_first_friday(2026, m) for m in range(1, 13)}


NFP_2026 = _get_nfp_dates_2026()


@dataclass
class MacroContext:
    is_event_day: bool = False
    event_type: str = ""  # "fomc", "cpi", "nfp", or ""
    edge_multiplier: float = 1.0  # Regime adjustment: 1.3-1.5x on event days

    # DXY (US Dollar Index) data
    dxy_value: float = 0.0
    dxy_trend: str = ""  # "rising", "falling", "flat"
    dxy_change_pct: float = 0.0

    # VIX (fear gauge)
    vix_value: float = 0.0

    # Fed funds rate
    fed_funds_rate: float = 0.0

    timestamp: float = field(default_factory=time.time)


def _is_event_day(today: date) -> tuple[bool, str, float]:
    """Check if today is a macro event day.

    Returns (is_event, event_type, edge_multiplier).
    """
    if today in FOMC_2026:
        return True, "fomc", 1.5  # FOMC = highest volatility
    if today in CPI_2026:
        return True, "cpi", 1.4
    if today in NFP_2026:
        return True, "nfp", 1.3

    # Also check day before FOMC (pre-positioning)
    tomorrow = today + timedelta(days=1)
    if tomorrow in FOMC_2026:
        return True, "fomc_eve", 1.2

    return False, "", 1.0


def _fred_get_latest(series_id: str) -> float | None:
    """Fetch latest observation from FRED."""
    if not _FRED_API_KEY:
        return None

    params = {
        "series_id": series_id,
        "api_key": _FRED_API_KEY,
        "file_type": "json",
        "sort_order": "desc",
        "limit": 5,
    }

    try:
        resp = get_session().get(_FRED_BASE, params=params, timeout=10)
        if resp.status_code != 200:
            return None
        data = resp.json()
        obs = data.get("observations", [])
        for o in obs:
            val = o.get("value", ".")
            if val != ".":
                return float(val)
        return None
    except Exception:
        return None


def _get_dxy_trend() -> tuple[float, str, float]:
    """Fetch DXY index and calculate trend.

    Returns (current_value, trend_label, pct_change).
    """
    if not _FRED_API_KEY:
        return 0.0, "", 0.0

    params = {
        "series_id": "DTWEXBGS",  # Broad trade-weighted USD index
        "api_key": _FRED_API_KEY,
        "file_type": "json",
        "sort_order": "desc",
        "limit": 30,
    }

    try:
        resp = get_session().get(_FRED_BASE, params=params, timeout=10)
        if resp.status_code != 200:
            return 0.0, "", 0.0

        data = resp.json()
        obs = data.get("observations", [])
        values = []
        for o in obs:
            val = o.get("value", ".")
            if val != ".":
                values.append(float(val))

        if len(values) < 2:
            return values[0] if values else 0.0, "flat", 0.0

        current = values[0]
        week_ago = values[min(4, len(values) - 1)]
        pct_change = (current - week_ago) / week_ago * 100

        if pct_change > 0.3:
            trend = "rising"
        elif pct_change < -0.3:
            trend = "falling"
        else:
            trend = "flat"

        return current, trend, pct_change

    except Exception:
        return 0.0, "", 0.0


def get_context() -> MacroContext:
    """Get current macro context. Cached for 1 hour.

    Returns MacroContext with event detection + DXY trend + VIX.
    """
    now = time.time()
    cached = _cache.get("macro")
    if cached and now - cached[1] < _CACHE_TTL:
        return cached[0]

    today = datetime.now(ET).date()
    is_event, event_type, edge_mult = _is_event_day(today)

    ctx = MacroContext(
        is_event_day=is_event,
        event_type=event_type,
        edge_multiplier=edge_mult,
    )

    # Fetch DXY trend
    dxy_val, dxy_trend, dxy_change = _get_dxy_trend()
    ctx.dxy_value = dxy_val
    ctx.dxy_trend = dxy_trend
    ctx.dxy_change_pct = dxy_change

    # Fetch VIX
    vix = _fred_get_latest("VIXCLS")
    if vix is not None:
        ctx.vix_value = vix

    # Fetch Fed funds rate
    ffr = _fred_get_latest("FEDFUNDS")
    if ffr is not None:
        ctx.fed_funds_rate = ffr

    ctx.timestamp = now
    _cache["macro"] = (ctx, now)

    if is_event:
        log.info(
            "[MACRO] EVENT DAY: %s | edge_multiplier=%.1fx | DXY=%.1f (%s, %.1f%%) | VIX=%.1f",
            event_type.upper(), edge_mult, dxy_val, dxy_trend, dxy_change, ctx.vix_value,
        )
    else:
        log.debug(
            "[MACRO] Normal day | DXY=%.1f (%s, %.1f%%) | VIX=%.1f | FFR=%.2f%%",
            dxy_val, dxy_trend, dxy_change, ctx.vix_value, ctx.fed_funds_rate,
        )

    return ctx
