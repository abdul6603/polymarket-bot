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

# Track condition IDs that failed redeem (already claimed or reverted)
_failed_cids: dict[str, float] = {}  # conditionId -> timestamp of last failure
_FAIL_COOLDOWN = 3600  # skip retrying for 1 hour after failure

# Polygon contracts
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
POLYGON_RPC = "https://polygon-bor-rpc.publicnode.com"
MAX_GAS_PRICE = 300 * 10**9  # 300 gwei cap — Polygon gas spikes above 200 sometimes

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
        # Redeem each index set separately — [1,2] is a merge (needs both sides),
        # but we typically hold only the winning side.
        for idx_set in [[1], [2]]:
            tx = ctf.functions.redeemPositions(
                collateral, parent, cid_bytes, idx_set
            ).build_transaction({
                "from": acct.address,
                "nonce": nonce,
                "gasPrice": min(w3.eth.gas_price, MAX_GAS_PRICE),
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

    # Check USDC balance before claiming
    usdc_abi = [{"constant":True,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"}]
    usdc_contract = w3.eth.contract(address=Web3.to_checksum_address(USDC_E), abi=usdc_abi)
    pre_bal = usdc_contract.functions.balanceOf(proxy).call() / 1e6

    # Gnosis Safe pre-approved signature (owner == msg.sender)
    sig = (
        b"\x00" * 12
        + bytes.fromhex(acct.address[2:])
        + b"\x00" * 32
        + b"\x01"
    )

    for cid in condition_ids:
        cid_bytes = bytes.fromhex(cid.replace("0x", ""))
        # Redeem each index set separately — [1,2] is a merge (needs both sides),
        # but we typically hold only the winning side.
        for idx_set in [[1], [2]]:
            call_data = ctf.encode_abi(
                "redeemPositions",
                [collateral, parent, cid_bytes, idx_set],
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
                "gasPrice": min(w3.eth.gas_price, MAX_GAS_PRICE),
                "gas": 350_000,
                "chainId": 137,
            })
            signed = acct.sign_transaction(tx)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            tx_hashes.append(tx_hash.hex())
            log.info("[CLAIM] Sent proxy redeem tx %s (nonce=%d, idx=%s)",
                     tx_hash.hex()[:18], nonce, idx_set)
            # Wait for confirmation before next tx
            try:
                receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
                log.info("[CLAIM] Confirmed block=%d status=%d gas_used=%d",
                         receipt.blockNumber, receipt.status, receipt.gasUsed)
            except Exception as e:
                log.warning("[CLAIM] Wait for receipt failed: %s", str(e)[:100])
                _failed_cids[cid] = time.time()
            nonce += 1

    # Check USDC balance after claiming — if unchanged, mark all cids as failed
    try:
        post_bal = usdc_contract.functions.balanceOf(proxy).call() / 1e6
        if post_bal <= pre_bal + 0.01:
            for cid in condition_ids:
                _failed_cids[cid] = time.time()
            log.warning("[CLAIM] USDC unchanged (%.2f -> %.2f) — %d cids marked failed (1h cooldown)",
                       pre_bal, post_bal, len(condition_ids))
    except Exception:
        pass

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

    # Filter out condition IDs that recently failed (already claimed)
    now = time.time()
    all_cids = {p["conditionId"] for p in redeemable if p.get("conditionId")}
    condition_ids = [
        cid for cid in all_cids
        if cid not in _failed_cids or (now - _failed_cids[cid]) > _FAIL_COOLDOWN
    ]
    if not condition_ids:
        skipped = len(all_cids)
        if skipped:
            log.debug("[CLAIM] Skipping %d condition IDs in cooldown (already claimed)", skipped)
        return result

    log.info(
        "[CLAIM] %d redeemable positions worth $%.2f on %s (%d skipped in cooldown)",
        len(redeemable), total_value, wallet_address[:10],
        len(all_cids) - len(condition_ids),
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
