"""Whale Tracker configuration — Smart Money Follower for Garves."""
from __future__ import annotations

import os

# ── API Endpoints ──
DATA_API = "https://data-api.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"

# ── Wallet Discovery ──
LEADERBOARD_CATEGORIES = ["CRYPTO"]
LEADERBOARD_PERIODS = ["WEEK", "MONTH", "ALL"]
LEADERBOARD_TOP_N = 75                # Wide funnel — score & filter to top 20
MAX_TRACKED_WALLETS = 40
MIN_WALLET_SCORE = 50

# ── Polling ──
POLL_INTERVAL_S = 4
POSITION_CHANGE_MIN_USD = 500         # Whale moves $500+ (catches more entries)
CACHE_TTL_S = 15                      # Fast polling — catch moves early

# ── Copy Trade Rules ──
_BANKROLL = float(os.getenv("BANKROLL_USD", "1000"))
MAX_COPY_SIZE_USD = round(_BANKROLL * 0.03, 2)  # 3% of bankroll per trade
MAX_COPY_PCT_OF_WHALE = 0.15          # 15% of whale's trade size
MAX_DAILY_EXPOSURE_USD = round(_BANKROLL * 0.25, 2)  # 25% of bankroll/day
MAX_SLIPPAGE_PCT = 15.0               # Allow more slippage — whales move price
MIN_CONSENSUS = 1                     # 1 high-score whale is enough to copy
MAX_IMPLIED_PRICE = 0.85              # Allow conviction plays up to $0.85
MIN_MARKET_DURATION_S = 3600          # Only copy on markets with 1h+ remaining

# ── Performance Tracking / Auto-blacklist ──
MIN_TRADES_FOR_BLACKLIST = 30         # Give whales more runway before judging
MIN_WR_THRESHOLD = 0.52              # 52% WR still +EV with proper sizing

# ── Manipulation Detection ──
RAPID_EXIT_WINDOW_S = 300             # 5 min — entry→exit = manipulation signal
MAX_MANIPULATION_SCORE = 3            # Auto-blacklist after 3 rapid reversals

# ── Scoring Weights (sum = 100) ──
SCORE_WEIGHTS = {
    "ev_per_trade": 30,
    "sharpe_ratio": 25,
    "profit_factor": 15,
    "consistency": 15,
    "sample_size": 10,
    "recency": 5,
}

# ── Rate Limiting ──
REQUESTS_PER_MINUTE = 50             # Stay under 60 API limit
LEADERBOARD_REFRESH_H = 24           # Rescan leaderboard every 24h
RESCORE_INTERVAL_H = 168             # Full rescore every 7 days (weekly)

# ── Backtesting ──
BACKTEST_DAYS = 30                   # 30-day historical lookback
BACKTEST_MIN_WR = 0.52               # Match the blacklist threshold
BACKTEST_MIN_TRADES = 10             # Minimum trades in backtest period
