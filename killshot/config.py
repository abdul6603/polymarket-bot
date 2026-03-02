"""Killshot configuration — reads from shared .env file."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _bool(key: str, default: str = "true") -> bool:
    return _env(key, default).lower() in ("true", "1", "yes")


@dataclass(frozen=True)
class KillshotConfig:
    """All Killshot parameters — read from .env with sane defaults."""

    # Mode
    dry_run: bool = _bool("KILLSHOT_DRY_RUN", "true")
    enabled: bool = _bool("KILLSHOT_ENABLED", "true")

    # Bankroll
    bankroll_usd: float = float(_env("KILLSHOT_BANKROLL_USD", "150"))
    max_bet_usd: float = float(_env("KILLSHOT_MAX_BET_USD", "30"))
    daily_loss_cap_usd: float = float(_env("KILLSHOT_DAILY_LOSS_CAP_USD", "45"))

    # Direction detection — minimum spot price delta to consider direction "locked"
    direction_threshold: float = float(_env("KILLSHOT_DIRECTION_THRESHOLD", "0.0005"))

    # Entry pricing — simulated maker limit order price range
    entry_price_min: float = float(_env("KILLSHOT_ENTRY_PRICE_MIN", "0.90"))
    entry_price_max: float = float(_env("KILLSHOT_ENTRY_PRICE_MAX", "0.95"))

    # Kill zone — how many seconds before window close to evaluate
    window_seconds: int = int(_env("KILLSHOT_WINDOW_SECONDS", "60"))
    min_window_seconds: int = int(_env("KILLSHOT_MIN_WINDOW_SECONDS", "10"))

    # Assets (comma-separated)
    assets_str: str = _env("KILLSHOT_ASSETS", "bitcoin")

    # Loop intervals
    tick_interval_s: float = float(_env("KILLSHOT_TICK_INTERVAL_S", "0.1"))
    scan_interval_s: float = float(_env("KILLSHOT_SCAN_INTERVAL_S", "60"))

    # Separate wallet (live mode only)
    private_key: str = _env("KILLSHOT_PRIVATE_KEY", "")
    clob_api_key: str = _env("KILLSHOT_CLOB_API_KEY", "")
    clob_api_secret: str = _env("KILLSHOT_CLOB_API_SECRET", "")
    clob_api_passphrase: str = _env("KILLSHOT_CLOB_API_PASSPHRASE", "")
    funder_address: str = _env("KILLSHOT_FUNDER_ADDRESS", "")

    # ── Phase 1: Adaptive threshold ─────────────────────────
    adaptive_threshold: bool = _bool("KILLSHOT_ADAPTIVE_THRESHOLD", "true")

    # ── Phase 1: Kelly Criterion sizing ─────────────────────
    kelly_enabled: bool = _bool("KILLSHOT_KELLY_ENABLED", "true")
    kelly_fraction: float = float(_env("KILLSHOT_KELLY_FRACTION", "0.5"))

    # ── Phase 1: Sum-to-one arb detection ───────────────────
    arb_enabled: bool = _bool("KILLSHOT_ARB_ENABLED", "true")
    arb_threshold: float = float(_env("KILLSHOT_ARB_THRESHOLD", "0.98"))

    # ── Phase 1: Exposure cap ───────────────────────────────
    max_exposure_usd: float = float(_env("KILLSHOT_MAX_EXPOSURE_USD", "100"))

    # ── Phase 2: Rust executor ──────────────────────────────
    rust_executor_enabled: bool = _bool("KILLSHOT_RUST_EXECUTOR", "true")
    rust_executor_url: str = _env("KILLSHOT_RUST_EXECUTOR_URL", "http://127.0.0.1:9999")

    # ── Phase 2: Binance @aggTrade leading indicator ────────
    binance_agg_enabled: bool = _bool("KILLSHOT_BINANCE_AGG", "true")

    # ── Phase 2: Correlation-aware limits ───────────────────
    correlation_reduction: float = float(_env("KILLSHOT_CORRELATION_REDUCTION", "0.5"))
    avg_correlation: float = float(_env("KILLSHOT_AVG_CORRELATION", "0.90"))

    # ── Phase 3: Volatility-adaptive threshold ──────────────
    volatility_adaptive: bool = _bool("KILLSHOT_VOLATILITY_ADAPTIVE", "true")

    # ── Phase 3: Market types (5m, 1m) ─────────────────────
    market_types: str = _env("KILLSHOT_MARKET_TYPES", "5m")

    # ── Phase 3: Direction cooldown (conflict avoidance) ────
    direction_cooldown_s: int = int(_env("KILLSHOT_DIRECTION_COOLDOWN_S", "300"))

    # ── Phase 4: Multi-asset cascade ────────────────────────
    cascade_enabled: bool = _bool("KILLSHOT_CASCADE", "true")
    cascade_delay_s: float = float(_env("KILLSHOT_CASCADE_DELAY_S", "0.5"))

    @property
    def assets(self) -> list[str]:
        return [a.strip() for a in self.assets_str.split(",")]
