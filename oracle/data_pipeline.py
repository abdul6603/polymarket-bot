"""Oracle data pipeline — pulls external data for weekly analysis.

Phase 1 sources (all free):
  - ccxt: BTC/ETH/SOL/XRP prices + weekly candles
  - CoinGlass: funding rates, OI, liquidations
  - FRED: DXY, Fed funds rate, M2
  - Fear & Greed index
  - Atlas KB: macro research + news catalysts
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from oracle.config import OracleConfig

log = logging.getLogger(__name__)


@dataclass
class MarketContext:
    """Unified data context for Oracle's weekly analysis."""
    timestamp: str = ""

    # Price data
    prices: dict[str, float] = field(default_factory=dict)        # {"bitcoin": 67500.0, ...}
    weekly_change_pct: dict[str, float] = field(default_factory=dict)
    weekly_high: dict[str, float] = field(default_factory=dict)
    weekly_low: dict[str, float] = field(default_factory=dict)

    # Derivatives (CoinGlass)
    funding_rates: dict[str, float] = field(default_factory=dict)  # {"bitcoin": 0.01, ...}
    open_interest: dict[str, float] = field(default_factory=dict)
    oi_change_24h_pct: dict[str, float] = field(default_factory=dict)
    liquidations_24h: dict[str, float] = field(default_factory=dict)

    # Macro (FRED)
    dxy: float | None = None
    fed_rate: float | None = None
    fear_greed: int | None = None
    fear_greed_label: str = ""

    # Atlas intelligence
    atlas_insights: list[str] = field(default_factory=list)
    news_catalysts: list[str] = field(default_factory=list)

    # Agent perspectives (from swarm)
    agent_signals: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "prices": self.prices,
            "weekly_change_pct": self.weekly_change_pct,
            "weekly_high": self.weekly_high,
            "weekly_low": self.weekly_low,
            "funding_rates": self.funding_rates,
            "open_interest": self.open_interest,
            "oi_change_24h_pct": self.oi_change_24h_pct,
            "liquidations_24h": self.liquidations_24h,
            "dxy": self.dxy,
            "fed_rate": self.fed_rate,
            "fear_greed": self.fear_greed,
            "fear_greed_label": self.fear_greed_label,
            "atlas_insights": self.atlas_insights,
            "news_catalysts": self.news_catalysts,
            "agent_signals": self.agent_signals,
        }

    def summary_text(self) -> str:
        """Human-readable summary for LLM context."""
        lines = [f"Market Data as of {self.timestamp}"]
        lines.append("")

        # Prices
        lines.append("CURRENT PRICES:")
        for asset, price in self.prices.items():
            chg = self.weekly_change_pct.get(asset, 0)
            hi = self.weekly_high.get(asset, 0)
            lo = self.weekly_low.get(asset, 0)
            lines.append(f"  {asset.upper()}: ${price:,.2f} (week: {chg:+.1f}%, high=${hi:,.0f}, low=${lo:,.0f})")

        # Derivatives
        if self.funding_rates:
            lines.append("\nDERIVATIVES:")
            for asset in self.prices:
                fr = self.funding_rates.get(asset, 0)
                oi = self.open_interest.get(asset, 0)
                oi_chg = self.oi_change_24h_pct.get(asset, 0)
                liq = self.liquidations_24h.get(asset, 0)
                lines.append(f"  {asset.upper()}: funding={fr:.4f}% OI=${oi:,.0f}M ({oi_chg:+.1f}%) liq_24h=${liq:,.0f}M")

        # Macro
        lines.append("\nMACRO:")
        if self.dxy is not None:
            lines.append(f"  DXY: {self.dxy:.2f}")
        if self.fed_rate is not None:
            lines.append(f"  Fed Rate: {self.fed_rate:.2f}%")
        if self.fear_greed is not None:
            lines.append(f"  Fear & Greed: {self.fear_greed} ({self.fear_greed_label})")

        # Atlas insights
        if self.atlas_insights:
            lines.append("\nATLAS INTELLIGENCE:")
            for insight in self.atlas_insights[:5]:
                lines.append(f"  - {insight[:150]}")

        # Agent signals
        if self.agent_signals:
            lines.append("\nAGENT SIGNALS:")
            for agent, signal in self.agent_signals.items():
                lines.append(f"  {agent}: {signal}")

        return "\n".join(lines)


