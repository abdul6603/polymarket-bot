"""Load historical candle and trade data for backtesting."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from bot.price_cache import Candle

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CANDLE_DIR = DATA_DIR / "candles"

# All trade files to merge (resolved trades only)
TRADE_FILES = [
    DATA_DIR / "trades.jsonl",
    DATA_DIR / "trades_old_strategy_feb15.jsonl",
    DATA_DIR / "trades_pre_fix_20260214_2359.jsonl",
]


def _load_jsonl(path: Path) -> list[dict]:
    """Load a JSONL file, returning list of dicts."""
    if not path.exists():
        return []
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return rows


def load_all_candles() -> dict[str, list[Candle]]:
    """Load candles from data/candles/*.jsonl → {asset: [Candle, ...]}."""
    result: dict[str, list[Candle]] = {}
    if not CANDLE_DIR.exists():
        log.warning("Candle directory not found: %s", CANDLE_DIR)
        return result

    for fpath in CANDLE_DIR.glob("*.jsonl"):
        asset = fpath.stem  # "bitcoin", "ethereum", "solana"
        candles = []
        for row in _load_jsonl(fpath):
            try:
                candles.append(Candle(
                    timestamp=row["timestamp"],
                    open=row["open"],
                    high=row["high"],
                    low=row["low"],
                    close=row["close"],
                    volume=row.get("volume", 0),
                ))
            except (KeyError, TypeError):
                continue
        # Sort by timestamp
        candles.sort(key=lambda c: c.timestamp)
        result[asset] = candles
        log.info("Loaded %d candles for %s", len(candles), asset)

    return result


def load_all_trades() -> list[dict]:
    """Load and merge all trade files, filtering to resolved trades only."""
    seen_ids: set[str] = set()
    all_trades: list[dict] = []

    for fpath in TRADE_FILES:
        rows = _load_jsonl(fpath)
        for t in rows:
            tid = t.get("trade_id", "")
            if not tid or tid in seen_ids:
                continue
            # Only include resolved trades with valid indicator_votes
            if not t.get("resolved", False):
                continue
            if not t.get("indicator_votes"):
                continue
            seen_ids.add(tid)
            all_trades.append(t)

    # Sort by timestamp
    all_trades.sort(key=lambda t: t.get("timestamp", 0))
    log.info("Loaded %d resolved trades from %d files", len(all_trades), len(TRADE_FILES))
    return all_trades


def load_indicator_accuracy() -> dict:
    """Load indicator_accuracy.json → {name: {total_votes, correct_votes, accuracy}}."""
    path = DATA_DIR / "indicator_accuracy.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}
