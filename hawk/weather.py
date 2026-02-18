"""OpenWeatherMap — outdoor sports weather impact for Hawk.

NFL/MLB outdoor games only. Free tier: 1M calls/mo.
Cache TTL: 1800s (30 min).

Requires OPENWEATHER_API_KEY in .env.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

import requests

log = logging.getLogger(__name__)

_API_KEY = os.environ.get("OPENWEATHER_API_KEY", "")
_BASE_URL = "https://api.openweathermap.org/data/2.5"

# Cache: venue_key -> (WeatherImpact, timestamp)
_cache: dict[str, tuple["WeatherImpact | None", float]] = {}
_CACHE_TTL = 1800  # 30 minutes

# Outdoor NFL venues -> cities (dome stadiums excluded)
_VENUE_CITIES: dict[str, str] = {
    # NFL Outdoor
    "arrowhead stadium": "Kansas City,US",
    "empower field": "Denver,US",
    "gillette stadium": "Foxborough,US",
    "lambeau field": "Green Bay,US",
    "highmark stadium": "Orchard Park,US",
    "soldier field": "Chicago,US",
    "metlife stadium": "East Rutherford,US",
    "lincoln financial field": "Philadelphia,US",
    "fedexfield": "Landover,US",
    "levi's stadium": "Santa Clara,US",
    "paycor stadium": "Cincinnati,US",
    "firstenergy stadium": "Cleveland,US",
    "acrisure stadium": "Pittsburgh,US",
    "m&t bank stadium": "Baltimore,US",
    "nissan stadium": "Nashville,US",
    "efe young stadium": "Los Angeles,US",
    "tiaa bank field": "Jacksonville,US",
    "raymond james stadium": "Tampa,US",
    "hard rock stadium": "Miami Gardens,US",
    "bank of america stadium": "Charlotte,US",
    "lumen field": "Seattle,US",
    # MLB Outdoor (most are outdoor)
    "yankee stadium": "Bronx,US",
    "fenway park": "Boston,US",
    "wrigley field": "Chicago,US",
    "dodger stadium": "Los Angeles,US",
    "oracle park": "San Francisco,US",
    "citi field": "New York,US",
    "petco park": "San Diego,US",
    "kauffman stadium": "Kansas City,US",
    "comerica park": "Detroit,US",
    "great american ball park": "Cincinnati,US",
    "pnc park": "Pittsburgh,US",
    "progressive field": "Cleveland,US",
    "coors field": "Denver,US",
    "citizens bank park": "Philadelphia,US",
    "camden yards": "Baltimore,US",
    "nationals park": "Washington,US",
    "target field": "Minneapolis,US",
    "busch stadium": "St. Louis,US",
    "t-mobile park": "Seattle,US",
    "angel stadium": "Anaheim,US",
    "oakland coliseum": "Oakland,US",
    "guaranteed rate field": "Chicago,US",
    "truist park": "Atlanta,US",
    # Team name -> city fallback
    "chiefs": "Kansas City,US",
    "broncos": "Denver,US",
    "patriots": "Foxborough,US",
    "packers": "Green Bay,US",
    "bills": "Orchard Park,US",
    "bears": "Chicago,US",
    "giants": "East Rutherford,US",
    "jets": "East Rutherford,US",
    "eagles": "Philadelphia,US",
    "49ers": "Santa Clara,US",
    "bengals": "Cincinnati,US",
    "browns": "Cleveland,US",
    "steelers": "Pittsburgh,US",
    "ravens": "Baltimore,US",
    "titans": "Nashville,US",
    "jaguars": "Jacksonville,US",
    "buccaneers": "Tampa,US",
    "dolphins": "Miami Gardens,US",
    "panthers": "Charlotte,US",
    "seahawks": "Seattle,US",
    "yankees": "Bronx,US",
    "red sox": "Boston,US",
    "cubs": "Chicago,US",
    "dodgers": "Los Angeles,US",
    "mets": "New York,US",
    "padres": "San Diego,US",
}


@dataclass
class WeatherImpact:
    city: str
    temperature_f: float
    wind_speed_mph: float
    rain_chance_pct: float
    snow_chance: bool
    description: str
    impact_level: str  # "none", "minor", "moderate", "major"
    impact_summary: str


def _detect_city_from_question(question: str, sport_key: str = "") -> str | None:
    """Try to detect the game city from the market question."""
    q_lower = question.lower()

    for keyword, city in _VENUE_CITIES.items():
        if keyword in q_lower:
            return city

    return None


def get_game_weather(
    question: str,
    sport_key: str = "",
) -> WeatherImpact | None:
    """Get weather forecast for an outdoor sports game.

    Args:
        question: Market question (used to detect city)
        sport_key: Sport key (e.g., "americanfootball_nfl", "baseball_mlb")

    Returns:
        WeatherImpact if applicable outdoor game found, else None.
    """
    if not _API_KEY:
        return None

    # Only for outdoor sports
    outdoor_sports = {"americanfootball_nfl", "baseball_mlb"}
    if sport_key and sport_key not in outdoor_sports:
        return None

    city = _detect_city_from_question(question, sport_key)
    if not city:
        return None

    # Check cache
    cache_key = city
    cached = _cache.get(cache_key)
    if cached and time.time() - cached[1] < _CACHE_TTL:
        return cached[0]

    try:
        resp = requests.get(
            f"{_BASE_URL}/forecast",
            params={"q": city, "appid": _API_KEY, "units": "imperial"},
            timeout=10,
        )
        if resp.status_code != 200:
            log.debug("OpenWeather HTTP %d for %s", resp.status_code, city)
            _cache[cache_key] = (None, time.time())
            return None

        data = resp.json()
        # Use first forecast entry (next 3h window)
        forecasts = data.get("list", [])
        if not forecasts:
            return None

        fc = forecasts[0]
        main = fc.get("main", {})
        wind = fc.get("wind", {})
        weather = fc.get("weather", [{}])[0]
        rain = fc.get("rain", {})
        snow = fc.get("snow", {})

        temp_f = float(main.get("temp", 70))
        wind_mph = float(wind.get("speed", 0))
        rain_3h = float(rain.get("3h", 0))
        snow_3h = float(snow.get("3h", 0))
        description = weather.get("description", "clear")
        rain_chance = float(fc.get("pop", 0)) * 100  # probability of precipitation

    except Exception as e:
        log.debug("OpenWeather fetch failed for %s: %s", city, str(e)[:100])
        _cache[cache_key] = (None, time.time())
        return None

    # Determine impact level
    impact_level = "none"
    impact_parts = []

    if temp_f < 20:
        impact_level = "major"
        impact_parts.append(f"Extreme cold ({temp_f:.0f}F) — affects passing accuracy and grip")
    elif temp_f < 35:
        impact_level = "moderate"
        impact_parts.append(f"Cold ({temp_f:.0f}F) — favors running game")
    elif temp_f > 95:
        impact_level = "moderate"
        impact_parts.append(f"Extreme heat ({temp_f:.0f}F) — fatigue factor")

    if wind_mph > 25:
        impact_level = "major"
        impact_parts.append(f"High winds ({wind_mph:.0f} mph) — affects kicking and deep passes")
    elif wind_mph > 15:
        if impact_level != "major":
            impact_level = "moderate"
        impact_parts.append(f"Gusty ({wind_mph:.0f} mph) — affects field goals")

    if rain_chance > 70 or rain_3h > 2:
        if impact_level == "none":
            impact_level = "moderate"
        impact_parts.append(f"Rain likely ({rain_chance:.0f}%) — slippery conditions")

    if snow_3h > 0:
        impact_level = "major"
        impact_parts.append("Snow — significant game impact")

    if not impact_parts:
        impact_parts.append("Good conditions — no significant weather impact")
        impact_level = "none"

    result = WeatherImpact(
        city=city,
        temperature_f=temp_f,
        wind_speed_mph=wind_mph,
        rain_chance_pct=rain_chance,
        snow_chance=snow_3h > 0,
        description=description,
        impact_level=impact_level,
        impact_summary="; ".join(impact_parts),
    )

    _cache[cache_key] = (result, time.time())

    if impact_level in ("moderate", "major"):
        log.info(
            "[WEATHER] %s: %s | %s",
            city, impact_level.upper(), result.impact_summary,
        )

    return result


def format_weather_for_gpt(weather: WeatherImpact) -> str:
    """Format weather data as context for GPT prompt injection."""
    if weather.impact_level == "none":
        return ""

    return (
        f"\n\nWEATHER IMPACT ({weather.impact_level.upper()}):\n"
        f"Location: {weather.city}\n"
        f"Conditions: {weather.description}, {weather.temperature_f:.0f}F, "
        f"wind {weather.wind_speed_mph:.0f} mph, rain chance {weather.rain_chance_pct:.0f}%\n"
        f"Impact: {weather.impact_summary}\n"
        f"Consider how these conditions affect gameplay and scoring."
    )
