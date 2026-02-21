"""RazorConfig — frozen dataclass. Reuses Garves CLOB creds from .env."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


@dataclass(frozen=True)
class RazorConfig:
    # Reuse Garves CLOB credentials
    private_key: str = _env("PRIVATE_KEY")
    clob_api_key: str = _env("CLOB_API_KEY")
    clob_api_secret: str = _env("CLOB_API_SECRET")
    clob_api_passphrase: str = _env("CLOB_API_PASSPHRASE")
    funder_address: str = _env("FUNDER_ADDRESS")
    clob_host: str = _env("CLOB_HOST", "https://clob.polymarket.com")
    gamma_host: str = _env("GAMMA_HOST", "https://gamma-api.polymarket.com")
    ws_url: str = _env("WS_URL", "wss://ws-subscriptions-clob.polymarket.com/ws/market")

    # Master toggle
    enabled: bool = _env("RAZOR_ENABLED", "true").lower() in ("true", "1", "yes")

    # Capital management
    bankroll_usd: float = float(_env("RAZOR_BANKROLL_USD", "1000"))
    max_per_trade: float = float(_env("RAZOR_MAX_PER_TRADE", "50"))
    min_per_trade: float = float(_env("RAZOR_MIN_PER_TRADE", "10"))
    max_concurrent: int = int(_env("RAZOR_MAX_CONCURRENT", "20"))
    max_exposure: float = float(_env("RAZOR_MAX_EXPOSURE", "800"))

    # Entry thresholds
    min_spread: float = float(_env("RAZOR_MIN_SPREAD", "0.015"))
    min_depth_usd: float = float(_env("RAZOR_MIN_DEPTH_USD", "50"))

    # Exit management — THE KILLER FEATURE
    exit_threshold: float = float(_env("RAZOR_EXIT_THRESHOLD", "0.70"))
    profit_lock: float = float(_env("RAZOR_PROFIT_LOCK", "0.95"))
    max_hold_s: int = int(_env("RAZOR_MAX_HOLD_S", "7200"))

    # Timing
    scan_interval_s: float = float(_env("RAZOR_SCAN_INTERVAL_S", "1.0"))
    gamma_refresh_s: int = int(_env("RAZOR_GAMMA_REFRESH_S", "300"))

    # Mode
    dry_run: bool = _env("RAZOR_DRY_RUN", "true").lower() in ("true", "1", "yes")
