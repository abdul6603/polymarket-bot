"""GPT-4o Probability Analyst V3 — Sportsbook-First Intelligence.

Architecture:
  Sports markets  → Sportsbook consensus (The Odds API) + ESPN data → GPT adjusts ±5%
  Non-sports      → Local LLM primary (via shared router) with confidence gate

Routes through shared/llm_client: non-sports → local 14B, sports → cloud GPT-4o.
Falls back to direct OpenAI if shared module unavailable.
"""
from __future__ import annotations

import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import openai

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


# ─────────────────────────────────────────────────────
# System prompts — separate for sports vs non-sports
# ─────────────────────────────────────────────────────

_SPORTS_SYSTEM_PROMPT = (
    "You are Hawk — a sports prediction market analyst. You have been given "
    "the SPORTSBOOK CONSENSUS probability from professional bookmakers (DraftKings, "
    "FanDuel, BetMGM, etc). This is your baseline — it reflects millions of dollars "
    "of sharp money and professional analysis.\n\n"
    "Your job is to ADJUST the sportsbook probability by -5% to +5% based on:\n"
    "1. Breaking news or injuries not yet reflected in the betting lines\n"
    "2. Matchup-specific factors (rivalry games, motivation, coaching styles)\n"
    "3. Situational spots (back-to-back, travel, rest advantage)\n"
    "4. Weather or venue factors\n\n"
    "CRITICAL: Do NOT deviate more than 5% from the sportsbook consensus unless "
    "you have VERY strong evidence (confirmed injury to star player, etc).\n"
    "The sportsbooks are sharper than you. Respect their number.\n\n"
    "Respond in EXACTLY this format:\n"
    "PROBABILITY: 0.XX\n"
    "CONFIDENCE: 0.X\n"
    "RISK_LEVEL: X\n"
    "REASONING: 2-3 sentences explaining your adjustment from the sportsbook line\n"
    "EDGE_SOURCE: sportsbook_divergence/injury_news/situational/weather/none\n"
    "WHY_MONEY: If I bet $15 at $0.XX, I profit $XX when this resolves YES/NO because...\n"
    "NEWS_FACTOR: What specific news/data drove your adjustment"
)

_NONSPORTS_SYSTEM_PROMPT = (
    "You are Hawk — a sharp prediction market analyst. You find mispriced markets "
    "by reasoning from first principles.\n\n"
    "CRITICAL RULES:\n"
    "1. DO NOT anchor to the market price. Form your estimate INDEPENDENTLY.\n"
    "2. Evaluate both YES and NO fairly. No directional bias.\n"
    "3. Use base rates: How often do events like this actually happen?\n"
    "4. If you have NO specific knowledge about this event, set CONFIDENCE to 0.3 or below.\n"
    "5. Only set CONFIDENCE above 0.6 if you have specific, concrete reasons.\n\n"
    "Common mispricings:\n"
    "1. BASE RATE NEGLECT — Market ignores historical frequency\n"
    "2. RECENCY BIAS — Crowd overweights recent events\n"
    "3. NEWS CATALYST — Breaking info not yet priced in\n"
    "4. ANCHORING — Crowd anchors on salient numbers instead of reasoning\n\n"
    "Respond in EXACTLY this format:\n"
    "PROBABILITY: 0.XX\n"
    "CONFIDENCE: 0.X\n"
    "RISK_LEVEL: X\n"
    "REASONING: 2-3 sentences with the core thesis\n"
    "EDGE_SOURCE: stale_price/base_rate/recency/anchoring/news\n"
    "WHY_MONEY: If I bet $15 at $0.XX, I profit $XX when this resolves YES/NO because...\n"
    "NEWS_FACTOR: What recent news/events affect this"
)


