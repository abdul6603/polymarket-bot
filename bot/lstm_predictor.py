"""LSTM Price Direction Predictor — PyTorch neural indicator for Garves.

Trains on candle history (OHLCV) to predict next-candle direction.
Plugs into the signal ensemble as indicator "lstm".

Architecture: 2-layer LSTM → Linear → Sigmoid (binary UP/DOWN).
Features per candle: returns, volume_change, high-low range, close position
in range, plus 5/10/20 SMA ratios.

Auto-trains on startup if model file missing or stale. Retrains every
6 hours during runtime.

Usage:
    from bot.lstm_predictor import predict_direction

    result = predict_direction("bitcoin", candles)
    # {"direction": "up", "confidence": 0.72, "model_age_hours": 1.5}
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
MODEL_DIR = DATA_DIR / "models"
CANDLE_DIR = DATA_DIR / "candles"

SEQUENCE_LENGTH = 30  # Look back 30 candles
FEATURE_COUNT = 8     # Features per candle
HIDDEN_SIZE = 64
NUM_LAYERS = 2
RETRAIN_INTERVAL = 6 * 3600  # 6 hours

_models: dict[str, tuple[nn.Module, float]] = {}  # asset -> (model, last_train_time)


class PriceLSTM(nn.Module):
    """2-layer LSTM for binary direction prediction."""

    def __init__(self, input_size=FEATURE_COUNT, hidden_size=HIDDEN_SIZE, num_layers=NUM_LAYERS):
        super().__init__()
        self.lstm = nn.LSTM(input_size, hidden_size, num_layers, batch_first=True, dropout=0.2)
        self.fc = nn.Linear(hidden_size, 1)

    def forward(self, x):
        # x: (batch, seq_len, features)
        lstm_out, _ = self.lstm(x)
        last_hidden = lstm_out[:, -1, :]  # Take last timestep
        return torch.sigmoid(self.fc(last_hidden))


def _load_candles(asset: str) -> list[dict]:
    """Load candle data from JSONL."""
    path = CANDLE_DIR / f"{asset}.jsonl"
    if not path.exists():
        return []
    candles = []
    for line in open(path):
        line = line.strip()
        if line:
            try:
                candles.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    # Sort by timestamp
    candles.sort(key=lambda c: c.get("timestamp", 0))
    return candles


def _extract_features(candles: list[dict]) -> np.ndarray:
    """Extract feature matrix from candles.

    Features per candle (8):
        0: log return (close/prev_close - 1)
        1: volume change ratio
        2: high-low range / close
        3: close position in range (0=low, 1=high)
        4: close / SMA5 ratio
        5: close / SMA10 ratio
        6: close / SMA20 ratio
        7: volume / avg_volume_20 ratio
    """
    if len(candles) < 25:  # Need at least 20 for SMA + some buffer
        return np.array([])

    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    volumes = [c.get("volume", 0) for c in candles]

    features = []
    for i in range(20, len(candles)):
        c = candles[i]
        prev_c = candles[i - 1]

        # Log return
        ret = (c["close"] / prev_c["close"] - 1) if prev_c["close"] > 0 else 0

        # Volume change
        vol_change = (volumes[i] / max(volumes[i - 1], 1e-10) - 1) if volumes[i - 1] > 0 else 0
        vol_change = max(-5, min(5, vol_change))  # Clamp

        # Range
        hl_range = (c["high"] - c["low"]) / max(c["close"], 1e-10)

        # Close position in range
        denom = c["high"] - c["low"]
        close_pos = (c["close"] - c["low"]) / denom if denom > 0 else 0.5

        # SMA ratios
        sma5 = np.mean(closes[i - 5:i])
        sma10 = np.mean(closes[i - 10:i])
        sma20 = np.mean(closes[i - 20:i])

        sma5_ratio = c["close"] / sma5 - 1 if sma5 > 0 else 0
        sma10_ratio = c["close"] / sma10 - 1 if sma10 > 0 else 0
        sma20_ratio = c["close"] / sma20 - 1 if sma20 > 0 else 0

        # Volume vs average
        avg_vol_20 = np.mean(volumes[i - 20:i])
        vol_ratio = volumes[i] / max(avg_vol_20, 1e-10) - 1

        features.append([ret, vol_change, hl_range, close_pos,
                         sma5_ratio, sma10_ratio, sma20_ratio, vol_ratio])

    return np.array(features, dtype=np.float32)


def _create_sequences(features: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Create training sequences and labels.

    X: sequences of SEQUENCE_LENGTH candles
    y: 1 if next candle went up, 0 if down
    """
    if len(features) < SEQUENCE_LENGTH + 1:
        return np.array([]), np.array([])

    X, y = [], []
    for i in range(SEQUENCE_LENGTH, len(features) - 1):
        X.append(features[i - SEQUENCE_LENGTH:i])
        # Label: did the next candle go up? (positive return = 1)
        y.append(1.0 if features[i][0] > 0 else 0.0)  # features[i][0] = return

    return np.array(X), np.array(y)


