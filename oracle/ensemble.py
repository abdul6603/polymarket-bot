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


def build_questions(markets: list[WeeklyMarket]) -> list[dict[str, str]]:
    """Build structured questions from tradeable markets."""
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

        questions.append({
            "id": q_key,
            "condition_id": m.condition_id,
            "question": q_text,
            "asset": m.asset,
            "market_type": m.market_type,
            "current_market_price": m.yes_price,
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

RESPOND WITH STRICT JSON ONLY. No text before or after. Format:
{{"predictions": {{"question_id": probability, ...}}, "overall_regime": "regime_name", "model_confidence": 0.0_to_1.0}}"""


def _build_user_prompt(questions: list[dict]) -> str:
    """Build the user prompt with questions."""
    lines = ["Estimate YES probability for each question:\n"]
    for q in questions:
        lines.append(f"- {q['id']}: {q['question']} (current market: {q['current_market_price']:.1%})")
    lines.append(f"\nRespond with strict JSON. Keys in predictions must match: {[q['id'] for q in questions]}")
    return "\n".join(lines)


def run_ensemble(
    cfg: OracleConfig,
    markets: list[WeeklyMarket],
    context: MarketContext,
) -> EnsembleResult:
    """Run the full ensemble: build questions → query models → average."""
    questions = build_questions(markets)
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