def gather_context(cfg: OracleConfig) -> MarketContext:
    """Pull all external data and return unified context."""
    ctx = MarketContext(
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    )

    _fetch_prices(cfg, ctx)
    _fetch_fear_greed(ctx)
    _fetch_coinglass(cfg, ctx)
    _fetch_fred(cfg, ctx)
    _fetch_atlas_intel(cfg, ctx)

    log.info(
        "Context gathered: %d prices, FnG=%s, %d atlas insights",
        len(ctx.prices), ctx.fear_greed, len(ctx.atlas_insights),
    )
    return ctx


# ---------------------------------------------------------------------------
# Data source fetchers
# ---------------------------------------------------------------------------

COINGECKO_IDS = {
    "bitcoin": "bitcoin",
    "ethereum": "ethereum",
    "solana": "solana",
    "xrp": "ripple",
}


def _fetch_prices(cfg: OracleConfig, ctx: MarketContext) -> None:
    """Fetch current prices + 7-day range from CoinGecko (free, no key)."""
    ids = ",".join(COINGECKO_IDS[a] for a in cfg.assets if a in COINGECKO_IDS)
    try:
        resp = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={
                "ids": ids,
                "vs_currencies": "usd",
                "include_24hr_change": "true",
                "include_7d_change": "true",
            },
            timeout=10,
        )
        data = resp.json()
        for asset, cg_id in COINGECKO_IDS.items():
            if cg_id in data:
                ctx.prices[asset] = data[cg_id].get("usd", 0)
                ctx.weekly_change_pct[asset] = data[cg_id].get("usd_7d_change", 0)
    except Exception:
        log.warning("CoinGecko price fetch failed")

    # Weekly high/low from CoinGecko market chart
    for asset, cg_id in COINGECKO_IDS.items():
        try:
            resp = requests.get(
                f"https://api.coingecko.com/api/v3/coins/{cg_id}/market_chart",
                params={"vs_currency": "usd", "days": "7"},
                timeout=10,
            )
            data = resp.json()
            prices_list = [p[1] for p in data.get("prices", [])]
            if prices_list:
                ctx.weekly_high[asset] = max(prices_list)
                ctx.weekly_low[asset] = min(prices_list)
        except Exception:
            pass


def _fetch_fear_greed(ctx: MarketContext) -> None:
    """Fetch crypto Fear & Greed index."""
    try:
        resp = requests.get("https://api.alternative.me/fng/?limit=1", timeout=5)
        data = resp.json().get("data", [{}])[0]
        ctx.fear_greed = int(data.get("value", 50))
        ctx.fear_greed_label = data.get("value_classification", "Neutral")
    except Exception:
        log.warning("Fear & Greed fetch failed")


