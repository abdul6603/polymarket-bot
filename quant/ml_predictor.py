"""XGBoost Trade Outcome Predictor — learns from resolved trades.

Trains on historical Hawk + Garves trades to predict win probability.
Features: edge, confidence, category, direction, volume, time_left,
risk_score, regime, indicator agreement, sportsbook backing.

Auto-trains when enough resolved trades exist (MIN_SAMPLES).
Model saved to data/models/xgb_trade_predictor.json.

Usage:
    from quant.ml_predictor import predict_trade, retrain_model

    # Score a new opportunity
    win_prob = predict_trade(opportunity_dict)

    # Retrain from latest data
    metrics = retrain_model()
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
MODEL_PATH = DATA_DIR / "models" / "xgb_trade_predictor.json"
HAWK_TRADES = DATA_DIR / "hawk_trades.jsonl"
GARVES_TRADES = DATA_DIR / "trades.jsonl"

MIN_SAMPLES = 30  # Minimum resolved trades to train
CATEGORY_MAP = {"sports": 0, "politics": 1, "crypto_event": 2, "other": 3, "crypto": 4}
DIRECTION_MAP = {"yes": 1, "no": 0, "up": 1, "down": 0}

_model = None


def _load_trades() -> list[dict]:
    """Load all resolved trades from Hawk + Garves."""
    trades = []
    for path in [HAWK_TRADES, GARVES_TRADES]:
        if not path.exists():
            continue
        for line in open(path):
            line = line.strip()
            if not line:
                continue
            try:
                t = json.loads(line)
                if t.get("resolved"):
                    trades.append(t)
            except json.JSONDecodeError:
                continue
    return trades


def _extract_features(t: dict) -> list[float]:
    """Extract feature vector from a trade dict.

    Features (14):
        0: edge
        1: confidence
        2: category_encoded
        3: direction_encoded
        4: entry_price
        5: size_usd
        6: risk_score
        7: time_left_hours
        8: estimated_prob
        9: expected_value
        10: has_sportsbook (1/0)
        11: volume (log-scaled)
        12: kelly_fraction
        13: vote_margin (from indicator_votes if available)
    """
    category = CATEGORY_MAP.get(t.get("category", "other"), 3)
    direction = DIRECTION_MAP.get(t.get("direction", "yes"), 1)
    volume = t.get("volume", t.get("ob_liquidity_usd", 10000))

    # Indicator vote margin (Garves has indicator_votes dict)
    votes = t.get("indicator_votes", {})
    if votes:
        up = sum(1 for v in votes.values() if v == "up")
        down = sum(1 for v in votes.values() if v == "down")
        vote_margin = abs(up - down) / max(len(votes), 1)
    else:
        vote_margin = 0.5  # Unknown

    return [
        float(t.get("edge", 0)),
        float(t.get("confidence", 0.5)),
        float(category),
        float(direction),
        float(t.get("entry_price", 0.5)),
        float(t.get("size_usd", 15)),
        float(t.get("risk_score", 5)),
        float(t.get("time_left_hours", 24)),
        float(t.get("estimated_prob", 0.5)),
        float(t.get("expected_value", 0)),
        1.0 if t.get("edge_source") == "sportsbook_divergence" else 0.0,
        float(np.log1p(volume)),
        float(t.get("kelly_fraction", 0.1)),
        float(vote_margin),
    ]


def retrain_model() -> dict:
    """Retrain XGBoost model on all resolved trades.

    Returns metrics dict with accuracy, precision, recall, f1, num_samples.
    """
    import xgboost as xgb
    from sklearn.model_selection import cross_val_score
    from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score

    trades = _load_trades()
    if len(trades) < MIN_SAMPLES:
        log.info("Not enough resolved trades to train: %d < %d", len(trades), MIN_SAMPLES)
        return {
            "status": "insufficient_data",
            "num_samples": len(trades),
            "min_required": MIN_SAMPLES,
        }

    X = np.array([_extract_features(t) for t in trades])
    y = np.array([1 if t.get("won") else 0 for t in trades])

    model = xgb.XGBClassifier(
        n_estimators=100,
        max_depth=4,
        learning_rate=0.1,
        min_child_weight=3,
        subsample=0.8,
        colsample_bytree=0.8,
        objective="binary:logistic",
        eval_metric="logloss",
        use_label_encoder=False,
    )

    # Cross-validation if enough data
    if len(trades) >= 50:
        cv_scores = cross_val_score(model, X, y, cv=5, scoring="accuracy")
        log.info("XGBoost CV accuracy: %.3f ± %.3f", cv_scores.mean(), cv_scores.std())
    else:
        cv_scores = np.array([0.0])

    # Train on full data
    model.fit(X, y)

    # Save model
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    model.save_model(str(MODEL_PATH))

    # Full-data metrics
    preds = model.predict(X)
    probs = model.predict_proba(X)[:, 1]

    # Feature importance
    feature_names = [
        "edge", "confidence", "category", "direction", "entry_price",
        "size_usd", "risk_score", "time_left_hours", "estimated_prob",
        "expected_value", "has_sportsbook", "log_volume", "kelly_fraction",
        "vote_margin",
    ]
    importances = dict(zip(feature_names, model.feature_importances_.tolist()))
    top_features = sorted(importances.items(), key=lambda x: x[1], reverse=True)[:5]

    metrics = {
        "status": "trained",
        "num_samples": len(trades),
        "win_rate": float(y.mean()),
        "accuracy": float(accuracy_score(y, preds)),
        "precision": float(precision_score(y, preds, zero_division=0)),
        "recall": float(recall_score(y, preds, zero_division=0)),
        "f1": float(f1_score(y, preds, zero_division=0)),
        "cv_accuracy": float(cv_scores.mean()) if len(trades) >= 50 else None,
        "top_features": top_features,
        "model_path": str(MODEL_PATH),
    }

    # Save metrics alongside model
    metrics_path = MODEL_PATH.with_suffix(".metrics.json")
    metrics_path.write_text(json.dumps(metrics, indent=2))

    log.info("XGBoost model trained: %d samples, acc=%.3f, f1=%.3f",
             len(trades), metrics["accuracy"], metrics["f1"])
    log.info("Top features: %s", ", ".join(f"{n}={v:.3f}" for n, v in top_features))

    global _model
    _model = model
    return metrics


def _get_model():
    """Load or return cached model."""
    global _model
    if _model is not None:
        return _model
    if not MODEL_PATH.exists():
        return None
    try:
        import xgboost as xgb
        _model = xgb.XGBClassifier()
        _model.load_model(str(MODEL_PATH))
        return _model
    except Exception:
        log.exception("Failed to load XGBoost model")
        return None


def predict_trade(trade: dict) -> float | None:
    """Predict win probability for a trade opportunity.

    Returns float 0-1 (win probability) or None if model not ready.
    """
    model = _get_model()
    if model is None:
        return None

    try:
        features = np.array([_extract_features(trade)])
        prob = model.predict_proba(features)[0][1]
        return float(prob)
    except Exception:
        log.exception("XGBoost prediction failed")
        return None


def score_opportunities(opportunities: list[dict]) -> list[dict]:
    """Add ml_win_prob to each opportunity dict. Returns enriched list."""
    model = _get_model()
    if model is None:
        return opportunities

    for opp in opportunities:
        prob = predict_trade(opp)
        if prob is not None:
            opp["ml_win_prob"] = round(prob, 4)
    return opportunities
