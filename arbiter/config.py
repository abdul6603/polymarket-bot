"""ArbiterConfig — frozen dataclass with risk params and CLOB credentials."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from dotenv import load_dotenv

_log = logging.getLogger(__name__)

load_dotenv()


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


@dataclass(frozen=True)
class ArbiterConfig:
    # Arbiter-specific wallet (own dedicated wallet)
    private_key: str = _env("ARBITER_PRIVATE_KEY")
    clob_api_key: str = _env("ARBITER_CLOB_API_KEY")
    clob_api_secret: str = _env("ARBITER_CLOB_API_SECRET")
    clob_api_passphrase: str = _env("ARBITER_CLOB_API_PASSPHRASE")
    funder_address: str = _env("ARBITER_FUNDER_ADDRESS")
    clob_host: str = _env("CLOB_HOST", "https://clob.polymarket.com")
    gamma_host: str = _env("GAMMA_HOST", "https://gamma-api.polymarket.com")

    # Risk params
    bankroll_usd: float = float(_env("ARBITER_BANKROLL_USD", "100"))
    max_bet_per_leg_usd: float = float(_env("ARBITER_MAX_BET_PER_LEG", "15"))
    max_per_arb_usd: float = float(_env("ARBITER_MAX_PER_ARB", "50"))
    min_deviation_pct: float = float(_env("ARBITER_MIN_DEVIATION_PCT", "3.0"))
    max_deviation_pct: float = float(_env("ARBITER_MAX_DEVIATION_PCT", "15.0"))
    min_liquidity: int = int(_env("ARBITER_MIN_LIQUIDITY", "500"))
    min_volume: int = int(_env("ARBITER_MIN_VOLUME", "1000"))
    cycle_minutes: int = int(_env("ARBITER_CYCLE_MINUTES", "5"))
    dry_run: bool = _env("ARBITER_DRY_RUN", "false").lower() in ("true", "1", "yes")
    max_concurrent_arbs: int = int(_env("ARBITER_MAX_CONCURRENT", "5"))
    fill_timeout_seconds: int = int(_env("ARBITER_FILL_TIMEOUT", "120"))
