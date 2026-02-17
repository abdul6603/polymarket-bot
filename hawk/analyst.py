"""GPT-4o Probability Analyst — enhanced with Viper intelligence feed."""
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


_SYSTEM_PROMPT = (
    "You are a contrarian prediction market analyst who profits from crowd mispricing.\n\n"
    "Common mispricing patterns you exploit:\n"
    "1. RECENCY BIAS — crowd overweights recent events, ignoring base rates\n"
    "2. ANCHORING — market price stuck near initial listing, hasn't updated for new info\n"
    "3. NARRATIVE BIAS — crowd follows a story, ignoring contradicting data\n"
    "4. EXTREME PRICES — markets at 90%+ often overestimate certainty; markets at 10%- underestimate tail risk\n"
    "5. NEGLECTED MARKETS — lower-volume markets get less trader attention = more mispricing\n"
    "6. BREAKING NEWS — events not yet reflected in market price\n\n"
    "Your job: estimate TRUE probability independent of current price. "
    "Don't anchor to market price. Think from first principles. "
    "If you see a clear edge based on the evidence, be bold — diverge from market.\n\n"
    "Respond in EXACTLY this format (no other text):\n"
    "PROBABILITY: 0.XX\n"
    "CONFIDENCE: 0.X\n"
    "REASONING: One sentence explanation"
)


def _parse_response(text: str) -> tuple[float, float, str]:
    """Parse GPT response into (prob, confidence, reasoning)."""
    prob = 0.5
    conf = 0.5
    reason = ""
    for line in text.strip().split("\n"):
        line = line.strip()
        if line.startswith("PROBABILITY:"):
            try:
                prob = float(line.split(":", 1)[1].strip())
            except (ValueError, IndexError):
                pass
        elif line.startswith("CONFIDENCE:"):
            try:
                conf = float(line.split(":", 1)[1].strip())
            except (ValueError, IndexError):
                pass
        elif line.startswith("REASONING:"):
            reason = line.split(":", 1)[1].strip()
    return max(0.01, min(0.99, prob)), max(0.1, min(1.0, conf)), reason


def _get_viper_context(market: HawkMarket) -> str:
    """Load Viper intelligence relevant to this market."""
    try:
        from viper.intel import get_context_for_market
        intel_items = get_context_for_market(market.condition_id)
        if not intel_items:
            return ""

        lines = []
        for item in intel_items[:5]:  # Max 5 intel items per market
            headline = item.get("headline", "")
            summary = item.get("summary", "")[:200]
            source = item.get("source", "")
            url = item.get("url", "")
            match_type = item.get("match_type", "unknown")
            sentiment = item.get("sentiment", 0)
            sent_label = "positive" if sentiment > 0.2 else "negative" if sentiment < -0.2 else "neutral"
            url_note = f" | {url}" if url else ""
            match_note = f" [{match_type}]" if match_type != "unknown" else ""
            lines.append(f"- [{source}]{match_note} {headline}: {summary} (sentiment: {sent_label}){url_note}")

        if lines:
            return "\n\nREAL-TIME INTELLIGENCE (from Viper scanner):\n" + "\n".join(lines)
    except Exception:
        log.debug("Could not load Viper context for %s", market.condition_id[:12])
    return ""


def analyze_market(cfg: HawkConfig, market: HawkMarket) -> ProbabilityEstimate | None:
    """Send question + current odds + Viper intel to GPT-4o, get probability estimate."""
    # Get current market price from tokens
    yes_price = 0.5
    for t in market.tokens:
        outcome = (t.get("outcome") or "").lower()
        if outcome in ("yes", "up"):
            try:
                yes_price = float(t.get("price", 0.5))
            except (ValueError, TypeError):
                pass
            break

    # Build user message with Viper intelligence
    user_msg = (
        f"Market question: {market.question}\n"
        f"Current market price (YES): {yes_price:.2f} ({yes_price*100:.0f}%)\n"
        f"Category: {market.category}\n"
        f"Volume: ${market.volume:,.0f}\n"
        f"\nWhat is the TRUE probability of YES?"
    )

    # Inject Viper intelligence if available
    viper_context = _get_viper_context(market)
    if viper_context:
        user_msg += viper_context

    try:
        client = openai.OpenAI(api_key=cfg.openai_api_key)
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=300,
            temperature=0.3,
        )
        text = resp.choices[0].message.content.strip()
        prob, conf, reason = _parse_response(text)

        return ProbabilityEstimate(
            market_id=market.condition_id,
            question=market.question,
            estimated_prob=prob,
            confidence=conf,
            reasoning=reason,
            category=market.category,
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

    log.info("Analyzed %d/%d markets with GPT-4o (+ Viper intel)", len(estimates), len(markets))
    return estimates
