"""Oracle configuration — loads from environment / .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_ROOT / ".env")


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


@dataclass
class OracleConfig:
    # --- Identity ---
    name: str = "Oracle"
    title: str = "The Weekly Crypto Oracle"

    # --- Trading Setup (Phase 1) ---
    bankroll: float = float(_env("ORACLE_BANKROLL", "150"))
    risk_per_trade: float = float(_env("ORACLE_RISK_PER_TRADE", "25"))
    max_trades_per_week: int = int(_env("ORACLE_MAX_TRADES", "8"))
    max_exposure: float = float(_env("ORACLE_MAX_EXPOSURE", "100"))
    weekly_loss_limit: float = float(_env("ORACLE_WEEKLY_LOSS_LIMIT", "75"))
    cash_reserve_pct: float = float(_env("ORACLE_CASH_RESERVE", "0.30"))
    min_edge_pct: float = float(_env("ORACLE_MIN_EDGE", "0.08"))
    dry_run: bool = _env("ORACLE_DRY_RUN", "true").lower() == "true"

    # --- Conviction Tiers (edge thresholds) ---
    edge_low: float = 0.08       # 8% edge → 2% bankroll
    edge_medium: float = 0.12    # 12% edge → 4% bankroll
    edge_high: float = 0.18      # 18% edge → 5% bankroll

    # --- Assets ---
    assets: list[str] = field(default_factory=lambda: ["bitcoin", "ethereum", "solana", "xrp"])

    # --- Model Ensemble ---
    claude_api_key: str = _env("ANTHROPIC_API_KEY")
    claude_model: str = _env("ORACLE_CLAUDE_MODEL", "claude-sonnet-4-20250514")
    gemini_api_key: str = _env("GEMINI_API_KEY")
    gemini_model: str = _env("ORACLE_GEMINI_MODEL", "gemini-2.5-flash")
    grok_api_key: str = _env("XAI_API_KEY")
    grok_model: str = _env("ORACLE_GROK_MODEL", "grok-3")
    ensemble_weights: dict[str, float] = field(default_factory=lambda: {
        "claude": 0.45,
        "grok": 0.30,
        "gemini": 0.25,
    })

    # --- External Data ---
    coinglass_api_key: str = _env("COINGLASS_API_KEY")
    fred_api_key: str = _env("FRED_API_KEY")

    # --- Polymarket ---
    clob_host: str = _env("CLOB_HOST", "https://clob.polymarket.com")
    gamma_host: str = "https://gamma-api.polymarket.com"

    # --- Paths ---
    data_dir: Path = field(default_factory=lambda: _ROOT / "data")
    status_file: str = "oracle_status.json"
    db_file: str = "oracle_predictions.db"

    # --- Scheduling ---
    cycle_day: str = "sunday"  # day of week to run
    cycle_hour_utc: int = 0    # 00:00 UTC
    emergency_volatility_pct: float = 0.08  # 8% BTC move triggers mid-week update

    def status_path(self) -> Path:
        return self.data_dir / self.status_file

    def db_path(self) -> Path:
        return self.data_dir / self.db_file

    def conviction_size(self, edge: float) -> float:
        """Return position size based on edge conviction tier."""
        if edge < self.edge_low:
            return 0.0
        elif edge < self.edge_medium:
            return self.bankroll * 0.02
        elif edge < self.edge_high:
            return self.bankroll * 0.04
        else:
            return self.bankroll * 0.05

    def conviction_label(self, edge: float) -> str:
        if edge < self.edge_low:
            return "SKIP"
        elif edge < self.edge_medium:
            return "LOW"
        elif edge < self.edge_high:
            return "MEDIUM"
        else:
            return "HIGH"
