"""Oracle ensemble — parallel LLM calls for probability estimation.

Sends identical structured prompts to Claude, Grok, and Gemini.
Each model returns strict JSON with probability estimates.
Oracle averages the outputs using weighted averaging.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import requests

from oracle.config import OracleConfig
from oracle.data_pipeline import MarketContext
from oracle.scanner import WeeklyMarket, TYPE_ABOVE, TYPE_RANGE, TYPE_HIT

log = logging.getLogger(__name__)


@dataclass
class EnsembleResult:
    """Result of the ensemble probability estimation."""
    predictions: dict[str, float]       # condition_id → averaged probability
    model_outputs: dict[str, dict]      # model_name → raw predictions
    regime: str                         # consensus regime
    confidence: float                   # average model confidence
    questions: list[dict[str, str]]     # the questions sent to models


def build_questions(
    markets: list[WeeklyMarket],
    current_prices: dict[str, float] | None = None,
) -> list[dict[str, str]]:
    """Build structured questions from tradeable markets.

    Args:
        markets: Tradeable weekly markets.
        current_prices: Current asset prices {"bitcoin": 66137, "solana": 81.87, ...}.
    """
    prices = current_prices or {}
    questions = []
    for m in markets:
        q_key = m.condition_id[:12]
        if m.market_type == TYPE_ABOVE:
            if m.threshold is not None:
                q_text = f"Will {m.asset.upper()} be above ${m.threshold:,.0f} at end of week?"
            else:
                q_text = f"Will {m.asset.upper()} be above the threshold at end of week?"
        elif m.market_type == TYPE_RANGE:
            low = m.range_low or 0
            high = m.range_high or float("inf")
            if high == float("inf"):
                q_text = f"Will {m.asset.upper()} be above ${low:,.0f} at end of week?"
            elif low == 0:
                q_text = f"Will {m.asset.upper()} be below ${high:,.0f} at end of week?"
            else:
                q_text = f"Will {m.asset.upper()} end the week between ${low:,.0f}-${high:,.0f}?"
        elif m.market_type == TYPE_HIT:
            if m.threshold is not None:
                q_text = f"Will {m.asset.upper()} reach ${m.threshold:,.0f} at any point this week?"
            else:
                q_text = f"Will {m.asset.upper()} hit the price target this week?"
        else:
            continue

        # Compute distance from current price to threshold
        asset_price = prices.get(m.asset, 0)
        distance_pct = 0.0
        if asset_price > 0 and m.threshold and m.threshold > 0:
            distance_pct = (asset_price - m.threshold) / m.threshold * 100

        questions.append({
            "id": q_key,
            "condition_id": m.condition_id,
            "question": q_text,
            "asset": m.asset,
            "market_type": m.market_type,
            "current_market_price": m.yes_price,
            "current_asset_price": asset_price,
            "threshold": m.threshold or 0,
            "range_low": m.range_low or 0,
            "range_high": m.range_high or 0,
            "distance_pct": round(distance_pct, 1),
        })
    return questions


def _build_system_prompt(context: MarketContext) -> str:
    """Build the system prompt for all models."""
    return f"""You are Oracle, a calm, wise crypto analyst with 10 years of experience.
You speak in probabilities, never certainties. You are risk-aware and never hype.

Your core belief: "The market is a living entity. My job is to read its breath,
heartbeat, and hidden intentions on the weekly scale."

CURRENT MARKET DATA:
{context.summary_text()}

INSTRUCTIONS:
- For each question, estimate the probability (0.0 to 1.0) that YES is correct.
- Base your estimates on the market data, macro conditions, derivatives positioning,
  and historical price behavior.
- Consider: funding rates, open interest changes, Fear & Greed sentiment,
  weekly price range, and any catalysts.
- Be calibrated: if you say 0.70, it should happen ~70% of the time.
- Do NOT just copy the current market price — apply your analysis.

CRITICAL — PRICE DISTANCE ANCHORING:
- Each question shows the CURRENT price and how far it is from the threshold.
- Use this distance as your anchor. Crypto rarely moves more than 10-15%/week.
- If the current price is 15% ABOVE the threshold, P(stays above) should be HIGH
  (likely 70-90%), NOT low. It takes a major crash to move 15% in a few days.
- If the current price is only 2-3% above, the probability is more uncertain (40-60%).
- If the current price is BELOW the threshold, P(above) should be LOW (10-40%).
- Weekly BTC volatility is typically 5-8%. SOL/ETH 8-12%. XRP 10-15%.
  A 15%+ move in one week is a rare tail event (~5-10% probability).

