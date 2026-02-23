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
_CACHE_TTL = 120  # V6: 2 minutes (need fresher data for live games)


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
    score: int | None = None  # V6: live game score


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
    # V6: Live game fields
    game_status: str = ""       # "scheduled" | "in_progress" | "final"
    home_score: int | None = None
    away_score: int | None = None
    game_clock: str = ""        # "8:42"
    period: int = 0             # Quarter/half/inning
    is_live: bool = False


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

        # V6: Extract live scores from competitor data
        try:
            ctx.home_team.score = int(competitors[0].get("score", 0)) if competitors[0].get("score") else None
            ctx.away_team.score = int(competitors[1].get("score", 0)) if competitors[1].get("score") else None
            ctx.home_score = ctx.home_team.score
            ctx.away_score = ctx.away_team.score
        except (ValueError, TypeError):
            pass

    # V6: Extract live game status from competition status block
    status = competition.get("status", {})
    status_type = status.get("type", {})
    state = status_type.get("state", "")  # "pre", "in", "post"
    if state == "in":
        ctx.is_live = True
        ctx.game_status = "in_progress"
    elif state == "post":
        ctx.game_status = "final"
    elif state == "pre":
        ctx.game_status = "scheduled"
    ctx.game_clock = status.get("displayClock", "")
    ctx.period = int(status.get("period", 0) or 0)

    return ctx


def is_live_game(question: str, sport_key: str, teams: tuple[str, str] | None = None) -> bool:
    """Check if the market's game is currently live."""
    ctx = get_match_context(question, sport_key, teams)
    return ctx is not None and ctx.is_live


def get_live_games() -> list[dict]:
    """Get all live games across all leagues. Returns list of dicts with scores/clock/period."""
    live = []
    for sport_key, (sport, league) in _LEAGUE_MAP.items():
        events = _fetch_scoreboard(sport, league)
        for event in events:
            competition = event.get("competitions", [{}])[0]
            status = competition.get("status", {})
            state = status.get("type", {}).get("state", "")
            if state != "in":
                continue
            competitors = competition.get("competitors", [])
            home_name = competitors[0].get("team", {}).get("displayName", "") if len(competitors) > 0 else ""
            away_name = competitors[1].get("team", {}).get("displayName", "") if len(competitors) > 1 else ""
            home_score = competitors[0].get("score", "0") if len(competitors) > 0 else "0"
            away_score = competitors[1].get("score", "0") if len(competitors) > 1 else "0"
            live.append({
                "sport_key": sport_key,
                "sport": sport,
                "league": league,
                "headline": event.get("name", ""),
                "home_team": home_name,
                "away_team": away_name,
                "home_score": int(home_score) if home_score else 0,
                "away_score": int(away_score) if away_score else 0,
                "clock": status.get("displayClock", ""),
                "period": int(status.get("period", 0) or 0),
            })
    if live:
        log.info("[ESPN] %d live games across all leagues", len(live))
    return live


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

    # V6: Live game data
    if ctx.is_live:
        score_line = f"LIVE SCORE: {ctx.home_score}-{ctx.away_score}"
        if ctx.game_clock:
            score_line += f" | Clock: {ctx.game_clock}"
        if ctx.period:
            score_line += f" | Period: {ctx.period}"
        lines.append(score_line)
    elif ctx.game_status == "final":
        lines.append(f"FINAL: {ctx.home_score}-{ctx.away_score}")

    return "\n".join(lines)
