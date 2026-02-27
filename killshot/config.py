"""Killshot configuration — reads from shared .env file."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


@dataclass(frozen=True)
class KillshotConfig:
    """All Killshot parameters — read from .env with sane paper-mode defaults."""

    # Mode
    dry_run: bool = _env("KILLSHOT_DRY_RUN", "true").lower() in ("true", "1", "yes")
    enabled: bool = _env("KILLSHOT_ENABLED", "true").lower() in ("true", "1", "yes")

    # Bankroll
    bankroll_usd: float = float(_env("KILLSHOT_BANKROLL_USD", "50"))
    max_bet_usd: float = float(_env("KILLSHOT_MAX_BET_USD", "5"))
    daily_loss_cap_usd: float = float(_env("KILLSHOT_DAILY_LOSS_CAP_USD", "15"))

    # Direction detection — minimum spot price delta to consider direction "locked"
    direction_threshold: float = float(_env("KILLSHOT_DIRECTION_THRESHOLD", "0.0010"))

    # Entry pricing — simulated maker limit order price range
    entry_price_min: float = float(_env("KILLSHOT_ENTRY_PRICE_MIN", "0.60"))
    entry_price_max: float = float(_env("KILLSHOT_ENTRY_PRICE_MAX", "0.75"))

    # Kill zone — how many seconds before window close to evaluate
    window_seconds: int = int(_env("KILLSHOT_WINDOW_SECONDS", "60"))
    min_window_seconds: int = int(_env("KILLSHOT_MIN_WINDOW_SECONDS", "10"))

    # Assets (comma-separated)
    assets_str: str = _env("KILLSHOT_ASSETS", "bitcoin")

    # Loop intervals
    tick_interval_s: float = float(_env("KILLSHOT_TICK_INTERVAL_S", "1.0"))
    scan_interval_s: float = float(_env("KILLSHOT_SCAN_INTERVAL_S", "60"))

    # Separate wallet (live mode only — unused in paper)
    private_key: str = _env("KILLSHOT_PRIVATE_KEY", "")
    clob_api_key: str = _env("KILLSHOT_CLOB_API_KEY", "")
    clob_api_secret: str = _env("KILLSHOT_CLOB_API_SECRET", "")
    clob_api_passphrase: str = _env("KILLSHOT_CLOB_API_PASSPHRASE", "")
    funder_address: str = _env("KILLSHOT_FUNDER_ADDRESS", "")

    @property
    def assets(self) -> list[str]:
        return [a.strip() for a in self.assets_str.split(",")]
