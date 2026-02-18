"""Sportsbook Odds Integration via The Odds API.

Fetches real bookmaker odds (DraftKings, FanDuel, BetMGM, etc.) and calculates
devigged consensus probability. This replaces GPT-4o's uninformed 50% guesses
with actual sportsbook data — the closest thing to "true probability" in sports.

Free tier: 500 requests/month. Each call returns odds from 10-40 bookmakers.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from difflib import SequenceMatcher

from bot.http_session import get_session

log = logging.getLogger(__name__)

# The Odds API base URL
_BASE_URL = "https://api.the-odds-api.com/v4"

# Sport keys for The Odds API
_SPORT_KEYS = {
    "ncaab": "basketball_ncaab",
    "college basketball": "basketball_ncaab",
    "nba": "basketball_nba",
    "nfl": "americanfootball_nfl",
    "ncaaf": "americanfootball_ncaaf",
    "college football": "americanfootball_ncaaf",
    "mlb": "baseball_mlb",
    "nhl": "icehockey_nhl",
    "mls": "soccer_usa_mls",
    "epl": "soccer_epl",
    "ufc": "mma_mixed_martial_arts",
    "mma": "mma_mixed_martial_arts",
}

# Cache: {sport_key: (timestamp, events_list)}
_cache: dict[str, tuple[float, list[dict]]] = {}
_CACHE_TTL = 300  # 5 minutes


@dataclass
class SportsbookConsensus:
    """Result of sportsbook consensus calculation."""
    home_team: str
    away_team: str
    sport_key: str
    consensus_home_prob: float  # Devigged probability for home team
    consensus_away_prob: float  # Devigged probability for away team
    num_books: int  # How many sportsbooks contributed
    spread_home: float | None = None  # Home spread (e.g., -3.5)
    spread_home_prob: float | None = None  # Probability of covering spread
    total_line: float | None = None  # O/U line (e.g., 157.5)
    over_prob: float | None = None  # Probability of going over
    match_confidence: float = 0.0  # How well the market matched (0-1)
    raw_odds: list[dict] | None = None


def _american_to_prob(odds: int) -> float:
    """Convert American odds to implied probability."""
    if odds < 0:
        return abs(odds) / (abs(odds) + 100)
    else:
        return 100 / (odds + 100)


def _devig_probs(probs: list[float]) -> list[float]:
    """Remove vig/overround from a set of implied probabilities.

    Normalizes so they sum to 1.0 (the "true" probabilities).
    """
    total = sum(probs)
    if total <= 0:
        return probs
    return [p / total for p in probs]


def _detect_sport(question: str) -> str | None:
    """Detect which sport/league a Polymarket question refers to."""
    q = question.lower()

    # Check for explicit league mentions
    for keyword, sport_key in _SPORT_KEYS.items():
        if keyword in q:
            return sport_key

    # College basketball patterns (most common on Polymarket)
    college_bb_patterns = [
        r"(tar heels|wolfpack|gators|cardinals|wildcats|bulldogs|"
        r"wolverines|boilermakers|hawkeyes|badgers|buckeyes|"
        r"hoosiers|spartans|fighting irish|blue devils|"
        r"seminoles|yellow jackets|crimson tide|tigers|"
        r"redhawks|minutemen|golden flashes|hokies|hurricanes|"
        r"mustangs|red raiders|sun devils|billikens|bulls|"
        r"revolutionaries|rams|falcons|cowboys|razorbacks|"
        r"volunteers|commodores|gamecocks|aggies|"
        r"jayhawks|longhorns|sooners|mountaineers|cyclones|"
        r"ducks|beavers|huskies|cougars|bruins|trojans|"
        r"terrapins|nittany lions|scarlet knights|"
        r"demon deacons|cavaliers|orange)",
    ]
    for pattern in college_bb_patterns:
        if re.search(pattern, q):
            return "basketball_ncaab"

    # NBA team patterns
    nba_teams = [
        "lakers", "celtics", "warriors", "nets", "knicks", "heat",
        "bucks", "suns", "clippers", "mavericks", "nuggets", "76ers",
        "grizzlies", "pelicans", "rockets", "timberwolves", "thunder",
        "trail blazers", "kings", "spurs", "raptors", "jazz", "wizards",
        "pistons", "hornets", "magic", "pacers",
    ]
    if any(t in q for t in nba_teams):
        return "basketball_nba"

    # NFL team patterns
    nfl_teams = [
        "ravens", "steelers", "bengals", "browns", "bills", "dolphins",
        "patriots", "jets", "commanders", "giants", "saints", "buccaneers",
        "49ers", "seahawks", "chargers", "raiders", "broncos", "texans",
        "colts", "jaguars", "titans", "vikings", "packers", "chiefs",
        "eagles", "panthers", "bears", "lions",
    ]
    if any(t in q for t in nfl_teams):
        return "americanfootball_nfl"

    # Generic sports with "vs." — default to NCAAB (most common)
    if re.search(r"\bvs\.?\b", q) and ("spread" in q or "o/u" in q):
        return "basketball_ncaab"

    return None


def _extract_teams(question: str) -> tuple[str, str] | None:
    """Extract team names from a Polymarket market question.

    Handles formats:
    - "Team A vs. Team B"
    - "Team A vs. Team B: O/U 157.5"
    - "Spread: Team A (-3.5)"
    """
    # "Team A vs. Team B" pattern
    vs_match = re.match(
        r"^(.+?)\s+vs\.?\s+(.+?)(?:\s*:\s*O/U|\s*$)",
        question, re.IGNORECASE,
    )
    if vs_match:
        return vs_match.group(1).strip(), vs_match.group(2).strip()

    # "Spread: Team A (-X.X)" pattern
    spread_match = re.match(
        r"^Spread:\s+(.+?)\s+\(-?\d+\.?\d*\)$",
        question, re.IGNORECASE,
    )
    if spread_match:
        return spread_match.group(1).strip(), ""

    return None


def _match_event(
    question: str,
    teams: tuple[str, str],
    events: list[dict],
) -> tuple[dict | None, float]:
    """Find the best matching sportsbook event for a Polymarket question.

    Returns (event_dict, match_confidence).
    """
    best_match = None
    best_score = 0.0

    team_a, team_b = teams

    for event in events:
        home = event.get("home_team", "")
        away = event.get("away_team", "")

        # Score based on team name similarity
        score_a_home = SequenceMatcher(None, team_a.lower(), home.lower()).ratio()
        score_a_away = SequenceMatcher(None, team_a.lower(), away.lower()).ratio()
        score_b_home = SequenceMatcher(None, team_b.lower(), home.lower()).ratio() if team_b else 0
        score_b_away = SequenceMatcher(None, team_b.lower(), away.lower()).ratio() if team_b else 0

        # Best matching configuration
        if team_b:
            match_score = max(
                (score_a_home + score_b_away) / 2,
                (score_a_away + score_b_home) / 2,
            )
        else:
            match_score = max(score_a_home, score_a_away)

        if match_score > best_score:
            best_score = match_score
            best_match = event

    if best_score < 0.4:  # Too low — probably wrong event
        return None, 0.0

    return best_match, best_score


def fetch_odds(api_key: str, sport_key: str) -> list[dict]:
    """Fetch odds from The Odds API for a given sport.

    Returns list of events with bookmaker odds. Uses 5-minute cache.
    """
    if not api_key:
        return []

    # Check cache
    if sport_key in _cache:
        cached_time, cached_data = _cache[sport_key]
        if time.time() - cached_time < _CACHE_TTL:
            return cached_data

    session = get_session()
    try:
        resp = session.get(
            f"{_BASE_URL}/sports/{sport_key}/odds",
            params={
                "apiKey": api_key,
                "regions": "us",
                "markets": "h2h,spreads,totals",
                "oddsFormat": "american",
            },
            timeout=10,
        )
        if resp.status_code == 401:
            log.warning("The Odds API: invalid API key")
            return []
        if resp.status_code == 429:
            log.warning("The Odds API: rate limited (monthly quota reached)")
            return []
        if resp.status_code != 200:
            log.warning("The Odds API returned %d for %s", resp.status_code, sport_key)
            return []

        events = resp.json()
        _cache[sport_key] = (time.time(), events)

        # Log remaining quota from headers
        remaining = resp.headers.get("x-requests-remaining", "?")
        used = resp.headers.get("x-requests-used", "?")
        log.info("Odds API: %d events for %s (used %s/%s quota)", len(events), sport_key, used, remaining)

        return events

    except Exception:
        log.exception("Failed to fetch odds for %s", sport_key)
        return []


def calculate_consensus(event: dict) -> SportsbookConsensus | None:
    """Calculate devigged consensus probability from all bookmakers for an event.

    Averages implied probabilities across all bookmakers, then removes vig.
    """
    bookmakers = event.get("bookmakers", [])
    if not bookmakers:
        return None

    home = event.get("home_team", "")
    away = event.get("away_team", "")
    sport_key = event.get("sport_key", "")

    # Collect h2h (moneyline) probabilities
    home_probs = []
    away_probs = []
    spread_probs = []
    spread_lines = []
    total_over_probs = []
    total_lines = []

    for book in bookmakers:
        for market in book.get("markets", []):
            key = market.get("key", "")

            if key == "h2h":
                outcomes = {o["name"]: o["price"] for o in market.get("outcomes", [])}
                if home in outcomes and away in outcomes:
                    h_prob = _american_to_prob(outcomes[home])
                    a_prob = _american_to_prob(outcomes[away])
                    devigged = _devig_probs([h_prob, a_prob])
                    home_probs.append(devigged[0])
                    away_probs.append(devigged[1])

            elif key == "spreads":
                for o in market.get("outcomes", []):
                    if o.get("name") == home:
                        spread_lines.append(o.get("point", 0))
                        spread_probs.append(_american_to_prob(o["price"]))

            elif key == "totals":
                for o in market.get("outcomes", []):
                    if o.get("name") == "Over":
                        total_lines.append(o.get("point", 0))
                        total_over_probs.append(_american_to_prob(o["price"]))

    if not home_probs:
        return None

    consensus = SportsbookConsensus(
        home_team=home,
        away_team=away,
        sport_key=sport_key,
        consensus_home_prob=sum(home_probs) / len(home_probs),
        consensus_away_prob=sum(away_probs) / len(away_probs),
        num_books=len(home_probs),
    )

    if spread_probs:
        consensus.spread_home = sum(spread_lines) / len(spread_lines)
        consensus.spread_home_prob = sum(spread_probs) / len(spread_probs)

    if total_over_probs:
        consensus.total_line = sum(total_lines) / len(total_lines)
        consensus.over_prob = sum(total_over_probs) / len(total_over_probs)

    return consensus


def get_sportsbook_probability(
    api_key: str,
    question: str,
    market_type: str = "auto",
) -> tuple[float | None, SportsbookConsensus | None]:
    """Main entry point: get sportsbook consensus probability for a Polymarket market.

    Args:
        api_key: The Odds API key
        question: Polymarket market question (e.g., "Spread: Florida Gators (-23.5)")
        market_type: "h2h", "spread", "total", or "auto" (detect from question)

    Returns:
        (probability, consensus) or (None, None) if no match found.
        probability is the sportsbook-implied probability of YES outcome.
    """
    if not api_key:
        return None, None

    # Detect sport
    sport_key = _detect_sport(question)
    if not sport_key:
        log.debug("Could not detect sport for: %s", question[:50])
        return None, None

    # Extract teams
    teams = _extract_teams(question)
    if not teams:
        log.debug("Could not extract teams from: %s", question[:50])
        return None, None

    # Fetch odds
    events = fetch_odds(api_key, sport_key)
    if not events:
        return None, None

    # Match event
    event, confidence = _match_event(question, teams, events)
    if not event:
        log.debug("No sportsbook match for: %s (best conf: %.2f)", question[:50], confidence)
        return None, None

    # Calculate consensus
    consensus = calculate_consensus(event)
    if not consensus:
        return None, None

    consensus.match_confidence = confidence

    # Determine which probability to return based on market type
    if market_type == "auto":
        q_lower = question.lower()
        if "spread:" in q_lower:
            market_type = "spread"
        elif "o/u " in q_lower or "over/under" in q_lower:
            market_type = "total"
        else:
            market_type = "h2h"

    if market_type == "spread":
        # Extract the spread value from the question
        spread_match = re.search(r"\((-?\d+\.?\d*)\)", question)
        if spread_match and consensus.spread_home_prob is not None:
            asked_spread = float(spread_match.group(1))
            # If the asked spread matches the sportsbook spread, use the probability
            if consensus.spread_home is not None and abs(asked_spread - consensus.spread_home) < 2:
                prob = consensus.spread_home_prob
                log.info("Sportsbook spread: %s %.1f → prob=%.2f (%d books, conf=%.2f)",
                         consensus.home_team, consensus.spread_home, prob, consensus.num_books, confidence)
                return prob, consensus
        # Fallback to h2h
        prob = consensus.consensus_home_prob
    elif market_type == "total":
        if consensus.over_prob is not None:
            prob = consensus.over_prob
            log.info("Sportsbook total: %.1f → over_prob=%.2f (%d books, conf=%.2f)",
                     consensus.total_line or 0, prob, consensus.num_books, confidence)
            return prob, consensus
        return None, None
    else:
        # h2h: determine which team is the "YES" outcome
        team_a = teams[0].lower()
        if SequenceMatcher(None, team_a, consensus.home_team.lower()).ratio() > 0.5:
            prob = consensus.consensus_home_prob
        else:
            prob = consensus.consensus_away_prob

    log.info("Sportsbook h2h: %s vs %s → prob=%.2f (%d books, conf=%.2f)",
             consensus.home_team, consensus.away_team, prob, consensus.num_books, confidence)
    return prob, consensus
