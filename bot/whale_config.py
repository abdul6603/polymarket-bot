"""Whale Tracker configuration — Smart Money Follower for Garves."""
from __future__ import annotations

# ── API Endpoints ──
DATA_API = "https://data-api.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"

# ── Wallet Discovery ──
LEADERBOARD_CATEGORIES = ["CRYPTO"]
LEADERBOARD_PERIODS = ["WEEK", "MONTH", "ALL"]
LEADERBOARD_TOP_N = 50
MAX_TRACKED_WALLETS = 20
MIN_WALLET_SCORE = 60

# ── Polling ──
POLL_INTERVAL_S = 4
POSITION_CHANGE_MIN_USD = 500  # Ignore position changes < $500

# ── Copy Trade Rules ──
MAX_COPY_SIZE_USD = 25.0
MAX_COPY_PCT_OF_WHALE = 0.15          # 15% of whale's trade size
MAX_DAILY_EXPOSURE_USD = 100.0        # $100/day cap (separate from maker)
MAX_SLIPPAGE_PCT = 8.0                # Skip if price moved >8% from whale entry
MIN_CONSENSUS = 2                     # 2+ whales must agree on direction
MAX_IMPLIED_PRICE = 0.55              # Never buy above $0.55

# ── Performance Tracking / Auto-blacklist ──
MIN_TRADES_FOR_BLACKLIST = 20         # Need 20 copied trades before judging
MIN_WR_THRESHOLD = 0.55              # 55% WR floor — auto-blacklist below this

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
CACHE_TTL_S = 30                     # Cache wallet data for 30s
LEADERBOARD_REFRESH_H = 24           # Rescan leaderboard every 24h
RESCORE_INTERVAL_H = 168             # Full rescore every 7 days (weekly)

# ── Backtesting ──
BACKTEST_DAYS = 30                   # 30-day historical lookback
BACKTEST_MIN_WR = 0.55               # Minimum WR to enable live copying
BACKTEST_MIN_TRADES = 10             # Minimum trades in backtest period
