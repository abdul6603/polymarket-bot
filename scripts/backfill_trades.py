#!/usr/bin/env python3
"""One-time backfill: populate data/trades.jsonl from resolution + snipe trade files.

Reads resolution_trades.jsonl and snipe_trades.jsonl, normalizes to the
common schema consumed by PatternGate / conviction / Kelly / safety rails,
deduplicates by trade_id, and writes to trades.jsonl.

Usage:
    python scripts/backfill_trades.py           # dry-run (default)
    python scripts/backfill_trades.py --execute  # actually write
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
RESOLUTION_FILE = DATA_DIR / "resolution_trades.jsonl"
SNIPE_FILE = DATA_DIR / "snipe_trades.jsonl"
TRADES_FILE = DATA_DIR / "trades.jsonl"


def _load_jsonl(path: Path) -> list[dict]:
    """Load all JSON lines from a file."""
    if not path.exists():
        return []
    entries = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def _normalize_resolution(rec: dict) -> dict | None:
    """Normalize a resolution_scalper trade record."""
    if not rec.get("resolved"):
        return None
    if rec.get("won") is None:
        return None

    direction = rec.get("direction", "").lower()
    won = bool(rec["won"])
    outcome = direction if won else ("down" if direction == "up" else "up")
    order_id = rec.get("order_id", "")
    trade_id = f"res-{order_id}" if order_id else ""

    return {
        "timestamp": rec.get("timestamp", 0),
        "resolved": True,
        "outcome": outcome,
        "asset": rec.get("asset", "").lower(),
        "timeframe": "5m",
        "direction": direction,
        "won": won,
        "pnl": round(rec.get("pnl", 0), 4),
        "size_usd": round(rec.get("bet_size", 0), 4),
        "entry_price": round(rec.get("market_price", 0), 6),
        "probability": round(rec.get("probability", 0), 6),
        "edge": round(rec.get("edge", 0), 6),
        "engine": "resolution_scalper",
        "trade_id": trade_id,
        "dry_run": rec.get("dry_run", True),
    }


def _normalize_snipe(rec: dict) -> dict | None:
    """Normalize a snipe engine trade record."""
    if rec.get("won") is None:
        return None

    direction = rec.get("direction", "").lower()
    won = bool(rec["won"])
    outcome = direction if won else ("down" if direction == "up" else "up")
    market_id = rec.get("market_id", "")
    ts = rec.get("timestamp", 0)
    trade_id = f"snipe-{market_id[:12]}_{int(ts)}" if market_id else ""

    return {
        "timestamp": ts,
        "resolved": True,
        "outcome": outcome,
        "asset": rec.get("asset", "").lower(),
        "timeframe": "5m",
        "direction": direction,
        "won": won,
        "pnl": round(rec.get("pnl_usd", 0), 4),
        "size_usd": round(rec.get("total_size_usd", 0), 4),
        "entry_price": round(rec.get("avg_entry", 0), 6),
        "probability": 0.0,
        "edge": 0.0,
        "engine": "snipe",
        "trade_id": trade_id,
        "dry_run": rec.get("dry_run", True),
    }


def main():
    execute = "--execute" in sys.argv

    # Load existing trade_ids from trades.jsonl to avoid duplicates
    existing_ids: set[str] = set()
    if TRADES_FILE.exists():
        for rec in _load_jsonl(TRADES_FILE):
            tid = rec.get("trade_id", "")
            if tid:
                existing_ids.add(tid)

    # Process resolution trades
    resolution_raw = _load_jsonl(RESOLUTION_FILE)
    snipe_raw = _load_jsonl(SNIPE_FILE)

    normalized: list[dict] = []
    skipped_unresolved = 0
    skipped_dup = 0

    for rec in resolution_raw:
        norm = _normalize_resolution(rec)
        if norm is None:
            skipped_unresolved += 1
            continue
        if norm["trade_id"] and norm["trade_id"] in existing_ids:
            skipped_dup += 1
            continue
        existing_ids.add(norm["trade_id"])
        normalized.append(norm)

    for rec in snipe_raw:
        norm = _normalize_snipe(rec)
        if norm is None:
            skipped_unresolved += 1
            continue
        if norm["trade_id"] and norm["trade_id"] in existing_ids:
            skipped_dup += 1
            continue
        existing_ids.add(norm["trade_id"])
        normalized.append(norm)

    # Sort by timestamp
    normalized.sort(key=lambda r: r["timestamp"])

    # Stats
    wins = sum(1 for r in normalized if r["won"])
    losses = len(normalized) - wins
    total_pnl = sum(r["pnl"] for r in normalized)

    print(f"Source: {len(resolution_raw)} resolution + {len(snipe_raw)} snipe = {len(resolution_raw) + len(snipe_raw)} total")
    print(f"Skipped: {skipped_unresolved} unresolved, {skipped_dup} duplicates")
    print(f"Normalized: {len(normalized)} trades ({wins}W-{losses}L, PnL ${total_pnl:+.2f})")

    if not execute:
        print("\nDRY RUN — pass --execute to write trades.jsonl")
        return

    # Write
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(TRADES_FILE, "a") as f:
        for rec in normalized:
            f.write(json.dumps(rec) + "\n")
        f.flush()
        os.fsync(f.fileno())

    print(f"\nWrote {len(normalized)} trades to {TRADES_FILE}")


if __name__ == "__main__":
    main()