def _parse_response(text: str) -> dict:
    """Parse GPT 7-part response."""
    result = {
        "prob": 0.5, "conf": 0.5, "reasoning": "", "risk_level": 5,
        "edge_source": "", "money_thesis": "", "news_factor": "",
    }
    for line in text.strip().split("\n"):
        line = line.strip()
        if line.startswith("PROBABILITY:"):
            try:
                result["prob"] = float(line.split(":", 1)[1].strip())
            except (ValueError, IndexError):
                pass
        elif line.startswith("CONFIDENCE:"):
            try:
                result["conf"] = float(line.split(":", 1)[1].strip())
            except (ValueError, IndexError):
                pass
        elif line.startswith("RISK_LEVEL:"):
            try:
                result["risk_level"] = int(line.split(":", 1)[1].strip().split()[0])
            except (ValueError, IndexError):
                pass
        elif line.startswith("REASONING:"):
            result["reasoning"] = line.split(":", 1)[1].strip()
        elif line.startswith("EDGE_SOURCE:"):
            result["edge_source"] = line.split(":", 1)[1].strip()
        elif line.startswith("WHY_MONEY:"):
            result["money_thesis"] = line.split(":", 1)[1].strip()
        elif line.startswith("NEWS_FACTOR:"):
            result["news_factor"] = line.split(":", 1)[1].strip()

    result["prob"] = max(0.01, min(0.99, result["prob"]))
    result["conf"] = max(0.1, min(1.0, result["conf"]))
    result["risk_level"] = max(1, min(10, result["risk_level"]))
    return result


def _get_viper_context(market: HawkMarket) -> str:
    """Load Viper intelligence relevant to this market."""
    try:
        from viper.intel import get_context_for_market
        intel_items = get_context_for_market(market.condition_id)
        if not intel_items:
            return ""

        lines = []
        for item in intel_items[:5]:
            headline = item.get("headline", "")
            summary = item.get("summary", "")[:200]
            source = item.get("source", "")
            sent = item.get("sentiment", 0)
            sent_label = "positive" if sent > 0.2 else "negative" if sent < -0.2 else "neutral"
            lines.append(f"- [{source}] {headline}: {summary} (sentiment: {sent_label})")

        if lines:
            return "\n\nINSIDER INTELLIGENCE (Viper scanner):\n" + "\n".join(lines)
    except Exception:
        log.debug("Could not load Viper context for %s", market.condition_id[:12])
    return ""


def _get_news_context(market: HawkMarket) -> str:
    """Load per-market news from hawk.news module."""
    try:
        from hawk.news import fetch_market_news
        items = fetch_market_news(
            market.question, market.category,
            [], market.condition_id,
        )
        if not items:
            return ""
        lines = []
        for item in items[:5]:
            headline = item.get("headline", "")
            source = item.get("source", "")
            hours = item.get("hours_ago", 0)
            sent = item.get("sentiment", "neutral")
            lines.append(f"- [{source}, {hours:.0f}h ago] {headline} ({sent})")
        if lines:
            return "\n\nBREAKING NEWS:\n" + "\n".join(lines)
    except Exception:
        log.debug("Could not load news for %s", market.condition_id[:12])
    return ""


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


def _get_weather_context(market: HawkMarket) -> str:
    """Get weather impact for outdoor sports markets."""
    try:
        from hawk.weather import get_game_weather, format_weather_for_gpt
        # Detect sport key from category/question
        sport_key = ""
        q_lower = market.question.lower()
        if "nfl" in q_lower or "football" in q_lower:
            sport_key = "americanfootball_nfl"
        elif "mlb" in q_lower or "baseball" in q_lower:
            sport_key = "baseball_mlb"
        else:
            return ""

        weather = get_game_weather(market.question, sport_key)
        if weather and weather.impact_level != "none":
            return format_weather_for_gpt(weather)
    except Exception:
        pass
    return ""


