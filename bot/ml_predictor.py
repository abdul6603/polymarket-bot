"""Garves ML Win Predictor — Random Forest classifier for trade outcome prediction.

Trains on historical resolved trades to predict win probability.
Features: indicator votes (one-hot), asset, timeframe, edge, confidence,
probability, direction, hour_of_day, consensus metrics, orderbook metrics.

The model is:
- Trained offline via scripts/train_ml_model.py or auto-retrain on daily archive
- Saved to data/models/garves_rf_model.joblib
- Loaded once at bot startup
- Inference per signal: <1ms
- NEVER blocks trading if unavailable (graceful fallback)
"""
from __future__ import annotations

import json
import logging
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
MODELS_DIR = DATA_DIR / "models"
MODEL_PATH = MODELS_DIR / "garves_rf_model.joblib"
METRICS_PATH = MODELS_DIR / "garves_rf_metrics.json"

# All indicators the model knows about (sorted, stable order)
ALL_INDICATORS = [
    "bollinger", "ema", "funding_rate", "heikin_ashi", "liquidation",
    "liquidity", "macd", "momentum", "news", "order_flow", "orderbook",
    "price_div", "rsi", "sentiment", "spot_depth", "temporal_arb",
    "tvl_momentum", "volume_spike", "vwap",
]

ASSET_MAP = {"bitcoin": 0, "ethereum": 1, "solana": 2, "xrp": 3}
TF_MAP = {"5m": 0, "15m": 1, "1h": 2, "4h": 3, "weekly": 4}

# Feature names in order (for model consistency)
FEATURE_NAMES = (
    [f"ind_{name}" for name in ALL_INDICATORS]  # 19 indicator features
    + [
        "asset", "timeframe", "direction",
        "edge", "confidence", "probability", "implied_up_price",
        "hour_sin", "hour_cos",
        "num_indicators", "num_agreeing", "num_disagreeing", "consensus_ratio",
        "reward_risk_ratio", "regime_fng",
        "ob_liquidity_log", "ob_spread",
    ]
)


def _extract_features_from_dict(trade: dict) -> np.ndarray:
    """Extract feature vector from a trade dict (JSONL record).

    Returns numpy array of shape (len(FEATURE_NAMES),).
    """
    direction = trade.get("direction", "up")
    votes = trade.get("indicator_votes", {})

    # Indicator votes: +1 agrees with direction, -1 disagrees, 0 absent
    ind_features = []
    for ind_name in ALL_INDICATORS:
        vote = votes.get(ind_name)
        if vote is None:
            ind_features.append(0.0)
        elif vote == direction:
            ind_features.append(1.0)
        else:
            ind_features.append(-1.0)

    # Core features
    asset = float(ASSET_MAP.get(trade.get("asset", ""), 0))
    tf = float(TF_MAP.get(trade.get("timeframe", ""), 1))
    dir_bin = 1.0 if direction == "up" else 0.0
    edge = float(trade.get("edge", 0.0))
    confidence = float(trade.get("confidence", 0.0))
    probability = float(trade.get("probability", 0.5))
    implied_up = float(trade.get("implied_up_price", 0.5))

    # Time features (cyclical encoding)
    ts = trade.get("timestamp", 0)
    if ts > 0:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        hour = dt.hour + dt.minute / 60.0
    else:
        hour = 12.0  # default noon
    hour_sin = math.sin(2 * math.pi * hour / 24.0)
    hour_cos = math.cos(2 * math.pi * hour / 24.0)

    # Consensus metrics
    num_voting = len(votes)
    num_agreeing = sum(1 for v in votes.values() if v == direction)
    num_disagreeing = num_voting - num_agreeing
    consensus_ratio = num_agreeing / max(num_voting, 1)

    # Optional features (0 if missing)
    rr = float(trade.get("reward_risk_ratio", 0) or 0)
    fng = float(trade.get("regime_fng", -1))
    if fng < 0:
        fng = 50.0  # neutral default
    ob_liq = trade.get("ob_liquidity_usd", 0) or 0
    ob_liq_log = math.log1p(float(ob_liq))
    ob_spread = float(trade.get("ob_spread", 0) or 0)

    features = (
        ind_features
        + [
            asset, tf, dir_bin,
            edge, confidence, probability, implied_up,
            hour_sin, hour_cos,
            float(num_voting), float(num_agreeing), float(num_disagreeing), consensus_ratio,
            rr, fng,
            ob_liq_log, ob_spread,
        ]
    )
    return np.array(features, dtype=np.float64)


