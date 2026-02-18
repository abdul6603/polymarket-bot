"""Garves — Auto-Compounding Bankroll Manager.

Tracks running bankroll from resolved trades in trades.jsonl.
Scales position sizes proportionally to current bankroll vs initial.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
TRADES_FILE = DATA_DIR / "trades.jsonl"


def calculate_trade_pnl(won: bool, probability: float, size_usd: float) -> float:
    """Calculate P&L for a single Polymarket trade.

    Polymarket mechanics:
    - Buy tokens at `probability` price, spending `size_usd` dollars
    - Win: each token pays $1 minus 2% fee = $0.98
    - Loss: tokens worth $0, lose entire `size_usd`

    Returns profit (positive) or loss (negative) in USD.
    """
    if won:
        if 0.01 < probability < 0.99:
            return ((0.98 - probability) / probability) * size_usd
        return 0.0  # Edge case: can't profit at extreme probabilities
    return -size_usd


class BankrollManager:
    INITIAL_BANKROLL = 250.0  # Starting bankroll from .env
    MIN_MULTIPLIER = 0.75     # Floor: never size below 75% of base (prevents death spiral)
    MAX_MULTIPLIER = 2.0      # Cap: never size above 200% of base
    CACHE_TTL = 60            # Refresh every 60 seconds

    def __init__(self):
        self._cache: dict = {"multiplier": 1.0, "bankroll": self.INITIAL_BANKROLL, "pnl": 0.0, "timestamp": 0.0}

    def get_multiplier(self) -> float:
        """Calculate bankroll multiplier from trade history.

        Reads trades.jsonl, sums actual P&L:
        - Win: + (1/price - 1) * size_usd * 0.98  (net of 2% winner fee)
        - Loss: - size_usd

        current_bankroll = INITIAL_BANKROLL + total_pnl
        multiplier = current_bankroll / INITIAL_BANKROLL
        Clamped to [MIN_MULTIPLIER, MAX_MULTIPLIER]
        """
        now = time.time()
        if now - self._cache["timestamp"] < self.CACHE_TTL:
            return self._cache["multiplier"]

        total_pnl = 0.0
        trade_count = 0

        if TRADES_FILE.exists():
            try:
                with open(TRADES_FILE) as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        rec = json.loads(line)
                        if not rec.get("resolved") or rec.get("outcome") not in ("up", "down"):
                            continue

                        trade_count += 1
                        size_usd = rec.get("size_usd") or 30.0  # fallback to typical base size
                        prob = rec.get("probability", 0.5)
                        total_pnl += calculate_trade_pnl(
                            won=rec.get("won", False),
                            probability=prob,
                            size_usd=size_usd,
                        )

            except Exception:
                log.debug("BankrollManager: failed to read trades, using default")

        current_bankroll = self.INITIAL_BANKROLL + total_pnl
        multiplier = current_bankroll / self.INITIAL_BANKROLL
        multiplier = max(self.MIN_MULTIPLIER, min(self.MAX_MULTIPLIER, multiplier))

        self._cache = {
            "multiplier": multiplier,
            "bankroll": round(current_bankroll, 2),
            "pnl": round(total_pnl, 2),
            "trades": trade_count,
            "timestamp": now,
        }

        log.info(
            "BANKROLL: $%.2f (PnL: $%+.2f from %d trades) -> multiplier=%.2fx",
            current_bankroll, total_pnl, trade_count, multiplier,
        )
        return multiplier

    def get_status(self) -> dict:
        """Return current bankroll state for dashboard/monitoring."""
        self.get_multiplier()  # ensure cache is fresh
        return {
            "bankroll_usd": self._cache.get("bankroll", self.INITIAL_BANKROLL),
            "pnl_usd": self._cache.get("pnl", 0.0),
            "multiplier": self._cache.get("multiplier", 1.0),
            "trade_count": self._cache.get("trades", 0),
            "initial_bankroll": self.INITIAL_BANKROLL,
        }
