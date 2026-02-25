"""HawkConfig — frozen dataclass with risk params and CLOB credentials."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

_log = logging.getLogger(__name__)

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

    # Hawk-specific risk params — V4 sportsbook-pure (targeting 55-60% WR)
    bankroll_usd: float = float(_env("HAWK_BANKROLL_USD", "250"))
    max_bet_usd: float = float(_env("HAWK_MAX_BET_USD", "15"))
    max_concurrent: int = int(_env("HAWK_MAX_CONCURRENT", "8"))
    daily_loss_cap: float = float(_env("HAWK_DAILY_LOSS_CAP", "30"))
    cycle_minutes: int = int(_env("HAWK_CYCLE_MINUTES", "30"))
    min_edge: float = float(_env("HAWK_MIN_EDGE", "0.15"))
    min_volume: int = int(_env("HAWK_MIN_VOLUME", "5000"))
    min_liquidity: int = int(_env("HAWK_MIN_LIQUIDITY", "1000"))
    max_days: int = int(_env("HAWK_MAX_DAYS", "7"))
    min_hours: float = float(_env("HAWK_MIN_HOURS", "2.0"))  # Never bet on markets expiring < 2h
    dry_run: bool = _env("HAWK_DRY_RUN", "true").lower() in ("true", "1", "yes")

    # V3 precision params
    max_per_event_usd: float = float(_env("HAWK_MAX_PER_EVENT_USD", "30"))  # Max total exposure per event
    kelly_fraction: float = float(_env("HAWK_KELLY_FRACTION", "0.25"))
    max_risk_score: int = int(_env("HAWK_MAX_RISK_SCORE", "8"))

    # Toxic source kill switch — hard-block these edge sources immediately
    blocked_sources: tuple[str, ...] = tuple(
        s.strip() for s in _env("HAWK_BLOCKED_SOURCES", "news,base_rate").split(",") if s.strip()
    )
    compound_bankroll: bool = _env("HAWK_COMPOUND_BANKROLL", "true").lower() in ("true", "1", "yes")
    news_enrichment: bool = _env("HAWK_NEWS_ENRICHMENT", "true").lower() in ("true", "1", "yes")

    # V3: Sportsbook Odds API
    odds_api_key: str = _env("ODDS_API_KEY", "")

    # V4: Cross-platform intelligence
    openweather_api_key: str = _env("OPENWEATHER_API_KEY", "")
    kalshi_enabled: bool = _env("HAWK_KALSHI_ENABLED", "true").lower() in ("true", "1", "yes")
    metaculus_enabled: bool = _env("HAWK_METACULUS_ENABLED", "true").lower() in ("true", "1", "yes")
    predictit_enabled: bool = _env("HAWK_PREDICTIT_ENABLED", "true").lower() in ("true", "1", "yes")
    weather_enabled: bool = _env("HAWK_WEATHER_ENABLED", "true").lower() in ("true", "1", "yes")

    # V6: Weather intelligence — Open-Meteo ensemble + NWS, $0 cost
    weather_min_volume: int = int(_env("HAWK_WEATHER_MIN_VOLUME", "1000"))  # Lower threshold for weather markets

    # V8: Limit order mode — rest in book instead of crossing spread
    limit_discount: float = float(_env("HAWK_LIMIT_DISCOUNT", "0.02"))
    fill_timeout_minutes: int = int(_env("HAWK_FILL_TIMEOUT_MINUTES", "15"))
    aggressive_fallback: bool = _env("HAWK_AGGRESSIVE_FALLBACK", "false").lower() in ("true", "1", "yes")

    # V6: Dynamic cycle timing
    cycle_minutes_fast: int = int(_env("HAWK_CYCLE_MINUTES_FAST", "5"))
    cycle_minutes_normal: int = int(_env("HAWK_CYCLE_MINUTES_NORMAL", "30"))

    # V8: In-play live mispricing engine (stretch — disabled by default)
    inplay_enabled: bool = _env("HAWK_INPLAY_ENABLED", "false").lower() in ("true", "1", "yes")
    inplay_min_edge: float = float(_env("HAWK_INPLAY_MIN_EDGE", "0.10"))
    inplay_max_bet: float = float(_env("HAWK_INPLAY_MAX_BET", "8"))

    # V9: Live In-Play Position Management
    live_enabled: bool = _env("HAWK_LIVE_ENABLED", "true").lower() in ("true", "1", "yes")
    live_poll_seconds: int = int(_env("HAWK_LIVE_POLL_SECONDS", "30"))
    live_stop_loss_pct: float = float(_env("HAWK_LIVE_STOP_LOSS_PCT", "0.40"))
    live_max_scale: float = float(_env("HAWK_LIVE_MAX_SCALE", "1.5"))
    live_min_hold_minutes: int = int(_env("HAWK_LIVE_MIN_HOLD_MINUTES", "5"))
    live_max_actions_per_game: int = int(_env("HAWK_LIVE_MAX_ACTIONS_PER_GAME", "3"))
    live_score_exit_threshold: int = int(_env("HAWK_LIVE_SCORE_EXIT_THRESHOLD", "15"))
    live_scale_up_margin: int = int(_env("HAWK_LIVE_SCALE_UP_MARGIN", "10"))
    live_odds_check_minutes: int = int(_env("HAWK_LIVE_ODDS_CHECK_MINUTES", "5"))
    live_take_profit_threshold: float = float(_env("HAWK_LIVE_TAKE_PROFIT", "0.93"))  # Sell when price >= this (YES) or <= 1-this (NO)

    # V9: Kalshi trading integration
    kalshi_api_key: str = _env("KALSHI_API_KEY", "")
    kalshi_private_key_path: str = _env("KALSHI_PRIVATE_KEY_PATH", "")
    kalshi_trading_enabled: bool = _env("HAWK_KALSHI_TRADING", "false").lower() in ("true", "1", "yes")

    # V6: Smart sizing multipliers
    sizing_domain_wr_boost: float = float(_env("HAWK_SIZING_DOMAIN_WR_BOOST", "1.3"))
    sizing_domain_wr_penalty: float = float(_env("HAWK_SIZING_DOMAIN_WR_PENALTY", "0.5"))
    sizing_books_boost: float = float(_env("HAWK_SIZING_BOOKS_BOOST", "1.2"))
    sizing_books_penalty: float = float(_env("HAWK_SIZING_BOOKS_PENALTY", "0.75"))
    sizing_consensus_boost: float = float(_env("HAWK_SIZING_CONSENSUS_BOOST", "1.2"))
    sizing_consensus_penalty: float = float(_env("HAWK_SIZING_CONSENSUS_PENALTY", "0.7"))
    sizing_movement_boost: float = float(_env("HAWK_SIZING_MOVEMENT_BOOST", "1.2"))
    sizing_movement_penalty: float = float(_env("HAWK_SIZING_MOVEMENT_PENALTY", "0.7"))


DATA_DIR = Path(__file__).parent.parent / "data"
CATEGORY_OVERRIDES_FILE = DATA_DIR / "hawk_category_overrides.json"


@dataclass
class CategoryOverride:
    """Per-category parameter overrides (non-frozen, mutable)."""
    min_edge: float | None = None
    max_bet_usd: float | None = None
    kelly_fraction: float | None = None
    enabled: bool = True


def load_category_overrides() -> dict[str, CategoryOverride]:
    """Load per-category overrides from JSON file."""
    if not CATEGORY_OVERRIDES_FILE.exists():
        return {}
    try:
        raw = json.loads(CATEGORY_OVERRIDES_FILE.read_text())
        overrides = {}
        for cat, vals in raw.items():
            overrides[cat] = CategoryOverride(
                min_edge=vals.get("min_edge"),
                max_bet_usd=vals.get("max_bet_usd"),
                kelly_fraction=vals.get("kelly_fraction"),
                enabled=vals.get("enabled", True),
            )
        return overrides
    except Exception:
        _log.exception("Failed to load category overrides")
        return {}


def get_effective_config(base_config: HawkConfig, category: str) -> dict:
    """Return merged params: category override > global config.

    Returns dict with keys: min_edge, max_bet_usd, kelly_fraction, enabled.
    """
    overrides = load_category_overrides()
    override = overrides.get(category)

    result = {
        "min_edge": base_config.min_edge,
        "max_bet_usd": base_config.max_bet_usd,
        "kelly_fraction": base_config.kelly_fraction,
        "enabled": True,
    }

    if override:
        if override.min_edge is not None:
            result["min_edge"] = override.min_edge
        if override.max_bet_usd is not None:
            result["max_bet_usd"] = override.max_bet_usd
        if override.kelly_fraction is not None:
            result["kelly_fraction"] = override.kelly_fraction
        result["enabled"] = override.enabled

    return result

