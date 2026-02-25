"""Odin configuration — all settings from env vars with safe defaults."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Load .env from odin root
_ENV_PATH = Path(__file__).parent / ".env"
if _ENV_PATH.exists():
    load_dotenv(_ENV_PATH)
# Also load shared polymarket .env for any shared keys
_SHARED_ENV = Path.home() / "polymarket-bot" / ".env"
if _SHARED_ENV.exists():
    load_dotenv(_SHARED_ENV, override=False)


def _bool(val: str) -> bool:
    return val.strip().lower() in ("true", "1", "yes")


@dataclass(frozen=True)
class OdinConfig:
    # ── Hyperliquid API ──
    hl_secret_key: str = os.getenv("ODIN_HL_SECRET_KEY", "")
    hl_account_address: str = os.getenv("ODIN_HL_ACCOUNT_ADDRESS", "")
    hl_testnet: bool = _bool(os.getenv("ODIN_HL_TESTNET", "false"))

    # ── Trading Params ──
    symbols: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            os.getenv("ODIN_SYMBOLS", "BTCUSDT,ETHUSDT").split(",")
        )
    )
    primary_symbol: str = os.getenv("ODIN_PRIMARY_SYMBOL", "BTCUSDT")
    starting_capital: float = float(os.getenv("ODIN_STARTING_CAPITAL", "200"))
    max_leverage: int = int(os.getenv("ODIN_MAX_LEVERAGE", "50"))
    default_leverage: int = int(os.getenv("ODIN_DEFAULT_LEVERAGE", "10"))

    # ── Timeframes ──
    htf: str = os.getenv("ODIN_HTF", "1D")       # High timeframe (bias)
    mtf: str = os.getenv("ODIN_MTF", "4H")       # Mid timeframe (structure)
    ltf: str = os.getenv("ODIN_LTF", "15m")      # Low timeframe (entry)

    # ── Risk Management ──
    risk_per_trade_usd: float = float(os.getenv("ODIN_RISK_PER_TRADE_USD", "10"))
    risk_per_trade_pct: float = float(os.getenv("ODIN_RISK_PER_TRADE", "5.0"))
    target_rr: float = float(os.getenv("ODIN_TARGET_RR", "2.0"))
    max_daily_loss_pct: float = float(os.getenv("ODIN_MAX_DAILY_LOSS", "10.0"))
    max_weekly_loss_pct: float = float(os.getenv("ODIN_MAX_WEEKLY_LOSS", "6.0"))
    max_monthly_dd_pct: float = float(os.getenv("ODIN_MAX_MONTHLY_DD", "15.0"))
    max_total_dd_pct: float = float(os.getenv("ODIN_MAX_TOTAL_DD", "25.0"))
    max_consecutive_losses: int = int(os.getenv("ODIN_MAX_CONSEC_LOSSES", "3"))
    max_open_positions: int = int(os.getenv("ODIN_MAX_OPEN_POSITIONS", "2"))
    max_exposure_pct: float = float(os.getenv("ODIN_MAX_EXPOSURE", "50.0"))

    # ── Portfolio Risk Guard (Phase 2) ──
    portfolio_max_heat_pct: float = float(os.getenv("ODIN_MAX_HEAT_PCT", "10.0"))
    max_same_direction: int = int(os.getenv("ODIN_MAX_SAME_DIRECTION", "4"))
    coin_blacklist_after_losses: int = int(os.getenv("ODIN_BLACKLIST_LOSSES", "3"))
    notional_cap_major: float = float(os.getenv("ODIN_CAP_MAJOR", "1000"))
    notional_cap_mid: float = float(os.getenv("ODIN_CAP_MID", "600"))
    notional_cap_alt: float = float(os.getenv("ODIN_CAP_ALT", "400"))
    max_priority_coins: int = int(os.getenv("ODIN_MAX_PRIORITY_COINS", "20"))
    symbols_per_cycle: int = int(os.getenv("ODIN_SYMBOLS_PER_CYCLE", "8"))

    # ── WebSocket + Advanced Entries (Phase 3) ──
    ws_enabled: bool = _bool(os.getenv("ODIN_WS_ENABLED", "true"))
    ws_reconnect_delay: int = int(os.getenv("ODIN_WS_RECONNECT_DELAY", "5"))
    limit_order_ttl_seconds: int = int(os.getenv("ODIN_LIMIT_TTL", "7200"))
    max_pending_per_symbol: int = int(os.getenv("ODIN_MAX_PENDING", "3"))
    scaled_entry_tranches: int = int(os.getenv("ODIN_SCALED_TRANCHES", "3"))
    scaled_entry_spread_pct: float = float(os.getenv("ODIN_SCALED_SPREAD", "0.3"))
    zone_alert_radius_pct: float = float(os.getenv("ODIN_ZONE_ALERT_RADIUS", "1.5"))

    # ── Kelly Criterion (legacy, kept for reference) ──
    kelly_fraction: float = float(os.getenv("ODIN_KELLY_FRACTION", "0.1"))
    assumed_win_rate: float = float(os.getenv("ODIN_ASSUMED_WIN_RATE", "0.55"))
    assumed_rr: float = float(os.getenv("ODIN_ASSUMED_RR", "2.0"))

    # ── Cycle Timing ──
    cycle_seconds: int = int(os.getenv("ODIN_CYCLE_SECONDS", "300"))
    macro_poll_seconds: int = int(os.getenv("ODIN_MACRO_POLL", "600"))
    status_write_seconds: int = int(os.getenv("ODIN_STATUS_WRITE", "60"))

    # ── Confluence Thresholds ──
    min_confluence_score: float = float(os.getenv("ODIN_MIN_CONFLUENCE", "0.60"))
    no_trade_zone: float = float(os.getenv("ODIN_NO_TRADE_ZONE", "0.40"))

    # ── Paper Fee Model ──
    # 0.17% round-trip = 0.035% taker × 2 sides + 0.05% slippage × 2
    paper_fee_rate: float = float(os.getenv("ODIN_PAPER_FEE_RATE", "0.0017"))
    restrict_to_config_symbols: bool = _bool(os.getenv("ODIN_RESTRICT_SYMBOLS", "true"))

    # ── LLM Brain (V7) ──
    llm_analyst_model: str = os.getenv("ODIN_ANALYST_MODEL", "claude-opus-4-6")
    llm_min_conviction: int = int(os.getenv("ODIN_LLM_MIN_CONVICTION", "50"))
    llm_max_tokens_analyze: int = int(os.getenv("ODIN_LLM_ANALYZE_TOKENS", "1000"))
    llm_temperature: float = float(os.getenv("ODIN_LLM_TEMPERATURE", "0.15"))
    reflection_every_n: int = int(os.getenv("ODIN_REFLECT_EVERY_N", "5"))
    max_active_lessons: int = int(os.getenv("ODIN_MAX_LESSONS", "20"))
    screen_regime_threshold: float = float(os.getenv("ODIN_SCREEN_REGIME_THRESH", "45"))
    screen_volume_mult: float = float(os.getenv("ODIN_SCREEN_VOL_MULT", "2.0"))
    screen_move_pct: float = float(os.getenv("ODIN_SCREEN_MOVE_PCT", "1.5"))

    # ── Mode ──
    dry_run: bool = _bool(os.getenv("ODIN_DRY_RUN", "true"))
    log_level: str = os.getenv("ODIN_LOG_LEVEL", "INFO")

    # ── Paths ──
    data_dir: Path = field(
        default_factory=lambda: Path(
            os.getenv("ODIN_DATA_DIR", str(Path(__file__).parent / "data"))
        )
    )

    # ── CoinGecko (free tier) ──
    coingecko_api_key: str = os.getenv("COINGECKO_API_KEY", "")

    # ── CoinGlass API ──
    coinglass_api_key: str = os.getenv("COINGLASS_API_KEY", "")
    coinglass_poll_seconds: int = int(os.getenv("ODIN_COINGLASS_POLL", "180"))
    top_coins_count: int = int(os.getenv("ODIN_TOP_COINS", "100"))

    # ── Position Scaling ──
    scale_ob_pct: float = 0.50       # 50% at order block
    scale_fvg_mid_pct: float = 0.30  # 30% at FVG midpoint
    scale_extreme_pct: float = 0.20  # 20% at extreme

    # ── Discipline Layer ──
    slippage_budget_pct: float = float(os.getenv("ODIN_SLIPPAGE_BUDGET", "0.05"))
    min_market_score: int = int(os.getenv("ODIN_MIN_MARKET_SCORE", "50"))
    health_check_seconds: int = int(os.getenv("ODIN_HEALTH_CHECK", "1800"))
    weekly_review_day: int = int(os.getenv("ODIN_WEEKLY_REVIEW_DAY", "0"))  # 0=Monday
    weekly_review_hour: int = int(os.getenv("ODIN_WEEKLY_REVIEW_HOUR", "2"))  # 2 AM ET

    # ── Exit Management ──
    # Trailing stop: move SL to breakeven after 1R, trail at ATR*mult after 2R
    trail_atr_multiplier: float = float(os.getenv("ODIN_TRAIL_ATR_MULT", "1.5"))
    trail_breakeven_r: float = float(os.getenv("ODIN_TRAIL_BE_R", "1.0"))
    trail_activate_r: float = float(os.getenv("ODIN_TRAIL_ACTIVATE_R", "2.0"))

    # Partial TP: 50% at TP1, 30% at TP2, 20% runner at TP3
    partial_tp1_pct: float = float(os.getenv("ODIN_PARTIAL_TP1_PCT", "0.50"))
    partial_tp1_r: float = float(os.getenv("ODIN_PARTIAL_TP1_R", "1.5"))
    partial_tp2_pct: float = float(os.getenv("ODIN_PARTIAL_TP2_PCT", "0.30"))
    partial_tp2_r: float = float(os.getenv("ODIN_PARTIAL_TP2_R", "2.5"))
    partial_tp3_r: float = float(os.getenv("ODIN_PARTIAL_TP3_R", "4.0"))

    # Time-based exits: close stale trades
    max_stale_hours: float = float(os.getenv("ODIN_MAX_STALE_HOURS", "12"))
    stale_threshold_r: float = float(os.getenv("ODIN_STALE_THRESHOLD_R", "0.3"))

    # Regime exit modifiers (multiplier on trailing distance)
    exit_regime_chop_mult: float = 0.7     # Tighter trailing in chop
    exit_regime_trend_mult: float = 1.5    # Wider trailing in strong trends

    def __post_init__(self) -> None:
        # Ensure data dir exists
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "macro").mkdir(exist_ok=True)
