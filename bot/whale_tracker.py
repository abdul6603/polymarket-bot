"""Whale Alert — large transaction tracking (>$10M to/from exchanges).

Free tier: 10 req/min. We poll every 5 minutes = 0.2 req/min.
Classifies exchange deposits (sell pressure) vs withdrawals (accumulation).

Requires WHALE_ALERT_API_KEY from whale-alert.io (free).
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

from bot.http_session import get_session

log = logging.getLogger(__name__)

_API_KEY = os.environ.get("WHALE_ALERT_API_KEY", "")
_BASE_URL = "https://api.whale-alert.io/v1"

# Cache per asset
_cache: dict[str, tuple["WhaleFlowData | None", float]] = {}
_CACHE_TTL = 300  # 5 minutes

# Asset -> Whale Alert blockchain name
BLOCKCHAIN_MAP = {
    "bitcoin": "bitcoin",
    "ethereum": "ethereum",
    "solana": "solana",
    "xrp": "ripple",
}

# Known exchange wallets (partial list — Whale Alert labels them)
EXCHANGE_LABELS = {"binance", "coinbase", "kraken", "bitfinex", "okx", "bybit",
                   "huobi", "kucoin", "gemini", "bitstamp", "gate.io",
                   "crypto.com", "upbit", "bithumb", "mexc"}


@dataclass
class WhaleFlowData:
    deposits_usd: float = 0.0  # Exchange deposits (sell pressure)
    withdrawals_usd: float = 0.0  # Exchange withdrawals (accumulation)
    net_flow_usd: float = 0.0  # deposits - withdrawals (positive = sell pressure)
    tx_count: int = 0
    largest_tx_usd: float = 0.0
    largest_tx_direction: str = ""  # "deposit" or "withdrawal"
    timestamp: float = 0.0


def _is_exchange(owner_info: dict) -> bool:
    """Check if a wallet owner is a known exchange."""
    owner_type = (owner_info.get("owner_type") or "").lower()
    owner_name = (owner_info.get("owner") or "").lower()
    return owner_type == "exchange" or owner_name in EXCHANGE_LABELS


def get_flow(asset: str) -> WhaleFlowData | None:
    """Fetch whale transaction flow for an asset (last 1 hour).

    Returns None if API key missing or request fails.
    """
    if not _API_KEY:
        return None

    blockchain = BLOCKCHAIN_MAP.get(asset)
    if not blockchain:
        return None

    now = time.time()
    cached = _cache.get(asset)
    if cached and now - cached[1] < _CACHE_TTL:
        return cached[0]

    # Fetch transactions from last hour with min $10M
    start_ts = int(now - 3600)
    params = {
        "api_key": _API_KEY,
        "min_value": 10_000_000,
        "start": start_ts,
        "currency": blockchain,
    }

    try:
        resp = get_session().get(f"{_BASE_URL}/transactions", params=params, timeout=10)
        if resp.status_code != 200:
            log.debug("Whale Alert HTTP %d for %s", resp.status_code, asset)
            _cache[asset] = (None, now)
            return None

        data = resp.json()
        if data.get("result") != "success":
            log.debug("Whale Alert error: %s", data.get("message", "unknown"))
            _cache[asset] = (None, now)
            return None

        transactions = data.get("transactions", [])

    except Exception as e:
        log.debug("Whale Alert fetch failed for %s: %s", asset, str(e)[:100])
        _cache[asset] = (None, now)
        return None

    result = WhaleFlowData(timestamp=now)

    for tx in transactions:
        amount_usd = float(tx.get("amount_usd", 0))
        if amount_usd < 10_000_000:
            continue

        result.tx_count += 1
        from_info = tx.get("from", {})
        to_info = tx.get("to", {})

        from_exchange = _is_exchange(from_info)
        to_exchange = _is_exchange(to_info)

        if to_exchange and from_exchange:
            # Exchange-to-exchange transfer — neutral, skip
            continue
        elif to_exchange and not from_exchange:
            # Deposit to exchange = sell pressure
            result.deposits_usd += amount_usd
            if amount_usd > result.largest_tx_usd:
                result.largest_tx_usd = amount_usd
                result.largest_tx_direction = "deposit"
        elif from_exchange and not to_exchange:
            # Withdrawal from exchange = accumulation
            result.withdrawals_usd += amount_usd
            if amount_usd > result.largest_tx_usd:
                result.largest_tx_usd = amount_usd
                result.largest_tx_direction = "withdrawal"

    result.net_flow_usd = result.deposits_usd - result.withdrawals_usd

    _cache[asset] = (result, now)

    if result.tx_count > 0:
        log.info(
            "[WHALE] %s: %d txs | Deposits=$%.0fM | Withdrawals=$%.0fM | Net=$%+.0fM",
            asset.upper(), result.tx_count,
            result.deposits_usd / 1e6, result.withdrawals_usd / 1e6,
            result.net_flow_usd / 1e6,
        )

    return result
