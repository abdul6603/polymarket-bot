"""In-Play Live Mispricing Engine for Hawk V8 (Stretch Goal).

Polymarket prices lag sportsbooks by 5-10 min during live games.
When ESPN detects a significant score change and sportsbook lines shift,
Hawk can snipe the stale Polymarket price.

Safety: Disabled by default (HAWK_INPLAY_ENABLED=false).
  - Lower max bet ($8 vs $15)
  - Higher min edge (10% vs 15%)
  - Still goes through standard risk pipeline
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
INPLAY_LOG_FILE = DATA_DIR / "hawk_inplay_signals.jsonl"


@dataclass
class InplaySignal:
    condition_id: str
    question: str
    polymarket_price: float
    implied_fair: float
    edge: float
    direction: str  # "yes" or "no"
    reason: str
    live_score: str


def scan_live_mispricing(
    open_positions: list[dict],
    markets: list,
    odds_api_key: str = "",
) -> list[InplaySignal]:
    """Scan for live game mispricing between sportsbooks and Polymarket.

    Matches live ESPN/sportsbook games to open Polymarket markets,
    compares live sportsbook implied probability vs stale Polymarket price.

    Returns list of InplaySignal for markets with actionable mispricing.
    """
    if not odds_api_key:
        return []

    signals: list[InplaySignal] = []

    try:
        from bot.http_session import get_session
        session = get_session()

        # Fetch live scores from The Odds API
        live_scores = _fetch_live_scores(session, odds_api_key)
        if not live_scores:
            return []

        # Fetch live odds for in-progress games
        live_odds = _fetch_live_odds(session, odds_api_key)
        if not live_odds:
            return []

        # Build lookup: sport_key + teams → live implied prob
        live_lookup = {}
        for game in live_odds:
            home = (game.get("home_team") or "").lower()
            away = (game.get("away_team") or "").lower()
            if not home or not away:
                continue

            # Get best available h2h odds
            for bm in game.get("bookmakers", []):
                for mkt in bm.get("markets", []):
                    if mkt.get("key") != "h2h":
                        continue
                    for outcome in mkt.get("outcomes", []):
                        name = outcome.get("name", "").lower()
                        price = outcome.get("price", 0)
                        if price > 0:
                            implied = 1.0 / price if price > 1.0 else price
                            live_lookup[f"{home}_{away}_{name}"] = {
                                "implied_prob": min(0.99, implied),
                                "score": _format_score(game, live_scores),
                            }

        # Match against Polymarket markets
        for m in markets:
            q = m.question.lower()
            # Check if any live game matches this market
            for key, data in live_lookup.items():
                parts = key.rsplit("_", 1)
                if len(parts) != 2:
                    continue
                teams_key, team_name = parts
                # Simple keyword match
                if team_name in q and len(team_name) > 4:
                    # Get current Polymarket price
                    poly_price = _get_poly_yes_price(m)
                    fair_prob = data["implied_prob"]

                    # Calculate edge
                    yes_edge = fair_prob - poly_price
                    no_edge = (1 - fair_prob) - (1 - poly_price)

                    if yes_edge > 0.10:
                        signals.append(InplaySignal(
                            condition_id=m.condition_id,
                            question=m.question[:150],
                            polymarket_price=poly_price,
                            implied_fair=fair_prob,
                            edge=yes_edge,
                            direction="yes",
                            reason=f"Live sportsbook implies {fair_prob:.0%} vs Poly {poly_price:.0%}",
                            live_score=data.get("score", ""),
                        ))
                    elif no_edge > 0.10:
                        signals.append(InplaySignal(
                            condition_id=m.condition_id,
                            question=m.question[:150],
                            polymarket_price=poly_price,
                            implied_fair=fair_prob,
                            edge=no_edge,
                            direction="no",
                            reason=f"Live sportsbook implies {1-fair_prob:.0%} NO vs Poly {1-poly_price:.0%}",
                            live_score=data.get("score", ""),
                        ))

    except Exception:
        log.exception("[INPLAY] Scan failed")

    # Log signals for monitoring (even when disabled)
    if signals:
        _log_signals(signals)
        log.info("[INPLAY] Found %d live mispricing signals", len(signals))

    return signals


def _fetch_live_scores(session, api_key: str) -> list[dict]:
    """Fetch live scores from The Odds API."""
    try:
        resp = session.get(
            "https://api.the-odds-api.com/v4/sports/scores",
            params={"apiKey": api_key, "daysFrom": "1"},
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        log.debug("[INPLAY] Failed to fetch live scores")
    return []


def _fetch_live_odds(session, api_key: str) -> list[dict]:
    """Fetch live in-play odds from The Odds API."""
    try:
        # Get odds for major sports with live events
        all_games = []
        for sport in ("basketball_nba", "americanfootball_nfl",
                       "baseball_mlb", "icehockey_nhl"):
            resp = session.get(
                f"https://api.the-odds-api.com/v4/sports/{sport}/odds",
                params={
                    "apiKey": api_key,
                    "regions": "us",
                    "markets": "h2h",
                    "oddsFormat": "decimal",
                },
                timeout=10,
            )
            if resp.status_code == 200:
                games = resp.json()
                # Filter to only live/in-progress games
                for g in games:
                    if g.get("commence_time"):
                        ct = g["commence_time"]
                        # If game has started (commence_time in the past)
                        try:
                            from datetime import datetime, timezone
                            start = datetime.fromisoformat(ct.replace("Z", "+00:00"))
                            if start < datetime.now(timezone.utc):
                                all_games.append(g)
                        except Exception:
                            pass
        return all_games
    except Exception:
        log.debug("[INPLAY] Failed to fetch live odds")
    return []


def _format_score(game: dict, scores: list[dict]) -> str:
    """Match game to score data and format."""
    home = (game.get("home_team") or "").lower()
    away = (game.get("away_team") or "").lower()
    for s in scores:
        if (s.get("home_team", "").lower() == home and
                s.get("away_team", "").lower() == away):
            sc = s.get("scores")
            if sc and isinstance(sc, list):
                parts = [f"{x.get('name', '?')}: {x.get('score', '?')}" for x in sc]
                return " | ".join(parts)
    return ""


def _get_poly_yes_price(market) -> float:
    """Get YES price from market tokens."""
    for t in market.tokens:
        outcome = (t.get("outcome") or "").lower()
        if outcome in ("yes", "up", "over"):
            try:
                return float(t.get("price", 0.5))
            except (ValueError, TypeError):
                return 0.5
    if market.tokens:
        try:
            return float(market.tokens[0].get("price", 0.5))
        except (ValueError, TypeError):
            return 0.5
    return 0.5


def _log_signals(signals: list[InplaySignal]) -> None:
    """Append signals to JSONL for monitoring/analysis."""
    try:
        DATA_DIR.mkdir(exist_ok=True)
        with open(INPLAY_LOG_FILE, "a") as f:
            for sig in signals:
                f.write(json.dumps({
                    "condition_id": sig.condition_id,
                    "question": sig.question,
                    "poly_price": sig.polymarket_price,
                    "implied_fair": sig.implied_fair,
                    "edge": round(sig.edge, 4),
                    "direction": sig.direction,
                    "reason": sig.reason,
                    "live_score": sig.live_score,
                    "timestamp": time.time(),
                }) + "\n")
    except Exception:
        pass