def _fetch_coinglass(cfg: OracleConfig, ctx: MarketContext) -> None:
    """Fetch derivatives data from CoinGlass API."""
    if not cfg.coinglass_api_key:
        log.debug("No CoinGlass API key, skipping derivatives data")
        return

    headers = {"coinglassSecret": cfg.coinglass_api_key}
    symbols = {"bitcoin": "BTC", "ethereum": "ETH", "solana": "SOL", "xrp": "XRP"}

    # Funding rates
    try:
        resp = requests.get(
            "https://open-api-v3.coinglass.com/api/futures/funding-rate/current",
            headers=headers,
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            for item in data:
                symbol = item.get("symbol", "")
                for asset, sym in symbols.items():
                    if symbol == sym:
                        ctx.funding_rates[asset] = float(item.get("rate", 0)) * 100
    except Exception:
        log.warning("CoinGlass funding rate fetch failed")

    # Open interest
    try:
        resp = requests.get(
            "https://open-api-v3.coinglass.com/api/futures/open-interest/aggregated",
            headers=headers,
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            for item in data:
                symbol = item.get("symbol", "")
                for asset, sym in symbols.items():
                    if symbol == sym:
                        oi = float(item.get("openInterest", 0))
                        ctx.open_interest[asset] = oi / 1e6  # Convert to millions
                        ctx.oi_change_24h_pct[asset] = float(item.get("change24h", 0))
    except Exception:
        log.warning("CoinGlass OI fetch failed")

    # Liquidations
    try:
        resp = requests.get(
            "https://open-api-v3.coinglass.com/api/futures/liquidation/aggregated",
            headers=headers,
            params={"timeType": "1"},  # 24h
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            for item in data:
                symbol = item.get("symbol", "")
                for asset, sym in symbols.items():
                    if symbol == sym:
                        total = float(item.get("totalVolUsd", 0))
                        ctx.liquidations_24h[asset] = total / 1e6
    except Exception:
        log.warning("CoinGlass liquidation fetch failed")


def _fetch_fred(cfg: OracleConfig, ctx: MarketContext) -> None:
    """Fetch macro data from FRED (Federal Reserve)."""
    if not cfg.fred_api_key:
        log.debug("No FRED API key, skipping macro data")
        return

    # DXY (Dollar Index)
    try:
        resp = requests.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={
                "series_id": "DTWEXBGS",
                "api_key": cfg.fred_api_key,
                "file_type": "json",
                "sort_order": "desc",
                "limit": 1,
            },
            timeout=10,
        )
        obs = resp.json().get("observations", [])
        if obs and obs[0].get("value", ".") != ".":
            ctx.dxy = float(obs[0]["value"])
    except Exception:
        log.warning("FRED DXY fetch failed")

    # Fed Funds Rate
    try:
        resp = requests.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={
                "series_id": "FEDFUNDS",
                "api_key": cfg.fred_api_key,
                "file_type": "json",
                "sort_order": "desc",
                "limit": 1,
            },
            timeout=10,
        )
        obs = resp.json().get("observations", [])
        if obs and obs[0].get("value", ".") != ".":
            ctx.fed_rate = float(obs[0]["value"])
    except Exception:
        log.warning("FRED Fed rate fetch failed")


def _fetch_atlas_intel(cfg: OracleConfig, ctx: MarketContext) -> None:
    """Read Atlas knowledge base for macro research and news catalysts."""
    atlas_root = Path.home() / "atlas" / "data"

    # Atlas KB entries
    kb_file = atlas_root / "knowledge_base.json"
    if kb_file.exists():
        try:
            kb = json.loads(kb_file.read_text())
            entries = kb if isinstance(kb, list) else kb.get("entries", [])
            # Get recent entries relevant to crypto macro
            for entry in entries[-20:]:
                title = entry.get("title", "") if isinstance(entry, dict) else str(entry)
                if any(kw in title.lower() for kw in ("bitcoin", "ethereum", "crypto", "macro", "fed", "etf", "halving")):
                    summary = entry.get("summary", title) if isinstance(entry, dict) else title
                    ctx.atlas_insights.append(str(summary)[:200])
        except Exception:
            log.debug("Failed to read Atlas KB")

    # Atlas latest research
    research_file = atlas_root / "latest_research.json"
    if research_file.exists():
        try:
            research = json.loads(research_file.read_text())
            findings = research.get("findings", []) if isinstance(research, dict) else []
            for f in findings[:5]:
                text = f.get("text", str(f)) if isinstance(f, dict) else str(f)
                ctx.news_catalysts.append(text[:200])
        except Exception:
            pass
