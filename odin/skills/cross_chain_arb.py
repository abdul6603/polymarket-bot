"""Cross-Chain Arbitrage Scout — spots price edges across exchanges.

Scans Binance.US, Bybit, Hyperliquid for 0.3%+ price differences
on the same asset. Flags opportunities for manual execution.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.request
from dataclasses import dataclass, field

log = logging.getLogger("odin.skills.cross_chain_arb")

# Exchange price endpoints (public, no auth needed)
EXCHANGES = {
    "binance_us": {
        "url": "https://api.binance.us/api/v3/ticker/price?symbol={symbol}",
        "parser": lambda d: float(d.get("price", 0)),
    },
    "bybit": {
        "url": "https://api.bybit.com/v5/market/tickers?category=spot&symbol={symbol}",
        "parser": lambda d: float(d.get("result", {}).get("list", [{}])[0].get("lastPrice", 0)),
    },
    "hyperliquid": {
        "url": "https://api.hyperliquid.xyz/info",
        "method": "POST",
        "body": lambda sym: json.dumps({"type": "allMids"}).encode(),
        "parser": None,  # Special handling
    },
}

# Symbol mapping per exchange
SYMBOL_MAP = {
    "binance_us": {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT", "XRP": "XRPUSDT"},
    "bybit": {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT", "XRP": "XRPUSDT"},
    "hyperliquid": {"BTC": "BTC", "ETH": "ETH", "SOL": "SOL", "XRP": "XRP"},
}

MIN_EDGE_PCT = 0.3


@dataclass
class ArbOpportunity:
    """A detected arbitrage opportunity."""
    asset: str
    buy_exchange: str
    sell_exchange: str
    buy_price: float
    sell_price: float
    edge_pct: float
    estimated_profit_usd: float
    timestamp: float = 0.0

    @property
    def actionable(self) -> bool:
        return self.edge_pct >= MIN_EDGE_PCT


class CrossChainArbScout:
    """Scans multiple exchanges for price discrepancies."""

    def __init__(self):
        self._last_prices: dict[str, dict[str, float]] = {}
        self._opportunities: list[ArbOpportunity] = []
        self._scan_count = 0
        self._total_opportunities_found = 0

    def scan_all(self, assets: list[str] | None = None) -> list[ArbOpportunity]:
        """Scan all exchanges for price edges.

        Args:
            assets: List of base assets to scan (e.g., ["BTC", "ETH"])
        """
        assets = assets or ["BTC", "ETH", "SOL", "XRP"]
        self._scan_count += 1
        opportunities: list[ArbOpportunity] = []

        for asset in assets:
            prices = self._fetch_prices(asset)
            if len(prices) < 2:
                continue

            self._last_prices[asset] = prices

            # Compare all exchange pairs
            exchanges = list(prices.keys())
            for i in range(len(exchanges)):
                for j in range(i + 1, len(exchanges)):
                    ex1, ex2 = exchanges[i], exchanges[j]
                    p1, p2 = prices[ex1], prices[ex2]

                    if p1 <= 0 or p2 <= 0:
                        continue

                    edge = abs(p1 - p2) / min(p1, p2) * 100

                    if edge >= MIN_EDGE_PCT:
                        buy_ex = ex1 if p1 < p2 else ex2
                        sell_ex = ex2 if p1 < p2 else ex1
                        buy_p = min(p1, p2)
                        sell_p = max(p1, p2)

                        opp = ArbOpportunity(
                            asset=asset,
                            buy_exchange=buy_ex,
                            sell_exchange=sell_ex,
                            buy_price=buy_p,
                            sell_price=sell_p,
                            edge_pct=round(edge, 3),
                            estimated_profit_usd=round((sell_p - buy_p) * 100 / buy_p, 2),
                            timestamp=time.time(),
                        )
                        opportunities.append(opp)
                        self._total_opportunities_found += 1

                        log.info(
                            "[ARB] %s: BUY %s $%.2f → SELL %s $%.2f (%.3f%% edge)",
                            asset, buy_ex, buy_p, sell_ex, sell_p, edge,
                        )

        self._opportunities = opportunities
        return opportunities

    def _fetch_prices(self, asset: str) -> dict[str, float]:
        """Fetch price for an asset from all exchanges."""
        prices: dict[str, float] = {}

        for exchange, config in EXCHANGES.items():
            try:
                symbol = SYMBOL_MAP.get(exchange, {}).get(asset)
                if not symbol:
                    continue

                if exchange == "hyperliquid":
                    price = self._fetch_hyperliquid(asset)
                else:
                    url = config["url"].format(symbol=symbol)
                    req = urllib.request.Request(url, method="GET")
                    req.add_header("User-Agent", "Odin/1.0")

                    with urllib.request.urlopen(req, timeout=5) as resp:
                        data = json.loads(resp.read())
                        price = config["parser"](data)

                if price > 0:
                    prices[exchange] = price

            except Exception as e:
                log.debug("[ARB] %s %s fetch failed: %s", exchange, asset, str(e)[:80])

        return prices

    def _fetch_hyperliquid(self, asset: str) -> float:
        """Fetch from Hyperliquid's special API."""
        try:
            req = urllib.request.Request(
                "https://api.hyperliquid.xyz/info",
                data=json.dumps({"type": "allMids"}).encode(),
                method="POST",
            )
            req.add_header("Content-Type", "application/json")

            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
                return float(data.get(asset, 0))
        except Exception:
            return 0.0

    def get_status(self) -> dict:
        return {
            "scans": self._scan_count,
            "total_found": self._total_opportunities_found,
            "active_opportunities": len(self._opportunities),
            "last_prices": {
                asset: {ex: round(p, 2) for ex, p in prices.items()}
                for asset, prices in self._last_prices.items()
            },
            "opportunities": [
                {
                    "asset": o.asset,
                    "buy": f"{o.buy_exchange} ${o.buy_price:.2f}",
                    "sell": f"{o.sell_exchange} ${o.sell_price:.2f}",
                    "edge": f"{o.edge_pct:.3f}%",
                }
                for o in self._opportunities[:5]
            ],
        }
