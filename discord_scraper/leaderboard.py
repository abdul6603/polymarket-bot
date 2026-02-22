"""Discord Alpha Scraper — Trader Accuracy Leaderboard.

Resolves pending trade calls against actual price action.
Scores each Discord trader by win rate and P&L accuracy.
"""
from __future__ import annotations

import json
import logging
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from discord_scraper import db

ET = ZoneInfo("America/New_York")
log = logging.getLogger(__name__)

# Resolution: check price after 24h and 7d
RESOLUTION_HOURS = 24
RESOLUTION_THRESHOLD_PCT = 2.0  # 2% move in direction = win


def _get_current_price(ticker: str) -> float | None:
    """Fetch current price from CoinGecko (free, no key)."""
    # Map common tickers to CoinGecko IDs
    ticker_map = {
        "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
        "XRP": "ripple", "DOGE": "dogecoin", "AVAX": "avalanche-2",
        "LINK": "chainlink", "ADA": "cardano", "DOT": "polkadot",
        "MATIC": "matic-network", "IOTA": "iota", "NEAR": "near",
        "APT": "aptos", "ARB": "arbitrum", "OP": "optimism",
        "SUI": "sui", "INJ": "injective-protocol", "TIA": "celestia",
        "SEI": "sei-network", "JUP": "jupiter-exchange-solana",
        "PEPE": "pepe", "WIF": "dogwifcoin", "BONK": "bonk",
        "HYPE": "hyperliquid",
    }

    cg_id = ticker_map.get(ticker.upper())
    if not cg_id:
        return None

    url = f"https://api.coingecko.com/api/v3/simple/price?ids={cg_id}&vs_currencies=usd"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            return data.get(cg_id, {}).get("usd")
    except Exception as e:
        log.debug("[LEADERBOARD] Price fetch failed for %s: %s", ticker, e)
        return None


def resolve_pending_calls() -> int:
    """Check pending calls older than RESOLUTION_HOURS and resolve them."""
    conn = db._conn()
    cutoff = (datetime.now(ET) - timedelta(hours=RESOLUTION_HOURS)).isoformat()

    pending = conn.execute("""
        SELECT id, ticker, direction, entry_price, created_at
        FROM trader_scores
        WHERE outcome = 'pending' AND created_at < ?
    """, (cutoff,)).fetchall()

    resolved_count = 0
    for row in pending:
        row = dict(row)
        ticker = row.get("ticker")
        direction = row.get("direction")
        entry = row.get("entry_price")

        if not ticker or not entry or entry <= 0:
            # Can't resolve without ticker and entry — mark as unknown
            conn.execute(
                "UPDATE trader_scores SET outcome = 'unknown' WHERE id = ?",
                (row["id"],),
            )
            resolved_count += 1
            continue

        current_price = _get_current_price(ticker)
        if current_price is None:
            continue

        # Calculate P&L
        if direction == "LONG":
            pnl_pct = ((current_price - entry) / entry) * 100
        elif direction == "SHORT":
            pnl_pct = ((entry - current_price) / entry) * 100
        else:
            continue

        outcome = "win" if pnl_pct >= RESOLUTION_THRESHOLD_PCT else "loss"

        conn.execute(
            """UPDATE trader_scores
               SET outcome = ?, pnl_pct = ?, resolved_at = ?
               WHERE id = ?""",
            (outcome, round(pnl_pct, 2), datetime.now(ET).isoformat(), row["id"]),
        )
        resolved_count += 1
        log.info("[LEADERBOARD] Resolved %s %s %s: %.1f%% → %s",
                 row.get("ticker"), direction, "call", pnl_pct, outcome)

    conn.commit()
    conn.close()
    return resolved_count


def get_trader_profile(author: str) -> dict:
    """Get detailed profile for a specific trader."""
    conn = db._conn()

    # Overall stats
    stats = conn.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN outcome = 'win' THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN outcome = 'loss' THEN 1 ELSE 0 END) as losses,
            AVG(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct END) as avg_pnl,
            MAX(pnl_pct) as best_trade,
            MIN(pnl_pct) as worst_trade
        FROM trader_scores WHERE author = ?
    """, (author,)).fetchone()

    # Recent calls
    recent = conn.execute("""
        SELECT ticker, direction, entry_price, outcome, pnl_pct, created_at
        FROM trader_scores
        WHERE author = ?
        ORDER BY created_at DESC LIMIT 10
    """, (author,)).fetchall()

    # Favorite tickers
    tickers = conn.execute("""
        SELECT ticker, COUNT(*) as cnt
        FROM trader_scores
        WHERE author = ? AND ticker IS NOT NULL
        GROUP BY ticker ORDER BY cnt DESC LIMIT 5
    """, (author,)).fetchall()

    conn.close()

    stats = dict(stats) if stats else {}
    total = stats.get("total", 0)
    wins = stats.get("wins", 0)
    resolved = wins + stats.get("losses", 0)

    return {
        "author": author,
        "total_calls": total,
        "wins": wins,
        "losses": stats.get("losses", 0),
        "win_rate": round(wins / resolved * 100, 1) if resolved > 0 else 0,
        "avg_pnl": round(stats.get("avg_pnl") or 0, 2),
        "best_trade": round(stats.get("best_trade") or 0, 2),
        "worst_trade": round(stats.get("worst_trade") or 0, 2),
        "recent_calls": [dict(r) for r in recent],
        "favorite_tickers": [dict(r) for r in tickers],
    }
