"""Auto-claim resolved Polymarket positions.

Queries the data API for redeemable positions, then calls:
  - CTF.redeemPositions for standard markets
  - NegRiskAdapter.redeemPositions for neg-risk (bracket) markets

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
NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"
USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
POLYGON_RPC = "https://polygon-bor-rpc.publicnode.com"
MAX_GAS_PRICE = 300 * 10**9  # 300 gwei cap

CTF_ABI = [
    {"inputs":[{"name":"collateralToken","type":"address"},{"name":"parentCollectionId","type":"bytes32"},{"name":"conditionId","type":"bytes32"},{"name":"indexSets","type":"uint256[]"}],"name":"redeemPositions","outputs":[],"stateMutability":"nonpayable","type":"function"},
]
NEG_RISK_ABI = [
    {"inputs":[{"name":"_conditionId","type":"bytes32"},{"name":"_amounts","type":"uint256[]"}],"name":"redeemPositions","outputs":[],"stateMutability":"nonpayable","type":"function"},
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


def _build_redeem_calls(w3: Web3, positions: list[dict]) -> list[dict]:
    """Build redeem call params for each position.

    Returns list of {to, data, cid, label} dicts — one per position.
    NegRisk positions use NegRiskAdapter, standard use CTF directly.
    """
    ctf = w3.eth.contract(address=Web3.to_checksum_address(CTF_ADDRESS), abi=CTF_ABI)
    neg = w3.eth.contract(address=Web3.to_checksum_address(NEG_RISK_ADAPTER), abi=NEG_RISK_ABI)
    collateral = Web3.to_checksum_address(USDC_E)
    parent = b"\x00" * 32

    # Group positions by conditionId (deduplicate)
    seen_cids: set[str] = set()
    calls = []

    for pos in positions:
        cid = pos.get("conditionId", "")
        if not cid or cid in seen_cids:
            continue
        seen_cids.add(cid)

        cid_bytes = bytes.fromhex(cid.replace("0x", ""))
        is_neg_risk = pos.get("negativeRisk", False)
        size = float(pos.get("size", 0))
        outcome = pos.get("outcome", "Yes")

        if is_neg_risk:
            # NegRiskAdapter.redeemPositions(conditionId, [yes_amount, no_amount])
            size_raw = int(size * 1e6)
            if outcome == "Yes":
                amounts = [size_raw, 0]
            else:
                amounts = [0, size_raw]
            call_data = neg.encode_abi("redeemPositions", [cid_bytes, amounts])
            calls.append({
                "to": NEG_RISK_ADAPTER,
                "data": call_data,
                "cid": cid,
                "label": f"neg-risk {outcome}",
                "gas": 500_000,
            })
        else:
            # Standard CTF: redeem each index set separately
            for idx_set in [[1], [2]]:
                call_data = ctf.encode_abi(
                    "redeemPositions",
                    [collateral, parent, cid_bytes, idx_set],
                )
                calls.append({
                    "to": CTF_ADDRESS,
                    "data": call_data,
                    "cid": cid,
                    "label": f"ctf idx={idx_set}",
                    "gas": 200_000,
                })

    return calls


def claim_for_eoa(
    w3: Web3, private_key: str, positions: list[dict]
) -> list[str]:
    """Redeem positions directly from an EOA wallet. Returns tx hashes."""
    acct = Account.from_key(private_key)
    calls = _build_redeem_calls(w3, positions)
    tx_hashes = []
    nonce = w3.eth.get_transaction_count(acct.address)

    for call in calls:
        tx = {
            "from": acct.address,
            "to": Web3.to_checksum_address(call["to"]),
            "data": call["data"],
            "nonce": nonce,
            "gasPrice": min(w3.eth.gas_price, MAX_GAS_PRICE),
            "gas": call["gas"],
            "chainId": 137,
        }
        signed = acct.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        tx_hashes.append(tx_hash.hex())
        nonce += 1

    return tx_hashes


def claim_for_proxy(
    w3: Web3, private_key: str, proxy_address: str, positions: list[dict]
) -> list[str]:
    """Redeem positions through a Gnosis Safe proxy wallet. Returns tx hashes."""
    acct = Account.from_key(private_key)
    proxy = Web3.to_checksum_address(proxy_address)
    safe = w3.eth.contract(address=proxy, abi=SAFE_ABI)
    calls = _build_redeem_calls(w3, positions)
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

    claimed_cids: set[str] = set()
    for call in calls:
        call_data_hex = call["data"]
        tx = safe.functions.execTransaction(
            Web3.to_checksum_address(call["to"]),
            0,
            bytes.fromhex(call_data_hex[2:]),
            0, 0, 0, 0,
            "0x0000000000000000000000000000000000000000",
            "0x0000000000000000000000000000000000000000",
            sig,
        ).build_transaction({
            "from": acct.address,
            "nonce": nonce,
            "gasPrice": min(w3.eth.gas_price, MAX_GAS_PRICE),
            "gas": call["gas"],
            "chainId": 137,
        })
        signed = acct.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        tx_hashes.append(tx_hash.hex())
        claimed_cids.add(call["cid"])
        log.info("[CLAIM] Sent proxy redeem tx %s (nonce=%d, %s)",
                 tx_hash.hex()[:18], nonce, call["label"])
        # Wait for confirmation before next tx
        try:
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            log.info("[CLAIM] Confirmed block=%d status=%d gas_used=%d",
                     receipt.blockNumber, receipt.status, receipt.gasUsed)
        except Exception as e:
            log.warning("[CLAIM] Wait for receipt failed: %s", str(e)[:100])
            _failed_cids[call["cid"]] = time.time()
        nonce += 1

    # Check USDC balance after claiming — if unchanged, mark cids as failed
    try:
        post_bal = usdc_contract.functions.balanceOf(proxy).call() / 1e6
        if post_bal <= pre_bal + 0.01:
            for cid in claimed_cids:
                _failed_cids[cid] = time.time()
            log.warning("[CLAIM] USDC unchanged (%.2f -> %.2f) — %d cids marked failed (1h cooldown)",
                       pre_bal, post_bal, len(claimed_cids))
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
    positions = [
        p for p in redeemable
        if p.get("conditionId") and (
            p["conditionId"] not in _failed_cids
            or (now - _failed_cids[p["conditionId"]]) > _FAIL_COOLDOWN
        )
    ]
    skipped = len(redeemable) - len(positions)
    if not positions:
        if skipped:
            log.debug("[CLAIM] Skipping %d positions in cooldown (already claimed)", skipped)
        return result

    log.info(
        "[CLAIM] %d redeemable positions worth $%.2f on %s (%d skipped in cooldown)",
        len(redeemable), total_value, wallet_address[:10], skipped,
    )

    try:
        w3 = Web3(Web3.HTTPProvider(POLYGON_RPC, request_kwargs={"timeout": 15}))
        is_proxy = _is_proxy(w3, wallet_address)

        if is_proxy:
            tx_hashes = claim_for_proxy(w3, private_key, wallet_address, positions)
        else:
            tx_hashes = claim_for_eoa(w3, private_key, positions)

        result["claimed"] = len(positions)
        result["usdc"] = total_value
        result["tx_hashes"] = tx_hashes
        log.info("[CLAIM] Submitted %d redeem txs for $%.2f USDC", len(tx_hashes), total_value)

    except Exception as e:
        result["errors"].append(f"Redeem failed: {e}")
        log.exception("[CLAIM] Redeem error")

    return result
