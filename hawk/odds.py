"""Sportsbook Odds Integration via The Odds API.

Fetches real bookmaker odds (DraftKings, FanDuel, BetMGM, etc.) and calculates
devigged consensus probability. This replaces GPT-4o's uninformed 50% guesses
with actual sportsbook data — the closest thing to "true probability" in sports.

Free tier: 500 requests/month. Each call returns odds from 10-40 bookmakers.
Quota-aware: circuit breaker stops all API calls once quota is exhausted.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from difflib import SequenceMatcher

from bot.http_session import get_session

log = logging.getLogger(__name__)

# Circuit breaker: stop hitting the API after quota exhaustion
_quota_exhausted: bool = False
_quota_exhausted_at: float = 0.0
_QUOTA_RETRY_HOURS = 6  # Re-check quota every 6 hours (not every call)

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
    "premier league": "soccer_epl",
    "la liga": "soccer_spain_la_liga",
    "serie a": "soccer_italy_serie_a",
    "bundesliga": "soccer_germany_bundesliga",
    "ligue 1": "soccer_france_ligue_one",
    "champions league": "soccer_uefa_champs_league",
    "europa league": "soccer_uefa_europa_league",
    "fa cup": "soccer_fa_cup",
    "copa del rey": "soccer_spain_copa_del_rey",
    "eredivisie": "soccer_netherlands_eredivisie",
    "primeira liga": "soccer_portugal_primeira_liga",
    "ufc": "mma_mixed_martial_arts",
    "mma": "mma_mixed_martial_arts",
    "boxing": "boxing_boxing",
    "afl": "aussierules_afl",
    "nrl": "rugbyleague_nrl",
    "six nations": "rugbyunion_six_nations",
    "euroleague": "basketball_euroleague",
}

# All soccer leagues to scan when we detect a soccer question but can't identify the league
_ALL_SOCCER_KEYS = [
    "soccer_epl", "soccer_spain_la_liga", "soccer_italy_serie_a",
    "soccer_germany_bundesliga", "soccer_france_ligue_one",
    "soccer_uefa_champs_league", "soccer_uefa_europa_league",
    "soccer_uefa_europa_conference_league", "soccer_fa_cup",
    "soccer_efl_champ", "soccer_netherlands_eredivisie",
    "soccer_portugal_primeira_liga", "soccer_turkey_super_league",
    "soccer_spain_segunda_division", "soccer_italy_serie_b",
    "soccer_usa_mls", "soccer_mexico_ligamx", "soccer_brazil_campeonato",
    "soccer_argentina_primera_division", "soccer_spain_copa_del_rey",
    "soccer_australia_aleague", "soccer_spl",
]

# Cache: {sport_key: (timestamp, events_list)}
_cache: dict[str, tuple[float, list[dict]]] = {}
_CACHE_TTL = 300  # 5 minutes — matches Hawk cycle


@dataclass
class SportsbookConsensus:
    """Result of sportsbook consensus calculation."""
    home_team: str
    away_team: str
    sport_key: str
    consensus_home_prob: float  # Devigged probability for home team
    consensus_away_prob: float  # Devigged probability for away team
    consensus_draw_prob: float = 0.0  # Devigged draw probability (soccer 3-way)
    num_books: int = 0  # How many sportsbooks contributed
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

    # Soccer: "draw" + "vs" is a strong soccer signal
    if "draw" in q and re.search(r"\bvs\.?\b", q):
        return "_soccer_scan"  # Special key: scan all soccer leagues

    # Soccer: "FC" or "CF" or "United" with "vs" (European football clubs)
    if re.search(r"\bvs\.?\b", q) and re.search(r"\b(fc|cf|united|city|athletic|real|sporting|racing)\b", q):
        return "_soccer_scan"

    # Ice hockey
    if "ice hockey" in q or "hockey" in q:
        return "icehockey_nhl"

    # Tennis
    if "tennis" in q or "atp" in q or "wta" in q:
        return None  # Tennis matching is too complex for now

    # Generic sports with "vs." + spread/o/u — default to NCAAB (most common)
    if re.search(r"\bvs\.?\b", q) and ("spread" in q or "o/u" in q):
        return "basketball_ncaab"

    return None


def _extract_teams(question: str) -> tuple[str, str] | None:
    """Extract team names from a Polymarket market question.

    Handles formats:
    - "Team A vs. Team B"
    - "Team A vs. Team B: O/U 157.5"
    - "Spread: Team A (-3.5)"
    - "Will Team A vs. Team B end in a draw?"
    - "Will Team A beat Team B?"
    - "Will Team A win ... gold medal?"
    """
    # "Will Team A vs. Team B end in a draw?" pattern
    draw_match = re.match(
        r"^Will\s+(.+?)\s+vs\.?\s+(.+?)\s+end\s+in\s+a\s+draw",
        question, re.IGNORECASE,
    )
    if draw_match:
        return draw_match.group(1).strip(), draw_match.group(2).strip()

    # "Team A vs. Team B" pattern (with optional O/U suffix)
    vs_match = re.match(
        r"^(?:Will\s+)?(.+?)\s+vs\.?\s+(.+?)(?:\s*:\s*O/U|\s*[-—]|\s+end\b|\s*$)",
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

    # "Will Team A win/beat ...?" — single team extraction
    win_match = re.match(
        r"^Will\s+(.+?)\s+(win|beat|defeat)\b",
        question, re.IGNORECASE,
    )
    if win_match:
        return win_match.group(1).strip(), ""

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


def is_quota_exhausted() -> bool:
    """Check if the Odds API quota circuit breaker is tripped."""
    global _quota_exhausted, _quota_exhausted_at
    if not _quota_exhausted:
        return False
    # Auto-retry after _QUOTA_RETRY_HOURS
    if time.time() - _quota_exhausted_at > _QUOTA_RETRY_HOURS * 3600:
        log.info("Odds API: circuit breaker reset — retrying after %dh cooldown", _QUOTA_RETRY_HOURS)
        _quota_exhausted = False
        return False
    return True


def fetch_odds(api_key: str, sport_key: str) -> list[dict]:
    """Fetch odds from The Odds API for a given sport.

    Returns list of events with bookmaker odds. Uses 15-minute cache
    (aligned with Hawk cycle) and circuit breaker for quota exhaustion.
    """
    global _quota_exhausted, _quota_exhausted_at

    if not api_key:
        return []

    # Circuit breaker — don't hit the API if quota is exhausted
    if is_quota_exhausted():
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
            # Distinguish invalid key vs quota exhausted
            try:
                body = resp.json()
                error_code = body.get("error_code", "")
            except Exception:
                error_code = ""

            if error_code == "OUT_OF_USAGE_CREDITS":
                _quota_exhausted = True
                _quota_exhausted_at = time.time()
                log.warning(
                    "Odds API: QUOTA EXHAUSTED — monthly usage limit reached. "
                    "Circuit breaker ON, will retry in %dh. "
                    "Upgrade at https://the-odds-api.com to get more requests.",
                    _QUOTA_RETRY_HOURS,
                )
            else:
                log.warning("Odds API: invalid API key (error_code=%s)", error_code or "none")
            return []

        if resp.status_code == 429:
            log.warning("Odds API: rate limited (429) — backing off")
            return []

        if resp.status_code != 200:
            log.warning("Odds API: unexpected %d for %s", resp.status_code, sport_key)
            return []

        events = resp.json()
        _cache[sport_key] = (time.time(), events)

        # Log remaining quota from headers
        remaining = resp.headers.get("x-requests-remaining", "?")
        used = resp.headers.get("x-requests-used", "?")
        log.info("Odds API: %d events for %s (quota: %s used, %s remaining)",
                 len(events), sport_key, used, remaining)

        # Proactive warning when quota is running low
        try:
            rem_int = int(remaining)
            if rem_int <= 20:
                log.warning("Odds API: LOW QUOTA — only %d requests remaining this month!", rem_int)
        except (ValueError, TypeError):
            pass

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
    draw_probs = []
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
                    # 3-way market (soccer) — includes Draw
                    if "Draw" in outcomes:
                        d_prob = _american_to_prob(outcomes["Draw"])
                        devigged = _devig_probs([h_prob, d_prob, a_prob])
                        home_probs.append(devigged[0])
                        draw_probs.append(devigged[1])
                        away_probs.append(devigged[2])
                    else:
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
        consensus_draw_prob=sum(draw_probs) / len(draw_probs) if draw_probs else 0.0,
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

    # Fetch odds — for soccer scan, search all cached soccer leagues
    event = None
    confidence = 0.0
    if sport_key == "_soccer_scan":
        best_event = None
        best_conf = 0.0
        for sk in _ALL_SOCCER_KEYS:
            if sk in _cache:
                _, cached_events = _cache[sk]
                ev, conf = _match_event(question, teams, cached_events)
                if ev and conf > best_conf:
                    best_event = ev
                    best_conf = conf
        event, confidence = best_event, best_conf
    else:
        events = fetch_odds(api_key, sport_key)
        if not events:
            return None, None
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
        elif "draw" in q_lower:
            market_type = "draw"
        else:
            market_type = "h2h"

    if market_type == "draw":
        if consensus.consensus_draw_prob > 0:
            prob = consensus.consensus_draw_prob
            log.info("Sportsbook draw: %s vs %s → draw_prob=%.2f (%d books, conf=%.2f)",
                     consensus.home_team, consensus.away_team, prob, consensus.num_books, confidence)
            return prob, consensus
        return None, None

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
            # Extract the specific O/U line from the question (e.g., "O/U 1.5" → 1.5)
            line_match = re.search(r"o/u\s+([\d.]+)", question, re.IGNORECASE)
            asked_line = float(line_match.group(1)) if line_match else None
            sb_line = consensus.total_line or 0

            if asked_line is not None and sb_line > 0 and abs(asked_line - sb_line) > 0.5:
                # The question asks about a DIFFERENT line than what the sportsbook has
                # e.g., question is O/U 1.5 but sportsbook only has O/U 2.5 data
                # Using the wrong line's probability is DANGEROUS — reject entirely
                log.warning(
                    "Sportsbook line MISMATCH: question asks O/U %.1f but sportsbook has O/U %.1f — "
                    "REJECTING to avoid phantom edge | %s",
                    asked_line, sb_line, question[:60],
                )
                return None, None

            prob = consensus.over_prob
            log.info("Sportsbook total: %.1f → over_prob=%.2f (%d books, conf=%.2f)",
                     sb_line, prob, consensus.num_books, confidence)
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


def prefetch_sports(api_key: str, questions: list[str]) -> int:
    """Pre-warm the cache for all unique sports detected in a batch of questions.

    Call this ONCE before parallel GPT analysis to avoid race conditions
    and redundant API calls. Returns number of sports fetched.

    This is the key quota-saver: instead of N parallel threads each triggering
    a fetch_odds() call (cache miss race), we do 1 call per unique sport upfront.
    """
    if not api_key or is_quota_exhausted():
        return 0

    # Deduplicate sports across all questions
    sport_keys: set[str] = set()
    need_soccer_scan = False
    for q in questions:
        sk = _detect_sport(q)
        if sk == "_soccer_scan":
            need_soccer_scan = True
        elif sk:
            sport_keys.add(sk)

    # Expand soccer scan into all major leagues
    if need_soccer_scan:
        sport_keys.update(_ALL_SOCCER_KEYS)

    if not sport_keys:
        return 0

    fetched = 0
    for sk in sport_keys:
        # Skip if already cached
        if sk in _cache:
            cached_time, _ = _cache[sk]
            if time.time() - cached_time < _CACHE_TTL:
                fetched += 1  # Count cached as fetched
                continue
        events = fetch_odds(api_key, sk)
        if events:
            fetched += 1
        # Stop immediately if quota got exhausted during prefetch
        if is_quota_exhausted():
            log.warning("Odds API: quota exhausted during prefetch — stopping early (%d/%d sports fetched)",
                        fetched, len(sport_keys))
            break

    log.info("Odds API prefetch: %d/%d sports loaded into cache", fetched, len(sport_keys))
    return fetched
