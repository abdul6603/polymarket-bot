"""GPT-4o Probability Analyst V2 — The Wise Degen."""
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


_SYSTEM_PROMPT = (
    "You are Hawk — a sharp prediction market analyst. You find mispriced markets "
    "by reasoning from first principles, not anchoring to the current price.\n\n"
    "CRITICAL RULES:\n"
    "1. DO NOT anchor to the market price. Form your estimate INDEPENDENTLY first, "
    "then compare to the market price to see if there's a discrepancy.\n"
    "2. Evaluate both YES and NO fairly — bet YES when evidence supports YES, "
    "bet NO when evidence supports NO. No directional bias.\n"
    "3. Use base rates: How often do events like this actually happen historically?\n"
    "4. Consider what information the market might be missing or overweighting.\n"
    "5. Markets ending in <48h are highest priority — prices are often stale.\n\n"
    "Common mispricings you exploit:\n"
    "1. STALE PRICES — Market hasn't updated after recent news/developments\n"
    "2. BASE RATE NEGLECT — Market ignores historical frequency of similar events\n"
    "3. RECENCY BIAS — Crowd overweights last 24h, ignoring bigger picture\n"
    "4. ANCHORING — Market anchored to old price despite changed conditions\n"
    "5. NEWS CATALYST — Breaking info not yet priced in\n\n"
    "Think step by step: What's the base rate? What's changed recently? "
    "What does the evidence say? THEN give your probability.\n\n"
    "Respond in EXACTLY this format (no other text):\n"
    "PROBABILITY: 0.XX\n"
    "CONFIDENCE: 0.X\n"
    "RISK_LEVEL: X\n"
    "REASONING: 2-3 sentences with the core thesis\n"
    "EDGE_SOURCE: Which mispricing pattern applies (stale_price/base_rate/recency/anchoring/news)\n"
    "WHY_MONEY: If I bet $20 at $0.XX, I profit $XX when this resolves YES/NO because...\n"
    "NEWS_FACTOR: What recent news/events affect this and which direction"
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


def analyze_market(cfg: HawkConfig, market: HawkMarket) -> ProbabilityEstimate | None:
    """Send question + context to GPT-4o, get 7-part probability estimate."""
    yes_price = 0.5
    for t in market.tokens:
        outcome = (t.get("outcome") or "").lower()
        if outcome in ("yes", "up"):
            try:
                yes_price = float(t.get("price", 0.5))
            except (ValueError, TypeError):
                pass
            break

    # Time left info
    time_info = ""
    if market.time_left_hours > 0:
        if market.time_left_hours < 24:
            time_info = f"Time left: {market.time_left_hours:.1f} hours (ENDING SOON!)"
        elif market.time_left_hours < 48:
            time_info = f"Time left: {market.time_left_hours:.1f} hours (~{market.time_left_hours/24:.1f} days)"
        else:
            time_info = f"Time left: {market.time_left_hours/24:.1f} days"

    user_msg = (
        f"Market question: {market.question}\n"
        f"Category: {market.category}\n"
        f"Volume: ${market.volume:,.0f}\n"
    )
    # NOTE: Market price intentionally NOT shown to GPT to prevent anchoring.
    # GPT must form an independent probability estimate from first principles.
    if time_info:
        user_msg += f"{time_info}\n"
    if market.end_date:
        user_msg += f"End date: {market.end_date}\n"
    user_msg += "\nWhat is the TRUE probability of YES?"

    # Inject intelligence
    viper_context = _get_viper_context(market)
    if viper_context:
        user_msg += viper_context

    if cfg.news_enrichment:
        news_context = _get_news_context(market)
        if news_context:
            user_msg += news_context

    try:
        client = openai.OpenAI(api_key=cfg.openai_api_key)
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=500,
            temperature=0.2,
        )
        text = resp.choices[0].message.content.strip()
        parsed = _parse_response(text)

        return ProbabilityEstimate(
            market_id=market.condition_id,
            question=market.question,
            estimated_prob=parsed["prob"],
            confidence=parsed["conf"],
            reasoning=parsed["reasoning"],
            category=market.category,
            risk_level=parsed["risk_level"],
            edge_source=parsed["edge_source"],
            money_thesis=parsed["money_thesis"],
            news_factor=parsed["news_factor"],
        )
    except Exception:
        log.exception("GPT analysis failed for %s", market.condition_id[:12])
        return None


def batch_analyze(
    cfg: HawkConfig,
    markets: list[HawkMarket],
    max_concurrent: int = 5,
) -> list[ProbabilityEstimate]:
    """Parallel analysis with ThreadPoolExecutor."""
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

    log.info("Analyzed %d/%d markets with GPT-4o (V2 Wise Degen)", len(estimates), len(markets))
    return estimates
