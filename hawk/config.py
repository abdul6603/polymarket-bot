"""HawkConfig — frozen dataclass with risk params and CLOB credentials."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


@dataclass(frozen=True)
class HawkConfig:
    # Reuse Garves CLOB credentials
    private_key: str = _env("PRIVATE_KEY")
    clob_api_key: str = _env("CLOB_API_KEY")
    clob_api_secret: str = _env("CLOB_API_SECRET")
    clob_api_passphrase: str = _env("CLOB_API_PASSPHRASE")
    funder_address: str = _env("FUNDER_ADDRESS")
    clob_host: str = _env("CLOB_HOST", "https://clob.polymarket.com")
    gamma_host: str = _env("GAMMA_HOST", "https://gamma-api.polymarket.com")

    # OpenAI for GPT-4o analysis
    openai_api_key: str = _env("OPENAI_API_KEY")

    # Hawk-specific risk params
    bankroll_usd: float = float(_env("HAWK_BANKROLL_USD", "250"))
    max_bet_usd: float = float(_env("HAWK_MAX_BET_USD", "25"))
    max_concurrent: int = int(_env("HAWK_MAX_CONCURRENT", "8"))
    daily_loss_cap: float = float(_env("HAWK_DAILY_LOSS_CAP", "50"))
    cycle_minutes: int = int(_env("HAWK_CYCLE_MINUTES", "30"))
    min_edge: float = float(_env("HAWK_MIN_EDGE", "0.10"))
    min_volume: int = int(_env("HAWK_MIN_VOLUME", "5000"))
    min_liquidity: int = int(_env("HAWK_MIN_LIQUIDITY", "1000"))
    max_days: int = int(_env("HAWK_MAX_DAYS", "5"))
    dry_run: bool = _env("HAWK_DRY_RUN", "true").lower() in ("true", "1", "yes")