def _get_sportsbook_data(cfg: HawkConfig, market: HawkMarket) -> tuple[float | None, int, str]:
    """Get sportsbook consensus probability + ESPN context for a sports market.

    Returns (sportsbook_prob, num_books, espn_context_str).
    """
    sportsbook_prob = None
    num_books = 0
    espn_context = ""

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
            log.info("Sportsbook data for %s: prob=%.2f (%d books)",
                     market.question[:40], sb_prob, num_books)

        # Get ESPN context
        sport_key = _detect_sport(market.question)
        teams = _extract_teams(market.question)
        if sport_key and teams:
            ctx = get_match_context(market.question, sport_key, teams)
            if ctx:
                espn_context = format_context_for_gpt(ctx)

    except Exception:
        log.debug("Sportsbook/ESPN data fetch failed for %s", market.condition_id[:12])

    return sportsbook_prob, num_books, espn_context


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


def analyze_market(cfg: HawkConfig, market: HawkMarket) -> ProbabilityEstimate | None:
    """Analyze a market using the V5 architecture.

    Sports + sportsbook data: USE RAW SPORTSBOOK PROB (zero GPT cost!)
    Sports without sportsbook: SKIP (no edge without data)
    Weather: Pure weather model data (zero LLM cost!)
    Non-sports: Local LLM with strict confidence calibration
    """
    is_sports = market.category == "sports"

    # ── Weather: pure data, skip LLM ($0 cost) ──
    if market.category == "weather":
        weather_est = _get_weather_data(cfg, market)
        if weather_est is not None:
            return weather_est
        # If no weather data match, fall through to LLM analysis
        log.info("[WEATHER] No weather data match, falling through to LLM: %s", market.question[:60])

    # ── Sports: get sportsbook + ESPN data first ──
    sportsbook_prob = None
    sportsbook_books = 0
    espn_context = ""

    if is_sports:
        sportsbook_prob, sportsbook_books, espn_context = _get_sportsbook_data(cfg, market)

        # V4: Sports with sportsbook data → pure math, skip GPT entirely ($0 cost)
        if sportsbook_prob is not None:
            log.info("[V4] Sportsbook-pure: prob=%.2f (%d books) | %s",
                     sportsbook_prob, sportsbook_books, market.question[:60])
            return ProbabilityEstimate(
                market_id=market.condition_id,
                question=market.question,
                estimated_prob=sportsbook_prob,
                confidence=0.75,
                reasoning=f"Pure sportsbook consensus from {sportsbook_books} bookmakers — no GPT needed",
                category=market.category,
                risk_level=3,
                edge_source="sportsbook_divergence",
                money_thesis=f"Sportsbook says {sportsbook_prob:.0%}, Polymarket disagrees — free edge",
                news_factor="sportsbook consensus",
                sportsbook_prob=sportsbook_prob,
                sportsbook_books=sportsbook_books,
            )

        # V5: Sports WITHOUT sportsbook data → skip (GPT guesses are -EV, confirmed by trade history)
        log.info("[V5] No sportsbook data — skipping sports: %s", market.question[:60])
        return None

    # V5: Non-sports markets — analyze with local LLM ($0 cost)
    # Filter out crypto price range markets (Garves territory)
    q_lower = market.question.lower()
    _CRYPTO_RANGE_KEYWORDS = ["price of bitcoin", "price of btc", "price of ethereum",
                               "price of eth", "price of xrp", "price of sol",
                               "price of doge", "price of bnb", "price of ada",
                               "between $", "between \u00a3"]
    if any(kw in q_lower for kw in _CRYPTO_RANGE_KEYWORDS):
        log.info("[V5] Skipping crypto price range (Garves territory): %s", market.question[:60])
        return None

    # ── Build LLM prompt ──
    time_info = ""
    if market.time_left_hours > 0:
        if market.time_left_hours < 24:
            time_info = f"Time left: {market.time_left_hours:.1f} hours (ENDING SOON!)"
        elif market.time_left_hours < 48:
            time_info = f"Time left: {market.time_left_hours:.1f} hours"
        else:
            time_info = f"Time left: {market.time_left_hours / 24:.1f} days"

    user_msg = (
        f"Market question: {market.question}\n"
        f"Category: {market.category}\n"
        f"Volume: ${market.volume:,.0f}\n"
    )
    if time_info:
        user_msg += f"{time_info}\n"
    if market.end_date:
        user_msg += f"End date: {market.end_date}\n"

    # Sports-specific: inject sportsbook consensus + ESPN data
    if is_sports and sportsbook_prob is not None:
        user_msg += (
            f"\nSPORTSBOOK CONSENSUS PROBABILITY: {sportsbook_prob:.1%} "
            f"(averaged from {sportsbook_books} professional bookmakers)\n"
            f"This is your BASELINE. Adjust by -5% to +5% based on evidence below.\n"
        )
        system_prompt = _SPORTS_SYSTEM_PROMPT
    elif is_sports:
        # Sports but no sportsbook data — GPT must be cautious
        user_msg += (
            "\nNO sportsbook data available for this game. "
            "Be VERY cautious. Set CONFIDENCE to 0.3 or below unless you have "
            "specific knowledge about these teams.\n"
        )
        system_prompt = _NONSPORTS_SYSTEM_PROMPT
    else:
        system_prompt = _NONSPORTS_SYSTEM_PROMPT

    # Inject ESPN context
    if espn_context:
        user_msg += espn_context

    user_msg += "\n\nWhat is the TRUE probability of YES?"

    # Inject intelligence (Viper + news)
    viper_context = _get_viper_context(market)
    if viper_context:
        user_msg += viper_context

    if cfg.news_enrichment:
        news_context = _get_news_context(market)
        if news_context:
            user_msg += news_context

    # V4: Cross-platform intelligence (Kalshi + PredictIt + Metaculus)
    yes_price = 0.5
    for t in market.tokens:
        outcome = (t.get("outcome") or "").lower()
        if outcome in ("yes", "up"):
            try:
                yes_price = float(t.get("price", 0.5))
            except (ValueError, TypeError):
                pass
            break

    cross_context, cross_data = _get_cross_platform_data(market, yes_price)
    if cross_context:
        user_msg += cross_context

    # V4: Weather for outdoor sports
    if is_sports:
        weather_context = _get_weather_context(market)
        if weather_context:
            user_msg += weather_context

    # Atlas KB intelligence — learned patterns from research cycles
    try:
        from bot.atlas_feed import get_agent_summary
        atlas_context = get_agent_summary("hawk")
        if atlas_context:
            user_msg += f"\n\n{atlas_context}"
    except Exception:
        pass

    # ── Call LLM (shared router: non-sports → local, sports → cloud GPT-4o) ──
    try:
        text = ""
        if _USE_SHARED_LLM and _shared_llm_call:
            # Sports → cloud GPT-4o (per routing config)
            # Non-sports → local 14B (per routing config)
            task = "sports_analysis" if is_sports else "analysis"
            text = _shared_llm_call(
                system=system_prompt, user=user_msg, agent="hawk",
                task_type=task, max_tokens=700, temperature=0.2,
            )

        if not text:
            # Fallback: direct OpenAI → Claude chain
            try:
                client = openai.OpenAI(api_key=cfg.openai_api_key)
                resp = client.chat.completions.create(
                    model="gpt-4o",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_msg},
                    ],
                    max_tokens=700,
                    temperature=0.2,
                )
                text = resp.choices[0].message.content.strip()
            except Exception:
                log.warning("OpenAI fallback failed, trying Claude...")
                import anthropic
                import os
                a_client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
                a_resp = a_client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=700,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_msg}],
                    temperature=0.2,
                )
                text = a_resp.content[0].text.strip()

        parsed = _parse_response(text)

    except Exception:
        log.exception("LLM analysis failed for %s", market.condition_id[:12])
        # If we have sportsbook data, use that directly without GPT
        if sportsbook_prob is not None:
            log.info("Using raw sportsbook prob (GPT failed): %.2f for %s",
                     sportsbook_prob, market.question[:40])
            return ProbabilityEstimate(
                market_id=market.condition_id,
                question=market.question,
                estimated_prob=sportsbook_prob,
                confidence=0.7,  # High confidence in sportsbook data
                reasoning=f"Sportsbook consensus from {sportsbook_books} bookmakers (GPT unavailable)",
                category=market.category,
                risk_level=4,
                edge_source="sportsbook_divergence",
                money_thesis="",
                news_factor="",
                sportsbook_prob=sportsbook_prob,
                sportsbook_books=sportsbook_books,
            )
        return None

    # ── V3 Confidence Calibration ──
    final_prob = parsed["prob"]
    final_conf = parsed["conf"]

    if is_sports and sportsbook_prob is not None:
        # Clamp GPT's adjustment to ±5% from sportsbook consensus
        max_adjustment = 0.05
        gpt_adjustment = final_prob - sportsbook_prob
        if abs(gpt_adjustment) > max_adjustment:
            clamped = sportsbook_prob + max(min(gpt_adjustment, max_adjustment), -max_adjustment)
            log.info("Clamped GPT adjustment: %.2f → %.2f (sportsbook=%.2f, GPT wanted %.2f)",
                     final_prob, clamped, sportsbook_prob, final_prob)
            final_prob = clamped

        # Higher confidence when backed by sportsbook data
        final_conf = max(final_conf, 0.65)
        parsed["edge_source"] = "sportsbook_divergence"
    elif is_sports and sportsbook_prob is None:
        # Sports without sportsbook data — force low confidence
        final_conf = min(final_conf, 0.35)
        log.info("No sportsbook data: forcing low confidence %.2f for %s",
                 final_conf, market.question[:40])
    else:
        # Non-sports: penalize base_rate edge source
        if parsed["edge_source"].lower() in ("base_rate", ""):
            final_conf = min(final_conf, 0.4)

    return ProbabilityEstimate(
        market_id=market.condition_id,
        question=market.question,
        estimated_prob=max(0.01, min(0.99, final_prob)),
        confidence=final_conf,
        reasoning=parsed["reasoning"],
        category=market.category,
        risk_level=parsed["risk_level"],
        edge_source=parsed["edge_source"],
        money_thesis=parsed["money_thesis"],
        news_factor=parsed["news_factor"],
        sportsbook_prob=sportsbook_prob,
        sportsbook_books=sportsbook_books,
        kalshi_prob=cross_data.get("kalshi_prob"),
        metaculus_prob=cross_data.get("metaculus_prob"),
        predictit_prob=cross_data.get("predictit_prob"),
        cross_platform_count=cross_data.get("count", 0),
    )


def batch_analyze(
    cfg: HawkConfig,
    markets: list[HawkMarket],
    max_concurrent: int = 5,
) -> list[ProbabilityEstimate]:
    """V5 batch analysis — sportsbook-pure for sports, weather model for weather, LLM for rest."""
    sports = [m for m in markets if m.category == "sports"]
    weather = [m for m in markets if m.category == "weather"]
    non_sports = [m for m in markets if m.category not in ("sports", "weather")]
    log.info("Analyzing %d markets: %d sports ($0), %d weather ($0), %d non-sports (LLM)",
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

    # V5: Non-sports — analyze with local LLM ($0 cost via shared router)
    for m in non_sports:
        result = analyze_market(cfg, m)
        if result is not None:
            estimates.append(result)

    sports_count = sum(1 for e in estimates if e.category == "sports")
    weather_count = sum(1 for e in estimates if e.category == "weather")
    other_count = len(estimates) - sports_count - weather_count
    log.info("V5 Analysis: %d/%d markets | %d sports | %d weather | %d non-sports",
             len(estimates), len(markets), sports_count, weather_count, other_count)
    return estimates
