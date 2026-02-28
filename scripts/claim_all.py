"""Claim ALL redeemable positions across snipe + maker wallets.

One-shot script: run once to sweep expired winning tokens back to USDC.
Uses the existing bot/auto_claimer.py infrastructure.

Usage:
    .venv/bin/python -m scripts.claim_all             # dry-run (just report)
    .venv/bin/python -m scripts.claim_all --execute    # actually claim
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import urllib.request
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from bot.auto_claimer import _fetch_redeemable, auto_claim

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


# Wallets to sweep
WALLETS = {
    "snipe": {
        "address_env": "SNIPE_FUNDER_ADDRESS",
        "key_env": "SNIPE_PRIVATE_KEY",
    },
    "maker": {
        "address_env": "FUNDER_ADDRESS",
        "key_env": "PRIVATE_KEY",
    },
}


def scan_wallet(name: str, address: str) -> list[dict]:
    """Fetch redeemable positions for a wallet. Returns list of positions."""
    log.info("─── Scanning %s wallet: %s ───", name, address[:10] + "...")
    try:
        positions = _fetch_redeemable(address)
    except Exception as e:
        log.error("  Failed to fetch positions for %s: %s", name, e)
        return []

    if not positions:
        log.info("  No redeemable positions found.")
        return []

    total_value = sum(float(p.get("currentValue", 0)) for p in positions)
    condition_ids = list({p["conditionId"] for p in positions if p.get("conditionId")})
    log.info("  Found %d redeemable positions worth $%.4f (%d unique conditions)",
             len(positions), total_value, len(condition_ids))

    for p in positions:
        title = p.get("title", p.get("market", {}).get("question", "???"))[:60]
        val = float(p.get("currentValue", 0))
        size = float(p.get("size", 0))
        log.info("    • $%.4f  (%s shares)  %s", val, size, title)

    return positions


def claim_wallet(name: str, address: str, private_key: str) -> dict:
    """Run auto_claim for a wallet. Returns result dict."""
    log.info("─── Claiming %s wallet: %s ───", name, address[:10] + "...")
    result = auto_claim(address, private_key)
    if result["errors"]:
        for err in result["errors"]:
            log.error("  ERROR: %s", err)
    if result["claimed"] > 0:
        log.info("  Claimed %d conditions, ~$%.4f USDC", result["claimed"], result["usdc"])
        for tx in result["tx_hashes"]:
            log.info("    tx: https://polygonscan.com/tx/0x%s", tx if not tx.startswith("0x") else tx[2:])
    else:
        log.info("  Nothing to claim.")
    return result


def main():
    parser = argparse.ArgumentParser(description="Claim all redeemable Polymarket positions")
    parser.add_argument("--execute", action="store_true", help="Actually submit claim transactions (default: dry-run scan only)")
    args = parser.parse_args()

    grand_total = 0.0
    grand_positions = 0
    grand_claimed = 0
    all_errors = []

    for name, env in WALLETS.items():
        address = os.environ.get(env["address_env"], "")
        private_key = os.environ.get(env["key_env"], "")

        if not address:
            log.warning("Skipping %s — %s not set", name, env["address_env"])
            continue

        # Always scan first
        positions = scan_wallet(name, address)
        if not positions:
            continue

        total_value = sum(float(p.get("currentValue", 0)) for p in positions)
        grand_total += total_value
        grand_positions += len(positions)

        if args.execute:
            if not private_key:
                log.error("Cannot claim %s — %s not set", name, env["key_env"])
                all_errors.append(f"{name}: no private key")
                continue
            result = claim_wallet(name, address, private_key)
            grand_claimed += result["claimed"]
            all_errors.extend(result["errors"])
        else:
            log.info("  [DRY-RUN] Would claim $%.4f from %s. Use --execute to claim.", total_value, name)

    log.info("")
    log.info("═══ SUMMARY ═══")
    log.info("Total redeemable: %d positions worth $%.4f", grand_positions, grand_total)
    if args.execute:
        log.info("Claimed: %d conditions", grand_claimed)
    else:
        log.info("Mode: DRY-RUN (use --execute to actually claim)")
    if all_errors:
        log.warning("Errors: %s", "; ".join(all_errors))


if __name__ == "__main__":
    main()
