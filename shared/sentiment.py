"""FinBERT Sentiment Analyzer — Free local financial sentiment scoring.

Uses ProsusAI/finbert (fine-tuned BERT for financial text) to score
news headlines and articles without burning cloud API tokens.

Runs on CPU (Apple Silicon MPS when available). Model cached after
first load (~500MB download, then instant).

Usage:
    from shared.sentiment import score_headline, score_batch

    result = score_headline("Bitcoin crashes 10% amid market panic")
    # {"label": "negative", "score": 0.94, "positive": 0.02, "negative": 0.94, "neutral": 0.04}

    results = score_batch(["BTC up 5%", "Market crash fears grow"])
"""
from __future__ import annotations

import logging
from functools import lru_cache

log = logging.getLogger(__name__)

_pipeline = None
_load_attempted = False


def _get_pipeline():
    """Lazy-load FinBERT pipeline. Cached after first call."""
    global _pipeline, _load_attempted
    if _pipeline is not None:
        return _pipeline
    if _load_attempted:
        return None  # Already failed, don't retry this session

    _load_attempted = True
    try:
        from transformers import pipeline
        import torch

        # Use MPS (Apple Silicon GPU) if available, else CPU
        device = "mps" if torch.backends.mps.is_available() else "cpu"
        log.info("Loading FinBERT model (device=%s)...", device)

        _pipeline = pipeline(
            "sentiment-analysis",
            model="ProsusAI/finbert",
            device=device,
            truncation=True,
            max_length=512,
        )
        log.info("FinBERT loaded successfully on %s", device)
        return _pipeline
    except Exception:
        log.exception("Failed to load FinBERT — sentiment scoring disabled")
        return None


def score_headline(text: str) -> dict | None:
    """Score a single headline/text.

    Returns:
        {"label": "positive"|"negative"|"neutral",
         "score": float (confidence of top label),
         "positive": float, "negative": float, "neutral": float}
        or None if model unavailable.
    """
    pipe = _get_pipeline()
    if pipe is None:
        return None

    try:
        # top_k=None returns all class scores
        all_scores = pipe(text[:512], top_k=None)
        probs = {s["label"]: round(s["score"], 4) for s in all_scores}

        # Top label
        top = max(all_scores, key=lambda s: s["score"])

        return {
            "label": top["label"],
            "score": round(top["score"], 4),
            "positive": probs.get("positive", 0),
            "negative": probs.get("negative", 0),
            "neutral": probs.get("neutral", 0),
        }
    except Exception:
        log.debug("FinBERT scoring failed for: %s", text[:50])
        return None


def score_batch(texts: list[str], batch_size: int = 16) -> list[dict]:
    """Score a batch of headlines. Returns list of result dicts.

    Missing/failed entries are returned as
    {"label": "neutral", "score": 0.5, "positive": 0, "negative": 0, "neutral": 1}.
    """
    pipe = _get_pipeline()
    if pipe is None:
        return [_neutral() for _ in texts]

    try:
        truncated = [t[:512] for t in texts]

        # Batch inference with all scores
        batch_results = pipe(truncated, batch_size=batch_size, top_k=None)

        output = []
        for scores in batch_results:
            probs = {s["label"]: round(s["score"], 4) for s in scores}
            top = max(scores, key=lambda s: s["score"])
            output.append({
                "label": top["label"],
                "score": round(top["score"], 4),
                "positive": probs.get("positive", 0),
                "negative": probs.get("negative", 0),
                "neutral": probs.get("neutral", 0),
            })
        return output
    except Exception:
        log.exception("FinBERT batch scoring failed")
        return [_neutral() for _ in texts]


def sentiment_to_float(result: dict) -> float:
    """Convert sentiment result to a float: -1 (bearish) to +1 (bullish).

    Uses weighted combination: positive - negative.
    """
    if result is None:
        return 0.0
    return round(result.get("positive", 0) - result.get("negative", 0), 4)


def _neutral() -> dict:
    return {"label": "neutral", "score": 0.5, "positive": 0, "negative": 0, "neutral": 1}
