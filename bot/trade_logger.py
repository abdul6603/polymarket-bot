"""Centralized normalized trade writer for Garves learning systems.

All engines call append_normalized_trade() to cross-write resolved trades
into data/trades.jsonl — the single source consumed by PatternGate,
conviction scoring, Kelly sizing, safety rails, and LLM synthesis.

Zero imports from bot/* to avoid circular dependency risk.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
TRADES_FILE = DATA_DIR / "trades.jsonl"


def append_normalized_trade(
    *,
    asset: str,
    direction: str,
    won: bool,
    pnl: float,
    size_usd: float = 0.0,
    entry_price: float = 0.0,
    probability: float = 0.0,
    edge: float = 0.0,
    timeframe: str = "5m",
    engine: str = "",
    trade_id: str = "",
    indicator_votes: dict | None = None,
    regime_label: str = "",
    dry_run: bool = True,
) -> None:
    """Append a single resolved trade to data/trades.jsonl.

    Atomic: flush + fsync. Auto-creates file if missing.
    Never raises — all errors are logged and swallowed.
    """
    try:
        outcome = direction if won else ("down" if direction == "up" else "up")
        record = {
            "timestamp": time.time(),
            "resolved": True,
            "outcome": outcome,
            "asset": asset.lower(),
            "timeframe": timeframe,
            "direction": direction.lower(),
            "won": won,
            "pnl": round(pnl, 4),
            "size_usd": round(size_usd, 4),
            "entry_price": round(entry_price, 6),
            "probability": round(probability, 6),
            "edge": round(edge, 6),
            "engine": engine,
            "trade_id": trade_id,
            "dry_run": dry_run,
        }
        if indicator_votes:
            record["indicator_votes"] = indicator_votes
        if regime_label:
            record["regime_label"] = regime_label

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(TRADES_FILE, "a") as f:
            f.write(json.dumps(record) + "\n")
            f.flush()
            os.fsync(f.fileno())
    except Exception:
        log.exception("[TRADE_LOGGER] Failed to append normalized trade")
