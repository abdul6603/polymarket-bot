"""CryptoCompare gap-fill supplement for Quant historical data.

Fetches hourly candles to fill gaps in Binance bulk download data.
Free tier, no API key required for basic access.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import requests

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CANDLE_DIR = DATA_DIR / "candles"

BASE_URL = "https://min-api.cryptocompare.com/data/v2"

# Asset -> CryptoCompare symbol
ASSET_SYMBOLS = {
    "bitcoin": "BTC",
    "ethereum": "ETH",
    "solana": "SOL",
    "xrp": "XRP",
}


def fetch_hourly_candles(
    asset: str,
    limit: int = 2000,
    to_ts: int | None = None,
) -> list[dict]:
    """Fetch hourly candles from CryptoCompare.

    Args:
        asset: Internal asset name (bitcoin, ethereum, etc.)
        limit: Max candles per request (up to 2000 = ~83 days)
        to_ts: Unix timestamp for end of range (default: now)

    Returns:
        List of candle dicts matching Quant format.
    """
    symbol = ASSET_SYMBOLS.get(asset)
    if not symbol:
        log.error("Unknown asset: %s", asset)
        return []

    params = {
        "fsym": symbol,
        "tsym": "USD",
        "limit": min(limit, 2000),
    }
    if to_ts:
        params["toTs"] = to_ts

    try:
        resp = requests.get(f"{BASE_URL}/histohour", params=params, timeout=15)
        if resp.status_code != 200:
            log.warning("CryptoCompare HTTP %d for %s", resp.status_code, asset)
            return []

        data = resp.json()
        if data.get("Response") != "Success":
            log.warning("CryptoCompare error: %s", data.get("Message", "unknown"))
            return []

        candles = []
        for entry in data.get("Data", {}).get("Data", []):
            if entry.get("close", 0) == 0:
                continue
            candles.append({
                "timestamp": float(entry["time"]),
                "open": float(entry["open"]),
                "high": float(entry["high"]),
                "low": float(entry["low"]),
                "close": float(entry["close"]),
                "volume": float(entry.get("volumefrom", 0)),
            })
        return candles

    except Exception as e:
        log.warning("CryptoCompare fetch failed for %s: %s", asset, str(e)[:100])
        return []


def detect_gaps(candles: list[dict], interval_seconds: int = 300) -> list[tuple[float, float]]:
    """Detect gaps in sorted candle data.

    Returns list of (gap_start_ts, gap_end_ts) tuples where
    consecutive candles are more than 2x the expected interval apart.
    """
    if len(candles) < 2:
        return []

    gaps = []
    threshold = interval_seconds * 2
    for i in range(1, len(candles)):
        diff = candles[i]["timestamp"] - candles[i - 1]["timestamp"]
        if diff > threshold:
            gaps.append((candles[i - 1]["timestamp"], candles[i]["timestamp"]))

    return gaps


def fill_gaps(asset: str, interval_seconds: int = 300) -> int:
    """Fill gaps in existing candle data using CryptoCompare hourly data.

    Note: CryptoCompare only provides hourly data for free, so this
    supplements gaps but doesn't match 5m granularity.

    Returns number of candles added.
    """
    candle_file = CANDLE_DIR / f"{asset}.jsonl"
    if not candle_file.exists():
        log.warning("No candle file for %s", asset)
        return 0

    # Load existing candles
    existing = []
    existing_timestamps: set[float] = set()
    with open(candle_file) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                c = json.loads(line)
                existing.append(c)
                existing_timestamps.add(c["timestamp"])
            except (json.JSONDecodeError, KeyError):
                continue

    existing.sort(key=lambda c: c["timestamp"])
    gaps = detect_gaps(existing, interval_seconds)

    if not gaps:
        log.info("No gaps detected for %s", asset)
        return 0

    log.info("Found %d gaps in %s data", len(gaps), asset)

    added = 0
    for gap_start, gap_end in gaps:
        # Fetch hourly candles covering the gap
        cc_candles = fetch_hourly_candles(
            asset,
            limit=2000,
            to_ts=int(gap_end),
        )

        for c in cc_candles:
            ts = c["timestamp"]
            if gap_start < ts < gap_end and ts not in existing_timestamps:
                existing.append(c)
                existing_timestamps.add(ts)
                added += 1

        time.sleep(0.5)  # Be respectful to API

    if added > 0:
        # Sort and rewrite
        existing.sort(key=lambda c: c["timestamp"])
        with open(candle_file, "w") as f:
            for c in existing:
                f.write(json.dumps(c) + "\n")
        log.info("Added %d gap-fill candles for %s", added, asset)

    return added