def train_model(asset: str) -> dict:
    """Train LSTM model for an asset. Returns metrics dict."""
    candles = _load_candles(asset)
    if len(candles) < SEQUENCE_LENGTH + 50:
        return {"status": "insufficient_data", "candles": len(candles), "min_required": SEQUENCE_LENGTH + 50}

    features = _extract_features(candles)
    if len(features) == 0:
        return {"status": "feature_extraction_failed"}

    X, y = _create_sequences(features)
    if len(X) == 0:
        return {"status": "insufficient_sequences"}

    # Train/val split (80/20)
    split = int(len(X) * 0.8)
    X_train, X_val = X[:split], X[split:]
    y_train, y_val = y[:split], y[split:]

    # To tensors
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    X_train_t = torch.FloatTensor(X_train).to(device)
    y_train_t = torch.FloatTensor(y_train).unsqueeze(1).to(device)
    X_val_t = torch.FloatTensor(X_val).to(device)
    y_val_t = torch.FloatTensor(y_val).unsqueeze(1).to(device)

    # Model
    model = PriceLSTM().to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=0.001)
    criterion = nn.BCELoss()

    # Training loop
    best_val_acc = 0
    patience = 10
    patience_counter = 0
    epochs = 100

    for epoch in range(epochs):
        model.train()
        optimizer.zero_grad()
        output = model(X_train_t)
        loss = criterion(output, y_train_t)
        loss.backward()
        optimizer.step()

        # Validation
        model.eval()
        with torch.no_grad():
            val_out = model(X_val_t)
            val_loss = criterion(val_out, y_val_t)
            val_preds = (val_out > 0.5).float()
            val_acc = (val_preds == y_val_t).float().mean().item()

        if val_acc > best_val_acc:
            best_val_acc = val_acc
            patience_counter = 0
            # Save best model
            MODEL_DIR.mkdir(parents=True, exist_ok=True)
            torch.save(model.state_dict(), MODEL_DIR / f"lstm_{asset}.pt")
        else:
            patience_counter += 1
            if patience_counter >= patience:
                break

    # Cache model
    model.load_state_dict(torch.load(MODEL_DIR / f"lstm_{asset}.pt", weights_only=True))
    _models[asset] = (model, time.time())

    # Final metrics
    model.eval()
    with torch.no_grad():
        train_preds = (model(X_train_t) > 0.5).float()
        train_acc = (train_preds == y_train_t).float().mean().item()
        val_preds = (model(X_val_t) > 0.5).float()
        val_acc = (val_preds == y_val_t).float().mean().item()

    metrics = {
        "status": "trained",
        "asset": asset,
        "candles": len(candles),
        "sequences": len(X),
        "train_acc": round(train_acc, 4),
        "val_acc": round(val_acc, 4),
        "epochs": epoch + 1,
        "device": device,
    }

    # Save metrics
    (MODEL_DIR / f"lstm_{asset}.metrics.json").write_text(json.dumps(metrics, indent=2))
    log.info("LSTM %s trained: %d candles, train_acc=%.1f%%, val_acc=%.1f%% (%d epochs, %s)",
             asset, len(candles), train_acc * 100, val_acc * 100, epoch + 1, device)

    return metrics


def _get_model(asset: str) -> PriceLSTM | None:
    """Get cached or load model for asset."""
    # Check cache
    if asset in _models:
        model, train_time = _models[asset]
        # Retrain if stale
        if time.time() - train_time > RETRAIN_INTERVAL:
            log.info("LSTM %s model stale (%.1fh), retraining...",
                     asset, (time.time() - train_time) / 3600)
            train_model(asset)
        if asset in _models:
            return _models[asset][0]

    # Try loading from disk
    model_path = MODEL_DIR / f"lstm_{asset}.pt"
    if model_path.exists():
        try:
            device = "mps" if torch.backends.mps.is_available() else "cpu"
            model = PriceLSTM().to(device)
            model.load_state_dict(torch.load(model_path, map_location=device, weights_only=True))
            model.eval()
            _models[asset] = (model, model_path.stat().st_mtime)
            return model
        except Exception:
            log.exception("Failed to load LSTM model for %s", asset)

    # Train new model
    result = train_model(asset)
    if result.get("status") == "trained":
        return _models.get(asset, (None, 0))[0]
    return None


def predict_direction(asset: str, candles: list[dict] | None = None) -> dict | None:
    """Predict next candle direction for an asset.

    Args:
        asset: e.g. "bitcoin", "ethereum"
        candles: Optional candle list. If None, loads from disk.

    Returns:
        {"direction": "up"|"down", "confidence": float, "model_age_hours": float}
        or None if prediction unavailable.
    """
    model = _get_model(asset)
    if model is None:
        return None

    if candles is None:
        candles = _load_candles(asset)

    if len(candles) < SEQUENCE_LENGTH + 25:
        return None

    features = _extract_features(candles)
    if len(features) < SEQUENCE_LENGTH:
        return None

    # Take last SEQUENCE_LENGTH features
    seq = features[-SEQUENCE_LENGTH:]
    device = next(model.parameters()).device

    model.eval()
    with torch.no_grad():
        x = torch.FloatTensor(seq).unsqueeze(0).to(device)
        prob = model(x).item()

    direction = "up" if prob > 0.5 else "down"
    confidence = prob if prob > 0.5 else 1 - prob

    # Model age
    train_time = _models.get(asset, (None, 0))[1]
    age_hours = (time.time() - train_time) / 3600 if train_time else 0

    return {
        "direction": direction,
        "confidence": round(confidence, 4),
        "raw_prob": round(prob, 4),
        "model_age_hours": round(age_hours, 1),
    }
