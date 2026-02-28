from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


@dataclass(frozen=True)
class Config:
    # Wallet & auth
    private_key: str = _env("PRIVATE_KEY")
    clob_api_key: str = _env("CLOB_API_KEY")
    clob_api_secret: str = _env("CLOB_API_SECRET")
    clob_api_passphrase: str = _env("CLOB_API_PASSPHRASE")
    funder_address: str = _env("FUNDER_ADDRESS")

    # Endpoints
    clob_host: str = _env("CLOB_HOST", "https://clob.polymarket.com")
    gamma_host: str = _env("GAMMA_HOST", "https://gamma-api.polymarket.com")
    ws_url: str = _env("WS_URL", "wss://ws-subscriptions-clob.polymarket.com/ws/market")
    coingecko_url: str = _env("COINGECKO_URL", "https://api.coingecko.com/api/v3")
    binance_ws_url: str = _env("BINANCE_WS_URL", "wss://stream.binance.us:9443")

    # Risk — $250 bankroll, $150 total exposure cap
    max_position_usd: float = float(_env("MAX_POSITION_USD", "150.0"))
    max_concurrent_positions: int = int(_env("MAX_CONCURRENT_POSITIONS", "5"))
    min_edge_pct: float = float(_env("MIN_EDGE_PCT", "8.0"))
    order_size_usd: float = float(_env("ORDER_SIZE_USD", "10.0"))
    max_daily_loss_usd: float = float(_env("MAX_DAILY_LOSS_USD", "50.0"))
    max_drawdown_pct: float = float(_env("MAX_DRAWDOWN_PCT", "30.0"))
    bankroll_usd: float = float(_env("BANKROLL_USD", "250.0"))
    snipe_bankroll_usd: float = float(_env("SNIPE_BANKROLL_USD", "0"))  # 0 = use bankroll_usd

    # Bot
    tick_interval_s: int = int(_env("TICK_INTERVAL_S", "30"))
    dry_run: bool = _env("DRY_RUN", "true").lower() in ("true", "1", "yes")
    log_level: str = _env("LOG_LEVEL", "INFO")

    # Snipe engine — DEPRECATED (lost $75+, killed Feb 27 2026)
    snipe_enabled: bool = _env("SNIPE_ENABLED", "false").lower() in ("true", "1", "yes")
    snipe_dry_run: bool = _env("SNIPE_DRY_RUN", _env("DRY_RUN", "true")).lower() in ("true", "1", "yes")
    snipe_budget_per_window: float = float(_env("SNIPE_BUDGET_PER_WINDOW", "50.0"))
    snipe_delta_threshold: float = float(_env("SNIPE_DELTA_THRESHOLD", "0.08"))

    # Maker engine (disabled by default)
    maker_dry_run: bool = _env("MAKER_DRY_RUN", _env("DRY_RUN", "true")).lower() in ("true", "1", "yes")
    maker_enabled: bool = _env("MAKER_ENABLED", "false").lower() in ("true", "1", "yes")
    maker_quote_size_usd: float = float(_env("MAKER_QUOTE_SIZE_USD", "8.0"))
    maker_max_inventory_usd: float = float(_env("MAKER_MAX_INVENTORY_USD", "30.0"))
    maker_max_total_exposure: float = float(_env("MAKER_MAX_TOTAL_EXPOSURE", "60.0"))
    maker_tick_interval_s: float = float(_env("MAKER_TICK_INTERVAL_S", "5.0"))
    maker_bankroll_usd: float = float(_env("MAKER_BANKROLL_USD", "300"))
    maker_crypto_bankroll_usd: float = float(_env("MAKER_CRYPTO_BANKROLL_USD", "200"))
    maker_general_bankroll_usd: float = float(_env("MAKER_GENERAL_BANKROLL_USD", "100"))
    maker_max_imbalance: float = float(_env("MAKER_MAX_IMBALANCE", "0.70"))
    maker_daily_loss_pct: float = float(_env("MAKER_DAILY_LOSS_PCT", "0.05"))

    # Whale Follower — Smart Money copy trader (disabled by default)
    whale_enabled: bool = _env("WHALE_ENABLED", "false").lower() in ("true", "1", "yes")
    whale_dry_run: bool = _env("WHALE_DRY_RUN", "true").lower() in ("true", "1", "yes")
    whale_poll_interval_s: float = float(_env("WHALE_POLL_INTERVAL_S", "4.0"))

    # Resolution Scalper — DEPRECATED (killed Feb 27 2026)
    res_scalp_enabled: bool = _env("RES_SCALP_ENABLED", "false").lower() in ("true", "1", "yes")
    res_scalp_min_edge: float = float(_env("RES_SCALP_MIN_EDGE", "0.08"))
    res_scalp_min_prob: float = float(_env("RES_SCALP_MIN_PROB", "0.75"))
    res_scalp_max_price: float = float(_env("RES_SCALP_MAX_PRICE", "0.88"))
    res_scalp_max_bet: float = float(_env("RES_SCALP_MAX_BET", "20.0"))
    res_scalp_kelly_frac: float = float(_env("RES_SCALP_KELLY_FRAC", "0.25"))
    res_scalp_max_concurrent: int = int(_env("RES_SCALP_MAX_CONCURRENT", "3"))
    res_scalp_dry_run: bool = _env("RES_SCALP_DRY_RUN", "").lower() not in ("false", "0", "no")  # independent override


# ── Brotherhood Hierarchy ──
# Jordan (Owner) → Claude (Godfather) → Shelby (Commander) → Agents
# Atlas feeds intelligence to ALL agents including Shelby.
# Soren → Lisa (social media for Soren's content).
HIERARCHY = {
    "owner": "Jordan",
    "godfather": "Claude",
    "commander": "Shelby",
    "my_role": "The Trader — BTC/ETH/SOL prediction markets on Polymarket",
    "brothers": ["Atlas (The Scientist)", "Soren (The Thinker)", "Lisa (The Operator)", "Robotox (The Watchman)", "Quant (The Strategy Alchemist)"],
}
