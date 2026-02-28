"""Chainlink RTDS Feed — read price oracle that Polymarket resolves against.

Polymarket crypto Up/Down markets resolve using Chainlink price feeds on
Polygon, NOT Binance/Coinbase. This module reads the exact same feed so
our fair value matches what the market will actually resolve to.

Aggregator contracts (Polygon mainnet):
  BTC/USD: 0xc907E116054Ad103354f2D350FD2514433D57F6f
  ETH/USD: 0xF9680D99D6C9589e2a93a78A04A279e509205945
  SOL/USD: 0x4ffC43a60e009B551865A93d232E33Fce9f01507

Usage:
  feed = ChainlinkFeed()
  price = feed.get_price("bitcoin")   # -> 84123.45 (or None)
  price = feed.get_price("ethereum")  # -> 2815.67
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from web3 import Web3

log = logging.getLogger(__name__)

# Polygon RPC — public endpoint, free
POLYGON_RPC = "https://polygon-bor-rpc.publicnode.com"

# Chainlink aggregator addresses on Polygon
# SOL/USD not available on Polygon Chainlink — only BTC and ETH
FEEDS: dict[str, str] = {
    "bitcoin":  "0xc907E116054Ad103354f2D350FD2514433D57F6f",
    "ethereum": "0xF9680D99D6C9589e2a93a78A04A279e509205945",
}

# Minimal ABI — only latestRoundData() and decimals()
AGGREGATOR_ABI = [
    {
        "inputs": [],
        "name": "latestRoundData",
        "outputs": [
            {"name": "roundId", "type": "uint80"},
            {"name": "answer", "type": "int256"},
            {"name": "startedAt", "type": "uint256"},
            {"name": "updatedAt", "type": "uint256"},
            {"name": "answeredInRound", "type": "uint80"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function",
    },
]


class ChainlinkFeed:
    """Read Chainlink price feeds on Polygon with caching."""

    def __init__(self, cache_ttl: float = 2.0, rpc_url: str = POLYGON_RPC):
        self._w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 10}))
        self._cache_ttl = cache_ttl
        # asset -> (price, updated_at_chain, fetched_at_local)
        self._cache: dict[str, tuple[float, int, float]] = {}
        # asset -> decimals (fetched once)
        self._decimals: dict[str, int] = {}
        # Pre-build contract objects
        self._contracts: dict[str, any] = {}
        for asset, addr in FEEDS.items():
            self._contracts[asset] = self._w3.eth.contract(
                address=Web3.to_checksum_address(addr),
                abi=AGGREGATOR_ABI,
            )

    def _fetch_decimals(self, asset: str) -> int:
        """Fetch and cache decimals for an asset's price feed."""
        if asset in self._decimals:
            return self._decimals[asset]
        contract = self._contracts.get(asset)
        if not contract:
            return 8  # Chainlink default
        try:
            d = contract.functions.decimals().call()
            self._decimals[asset] = d
            return d
        except Exception:
            return 8

    def get_price(self, asset: str) -> Optional[float]:
        """Get the latest Chainlink price for an asset.

        Returns the price in USD, or None if unavailable.
        Cached for cache_ttl seconds to avoid excessive RPC calls.
        """
        asset = asset.lower()
        if asset not in self._contracts:
            return None

        # Check cache
        cached = self._cache.get(asset)
        if cached and (time.time() - cached[2]) < self._cache_ttl:
            return cached[0]

        try:
            contract = self._contracts[asset]
            result = contract.functions.latestRoundData().call()
            # result: (roundId, answer, startedAt, updatedAt, answeredInRound)
            answer = result[1]
            updated_at = result[3]
            decimals = self._fetch_decimals(asset)
            price = answer / (10 ** decimals)

            self._cache[asset] = (price, updated_at, time.time())
            return price

        except Exception as e:
            log.warning("[CHAINLINK] Failed to fetch %s price: %s", asset, str(e)[:100])
            # Return stale cache if available
            if cached:
                return cached[0]
            return None

    def get_price_with_age(self, asset: str) -> tuple[Optional[float], float]:
        """Get price and its age in seconds (from Chainlink's updatedAt).

        Returns (price, age_seconds). Age is how long ago Chainlink last
        updated the price on-chain (typically every heartbeat or on deviation).
        """
        asset = asset.lower()
        price = self.get_price(asset)
        if price is None:
            return None, float("inf")

        cached = self._cache.get(asset)
        if cached:
            chain_updated = cached[1]
            age = time.time() - chain_updated
            return price, age
        return price, float("inf")

    def get_all_prices(self) -> dict[str, Optional[float]]:
        """Fetch all supported asset prices."""
        return {asset: self.get_price(asset) for asset in FEEDS}