CRITICAL — DISTRIBUTION CONSTRAINT:
- Price range questions for the SAME asset are MUTUALLY EXCLUSIVE.
  The asset can only end in ONE range. Your probabilities for all ranges
  of the same asset MUST sum to approximately 1.0 (100%).
  Example: if BTC has ranges $60k-62k, $62k-64k, $64k-66k, $66k-68k,
  your probabilities might be 0.15, 0.35, 0.30, 0.20 (sum = 1.0).
- Similarly, "above $X" questions are related — if P(above $60k) = 0.90,
  then P(above $65k) must be LOWER, not higher.

RESPOND WITH STRICT JSON ONLY. No text before or after. Format:
{{"predictions": {{"question_id": probability, ...}}, "overall_regime": "regime_name", "model_confidence": 0.0_to_1.0}}"""


def _build_user_prompt(questions: list[dict]) -> str:
    """Build the user prompt with questions, grouped by asset for distribution awareness.

    Includes current asset price and distance-to-threshold for calibration anchoring.
    """
    from collections import defaultdict

    # Group by asset for clarity
    by_asset: dict[str, list[dict]] = defaultdict(list)
    for q in questions:
        by_asset[q["asset"]].append(q)

    lines = ["Estimate YES probability for each question:\n"]

    for asset, qs in sorted(by_asset.items()):
        asset_price = qs[0].get("current_asset_price", 0)
        if asset_price > 0:
            lines.append(f"--- {asset.upper()} (current price: ${asset_price:,.2f}) ---")
        else:
            lines.append(f"--- {asset.upper()} ---")
        range_count = sum(1 for q in qs if q["market_type"] == "price_range")
        if range_count > 1:
            lines.append(f"  (NOTE: {range_count} price ranges below are MUTUALLY EXCLUSIVE — probabilities must sum to ~1.0)")
        for q in qs:
            # Build distance context for anchoring
            dist = q.get("distance_pct", 0)
            threshold = q.get("threshold", 0)
            mtype = q.get("market_type", "")

            if mtype == "above_below" and threshold > 0 and asset_price > 0:
                if dist > 0:
                    dist_str = f" | price is {dist:+.1f}% ABOVE threshold"
                else:
                    dist_str = f" | price is {abs(dist):.1f}% BELOW threshold"
            elif mtype == "price_range":
                rlow = q.get("range_low", 0)
                rhigh = q.get("range_high", 0)
                if asset_price > 0 and rlow > 0 and rhigh > 0:
                    if rlow <= asset_price <= rhigh:
                        dist_str = " | price is INSIDE this range"
                    elif asset_price > rhigh:
                        pct_above = (asset_price - rhigh) / rhigh * 100
                        dist_str = f" | price is {pct_above:.1f}% above range"
                    else:
                        pct_below = (rlow - asset_price) / rlow * 100
                        dist_str = f" | price is {pct_below:.1f}% below range"
                else:
                    dist_str = ""
            else:
                dist_str = ""

            lines.append(
                f"- {q['id']}: {q['question']} "
                f"(market: {q['current_market_price']:.1%}{dist_str})"
            )
        lines.append("")

    lines.append(f"Respond with strict JSON. Keys in predictions must match: {[q['id'] for q in questions]}")
    return "\n".join(lines)


def run_ensemble(
    cfg: OracleConfig,
    markets: list[WeeklyMarket],
    context: MarketContext,
) -> EnsembleResult:
    """Run the full ensemble: build questions → query models → average."""
    questions = build_questions(markets, current_prices=context.prices)
    if not questions:
        return EnsembleResult({}, {}, "unknown", 0.0, [])

    system_prompt = _build_system_prompt(context)
    user_prompt = _build_user_prompt(questions)
    question_ids = [q["id"] for q in questions]

    model_outputs: dict[str, dict] = {}

    # Query each model
    if cfg.claude_api_key:
        result = _query_claude(cfg, system_prompt, user_prompt)
        if result:
            model_outputs["claude"] = result
            log.info("Claude returned %d predictions", len(result.get("predictions", {})))

    if cfg.gemini_api_key:
        result = _query_gemini(cfg, system_prompt, user_prompt)
        if result:
            model_outputs["gemini"] = result
            log.info("Gemini returned %d predictions", len(result.get("predictions", {})))
        else:
            log.warning("Gemini returned no parseable predictions")

    if cfg.grok_api_key:
        result = _query_grok(cfg, system_prompt, user_prompt)
        if result:
            model_outputs["grok"] = result
            log.info("Grok returned %d predictions", len(result.get("predictions", {})))

    # Fallback: local Qwen via shared LLM server
    if len(model_outputs) < 2:
        result = _query_local(system_prompt, user_prompt)
        if result:
            model_outputs["qwen_local"] = result
            log.info("Local Qwen returned %d predictions", len(result.get("predictions", {})))

    if not model_outputs:
        log.error("No model returned predictions")
        return EnsembleResult({}, {}, "unknown", 0.0, questions)

    # Weighted averaging
    averaged = _weighted_average(model_outputs, question_ids, cfg.ensemble_weights)

    # Normalize mutually exclusive ranges per asset
    averaged = _normalize_distributions(averaged, questions)

    # Sanity check: clamp probabilities based on price distance
    averaged = _sanity_check_probabilities(averaged, questions)

    # Map back to condition_ids
    id_to_cid = {q["id"]: q["condition_id"] for q in questions}
    predictions = {id_to_cid[qid]: prob for qid, prob in averaged.items() if qid in id_to_cid}

    # Consensus regime
    regimes = [m.get("overall_regime", "unknown") for m in model_outputs.values()]
    regime = max(set(regimes), key=regimes.count) if regimes else "unknown"

    # Average confidence
    confidences = [m.get("model_confidence", 0.5) for m in model_outputs.values()]
    avg_confidence = sum(confidences) / len(confidences) if confidences else 0.5

    log.info(
        "Ensemble complete: %d models, %d predictions, regime=%s, confidence=%.2f",
        len(model_outputs), len(predictions), regime, avg_confidence,
    )

    return EnsembleResult(
        predictions=predictions,
        model_outputs=model_outputs,
        regime=regime,
        confidence=avg_confidence,
        questions=questions,
    )


def _weighted_average(
    outputs: dict[str, dict],
    question_ids: list[str],
    weights: dict[str, float],
) -> dict[str, float]:
    """Compute weighted average of model predictions."""
    result: dict[str, float] = {}

    for qid in question_ids:
        total_weight = 0.0
        weighted_sum = 0.0

        for model_name, output in outputs.items():
            preds = output.get("predictions", {})
            if qid in preds:
                w = weights.get(model_name, 0.20)
                prob = float(preds[qid])
                prob = max(0.0, min(1.0, prob))  # clamp
                weighted_sum += prob * w
                total_weight += w

        if total_weight > 0:
            result[qid] = weighted_sum / total_weight
        else:
            result[qid] = 0.5  # no data → neutral

    return result


def _normalize_distributions(
    averaged: dict[str, float],
    questions: list[dict[str, str]],
) -> dict[str, float]:
    """Normalize mutually exclusive range predictions so they sum to ~1.0 per asset.

    Price range questions for the same asset are mutually exclusive — the asset
    can only end in one range. If the ensemble assigns 47% to every range,
    we normalize so all ranges for that asset sum to 1.0.
    """
    from collections import defaultdict

    # Group question IDs by (asset, market_type)
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    q_lookup = {q["id"]: q for q in questions}
    for q in questions:
        groups[(q["asset"], q["market_type"])].append(q["id"])

    result = dict(averaged)

    for (asset, mtype), qids in groups.items():
        if len(qids) < 2:
            continue
        # Only normalize price_range and above_below groups
        if mtype not in ("price_range", "above_below"):
            continue

        probs = [result.get(qid, 0.0) for qid in qids]
        total = sum(probs)

        if total <= 0:
            continue

        if mtype == "price_range" and total > 1.05:
            # Normalize ranges to sum to 1.0
            for qid, p in zip(qids, probs):
                result[qid] = p / total
            log.info(
                "[NORMALIZE] %s ranges: sum %.2f → 1.00 (%d markets)",
                asset.upper(), total, len(qids),
            )

        elif mtype == "above_below":
            # Sort by threshold (highest first) and ensure monotonic decreasing
            # P(above $70k) <= P(above $65k) <= P(above $60k)
            threshold_qids = []
            for qid in qids:
                q = q_lookup[qid]
                # Extract threshold from question text
                import re
                m = re.search(r"\$([0-9,]+)", q["question"])
                thresh = float(m.group(1).replace(",", "")) if m else 0
                threshold_qids.append((thresh, qid))
            threshold_qids.sort(reverse=True)  # highest threshold first

            # Enforce monotonic: P(above higher) <= P(above lower)
            prev_prob = 0.0
            for i, (thresh, qid) in enumerate(threshold_qids):
                if i > 0 and result[qid] < prev_prob:
                    # Already monotonic, good
                    pass
                elif i > 0 and result[qid] >= prev_prob:
                    # Fix: cap at previous level
                    result[qid] = min(result[qid], prev_prob + 0.02)
                prev_prob = result[qid]

    return result


def _sanity_check_probabilities(
    averaged: dict[str, float],
    questions: list[dict],
) -> dict[str, float]:
    """Clamp extreme probabilities based on price distance from threshold.

    LLMs systematically underestimate P(above threshold) when sentiment is
    bearish, even when the current price is far above the threshold. This
    guard enforces mathematical floors/ceilings:
    - If price is 15%+ above threshold, P(above) >= 0.75
    - If price is 10%+ above threshold, P(above) >= 0.55
    - If price is  5%+ above threshold, P(above) >= 0.40
    - If price is below threshold, P(above) <= 0.40
    - If price is 5%+ below threshold, P(above) <= 0.25
    """
    result = dict(averaged)

    # Distance floors for above_below markets
    ABOVE_FLOORS = [
        (15.0, 0.75),
        (10.0, 0.55),
        (5.0, 0.40),
    ]
    BELOW_CEILINGS = [
        (-5.0, 0.25),
        (0.0, 0.40),
    ]

    for q in questions:
        qid = q["id"]
        if qid not in result:
            continue
        mtype = q.get("market_type", "")
        dist = q.get("distance_pct", 0)
        old_p = result[qid]

        if mtype == "above_below":
            # Apply floors when price is above threshold
            for min_dist, floor in ABOVE_FLOORS:
                if dist >= min_dist and old_p < floor:
                    log.info(
                        "[SANITY] %s %s: dist +%.1f%%, P %.2f → %.2f (floor)",
                        q.get("asset", "?").upper(), qid, dist, old_p, floor,
                    )
                    result[qid] = floor
                    break
            # Apply ceilings when price is below threshold
            for max_dist, ceil in BELOW_CEILINGS:
                if dist <= max_dist and old_p > ceil:
                    log.info(
                        "[SANITY] %s %s: dist %.1f%%, P %.2f → %.2f (ceiling)",
                        q.get("asset", "?").upper(), qid, dist, old_p, ceil,
                    )
                    result[qid] = ceil
                    break

    return result


# ---------------------------------------------------------------------------
# Model-specific API callers
# ---------------------------------------------------------------------------

def _parse_json_response(text: str) -> dict | None:
    """Extract JSON from model response, handling markdown code blocks."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON in the text
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                pass
    return None


