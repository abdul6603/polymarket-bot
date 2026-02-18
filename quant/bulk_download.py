"""Bulk download Binance historical klines from data.binance.vision.

Downloads monthly ZIP files for BTC/ETH/SOL/XRP at 5m, 15m, 1h intervals,
converts CSV → JSONL matching Quant's Candle dataclass format.

Usage:
    .venv/bin/python -m quant.bulk_download --all-assets --interval 5m --months 12
    .venv/bin/python -m quant.bulk_download --asset bitcoin --interval 1h --months 6
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import shutil
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import requests

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CANDLE_DIR = DATA_DIR / "candles"

# Asset name -> Binance symbol
ASSET_SYMBOLS = {
    "bitcoin": "BTCUSDT",
    "ethereum": "ETHUSDT",
    "solana": "SOLUSDT",
    "xrp": "XRPUSDT",
}

BASE_URL = "https://data.binance.vision/data/spot/monthly/klines"


def _download_monthly_zip(symbol: str, interval: str, year: int, month: int) -> bytes | None:
    """Download a single monthly klines ZIP file from Binance."""
    month_str = f"{year}-{month:02d}"
    filename = f"{symbol}-{interval}-{month_str}.zip"
    url = f"{BASE_URL}/{symbol}/{interval}/{filename}"

    try:
        resp = requests.get(url, timeout=30)
        if resp.status_code == 200:
            return resp.content
        elif resp.status_code == 404:
            log.debug("Not found: %s", url)
            return None
        else:
            log.warning("HTTP %d for %s", resp.status_code, url)
            return None
    except Exception as e:
        log.warning("Download failed for %s: %s", url, str(e)[:100])
        return None


def _parse_csv_from_zip(zip_bytes: bytes) -> list[dict]:
    """Extract CSV from ZIP and parse into candle dicts."""
    candles = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for name in zf.namelist():
            if not name.endswith(".csv"):
                continue
            with zf.open(name) as f:
                reader = csv.reader(io.TextIOWrapper(f, encoding="utf-8"))
                for row in reader:
                    if len(row) < 6:
                        continue
                    try:
                        # Binance CSV columns:
                        # 0: open_time, 1: open, 2: high, 3: low, 4: close,
                        # 5: volume, 6: close_time, ...
                        open_time_ms = int(row[0])
                        # Handle microsecond timestamps (from Jan 2025+)
                        if open_time_ms > 1e15:
                            open_time_ms = open_time_ms // 1000
                        timestamp = open_time_ms / 1000.0

                        candles.append({
                            "timestamp": timestamp,
                            "open": float(row[1]),
                            "high": float(row[2]),
                            "low": float(row[3]),
                            "close": float(row[4]),
                            "volume": float(row[5]),
                        })
                    except (ValueError, IndexError):
                        continue
    return candles


def download_asset(
    asset: str,
    interval: str = "5m",
    months: int = 12,
) -> int:
    """Download historical candles for one asset.

    Returns total candle count written.
    """
    symbol = ASSET_SYMBOLS.get(asset)
    if not symbol:
        log.error("Unknown asset: %s", asset)
        return 0

    CANDLE_DIR.mkdir(parents=True, exist_ok=True)
    output_file = CANDLE_DIR / f"{asset}.jsonl"

    # Backup existing file
    if output_file.exists():
        backup = CANDLE_DIR / f"{asset}.jsonl.bak"
        shutil.copy2(output_file, backup)
        log.info("Backed up %s → %s", output_file.name, backup.name)

    # Load existing candles for dedup
    existing_timestamps: set[float] = set()
    existing_candles: list[dict] = []
    if output_file.exists():
        with open(output_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    c = json.loads(line)
                    existing_timestamps.add(c["timestamp"])
                    existing_candles.append(c)
                except (json.JSONDecodeError, KeyError):
                    continue
        log.info("Loaded %d existing candles for %s", len(existing_candles), asset)

    # Calculate month range
    now = datetime.utcnow()
    all_candles = list(existing_candles)
    new_count = 0

    for i in range(months):
        target = now - timedelta(days=30 * i)
        year = target.year
        month = target.month

        log.info("Downloading %s %s %d-%02d...", symbol, interval, year, month)
        zip_bytes = _download_monthly_zip(symbol, interval, year, month)
        if zip_bytes is None:
            continue

        parsed = _parse_csv_from_zip(zip_bytes)
        for c in parsed:
            if c["timestamp"] not in existing_timestamps:
                existing_timestamps.add(c["timestamp"])
                all_candles.append(c)
                new_count += 1

        log.info("  → %d candles parsed (%d new)", len(parsed), new_count)

    # Sort by timestamp and write
    all_candles.sort(key=lambda c: c["timestamp"])

    # Deduplicate by timestamp (keep last occurrence)
    seen: dict[float, dict] = {}
    for c in all_candles:
        seen[c["timestamp"]] = c
    all_candles = sorted(seen.values(), key=lambda c: c["timestamp"])

    with open(output_file, "w") as f:
        for c in all_candles:
            f.write(json.dumps(c) + "\n")

    log.info(
        "%s: %d total candles written (%d new from Binance)",
        asset, len(all_candles), new_count,
    )
    return len(all_candles)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    parser = argparse.ArgumentParser(description="Download Binance historical klines")
    parser.add_argument("--asset", type=str, help="Asset name (bitcoin, ethereum, solana, xrp)")
    parser.add_argument("--all-assets", action="store_true", help="Download all assets")
    parser.add_argument("--interval", type=str, default="5m", help="Kline interval (5m, 15m, 1h)")
    parser.add_argument("--months", type=int, default=12, help="Number of months to download")
    args = parser.parse_args()

    if args.all_assets:
        assets = list(ASSET_SYMBOLS.keys())
    elif args.asset:
        assets = [args.asset]
    else:
        parser.error("Specify --asset or --all-assets")
        return

    total = 0
    for asset in assets:
        count = download_asset(asset, args.interval, args.months)
        total += count

    log.info("Done. Total candles across all assets: %d", total)


if __name__ == "__main__":
    main()
