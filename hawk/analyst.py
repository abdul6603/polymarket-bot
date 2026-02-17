"""GPT-4o Probability Analyst — estimate real probabilities for Polymarket markets."""
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
    "You are a prediction market analyst. Given a market question and the current market price, "
    "estimate the TRUE probability of the event occurring. Be calibrated — if you think the "
    "market is efficient, say so. Only diverge from the market price when you have strong reason.\n\n"
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


def analyze_market(cfg: HawkConfig, market: HawkMarket) -> ProbabilityEstimate | None:
    """Send question + current odds to GPT-4o, get probability estimate."""
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

    user_msg = (
        f"Market question: {market.question}\n"
        f"Current market price (YES): {yes_price:.2f} ({yes_price*100:.0f}%)\n"
        f"Category: {market.category}\n"
        f"Volume: ${market.volume:,.0f}\n"
        f"\nWhat is the TRUE probability of YES?"
    )

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

    log.info("Analyzed %d/%d markets with GPT-4o", len(estimates), len(markets))
    return estimates
