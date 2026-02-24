"""Hawk V7 Probability Analyst — Data-First Intelligence.

Architecture:
  Sports markets  → Sportsbook consensus (The Odds API) + ESPN live scores
  Weather markets → NOAA/Open-Meteo ensemble (82 models)
  Non-sports      → Cross-platform data (Kalshi, Metaculus, PredictIt)

All paths are $0 cost — no LLM calls. Pure data-driven edge detection.
"""
from __future__ import annotations

import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from hawk.config import HawkConfig
from hawk.scanner import HawkMarket

# Wire up shared intelligence layer
sys.path.insert(0, str(Path.home() / "shared"))
sys.path.insert(0, str(Path.home()))
_USE_SHARED_LLM = False
_shared_llm_call = None
_hawk_brain = None
try:
    from llm_client import llm_call
    from agent_brain import AgentBrain
    _shared_llm_call = llm_call
    _USE_SHARED_LLM = True
except ImportError:
    pass

log = logging.getLogger(__name__)

# ── Atlas Intelligence File ──
_HAWK_ATLAS_INTEL_FILE = Path.home() / "polymarket-bot" / "data" / "hawk_atlas_intel.json"
_ATLAS_INTEL_STALENESS = 21600  # 6 hours


def _get_atlas_research_context(market_question: str, category: str) -> str:
    """Load Atlas research intel and extract entries relevant to this market."""
    if not _HAWK_ATLAS_INTEL_FILE.exists():
        return ""
    try:
        import json as _json
        import time as _time
        data = _json.loads(_HAWK_ATLAS_INTEL_FILE.read_text())

        # Check staleness
        ts_str = data.get("scanned_at", "")
        if ts_str:
            from datetime import datetime as _dt
            from zoneinfo import ZoneInfo
            ts_dt = _dt.fromisoformat(ts_str)
            if ts_dt.tzinfo is None:
                ts_dt = ts_dt.replace(tzinfo=ZoneInfo("America/New_York"))
            if _time.time() - ts_dt.timestamp() > _ATLAS_INTEL_STALENESS:
                return ""

        # Collect relevant entries
        q_lower = market_question.lower()
        cat_lower = category.lower()
        relevant = []

        for item in data.get("news_sentiment", []) + data.get("strategies", []):
            title = item.get("title", "").lower()
            snippet = item.get("snippet", "").lower()
            # Match if any keyword from the question appears
            q_words = set(q_lower.split()) - {"will", "the", "be", "to", "in", "a", "of", "on", "at"}
            if any(w in title or w in snippet for w in q_words if len(w) > 3):
                relevant.append(f"- {item.get('title', '')}: {item.get('snippet', '')[:150]}")

        if not relevant:
            return ""

        context = "\n\nATLAS RESEARCH CONTEXT:\n" + "\n".join(relevant[:5])
        log.debug("[HAWK] Atlas intel loaded: %d relevant entries for %s", len(relevant[:5]), market_question[:40])
        return context

    except Exception:
        return ""

# Sport-specific live score thresholds (replaces hard score_diff >= 10)
_LIVE_THRESHOLDS: dict[str, dict] = {
    "basketball_nba": {"significant": 15, "late_period": 3, "late_threshold": 10, "blowout": 25},
    "basketball_ncaab": {"significant": 15, "late_period": 3, "late_threshold": 10, "blowout": 25},
    "basketball_euroleague": {"significant": 15, "late_period": 3, "late_threshold": 10, "blowout": 25},
    "americanfootball_nfl": {"significant": 14, "late_period": 3, "late_threshold": 7, "blowout": 25},
    "americanfootball_ncaaf": {"significant": 14, "late_period": 3, "late_threshold": 7, "blowout": 25},
    "icehockey_nhl": {"significant": 2, "late_period": 3, "late_threshold": 2, "blowout": 4},
    "baseball_mlb": {"significant": 3, "late_period": 6, "late_threshold": 2, "blowout": 7},
}
# Soccer defaults (all soccer_ keys)
_SOCCER_THRESHOLD = {"significant": 1, "late_period": 2, "late_threshold": 1, "blowout": 3}
_DEFAULT_THRESHOLD = {"significant": 10, "late_period": 3, "late_threshold": 7, "blowout": 25}