def _extract_features_from_signal(signal, snapshot) -> np.ndarray:
    """Extract feature vector from a Signal + AssetSignalSnapshot at runtime.

    Maps the live objects to the same feature space as training data.
    """
    direction = signal.direction
    votes = snapshot.indicator_votes if hasattr(snapshot, "indicator_votes") else {}

    # Indicator votes
    ind_features = []
    for ind_name in ALL_INDICATORS:
        vote = votes.get(ind_name)
        if vote is None:
            ind_features.append(0.0)
        elif vote == direction:
            ind_features.append(1.0)
        else:
            ind_features.append(-1.0)

    asset = float(ASSET_MAP.get(signal.asset, 0))
    tf = float(TF_MAP.get(signal.timeframe, 1))
    dir_bin = 1.0 if direction == "up" else 0.0
    edge = float(signal.edge)
    confidence = float(signal.confidence)
    probability = float(signal.probability)
    # implied_up_price: probability if direction is up, else 1-probability
    implied_up = probability if direction == "up" else (1.0 - probability)

    # Time features
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("America/New_York"))
    hour = now.hour + now.minute / 60.0
    hour_sin = math.sin(2 * math.pi * hour / 24.0)
    hour_cos = math.cos(2 * math.pi * hour / 24.0)

    # Consensus
    num_voting = len(votes)
    num_agreeing = sum(1 for v in votes.values() if v == direction)
    num_disagreeing = num_voting - num_agreeing
    consensus_ratio = num_agreeing / max(num_voting, 1)

    # Optional (use defaults at runtime — these get filled during recording)
    rr = float(signal.reward_risk_ratio) if getattr(signal, "reward_risk_ratio", None) else 0.0
    fng = 50.0  # Will be overridden if regime is available
    ob_liq_log = 0.0
    ob_spread = 0.0

    features = (
        ind_features
        + [
            asset, tf, dir_bin,
            edge, confidence, probability, implied_up,
            hour_sin, hour_cos,
            float(num_voting), float(num_agreeing), float(num_disagreeing), consensus_ratio,
            rr, fng,
            ob_liq_log, ob_spread,
        ]
    )
    return np.array(features, dtype=np.float64)


