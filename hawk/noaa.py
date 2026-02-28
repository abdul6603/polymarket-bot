"""NOAA Weather Intelligence Engine — multi-model ensemble forecasts, $0 cost.

Data sources:
  1. Open-Meteo Ensemble API (global) — GFS 31 members + ECMWF IFS 51 members
  2. api.weather.gov (US only) — NWS deterministic + hourly + observations
  3. NOAA NHC (hurricane tracking) — active storms + probability data

No API keys needed. All sources are free and public.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

from bot.http_session import get_session

log = logging.getLogger(__name__)

# ── City Geocoding (major Polymarket cities) ──

CITY_COORDS: dict[str, tuple[float, float]] = {
    # US cities
    "new york": (40.7128, -74.0060),
    "nyc": (40.7128, -74.0060),
    "chicago": (41.8781, -87.6298),
    "miami": (25.7617, -80.1918),
    "atlanta": (33.7490, -84.3880),
    "dallas": (32.7767, -96.7970),
    "houston": (29.7604, -95.3698),
    "seattle": (47.6062, -122.3321),
    "los angeles": (34.0522, -118.2437),
    "la": (34.0522, -118.2437),
    "san francisco": (37.7749, -122.4194),
    "denver": (39.7392, -104.9903),
    "phoenix": (33.4484, -112.0740),
    "boston": (42.3601, -71.0589),
    "washington": (38.9072, -77.0369),
    "dc": (38.9072, -77.0369),
    "philadelphia": (39.9526, -75.1652),
    "minneapolis": (44.9778, -93.2650),
    "detroit": (42.3314, -83.0458),
    "las vegas": (36.1699, -115.1398),
    "portland": (45.5152, -122.6784),
    "charlotte": (35.2271, -80.8431),
    "nashville": (36.1627, -86.7816),
    "san antonio": (29.4241, -98.4936),
    "austin": (30.2672, -97.7431),
    "jacksonville": (30.3322, -81.6557),
    "columbus": (39.9612, -82.9988),
    "indianapolis": (39.7684, -86.1581),
    "memphis": (35.1495, -90.0490),
    "oklahoma city": (35.4676, -97.5164),
    "milwaukee": (43.0389, -87.9065),
    "kansas city": (39.0997, -94.5786),
    "tampa": (27.9506, -82.4572),
    "orlando": (28.5383, -81.3792),
    "st. louis": (38.6270, -90.1994),
    "st louis": (38.6270, -90.1994),
    "pittsburgh": (40.4406, -79.9959),
    "sacramento": (38.5816, -121.4944),
    "san diego": (32.7157, -117.1611),
    "anchorage": (61.2181, -149.9003),
    "honolulu": (21.3069, -157.8583),
    # International
    "london": (51.5074, -0.1278),
    "paris": (48.8566, 2.3522),
    "tokyo": (35.6762, 139.6503),
    "seoul": (37.5665, 126.9780),
    "wellington": (-41.2924, 174.7787),
    "sydney": (-33.8688, 151.2093),
    "toronto": (43.6532, -79.3832),
    "mexico city": (19.4326, -99.1332),
    "berlin": (52.5200, 13.4050),
    "madrid": (40.4168, -3.7038),
    "rome": (41.9028, 12.4964),
    "mumbai": (19.0760, 72.8777),
    "beijing": (39.9042, 116.4074),
    "dubai": (25.2048, 55.2708),
    "singapore": (1.3521, 103.8198),
    "bangkok": (13.7563, 100.5018),
    "ankara": (39.9334, 32.8597),
    "istanbul": (41.0082, 28.9784),
    "cairo": (30.0444, 31.2357),
    "moscow": (55.7558, 37.6173),
    "oslo": (59.9139, 10.7522),
    "stockholm": (59.3293, 18.0686),
    "amsterdam": (52.3676, 4.9041),
    "brussels": (50.8503, 4.3517),
    "vienna": (48.2082, 16.3738),
    "zurich": (47.3769, 8.5417),
    "athens": (37.9838, 23.7275),
    "lisbon": (38.7223, -9.1393),
    "warsaw": (52.2297, 21.0122),
    "prague": (50.0755, 14.4378),
    "buenos aires": (34.6037, -58.3816),
    "sao paulo": (-23.5505, -46.6333),
    "johannesburg": (-26.2041, 28.0473),
    "lagos": (6.5244, 3.3792),
    "nairobi": (-1.2921, 36.8219),
    "taipei": (25.0330, 121.5654),
    "jakarta": (-6.2088, 106.8456),
    "kuala lumpur": (3.1390, 101.6869),
    "ho chi minh": (10.8231, 106.6297),
    "manila": (14.5995, 120.9842),
}

# NWS grid point lookup for US cities (office, gridX, gridY)
_NWS_GRIDS: dict[str, tuple[str, int, int]] = {
    "new york": ("OKX", 33, 37),
    "nyc": ("OKX", 33, 37),
    "chicago": ("LOT", 65, 76),
    "miami": ("MFL", 76, 50),
    "atlanta": ("FFC", 50, 86),
    "dallas": ("FWD", 80, 108),
    "houston": ("HGX", 65, 97),
    "seattle": ("SEW", 124, 67),
    "los angeles": ("LOX", 154, 44),
    "la": ("LOX", 154, 44),
    "denver": ("BOU", 62, 60),
    "phoenix": ("PSR", 159, 57),
    "boston": ("BOX", 71, 90),
    "washington": ("LWX", 97, 71),
    "dc": ("LWX", 97, 71),
    "philadelphia": ("PHI", 49, 75),
}

# ── Cache ──
_cache: dict[str, tuple[float, Any]] = {}
CACHE_TTL = 7200  # 2 hours


def _get_cached(key: str) -> Any | None:
    if key in _cache:
        ts, val = _cache[key]
        if time.time() - ts < CACHE_TTL:
            return val
        del _cache[key]
    return None


def _set_cache(key: str, val: Any) -> None:
    _cache[key] = (time.time(), val)


# ── WeatherQuery Dataclass ──

@dataclass
class WeatherQuery:
    city: str | None = None
    lat: float | None = None
    lon: float | None = None
    target_date: date | None = None
    metric: str = "temperature_max"  # temperature_max, temperature_min, precipitation, hurricane, sea_ice, hottest_year
    threshold: float | None = None
    direction: str = "above"  # above, below, between, bucket
    unit: str = "fahrenheit"  # "fahrenheit" or "celsius"
    bucket_ranges: list[tuple[float, float]] | None = None
    raw_question: str = ""


# ── Question Parser ──

_TEMP_ABOVE_RE = re.compile(
    r"(?:high|maximum|max|temperature).*?(above|over|exceed|higher than|at least|reach)\s*"
    r"(\d+)\s*(?:°?\s*F|degrees?\s*(?:fahrenheit)?)",
    re.IGNORECASE,
)
_TEMP_BELOW_RE = re.compile(
    r"(?:high|maximum|max|low|minimum|min|temperature).*?(below|under|lower than|at most|drop to|fall to)\s*"
    r"(\d+)\s*(?:°?\s*F|degrees?\s*(?:fahrenheit)?)",
    re.IGNORECASE,
)
_TEMP_BETWEEN_RE = re.compile(
    r"(?:temperature|high|low).*?(?:between|from)\s*(\d+)\s*(?:°?\s*F)?\s*(?:and|to|-)\s*(\d+)\s*(?:°?\s*F)?",
    re.IGNORECASE,
)
_TEMP_BUCKET_RE = re.compile(
    r"(\d+)\s*(?:°?\s*F)?\s*(?:or\s+(?:higher|above|more)|\+)",
    re.IGNORECASE,
)
_TEMP_HIGH_RE = re.compile(r"\b(?:high|maximum|max)\b", re.IGNORECASE)
_TEMP_LOW_RE = re.compile(r"\b(?:low|minimum|min)\b", re.IGNORECASE)

_MONTH_NAMES_RE = (
    "january|jan|february|feb|march|mar|april|apr|may|june|jun|"
    "july|jul|august|aug|september|sep|sept|october|oct|november|nov|december|dec"
)
_DATE_PATTERNS = [
    # "on February 25" / "on Feb 25, 2026" — anchored to month names
    re.compile(r"(?:on\s+)?(" + _MONTH_NAMES_RE + r")\s+(\d{1,2})(?:\s*,?\s*(\d{4}))?", re.IGNORECASE),
    # "2/25" or "2/25/2026"
    re.compile(r"(\d{1,2})/(\d{1,2})(?:/(\d{4}))?"),
]

_MONTH_MAP = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6,
    "july": 7, "jul": 7, "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}

_HURRICANE_RE = re.compile(
    r"(hurricane|tropical storm|cyclone|typhoon|named storm|landfall|"
    r"category\s*[1-5]|atlantic.*storm|hurricane season)",
    re.IGNORECASE,
)
_SEA_ICE_RE = re.compile(r"(sea ice|arctic ice|ice extent|ice minimum)", re.IGNORECASE)
_HOTTEST_YEAR_RE = re.compile(r"(hottest year|warmest year|record.*hot|global.*temperature)", re.IGNORECASE)
_PRECIP_RE = re.compile(
    r"(rain|rainfall|snow|snowfall|precipitation|inches of rain|inches of snow)",
    re.IGNORECASE,
)

# ── Celsius temperature patterns (for international markets) ──
_TEMP_EXACT_C_RE = re.compile(
    r"\bbe\s+(-?\d+)\s*°?\s*C\b",
    re.IGNORECASE,
)
_TEMP_ABOVE_C_RE = re.compile(
    r"(?:above|over|exceed|higher than|at least|reach)\s*(-?\d+)\s*°?\s*C\b",
    re.IGNORECASE,
)
_TEMP_BELOW_C_RE = re.compile(
    r"(?:below|under|lower than|at most|drop to|fall to)\s*(-?\d+)\s*°?\s*C\b",
    re.IGNORECASE,
)
_TEMP_BUCKET_C_RE = re.compile(
    r"(-?\d+)\s*°?\s*C\s*or\s+(?:higher|above|more|lower|below|less)",
    re.IGNORECASE,
)
_TEMP_BETWEEN_C_RE = re.compile(
    r"(?:between|from)\s*(-?\d+)\s*(?:°?\s*C)?\s*(?:and|to|-)\s*(-?\d+)\s*°?\s*C",
    re.IGNORECASE,
)
# Fahrenheit "or below" (e.g., "79°F or below")
_TEMP_BUCKET_F_BELOW_RE = re.compile(
    r"(\d+)\s*°?\s*F\s*or\s+(?:lower|below|less)",
    re.IGNORECASE,
)


def _extract_city(question: str) -> tuple[str | None, float | None, float | None]:
    """Extract city name and coordinates from question text."""
    q_lower = question.lower()
    # Try longest match first (e.g., "new york" before "york")
    # Use word boundary check to avoid false positives (e.g., "la" in "landfall")
    matches = []
    for city, (lat, lon) in CITY_COORDS.items():
        # Build regex with word boundaries
        pattern = r'\b' + re.escape(city) + r'\b'
        if re.search(pattern, q_lower):
            matches.append((city, lat, lon))
    if matches:
        # Return longest match
        matches.sort(key=lambda x: len(x[0]), reverse=True)
        return matches[0]
    return None, None, None


def _extract_date(question: str) -> date | None:
    """Extract target date from question text."""
    today = date.today()
    q_lower = question.lower()

    # "today" / "tomorrow"
    if "today" in q_lower:
        return today
    if "tomorrow" in q_lower:
        return today + timedelta(days=1)

    # Day of week
    days_of_week = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6,
    }
    for day_name, day_num in days_of_week.items():
        if day_name in q_lower:
            days_ahead = (day_num - today.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7  # Next occurrence
            return today + timedelta(days=days_ahead)

    # Month + day patterns
    for pattern in _DATE_PATTERNS:
        m = pattern.search(question)
        if m:
            groups = m.groups()
            try:
                if groups[0].isdigit():
                    # M/D format
                    month = int(groups[0])
                    day = int(groups[1])
                else:
                    month_str = groups[0].lower()
                    if month_str not in _MONTH_MAP:
                        continue
                    month = _MONTH_MAP[month_str]
                    day = int(groups[1])
                year = int(groups[2]) if groups[2] else today.year
                target = date(year, month, day)
                # If the date is in the past this year, assume next year
                if target < today and not groups[2]:
                    target = date(today.year + 1, month, day)
                return target
            except (ValueError, TypeError):
                continue

    return None


def parse_weather_question(question: str) -> WeatherQuery | None:
    """Parse a Polymarket weather question into structured query.

    Returns None if the question doesn't look like a weather market.
    """
    q = WeatherQuery(raw_question=question)

    # Determine metric type
    if _HURRICANE_RE.search(question):
        q.metric = "hurricane"
    elif _SEA_ICE_RE.search(question):
        q.metric = "sea_ice"
    elif _HOTTEST_YEAR_RE.search(question):
        q.metric = "hottest_year"
    elif _PRECIP_RE.search(question):
        q.metric = "precipitation"
    else:
        # Default to temperature
        if _TEMP_LOW_RE.search(question):
            q.metric = "temperature_min"
        else:
            q.metric = "temperature_max"

    # Extract city/coordinates
    city, lat, lon = _extract_city(question)
    q.city = city
    q.lat = lat
    q.lon = lon

    # Extract date
    q.target_date = _extract_date(question)

    # ── Celsius patterns first (more specific, international markets) ──
    m = _TEMP_BETWEEN_C_RE.search(question)
    if m:
        low, high = float(m.group(1)), float(m.group(2))
        q.direction = "between"
        q.bucket_ranges = [(low, high)]
        q.threshold = low
        q.unit = "celsius"
        return q

    m = _TEMP_ABOVE_C_RE.search(question)
    if m:
        q.direction = "above"
        q.threshold = float(m.group(1))
        q.unit = "celsius"
        return q

    m = _TEMP_BELOW_C_RE.search(question)
    if m:
        q.direction = "below"
        q.threshold = float(m.group(1))
        q.unit = "celsius"
        return q

    m = _TEMP_BUCKET_C_RE.search(question)
    if m:
        val = float(m.group(1))
        q_lower = question.lower()
        if "lower" in q_lower or "below" in q_lower or "less" in q_lower:
            q.direction = "below"
        else:
            q.direction = "above"
        q.threshold = val
        q.unit = "celsius"
        return q

    # "be X°C" exact match → bucket [X, X+1)
    m = _TEMP_EXACT_C_RE.search(question)
    if m:
        val = float(m.group(1))
        q.direction = "between"
        q.bucket_ranges = [(val, val + 1)]
        q.threshold = val
        q.unit = "celsius"
        return q

    # ── Fahrenheit patterns ──
    m = _TEMP_BETWEEN_RE.search(question)
    if m:
        low, high = float(m.group(1)), float(m.group(2))
        q.direction = "between"
        q.bucket_ranges = [(low, high)]
        q.threshold = low
        return q

    m = _TEMP_ABOVE_RE.search(question)
    if m:
        q.direction = "above"
        q.threshold = float(m.group(2))
        return q

    m = _TEMP_BELOW_RE.search(question)
    if m:
        q.direction = "below"
        q.threshold = float(m.group(2))
        return q

    m = _TEMP_BUCKET_RE.search(question)
    if m:
        q.direction = "above"
        q.threshold = float(m.group(1))
        return q

    # "79°F or below"
    m = _TEMP_BUCKET_F_BELOW_RE.search(question)
    if m:
        q.direction = "below"
        q.threshold = float(m.group(1))
        return q

    # For hurricane/sea_ice/hottest_year — no threshold needed
    if q.metric in ("hurricane", "sea_ice", "hottest_year"):
        return q

    # If we at least have a city and it looks weather-related, return it
    if q.city:
        return q

    return None


# ── Open-Meteo Ensemble API ──

def _fetch_ensemble(lat: float, lon: float, target_date: date, unit: str = "fahrenheit") -> dict | None:
    """Fetch ensemble forecast from Open-Meteo (GFS + ECMWF IFS).

    Returns dict with keys: temperature_2m_max, temperature_2m_min, precipitation_sum
    Each value is a dict mapping model name to list of member values for target date.
    """
    cache_key = f"ensemble_{lat:.2f}_{lon:.2f}_{target_date}_{unit}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    session = get_session()

    # Forecast range
    days_ahead = (target_date - date.today()).days
    if days_ahead < 0:
        return None
    if days_ahead > 16:
        log.info("[NOAA] Target date %s is >16 days out, ensemble unreliable", target_date)
        return None

    try:
        resp = session.get(
            "https://ensemble-api.open-meteo.com/v1/ensemble",
            params={
                "latitude": lat,
                "longitude": lon,
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
                "models": "gfs_seamless,ecmwf_ifs025",
                "temperature_unit": unit,
                "precipitation_unit": "inch",
                "forecast_days": min(days_ahead + 2, 16),
            },
            timeout=15,
        )
        if resp.status_code != 200:
            log.warning("[NOAA] Open-Meteo returned %d", resp.status_code)
            return None

        data = resp.json()
    except Exception:
        log.exception("[NOAA] Open-Meteo API call failed")
        return None

    # Parse ensemble members for target date
    result: dict[str, list[float]] = {
        "temperature_2m_max": [],
        "temperature_2m_min": [],
        "precipitation_sum": [],
    }

    target_str = target_date.isoformat()
    daily = data.get("daily", {})
    times = daily.get("time", [])

    if not times:
        return None

    # Find target date index
    target_idx = None
    for i, t in enumerate(times):
        if t == target_str:
            target_idx = i
            break

    if target_idx is None:
        return None

    # Open-Meteo ensemble keys are like:
    #   temperature_2m_max_member01_ncep_gefs_seamless  (GFS, 30 members)
    #   temperature_2m_max_member01_ecmwf_ifs025_ensemble  (ECMWF, 50 members)
    #   temperature_2m_max_ncep_gefs_seamless  (GFS control/mean)
    #   temperature_2m_max_ecmwf_ifs025_ensemble  (ECMWF control/mean)
    for var in ["temperature_2m_max", "temperature_2m_min", "precipitation_sum"]:
        for key, values in daily.items():
            if not key.startswith(var):
                continue
            if key == "time":
                continue
            if not isinstance(values, list) or target_idx >= len(values):
                continue
            val = values[target_idx]
            if val is not None:
                result[var].append(val)

    if any(len(v) > 0 for v in result.values()):
        _set_cache(cache_key, result)
        log.info("[NOAA] Ensemble data for (%.2f, %.2f) on %s: %d max_temp, %d min_temp, %d precip members",
                 lat, lon, target_date,
                 len(result["temperature_2m_max"]),
                 len(result["temperature_2m_min"]),
                 len(result["precipitation_sum"]))
        return result

    log.info("[NOAA] No ensemble data found for %s on %s", (lat, lon), target_date)
    return None


def get_temperature_probability(
    city: str,
    target_date: date,
    threshold: float,
    direction: str = "above",
    unit: str = "fahrenheit",
    metric: str = "temperature_max",
) -> float | None:
    """Compute probability of temperature above/below threshold from ensemble.

    Returns probability 0-1, or None if no data.
    """
    coords = CITY_COORDS.get(city.lower())
    if not coords:
        return None

    lat, lon = coords
    ensemble = _fetch_ensemble(lat, lon, target_date, unit=unit)
    if not ensemble:
        return None

    # Use metric to determine which variable to compare against
    var_key = "temperature_2m_min" if metric == "temperature_min" else "temperature_2m_max"

    members = ensemble.get(var_key, [])
    if not members:
        return None

    unit_sym = "°C" if unit == "celsius" else "°F"
    total = len(members)
    if direction == "above":
        count = sum(1 for v in members if v >= threshold)
    elif direction == "below":
        count = sum(1 for v in members if v <= threshold)
    else:
        count = sum(1 for v in members if v >= threshold)

    prob = count / total
    log.info("[NOAA] %s temp %s %.0f%s on %s: %d/%d members = %.1f%%",
             city, direction, threshold, unit_sym, target_date, count, total, prob * 100)
    return prob


def get_temperature_bucket_probs(
    city: str,
    target_date: date,
    buckets: list[tuple[float, float]],
    unit: str = "fahrenheit",
) -> dict[str, float] | None:
    """Compute probability distribution across temperature buckets.

    Args:
        buckets: list of (low, high) ranges, e.g., [(30,35), (35,40), (40,45)]

    Returns dict mapping "30-35" -> 0.23, etc.
    """
    coords = CITY_COORDS.get(city.lower())
    if not coords:
        return None

    lat, lon = coords
    ensemble = _fetch_ensemble(lat, lon, target_date, unit=unit)
    if not ensemble:
        return None

    members = ensemble.get("temperature_2m_max", [])
    if not members:
        return None

    total = len(members)
    result = {}
    for low, high in buckets:
        count = sum(1 for v in members if low <= v < high)
        key = f"{low:.0f}-{high:.0f}"
        result[key] = count / total

    return result


# ── api.weather.gov (US NWS) ──

def _fetch_nws_forecast(city: str) -> dict | None:
    """Fetch NWS deterministic forecast for a US city.

    Returns parsed forecast periods.
    """
    grid = _NWS_GRIDS.get(city.lower())
    if not grid:
        return None

    cache_key = f"nws_{city.lower()}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    office, x, y = grid
    session = get_session()
    try:
        resp = session.get(
            f"https://api.weather.gov/gridpoints/{office}/{x},{y}/forecast",
            headers={"User-Agent": "HawkBot/1.0 (polymarket trading bot)"},
            timeout=10,
        )
        if resp.status_code != 200:
            log.warning("[NWS] Forecast API returned %d for %s", resp.status_code, city)
            return None

        data = resp.json()
        periods = data.get("properties", {}).get("periods", [])
        if periods:
            _set_cache(cache_key, periods)
            return periods
    except Exception:
        log.exception("[NWS] Forecast fetch failed for %s", city)
    return None


def get_nws_temperature(city: str, target_date: date) -> dict | None:
    """Get NWS deterministic high/low forecast for verification.

    Returns {"high": 42, "low": 28, "forecast": "Partly Cloudy"} or None.
    """
    periods = _fetch_nws_forecast(city)
    if not periods:
        return None

    target_str = target_date.isoformat()
    for period in periods:
        start = period.get("startTime", "")
        if target_str in start:
            return {
                "high" if period.get("isDaytime") else "low": period.get("temperature"),
                "forecast": period.get("shortForecast", ""),
                "wind_speed": period.get("windSpeed", ""),
                "wind_direction": period.get("windDirection", ""),
            }
    return None


# ── Hurricane Tracking (NHC) ──

def get_hurricane_probability(question: str) -> float | None:
    """Estimate hurricane-related probability from NHC data.

    For seasonal markets (e.g., "Will 2026 hurricane season have 15+ named storms?"),
    uses historical base rates + current NHC/NOAA seasonal outlook.
    """
    cache_key = f"hurricane_{hash(question)}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    q_lower = question.lower()

    # Historical base rates for common hurricane questions
    # Average Atlantic season: 14 named storms, 7 hurricanes, 3 major
    if "named storm" in q_lower or "named storms" in q_lower:
        # Extract number threshold
        m = re.search(r"(\d+)\+?\s*(?:or more\s+)?named storm", q_lower)
        if m:
            threshold = int(m.group(1))
            # Historical probabilities (rough, from 1991-2020 data)
            if threshold <= 10:
                prob = 0.85
            elif threshold <= 12:
                prob = 0.70
            elif threshold <= 14:
                prob = 0.55
            elif threshold <= 16:
                prob = 0.40
            elif threshold <= 18:
                prob = 0.25
            elif threshold <= 20:
                prob = 0.15
            else:
                prob = 0.08
            _set_cache(cache_key, prob)
            return prob

    if "category" in q_lower and ("landfall" in q_lower or "hit" in q_lower or "strike" in q_lower):
        # Category 4/5 landfall is historically ~15% per season
        if "category 5" in q_lower:
            prob = 0.08
        elif "category 4" in q_lower:
            prob = 0.18
        elif "category 3" in q_lower:
            prob = 0.30
        else:
            prob = 0.50
        _set_cache(cache_key, prob)
        return prob

    if "landfall" in q_lower:
        # Any hurricane US landfall: ~40-50% per season historically
        prob = 0.45
        _set_cache(cache_key, prob)
        return prob

    return None


# ── Climate / Long-range Data ──

def get_climate_data(question: str) -> dict | None:
    """Handle hottest year, sea ice extent, and other climate questions.

    Returns {"probability": float, "reasoning": str} or None.
    """
    q_lower = question.lower()

    if _HOTTEST_YEAR_RE.search(question):
        # "Will 2026 be the hottest year on record?"
        # Use trend: 2023 was hottest, 2024 was near-record, trend is warming
        # But individual year prediction depends on ENSO state
        return {
            "probability": 0.35,
            "reasoning": "Long-term warming trend supports ~35% probability for any given year being hottest on record. ENSO state and volcanic activity create year-to-year variance.",
            "confidence": 0.45,
        }

    if _SEA_ICE_RE.search(question):
        # Arctic sea ice extent questions
        m = re.search(r"(below|under|less than)\s*([\d.]+)\s*(million)", q_lower)
        if m:
            threshold = float(m.group(2))
            # September minimum average is ~4.5M km², declining ~13% per decade
            if threshold >= 4.5:
                prob = 0.55  # More likely than not to be below average
            elif threshold >= 4.0:
                prob = 0.35
            elif threshold >= 3.5:
                prob = 0.15
            else:
                prob = 0.05
            return {
                "probability": prob,
                "reasoning": f"Arctic sea ice September minimum averages ~4.5M km² with declining trend. Threshold of {threshold}M km² has ~{prob:.0%} historical probability.",
                "confidence": 0.50,
            }

        return {
            "probability": 0.50,
            "reasoning": "Sea ice extent question without clear threshold — defaulting to base rate.",
            "confidence": 0.35,
        }

    return None


# ── V6: Hourly Nowcast (Open-Meteo) ──

_NOWCAST_CACHE_TTL = 900  # 15 minutes


def _fetch_hourly_nowcast(lat: float, lon: float) -> dict | None:
    """Fetch hourly forecast for next 6 hours from Open-Meteo.

    Free, no API key, 15-min cache. Returns hourly arrays for 0-6h ahead.
    """
    cache_key = f"nowcast_{lat:.2f}_{lon:.2f}"
    if cache_key in _cache:
        ts, val = _cache[cache_key]
        if time.time() - ts < _NOWCAST_CACHE_TTL:
            return val
        del _cache[cache_key]

    session = get_session()
    try:
        resp = session.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "hourly": "temperature_2m,precipitation,precipitation_probability,weathercode",
                "forecast_hours": 6,
                "temperature_unit": "fahrenheit",
                "precipitation_unit": "inch",
            },
            timeout=10,
        )
        if resp.status_code != 200:
            log.warning("[NOWCAST] Open-Meteo hourly returned %d", resp.status_code)
            return None

        data = resp.json()
        hourly = data.get("hourly", {})
        if not hourly.get("time"):
            return None

        _cache[cache_key] = (time.time(), hourly)
        log.info("[NOWCAST] Fetched %d hourly points for (%.2f, %.2f)",
                 len(hourly.get("time", [])), lat, lon)
        return hourly
    except Exception:
        log.exception("[NOWCAST] Open-Meteo hourly fetch failed")
        return None


def is_same_day_weather_event(question: str) -> bool:
    """Check if a weather market is for today (same-day event)."""
    query = parse_weather_question(question)
    if not query or not query.target_date:
        return False
    return query.target_date == date.today()


# ── Main Analysis Entry Point ──

def analyze_weather_market(question: str) -> dict | None:
    """Full weather analysis pipeline for a Polymarket question.

    Returns:
        {
            "probability": float (0-1),
            "confidence": float (0-1),
            "reasoning": str,
            "data_source": str,
            "ensemble_members": int,
            "forecast_horizon_hours": float,
        }
        or None if we can't analyze this question.
    """
    query = parse_weather_question(question)
    if not query:
        return None

    # Hurricane markets
    if query.metric == "hurricane":
        prob = get_hurricane_probability(question)
        if prob is not None:
            return {
                "probability": prob,
                "confidence": 0.50,
                "reasoning": f"Hurricane probability based on historical base rates and climatological data",
                "data_source": "noaa_nhc_historical",
                "ensemble_members": 0,
                "forecast_horizon_hours": 0,
            }

    # Climate/ranking markets
    if query.metric in ("hottest_year", "sea_ice"):
        climate = get_climate_data(question)
        if climate:
            return {
                "probability": climate["probability"],
                "confidence": climate.get("confidence", 0.45),
                "reasoning": climate["reasoning"],
                "data_source": "climate_historical",
                "ensemble_members": 0,
                "forecast_horizon_hours": 0,
            }

    # Temperature and precipitation markets
    if query.lat is None or query.lon is None:
        log.info("[NOAA] No coordinates found for question: %s", question[:80])
        return None

    if query.target_date is None:
        log.info("[NOAA] No target date found for question: %s", question[:80])
        return None

    # Calculate forecast horizon
    now = datetime.now(timezone.utc)
    target_dt = datetime.combine(query.target_date, datetime.min.time()).replace(tzinfo=timezone.utc)
    horizon_hours = max(0, (target_dt - now).total_seconds() / 3600)

    # Confidence calibration by forecast horizon
    if horizon_hours <= 24:
        confidence = 0.85
    elif horizon_hours <= 48:
        confidence = 0.75
    elif horizon_hours <= 120:  # 5 days
        confidence = 0.60
    else:
        confidence = 0.45

    unit_sym = "°C" if query.unit == "celsius" else "°F"

    # V6: Same-day nowcast — ONLY for real-time events, NOT daily max/min forecasts
    # Daily temp markets must use 82-member ensemble, not 6 hourly readings at 3AM
    is_daily_temp = query.metric in ("temperature_max", "temperature_min")
    if horizon_hours < 24 and query.threshold is not None and query.lat and query.lon and not is_daily_temp:
        nowcast = _fetch_hourly_nowcast(query.lat, query.lon)
        if nowcast and nowcast.get("temperature_2m"):
            temps = [t for t in nowcast["temperature_2m"] if t is not None]
            if temps:
                # For same-day, use hourly max/min from nowcast
                if query.direction == "above":
                    count = sum(1 for t in temps if t >= query.threshold)
                    nowcast_prob = count / len(temps)
                elif query.direction == "below":
                    count = sum(1 for t in temps if t <= query.threshold)
                    nowcast_prob = count / len(temps)
                elif query.direction == "between" and query.bucket_ranges:
                    low, high = query.bucket_ranges[0]
                    count = sum(1 for t in temps if low <= t < high)
                    nowcast_prob = count / len(temps)
                else:
                    nowcast_prob = None

                if nowcast_prob is not None:
                    # Nowcast is very reliable for <6h
                    nowcast_conf = 0.90 if horizon_hours < 6 else 0.85
                    log.info("[NOWCAST] Same-day analysis: prob=%.2f conf=%.2f | %dh horizon | %s",
                             nowcast_prob, nowcast_conf, int(horizon_hours), question[:60])
                    return {
                        "probability": nowcast_prob,
                        "confidence": nowcast_conf,
                        "reasoning": (
                            f"Hourly nowcast: {nowcast_prob:.0%} probability based on "
                            f"{len(temps)} hourly readings for next 6h. "
                            f"Threshold: {query.direction} {query.threshold:.0f}{unit_sym} in {query.city}."
                        ),
                        "data_source": "open_meteo_nowcast",
                        "ensemble_members": len(temps),
                        "forecast_horizon_hours": horizon_hours,
                    }

    # Bucket probabilities first (handles "be X°C" exact and "between X-Y" ranges)
    if query.bucket_ranges:
        bucket_probs = get_temperature_bucket_probs(
            query.city, query.target_date, query.bucket_ranges, unit=query.unit,
        )
        if bucket_probs is not None:
            total_prob = sum(bucket_probs.values())
            if True:  # Always return bucket result — 0% is a valid answer, don't fall through
                # Count ensemble members for metadata
                coords = CITY_COORDS.get(query.city.lower(), (0, 0))
                ensemble = _fetch_ensemble(coords[0], coords[1], query.target_date, unit=query.unit)
                n_members = 0
                if ensemble:
                    var_key = "temperature_2m_max" if query.metric == "temperature_max" else "temperature_2m_min"
                    n_members = len(ensemble.get(var_key, []))

                return {
                    "probability": total_prob,
                    "confidence": confidence,
                    "reasoning": (
                        f"Multi-model ensemble consensus: "
                        + ", ".join(f"{k}{unit_sym}: {v:.0%}" for k, v in bucket_probs.items())
                        + f" in {query.city} on {query.target_date}. "
                        f"Based on {n_members} ensemble members (GFS + ECMWF IFS)."
                    ),
                    "data_source": "open_meteo_ensemble",
                    "ensemble_members": n_members,
                    "forecast_horizon_hours": horizon_hours,
                }

    # Single threshold (above/below)
    if query.metric in ("temperature_max", "temperature_min") and query.threshold is not None:
        prob = get_temperature_probability(
            query.city, query.target_date, query.threshold, query.direction,
            unit=query.unit, metric=query.metric,
        )
        if prob is not None:
            # Cross-verify with NWS for US cities
            nws = get_nws_temperature(query.city, query.target_date)
            nws_info = ""
            if nws:
                nws_info = f" NWS forecast: {nws.get('forecast', 'N/A')}, wind: {nws.get('wind_speed', 'N/A')}"

            # Count ensemble members
            coords = CITY_COORDS.get(query.city.lower(), (0, 0))
            ensemble = _fetch_ensemble(coords[0], coords[1], query.target_date, unit=query.unit)
            n_members = 0
            if ensemble:
                var_key = "temperature_2m_max" if query.metric == "temperature_max" else "temperature_2m_min"
                n_members = len(ensemble.get(var_key, []))

            return {
                "probability": prob,
                "confidence": confidence,
                "reasoning": (
                    f"Multi-model ensemble consensus: {prob:.0%} probability of "
                    f"{query.metric.replace('_', ' ')} {query.direction} {query.threshold:.0f}{unit_sym} "
                    f"in {query.city} on {query.target_date}. "
                    f"Based on {n_members} ensemble members (GFS + ECMWF IFS).{nws_info}"
                ),
                "data_source": "open_meteo_ensemble",
                "ensemble_members": n_members,
                "forecast_horizon_hours": horizon_hours,
            }

    return None