def _get_live_threshold(sport_key: str) -> dict:
    """Get sport-specific live score thresholds."""
    if sport_key in _LIVE_THRESHOLDS:
        return _LIVE_THRESHOLDS[sport_key]
    if sport_key.startswith("soccer_"):
        return _SOCCER_THRESHOLD
    return _DEFAULT_THRESHOLD


def _live_confidence_boost(score_diff: int, sport_key: str) -> float:
    """Calculate confidence boost from live score lead.

    Returns 0.0 to 0.15 depending on score differential.
    """
    thresholds = _get_live_threshold(sport_key)
    blowout = thresholds.get("blowout", 25)
    significant = thresholds.get("significant", 10)
    # Scale: blowout → +0.15, significant → +0.10, half-significant → +0.05
    if score_diff >= blowout:
        return 0.15
    if score_diff >= significant:
        return 0.10
    if score_diff >= max(1, significant // 2):
        return 0.05
    return 0.0


@dataclass
class ProbabilityEstimate:
    market_id: str
    question: str
    estimated_prob: float
    confidence: float
    reasoning: str
    category: str
    risk_level: int = 5
    edge_source: str = ""
    money_thesis: str = ""
    news_factor: str = ""
    sportsbook_prob: float | None = None  # V3: sportsbook consensus if available
    sportsbook_books: int = 0  # V3: how many bookmakers contributed
    # V4: Cross-platform intelligence
    kalshi_prob: float | None = None
    metaculus_prob: float | None = None
    predictit_prob: float | None = None
    cross_platform_count: int = 0  # How many cross-platform matches found
    _consensus: object = None  # SportsbookConsensus for smart sizing (not serialized)


# ── Data Fetchers ──

def _get_cross_platform_data(market: HawkMarket, yes_price: float) -> tuple[str, dict]:
    """Gather cross-platform intelligence from Kalshi, PredictIt, Metaculus.

    Returns (context_string, cross_platform_dict).
    cross_platform_dict has keys: kalshi_prob, metaculus_prob, predictit_prob, count.
    """
    context_parts = []
    cp = {"kalshi_prob": None, "metaculus_prob": None, "predictit_prob": None, "count": 0}

    # Kalshi (all markets)
    try:
        from hawk.kalshi import get_kalshi_divergence
        kalshi = get_kalshi_divergence(market.question, yes_price)
        if kalshi and kalshi.match_confidence >= 0.55:
            cp["kalshi_prob"] = kalshi.kalshi_price
            cp["count"] += 1
            context_parts.append(
                f"Kalshi: {kalshi.kalshi_price:.1%} (match: {kalshi.match_confidence:.0%}, "
                f"divergence: {kalshi.price_divergence:+.1%})"
            )
    except Exception:
        pass

    # PredictIt (political markets only)
    if market.category == "politics":
        try:
            from hawk.predictit import match_political_market
            pi = match_political_market(market.question, yes_price)
            if pi and pi.match_confidence >= 0.50:
                cp["predictit_prob"] = pi.pi_price
                cp["count"] += 1
                context_parts.append(
                    f"PredictIt: {pi.pi_price:.1%} (match: {pi.match_confidence:.0%}, "
                    f"divergence: {pi.price_divergence:+.1%})"
                )
        except Exception:
            pass

    # Metaculus (non-sports markets)
    if market.category != "sports":
        try:
            from hawk.metaculus import get_crowd_probability
            mc = get_crowd_probability(market.question, yes_price)
            if mc and mc.match_confidence >= 0.40:
                cp["metaculus_prob"] = mc.community_prob
                cp["count"] += 1
                context_parts.append(
                    f"Metaculus: {mc.community_prob:.1%} community median "
                    f"({mc.num_predictions} predictions, match: {mc.match_confidence:.0%})"
                )
        except Exception:
            pass

    context = ""
    if context_parts:
        context = "\n\nCROSS-PLATFORM INTELLIGENCE:\n" + "\n".join(f"- {p}" for p in context_parts)

    return context, cp


def _get_sportsbook_data(cfg: HawkConfig, market: HawkMarket) -> tuple[float | None, int, str, bool, object, int]:
    """Get sportsbook consensus probability + ESPN context for a sports market.

    Returns (sportsbook_prob, num_books, espn_context_str, is_live_score_shift, consensus_obj, live_score_diff).
    """
    sportsbook_prob = None
    num_books = 0
    espn_context = ""
    is_live_score_shift = False
    consensus_obj = None
    live_score_diff = 0

    try:
        from hawk.odds import get_sportsbook_probability, _detect_sport, _extract_teams
        from hawk.espn import get_match_context, format_context_for_gpt

        # Get sportsbook odds
        odds_api_key = getattr(cfg, 'odds_api_key', '')
        sb_prob, consensus = get_sportsbook_probability(
            odds_api_key, market.question,
        )
        if sb_prob is not None and consensus is not None:
            sportsbook_prob = sb_prob
            num_books = consensus.num_books
            consensus_obj = consensus
            log.info("Sportsbook data for %s: prob=%.2f (%d books, stdev=%.4f)",
                     market.question[:40], sb_prob, num_books,
                     getattr(consensus, 'book_stdev', 0.0))

        # Get ESPN context
        sport_key = _detect_sport(market.question)
        teams = _extract_teams(market.question)
        if sport_key and teams:
            ctx = get_match_context(market.question, sport_key, teams)
            if ctx:
                espn_context = format_context_for_gpt(ctx)
                # V6: Detect live score shift with sport-specific thresholds
                if ctx.is_live and ctx.home_score is not None and ctx.away_score is not None:
                    score_diff = abs(ctx.home_score - ctx.away_score)
                    detected_sport = sport_key or ""
                    thresholds = _get_live_threshold(detected_sport)
                    period = getattr(ctx, 'period', 0) or 0

                    # Check late-game threshold first (lower bar)
                    late_period = thresholds.get("late_period", 3)
                    late_threshold = thresholds.get("late_threshold", 7)
                    significant = thresholds.get("significant", 10)
                    blowout = thresholds.get("blowout", 25)

                    if score_diff >= significant or (period >= late_period and score_diff >= late_threshold):
                        is_live_score_shift = True
                        live_score_diff = score_diff
                        tag = "[LIVE-BLOWOUT]" if score_diff >= blowout else "[LIVE]"
                        log.info("%s Score shift: %d-%d (diff=%d, period=%d, sport=%s) | %s",
                                 tag, ctx.home_score, ctx.away_score, score_diff,
                                 period, detected_sport, market.question[:60])

    except Exception:
        log.debug("Sportsbook/ESPN data fetch failed for %s", market.condition_id[:12])

    return sportsbook_prob, num_books, espn_context, is_live_score_shift, consensus_obj, live_score_diff


def _get_weather_data(cfg: HawkConfig, market: HawkMarket) -> ProbabilityEstimate | None:
    """Pure data-driven weather analysis — $0 cost, no LLM needed."""
    try:
        from hawk.noaa import analyze_weather_market
        result = analyze_weather_market(market.question)
        if result is None:
            return None

        prob = result["probability"]
        confidence = result["confidence"]
        reasoning = result["reasoning"]
        n_members = result.get("ensemble_members", 0)
        horizon = result.get("forecast_horizon_hours", 0)
        data_source = result.get("data_source", "weather_model")

        member_info = f" ({n_members} ensemble members)" if n_members > 0 else ""
        horizon_info = f" | {horizon:.0f}h forecast horizon" if horizon > 0 else ""

        log.info("[WEATHER] Data-driven analysis: prob=%.2f conf=%.2f | %s%s%s",
                 prob, confidence, market.question[:60], member_info, horizon_info)

        return ProbabilityEstimate(
            market_id=market.condition_id,
            question=market.question,
            estimated_prob=prob,
            confidence=confidence,
            reasoning=f"Multi-model weather ensemble consensus — no LLM needed. {reasoning}",
            category="weather",
            risk_level=2,
            edge_source="weather_model",
            money_thesis=f"Weather models say {prob:.0%} — if market disagrees, that is free edge",
            news_factor=f"Source: {data_source}{member_info}{horizon_info}",
        )
    except Exception:
        log.exception("[WEATHER] Weather data analysis failed for %s", market.question[:60])
        return None


def _get_yes_price(market: HawkMarket) -> float:
    """Extract YES token price from market tokens."""
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


# ── Main Analysis ──

def analyze_market(cfg: HawkConfig, market: HawkMarket) -> ProbabilityEstimate | None:
    """Analyze a market using the V7 data-first architecture.

    Sports + sportsbook data: USE RAW SPORTSBOOK PROB (zero cost)
    Sports without sportsbook: SKIP (no edge without data)
    Weather: Pure weather model data (zero cost)
    Non-sports: Cross-platform data (Kalshi, Metaculus, PredictIt)
    """
    is_sports = market.category == "sports"

    # ── Weather: pure data ($0 cost) ──
    if market.category == "weather":
        weather_est = _get_weather_data(cfg, market)
        if weather_est is not None:
            return weather_est
        log.info("[WEATHER] No weather data match, skipping: %s", market.question[:60])
        return None

    # ── Sports: sportsbook + ESPN data ($0 cost) ──
    if is_sports:
        sportsbook_prob, sportsbook_books, espn_context, live_shift, consensus, live_diff = _get_sportsbook_data(cfg, market)

        # Sports with sportsbook data → pure math, skip GPT ($0 cost)
        if sportsbook_prob is not None:
            edge_src = "live_score_shift" if live_shift else "sportsbook_divergence"
            log.info("[V7] Sportsbook-pure: prob=%.2f (%d books) edge_src=%s | %s",
                     sportsbook_prob, sportsbook_books, edge_src, market.question[:60])
            # Graduated live confidence boost based on score differential
            base_conf = 0.75
            if live_shift:
                try:
                    from hawk.odds import _detect_sport as _ds
                    _sport = _ds(market.question) or ""
                except Exception:
                    _sport = ""
                boost = _live_confidence_boost(live_diff, _sport)
                base_conf = min(0.95, 0.80 + boost)

            # Spread confirmation boost (+0.05 when spread and h2h agree within 8%)
            spread_confirmed = False
            if consensus is not None:
                spread_derived = getattr(consensus, 'spread_derived_prob', None)
                home_prob = getattr(consensus, 'consensus_home_prob', None)
                if spread_derived is not None and home_prob is not None and abs(home_prob - spread_derived) < 0.08:
                    base_conf = min(0.95, base_conf + 0.05)
                    spread_confirmed = True
                    log.info("[SPREAD] Confirmation boost: h2h_home=%.3f spread=%.3f -> conf=%.2f",
                             home_prob, spread_derived, base_conf)

            est = ProbabilityEstimate(
                market_id=market.condition_id,
                question=market.question,
                estimated_prob=sportsbook_prob,
                confidence=base_conf,
                reasoning=f"Pure sportsbook consensus from {sportsbook_books} bookmakers"
                          + (" (LIVE score shift detected)" if live_shift else "")
                          + (" (spread confirms)" if spread_confirmed else ""),
                category=market.category,
                risk_level=3,
                edge_source=edge_src,
                money_thesis=f"Sportsbook says {sportsbook_prob:.0%}, Polymarket disagrees",
                news_factor="sportsbook consensus" + (" + live score shift" if live_shift else "")
                            + (" + spread confirmation" if spread_confirmed else ""),
                sportsbook_prob=sportsbook_prob,
                sportsbook_books=sportsbook_books,
            )
            # Thread consensus object through for smart sizing in edge.py
            if consensus is not None:
                est._consensus = consensus
            return est

        # Sports WITHOUT sportsbook data → skip (no data = no edge)
        log.info("[V7] No sportsbook data — skipping sports: %s", market.question[:60])
        return None

    # ── Non-sports: cross-platform data (Kalshi, Metaculus, PredictIt) ──
    # Load Atlas research context for non-sports markets
    atlas_context = _get_atlas_research_context(market.question, market.category)
    yes_price = _get_yes_price(market)
    _, cross_data = _get_cross_platform_data(market, yes_price)

    if cross_data["count"] >= 1:
        # Use cross-platform consensus as probability estimate
        probs = [v for v in [
            cross_data["kalshi_prob"],
            cross_data["metaculus_prob"],
            cross_data["predictit_prob"],
        ] if v is not None]
        cross_prob = sum(probs) / len(probs)

        # Confidence scales with number of sources
        conf = min(0.75, 0.50 + cross_data["count"] * 0.10)

        log.info("[V7] Cross-platform: prob=%.2f (%d sources) | %s",
                 cross_prob, cross_data["count"], market.question[:60])

        reasoning = f"Cross-platform consensus from {cross_data['count']} prediction market(s)"
        if atlas_context:
            reasoning += " + Atlas research intel"
        return ProbabilityEstimate(
            market_id=market.condition_id,
            question=market.question,
            estimated_prob=max(0.01, min(0.99, cross_prob)),
            confidence=conf,
            reasoning=reasoning,
            category=market.category,
            risk_level=4,
            edge_source="cross_platform",
            money_thesis=f"Cross-platform says {cross_prob:.0%}, Polymarket disagrees",
            news_factor=f"{cross_data['count']} cross-platform match(es)",
            kalshi_prob=cross_data["kalshi_prob"],
            metaculus_prob=cross_data["metaculus_prob"],
            predictit_prob=cross_data["predictit_prob"],
            cross_platform_count=cross_data["count"],
        )

    # No data source available for this market
    log.info("[V7] No data source for market: %s (%s)", market.question[:60], market.category)
    return None


def batch_analyze(
    cfg: HawkConfig,
    markets: list[HawkMarket],
    max_concurrent: int = 5,
) -> list[ProbabilityEstimate]:
    """V7 batch analysis — all data-driven, $0 LLM cost."""
    sports = [m for m in markets if m.category == "sports"]
    weather = [m for m in markets if m.category == "weather"]
    non_sports = [m for m in markets if m.category not in ("sports", "weather")]
    log.info("Analyzing %d markets: %d sports, %d weather, %d non-sports (all $0)",
             len(markets), len(sports), len(weather), len(non_sports))

    # Prefetch sportsbook data BEFORE analysis
    if sports:
        try:
            from hawk.odds import prefetch_sports
            odds_key = getattr(cfg, 'odds_api_key', '')
            prefetch_sports(odds_key, [m.question for m in sports])
        except Exception:
            log.debug("Sportsbook prefetch failed — sports markets will be skipped")

    estimates: list[ProbabilityEstimate] = []

    # Sports: process synchronously (no GPT calls, instant)
    for m in sports:
        result = analyze_market(cfg, m)
        if result is not None:
            estimates.append(result)

    # Weather: process synchronously (no LLM calls, instant)
    for m in weather:
        result = analyze_market(cfg, m)
        if result is not None:
            estimates.append(result)

    # Non-sports: cross-platform data ($0 cost)
    for m in non_sports:
        result = analyze_market(cfg, m)
        if result is not None:
            estimates.append(result)

    sports_count = sum(1 for e in estimates if e.category == "sports")
    weather_count = sum(1 for e in estimates if e.category == "weather")
    other_count = len(estimates) - sports_count - weather_count
    log.info("V7 Analysis: %d/%d markets | %d sports | %d weather | %d non-sports",
             len(estimates), len(markets), sports_count, weather_count, other_count)
    return estimates
