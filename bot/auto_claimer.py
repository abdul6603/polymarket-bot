"""Auto-claim resolved Polymarket positions.

Queries the data API for redeemable positions, then calls
CTF.redeemPositions on-chain to convert winning tokens back to USDC.

Works for EOA wallets (Hawk, Snipe) directly via web3.
For proxy wallets (Maker), calls through the Gnosis Safe execTransaction.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from typing import Any

from web3 import Web3
from eth_account import Account

log = logging.getLogger(__name__)

# Polygon contracts
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
POLYGON_RPC = "https://polygon-bor-rpc.publicnode.com"

CTF_ABI = [
    {"inputs":[{"name":"collateralToken","type":"address"},{"name":"parentCollectionId","type":"bytes32"},{"name":"conditionId","type":"bytes32"},{"name":"indexSets","type":"uint256[]"}],"name":"redeemPositions","outputs":[],"stateMutability":"nonpayable","type":"function"},
]

SAFE_ABI = [
    {"inputs":[{"name":"to","type":"address"},{"name":"value","type":"uint256"},{"name":"data","type":"bytes"},{"name":"operation","type":"uint8"},{"name":"safeTxGas","type":"uint256"},{"name":"baseGas","type":"uint256"},{"name":"gasPrice","type":"uint256"},{"name":"gasToken","type":"address"},{"name":"refundReceiver","type":"address"},{"name":"signatures","type":"bytes"}],"name":"execTransaction","outputs":[{"name":"success","type":"bool"}],"stateMutability":"payable","type":"function"},
    {"inputs":[],"name":"nonce","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"getThreshold","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"},
]


def _fetch_redeemable(wallet: str) -> list[dict]:
    """Query Polymarket data API for redeemable positions with value."""
    url = f"https://data-api.polymarket.com/positions?user={wallet.lower()}&limit=500"
    req = urllib.request.Request(url, headers={"User-Agent": "AutoClaimer/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        positions = json.loads(resp.read().decode())
    return [
        p for p in positions
        if p.get("redeemable") and float(p.get("currentValue", 0)) > 0
    ]


def _is_proxy(w3: Web3, address: str) -> bool:
    """Check if an address is a contract (proxy wallet) or EOA."""
    return len(w3.eth.get_code(Web3.to_checksum_address(address))) > 0


def claim_for_eoa(
    w3: Web3, private_key: str, condition_ids: list[str]
) -> list[str]:
    """Redeem positions directly from an EOA wallet. Returns tx hashes."""
    acct = Account.from_key(private_key)
    ctf = w3.eth.contract(
        address=Web3.to_checksum_address(CTF_ADDRESS), abi=CTF_ABI
    )
    collateral = Web3.to_checksum_address(USDC_E)
    parent = b"\x00" * 32
    tx_hashes = []
    nonce = w3.eth.get_transaction_count(acct.address)

    for cid in condition_ids:
        cid_bytes = bytes.fromhex(cid.replace("0x", ""))
        tx = ctf.functions.redeemPositions(
            collateral, parent, cid_bytes, [1, 2]
        ).build_transaction({
            "from": acct.address,
            "nonce": nonce,
            "gasPrice": w3.eth.gas_price,
            "gas": 200_000,
            "chainId": 137,
        })
        signed = acct.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        tx_hashes.append(tx_hash.hex())
        nonce += 1

    return tx_hashes


def claim_for_proxy(
    w3: Web3, private_key: str, proxy_address: str, condition_ids: list[str]
) -> list[str]:
    """Redeem positions through a Gnosis Safe proxy wallet. Returns tx hashes."""
    acct = Account.from_key(private_key)
    proxy = Web3.to_checksum_address(proxy_address)
    safe = w3.eth.contract(address=proxy, abi=SAFE_ABI)
    ctf = w3.eth.contract(
        address=Web3.to_checksum_address(CTF_ADDRESS), abi=CTF_ABI
    )
    collateral = Web3.to_checksum_address(USDC_E)
    parent = b"\x00" * 32
    tx_hashes = []
    nonce = w3.eth.get_transaction_count(acct.address)

    for cid in condition_ids:
        cid_bytes = bytes.fromhex(cid.replace("0x", ""))
        call_data = ctf.functions.redeemPositions(
            collateral, parent, cid_bytes, [1, 2]
        ).build_transaction({"from": proxy, "gas": 0})["data"]

        # Gnosis Safe pre-approved signature (owner == msg.sender)
        sig = (
            b"\x00" * 12
            + bytes.fromhex(acct.address[2:])
            + b"\x00" * 32
            + b"\x01"
        )

        tx = safe.functions.execTransaction(
            Web3.to_checksum_address(CTF_ADDRESS),  # to
            0,                                       # value
            bytes.fromhex(call_data[2:]),            # data
            0,                                       # operation (CALL)
            0, 0, 0,                                 # gas params (0 = use tx gas)
            "0x0000000000000000000000000000000000000000",  # gasToken
            "0x0000000000000000000000000000000000000000",  # refundReceiver
            sig,                                     # signatures
        ).build_transaction({
            "from": acct.address,
            "nonce": nonce,
            "gasPrice": w3.eth.gas_price,
            "gas": 300_000,
            "chainId": 137,
        })
        signed = acct.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        tx_hashes.append(tx_hash.hex())
        nonce += 1

    return tx_hashes


def auto_claim(wallet_address: str, private_key: str) -> dict[str, Any]:
    """Auto-claim all redeemable positions for a wallet.

    Returns: {claimed: int, usdc: float, tx_hashes: list, errors: list}
    """
    result = {"claimed": 0, "usdc": 0.0, "tx_hashes": [], "errors": []}

    try:
        redeemable = _fetch_redeemable(wallet_address)
    except Exception as e:
        result["errors"].append(f"API fetch failed: {e}")
        return result

    if not redeemable:
        return result

    total_value = sum(float(p.get("currentValue", 0)) for p in redeemable)
    condition_ids = list({p["conditionId"] for p in redeemable if p.get("conditionId")})
    log.info(
        "[CLAIM] %d redeemable positions worth $%.2f on %s",
        len(redeemable), total_value, wallet_address[:10],
    )

    try:
        w3 = Web3(Web3.HTTPProvider(POLYGON_RPC, request_kwargs={"timeout": 15}))
        is_proxy = _is_proxy(w3, wallet_address)

        if is_proxy:
            tx_hashes = claim_for_proxy(w3, private_key, wallet_address, condition_ids)
        else:
            tx_hashes = claim_for_eoa(w3, private_key, condition_ids)

        result["claimed"] = len(condition_ids)
        result["usdc"] = total_value
        result["tx_hashes"] = tx_hashes
        log.info("[CLAIM] Submitted %d redeem txs for $%.2f USDC", len(tx_hashes), total_value)

    except Exception as e:
        result["errors"].append(f"Redeem failed: {e}")
        log.exception("[CLAIM] Redeem error")

    return result
