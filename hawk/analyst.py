"""GPT-4o Probability Analyst V3 — Sportsbook-First Intelligence.

Architecture:
  Sports markets  → Sportsbook consensus (The Odds API) + ESPN data → GPT adjusts ±5%
  Non-sports      → GPT-4o primary with confidence gate + news enrichment

GPT-4o is NO LONGER the probability estimator for sports. The sportsbook consensus
(averaged from 40+ bookmakers) IS the probability. GPT-4o's role is to adjust
±5% based on qualitative factors the books might miss.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

import openai

from hawk.config import HawkConfig
from hawk.scanner import HawkMarket

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
    "1. STALE PRICES — Market hasn't updated after recent news\n"
    "2. BASE RATE NEGLECT — Market ignores historical frequency\n"
    "3. RECENCY BIAS — Crowd overweights recent events\n"
    "4. NEWS CATALYST — Breaking info not yet priced in\n\n"
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


def analyze_market(cfg: HawkConfig, market: HawkMarket) -> ProbabilityEstimate | None:
    """Analyze a market using the V3 sportsbook-first architecture.

    Sports: sportsbook consensus → GPT adjusts ±5%
    Non-sports: GPT-4o primary with confidence calibration
    """
    is_sports = market.category == "sports"

    # ── Sports: get sportsbook + ESPN data first ──
    sportsbook_prob = None
    sportsbook_books = 0
    espn_context = ""

    if is_sports:
        sportsbook_prob, sportsbook_books, espn_context = _get_sportsbook_data(cfg, market)

    # ── Build GPT prompt ──
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

    # ── Call GPT-4o ──
    try:
        client = openai.OpenAI(api_key=cfg.openai_api_key)
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=500,
            temperature=0.2,
        )
        text = resp.choices[0].message.content.strip()
        parsed = _parse_response(text)

    except Exception:
        log.exception("GPT analysis failed for %s", market.condition_id[:12])
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
    )


def batch_analyze(
    cfg: HawkConfig,
    markets: list[HawkMarket],
    max_concurrent: int = 5,
) -> list[ProbabilityEstimate]:
    """Parallel analysis with ThreadPoolExecutor."""
    # Separate sports and non-sports for logging
    sports = [m for m in markets if m.category == "sports"]
    non_sports = [m for m in markets if m.category != "sports"]
    log.info("Analyzing %d markets: %d sports (sportsbook-first), %d non-sports (GPT primary)",
             len(markets), len(sports), len(non_sports))

    estimates: list[ProbabilityEstimate] = []

    with ThreadPoolExecutor(max_workers=max_concurrent) as pool:
        futures = {
            pool.submit(analyze_market, cfg, m): m
            for m in markets
        }
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                estimates.append(result)

    # Log V3 stats
    sb_count = sum(1 for e in estimates if e.sportsbook_prob is not None)
    log.info("V3 Analysis: %d/%d markets | %d with sportsbook data | %d GPT-only",
             len(estimates), len(markets), sb_count, len(estimates) - sb_count)
    return estimates
