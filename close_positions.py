#!/usr/bin/env python3
"""Emergency position closer — sells specific positions at best bid."""
import json
import os
import sys
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType
from py_clob_client.order_builder.constants import SELL

# Positions to close: (token_id, shares, label)
POSITIONS = [
    (
        "51375462925125051162823486977809389025755055807755142299538190411976812420724",
        373.2123,
        "Man City vs Newcastle O/U 1.5 Under",
    ),
    (
        "7174431114858108911616733939476685659961596359793284598583092349402637457891",
        25.68,
        "Man City vs Newcastle O/U 2.5 Under",
    ),
]

CLOB_HOST = os.getenv("CLOB_HOST", "https://clob.polymarket.com")


def main():
    pk = os.getenv("PRIVATE_KEY") or os.getenv("PK")
    funder = os.getenv("FUNDER_ADDRESS")
    api_key = os.getenv("CLOB_API_KEY")
    api_secret = os.getenv("CLOB_API_SECRET")
    api_passphrase = os.getenv("CLOB_API_PASSPHRASE")

    if not pk:
        print("ERROR: No PRIVATE_KEY in env")
        sys.exit(1)

    client = ClobClient(
        CLOB_HOST,
        key=pk,
        chain_id=137,
        funder=funder or None,
        signature_type=2,
    )

    if api_key:
        client.set_api_creds(ApiCreds(
            api_key=api_key,
            api_secret=api_secret,
            api_passphrase=api_passphrase,
        ))
        print("L2 API creds set")
    else:
        print("WARNING: No L2 API key — L1 only")

    # Connection check
    try:
        ok = client.get_ok()
        print(f"CLOB connected: {ok}")
    except Exception as e:
        print(f"CLOB connection failed: {e}")
        sys.exit(1)

    for token_id, shares, label in POSITIONS:
        print(f"\n--- Closing: {label} ({shares} shares) ---")

        # Fetch best bid
        try:
            resp = requests.get(
                f"{CLOB_HOST}/book?token_id={token_id}",
                timeout=10,
            )
            book = resp.json()
            bids = book.get("bids", [])
            if bids:
                best_bid = max(float(b.get("price", 0.01)) for b in bids)
            else:
                best_bid = 0.01
                print(f"  WARNING: No bids — selling at $0.01")
            print(f"  Best bid: ${best_bid:.4f}")
        except Exception as e:
            print(f"  ERROR fetching book: {e}")
            continue

        # Place sell order
        try:
            args = OrderArgs(
                price=round(best_bid, 2),
                size=shares,
                side=SELL,
                token_id=token_id,
            )
            signed = client.create_order(args)
            resp = client.post_order(signed, OrderType.GTC)
            order_id = resp.get("orderID") or resp.get("id", "unknown")
            print(f"  SELL ORDER PLACED: {order_id}")
            print(f"  Expected return: ${best_bid * shares:.2f}")
        except Exception as e:
            print(f"  ERROR placing sell: {e}")


if __name__ == "__main__":
    main()
