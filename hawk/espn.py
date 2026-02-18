"""ESPN Data Enrichment — free team stats, injuries, standings.

Uses ESPN's undocumented public API (no key required).
Provides the contextual data GPT-4o needs to make informed predictions
instead of guessing 50/50.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from bot.http_session import get_session

log = logging.getLogger(__name__)

# ESPN API base URLs
_SITE_API = "https://site.api.espn.com/apis/site/v2/sports"

# League mappings for ESPN API path
_LEAGUE_MAP = {
    "basketball_ncaab": ("basketball", "mens-college-basketball"),
    "basketball_nba": ("basketball", "nba"),
    "americanfootball_nfl": ("football", "nfl"),
    "americanfootball_ncaaf": ("football", "college-football"),
    "baseball_mlb": ("baseball", "mlb"),
    "icehockey_nhl": ("hockey", "nhl"),
    "soccer_epl": ("soccer", "eng.1"),
    "soccer_usa_mls": ("soccer", "usa.1"),
}

# Cache: {cache_key: (timestamp, data)}
_cache: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 600  # 10 minutes


@dataclass
class TeamInfo:
    """Team data from ESPN."""
    name: str
    abbreviation: str = ""
    record: str = ""  # e.g., "22-5"
    conference_record: str = ""
    standing: str = ""  # e.g., "3rd in ACC"
    injuries: list[str] = field(default_factory=list)  # Key injuries
    recent_games: list[str] = field(default_factory=list)  # Last 5 results


@dataclass
class MatchContext:
    """Full match context for GPT enrichment."""
    home_team: TeamInfo | None = None
    away_team: TeamInfo | None = None
    venue: str = ""
    game_time: str = ""
    headline: str = ""
    sport: str = ""
    league: str = ""


def _get_cached(key: str) -> dict | None:
    if key in _cache:
        ts, data = _cache[key]
        if time.time() - ts < _CACHE_TTL:
            return data
    return None


def _fetch_scoreboard(sport: str, league: str) -> list[dict]:
    """Fetch today's scoreboard to find specific games."""
    cache_key = f"scoreboard_{sport}_{league}"
    cached = _get_cached(cache_key)
    if cached:
        return cached.get("events", [])

    session = get_session()
    try:
        resp = session.get(
            f"{_SITE_API}/{sport}/{league}/scoreboard",
            timeout=10,
        )
        if resp.status_code != 200:
            log.debug("ESPN scoreboard %d for %s/%s", resp.status_code, sport, league)
            return []

        data = resp.json()
        _cache[cache_key] = (time.time(), data)
        return data.get("events", [])

    except Exception:
        log.debug("ESPN scoreboard fetch failed for %s/%s", sport, league)
        return []


def _extract_team_info(competitor: dict) -> TeamInfo:
    """Extract team info from an ESPN competitor object."""
    team = competitor.get("team", {})
    records = competitor.get("records", [])

    record = ""
    conf_record = ""
    for rec in records:
        if rec.get("type") == "total":
            record = rec.get("summary", "")
        elif rec.get("type") == "vsconf":
            conf_record = rec.get("summary", "")

    return TeamInfo(
        name=team.get("displayName", team.get("name", "")),
        abbreviation=team.get("abbreviation", ""),
        record=record,
        conference_record=conf_record,
    )


def _match_game(
    question: str,
    teams: tuple[str, str],
    events: list[dict],
) -> dict | None:
    """Find the best matching ESPN event for a Polymarket question."""
    team_a, team_b = teams
    best_match = None
    best_score = 0.0

    for event in events:
        competitors = event.get("competitions", [{}])[0].get("competitors", [])
        if len(competitors) < 2:
            continue

        home_name = competitors[0].get("team", {}).get("displayName", "")
        away_name = competitors[1].get("team", {}).get("displayName", "")

        score_a_home = SequenceMatcher(None, team_a.lower(), home_name.lower()).ratio()
        score_a_away = SequenceMatcher(None, team_a.lower(), away_name.lower()).ratio()

        if team_b:
            score_b_home = SequenceMatcher(None, team_b.lower(), home_name.lower()).ratio()
            score_b_away = SequenceMatcher(None, team_b.lower(), away_name.lower()).ratio()
            match_score = max(
                (score_a_home + score_b_away) / 2,
                (score_a_away + score_b_home) / 2,
            )
        else:
            match_score = max(score_a_home, score_a_away)

        if match_score > best_score:
            best_score = match_score
            best_match = event

    if best_score < 0.4:
        return None
    return best_match


def get_match_context(
    question: str,
    sport_key: str,
    teams: tuple[str, str] | None = None,
) -> MatchContext | None:
    """Get full match context from ESPN for a Polymarket sports market.

    Args:
        question: Polymarket market question
        sport_key: The Odds API sport key (e.g., "basketball_ncaab")
        teams: Pre-extracted team names tuple, or None to skip matching

    Returns:
        MatchContext with team records, injuries, venue, or None.
    """
    league_info = _LEAGUE_MAP.get(sport_key)
    if not league_info:
        return None

    sport, league = league_info

    # Fetch scoreboard
    events = _fetch_scoreboard(sport, league)
    if not events:
        return None

    if not teams:
        return None

    # Match the game
    event = _match_game(question, teams, events)
    if not event:
        return None

    # Extract context
    competition = event.get("competitions", [{}])[0]
    competitors = competition.get("competitors", [])

    ctx = MatchContext(
        sport=sport,
        league=league,
        venue=competition.get("venue", {}).get("fullName", ""),
        game_time=event.get("date", ""),
        headline=event.get("name", ""),
    )

    if len(competitors) >= 2:
        # ESPN: index 0 = home, index 1 = away
        ctx.home_team = _extract_team_info(competitors[0])
        ctx.away_team = _extract_team_info(competitors[1])

    return ctx


def format_context_for_gpt(ctx: MatchContext) -> str:
    """Format ESPN match context as text for GPT-4o prompt injection."""
    if not ctx:
        return ""

    lines = ["\n\nGAME DATA (ESPN):"]

    if ctx.headline:
        lines.append(f"Matchup: {ctx.headline}")

    if ctx.home_team:
        h = ctx.home_team
        parts = [f"HOME: {h.name}"]
        if h.record:
            parts.append(f"Record: {h.record}")
        if h.conference_record:
            parts.append(f"Conference: {h.conference_record}")
        if h.injuries:
            parts.append(f"Injuries: {', '.join(h.injuries[:3])}")
        lines.append(" | ".join(parts))

    if ctx.away_team:
        a = ctx.away_team
        parts = [f"AWAY: {a.name}"]
        if a.record:
            parts.append(f"Record: {a.record}")
        if a.conference_record:
            parts.append(f"Conference: {a.conference_record}")
        if a.injuries:
            parts.append(f"Injuries: {', '.join(a.injuries[:3])}")
        lines.append(" | ".join(parts))

    if ctx.venue:
        lines.append(f"Venue: {ctx.venue}")

    return "\n".join(lines)