def _query_claude(cfg: OracleConfig, system: str, user: str) -> dict | None:
    """Query Anthropic Claude API."""
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": cfg.claude_api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": cfg.claude_model,
                "max_tokens": 2000,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
            timeout=60,
        )
        if resp.status_code == 200:
            data = resp.json()
            text = data.get("content", [{}])[0].get("text", "")
            return _parse_json_response(text)
        log.warning("Claude API error: %d", resp.status_code)
    except Exception as e:
        log.warning("Claude query failed: %s", e)
    return None


def _query_gemini(cfg: OracleConfig, system: str, user: str) -> dict | None:
    """Query Google Gemini API."""
    try:
        resp = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{cfg.gemini_model}:generateContent",
            params={"key": cfg.gemini_api_key},
            headers={"content-type": "application/json"},
            json={
                "system_instruction": {"parts": [{"text": system}]},
                "contents": [{"parts": [{"text": user}]}],
                "generationConfig": {"temperature": 0.3, "maxOutputTokens": 2000},
            },
            timeout=60,
        )
        if resp.status_code == 200:
            data = resp.json()
            text = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
            return _parse_json_response(text)
        log.warning("Gemini API error: %d", resp.status_code)
    except Exception as e:
        log.warning("Gemini query failed: %s", e)
    return None


def _query_grok(cfg: OracleConfig, system: str, user: str) -> dict | None:
    """Query xAI Grok API (OpenAI-compatible endpoint)."""
    try:
        resp = requests.post(
            "https://api.x.ai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {cfg.grok_api_key}",
                "content-type": "application/json",
            },
            json={
                "model": cfg.grok_model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.3,
                "max_tokens": 2000,
            },
            timeout=60,
        )
        if resp.status_code == 200:
            data = resp.json()
            text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            return _parse_json_response(text)
        log.warning("Grok API error: %d", resp.status_code)
    except Exception as e:
        log.warning("Grok query failed: %s", e)
    return None


def _query_local(system: str, user: str) -> dict | None:
    """Query local Qwen model via MLX server on Pro."""
    try:
        resp = requests.post(
            "http://localhost:11434/v1/chat/completions",
            json={
                "model": "mlx-community/Qwen2.5-14B-Instruct-4bit",
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.3,
                "max_tokens": 2000,
            },
            timeout=120,
        )
        if resp.status_code == 200:
            data = resp.json()
            text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            return _parse_json_response(text)
    except Exception as e:
        log.debug("Local Qwen query failed: %s", e)
    return None