class GarvesMLPredictor:
    """Random Forest win predictor — loaded once at bot startup."""

    def __init__(self):
        self._model = None
        self._load_model()

    def _load_model(self) -> bool:
        """Load saved model from disk."""
        if not MODEL_PATH.exists():
            log.info("ML Predictor: no model at %s (run scripts/train_ml_model.py)", MODEL_PATH)
            return False
        try:
            import joblib
            self._model = joblib.load(MODEL_PATH)
            log.info("ML Predictor: model loaded (%s)", MODEL_PATH.name)
            return True
        except Exception as e:
            log.warning("ML Predictor: failed to load model: %s", str(e)[:100])
            return False

    def predict(self, signal, asset_snapshot) -> Optional[float]:
        """Predict win probability for a live signal.

        Returns float (0-1) or None if model unavailable.
        """
        if self._model is None:
            return None
        try:
            X = _extract_features_from_signal(signal, asset_snapshot).reshape(1, -1)
            proba = self._model.predict_proba(X)[0]
            # Class 1 = win
            win_idx = list(self._model.classes_).index(1) if 1 in self._model.classes_ else 1
            return float(proba[win_idx])
        except Exception as e:
            log.debug("ML predict failed: %s", str(e)[:100])
            return None

    def predict_from_dict(self, trade: dict) -> Optional[float]:
        """Predict from a raw trade dict (for batch scoring)."""
        if self._model is None:
            return None
        try:
            X = _extract_features_from_dict(trade).reshape(1, -1)
            proba = self._model.predict_proba(X)[0]
            win_idx = list(self._model.classes_).index(1) if 1 in self._model.classes_ else 1
            return float(proba[win_idx])
        except Exception:
            return None

    def reload(self) -> bool:
        """Reload model from disk (after retrain). Thread-safe swap."""
        return self._load_model()

    @staticmethod
    def collect_training_data() -> list[dict]:
        """Collect ALL resolved trades from every JSONL file."""
        trade_files = list(DATA_DIR.glob("trades*.jsonl"))
        # Also check for archive subdirectory
        archive_dir = DATA_DIR / "archives"
        if archive_dir.exists():
            trade_files.extend(archive_dir.glob("*.jsonl"))

        seen_ids: set[str] = set()
        trades: list[dict] = []

        for fpath in trade_files:
            try:
                with open(fpath) as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        trade = json.loads(line)
                        tid = trade.get("trade_id", "")
                        if not tid or tid in seen_ids:
                            continue
                        # Only resolved trades with clear outcome
                        if not trade.get("resolved"):
                            continue
                        outcome = trade.get("outcome", "")
                        if outcome not in ("up", "down"):
                            continue
                        seen_ids.add(tid)
                        trades.append(trade)
            except Exception as e:
                log.debug("Skipping %s: %s", fpath.name, str(e)[:80])

        log.info("Collected %d resolved trades from %d files", len(trades), len(trade_files))
        return trades

    @classmethod
    def train(cls, min_samples: int = 30) -> dict:
        """Train Random Forest on all resolved trades.

        Returns metrics dict. Saves model to disk.
        """
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.model_selection import StratifiedKFold, cross_val_score
        from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score
        import joblib

        trades = cls.collect_training_data()
        if len(trades) < min_samples:
            return {
                "status": "insufficient_data",
                "num_samples": len(trades),
                "min_required": min_samples,
            }

        # Build feature matrix and labels
        X_list = []
        y_list = []
        for trade in trades:
            X_list.append(_extract_features_from_dict(trade))
            y_list.append(1 if trade.get("won", False) else 0)

        X = np.array(X_list)
        y = np.array(y_list)

        win_rate = y.mean()
        log.info("Training: %d samples, %.1f%% win rate, %d features",
                 len(y), win_rate * 100, X.shape[1])

        # Train model
        model = RandomForestClassifier(
            n_estimators=200,
            max_depth=8,
            min_samples_leaf=5,
            min_samples_split=10,
            class_weight="balanced",
            random_state=42,
            n_jobs=-1,
        )
        model.fit(X, y)

        # Cross-validation
        cv = StratifiedKFold(n_splits=min(5, min(sum(y), sum(1 - y))), shuffle=True, random_state=42)
        cv_scores = cross_val_score(model, X, y, cv=cv, scoring="accuracy")
        cv_accuracy = float(cv_scores.mean())

        # Full-set metrics (for reference, not primary eval)
        y_pred = model.predict(X)
        accuracy = float(accuracy_score(y, y_pred))
        precision = float(precision_score(y, y_pred, zero_division=0))
        recall = float(recall_score(y, y_pred, zero_division=0))
        f1 = float(f1_score(y, y_pred, zero_division=0))

        # Feature importance
        importances = model.feature_importances_
        feature_imp = sorted(
            zip(FEATURE_NAMES, importances.tolist()),
            key=lambda x: -x[1],
        )

        # Save model
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        joblib.dump(model, MODEL_PATH)

        # Save metrics
        metrics = {
            "status": "trained",
            "num_samples": len(y),
            "win_rate": float(win_rate),
            "accuracy": accuracy,
            "cv_accuracy": cv_accuracy,
            "cv_std": float(cv_scores.std()),
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "num_features": X.shape[1],
            "top_features": feature_imp[:20],
            "model_path": str(MODEL_PATH),
            "trained_at": time.time(),
        }
        METRICS_PATH.write_text(json.dumps(metrics, indent=2))

        log.info("Model trained: CV=%.1f%% (±%.1f%%), F1=%.3f, saved to %s",
                 cv_accuracy * 100, cv_scores.std() * 100, f1, MODEL_PATH)
        log.info("Top 5 features: %s",
                 ", ".join(f"{n}={v:.3f}" for n, v in feature_imp[:5]))

        return metrics
