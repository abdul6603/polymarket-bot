"""Quant configuration."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class QuantConfig:
    cycle_minutes: int = 30
    max_combinations: int = 500
    min_trades_for_significance: int = 20
    assets: list[str] = field(default_factory=lambda: ["bitcoin", "ethereum", "solana"])
    timeframes: list[str] = field(default_factory=lambda: ["5m", "15m", "1h", "4h"])
    hawk_review: bool = True
    event_poll_interval: int = 30       # seconds between event bus polls
    mini_opt_threshold: int = 10        # trades studied before auto mini-optimization

    # ── Phase 1: Intelligence Engine ──
    # Walk-Forward V2
    wfv2_max_overfit_gap: float = 10.0  # max IS-OOS gap (pp) to accept params
    wfv2_method: str = "anchored"       # "anchored" or "rolling"
    wfv2_folds: int = 5

    # Monte Carlo
    monte_carlo_sims: int = 10_000
    monte_carlo_ruin_threshold: float = 50.0  # % drawdown = ruin
    max_ruin_pct: float = 5.0                 # max acceptable ruin probability

    # CUSUM Edge Decay
    cusum_threshold: float = 5.0
    cusum_drift: float = 0.5
    cusum_rolling_window: int = 30

    # Kelly
    kelly_bankroll: float = 250.0
    kelly_fraction: str = "half"        # "full", "half", "quarter"

    # Live Push
    push_target: str = "garves"         # "garves", "odin", "both"
    push_dry_run: bool = True           # default to dry-run (safe)
    push_require_approval: bool = True  # needs human OK before applying

    # Odin integration
    odin_enabled: bool = True           # analyze Odin trades too

    # ── Odin Strategy Backtest ──
    odin_backtest_enabled: bool = True
    odin_backtest_symbols: list[str] = field(default_factory=lambda: [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT",
    ])
    odin_backtest_risk_per_trade: float = 15.0   # $ risk per simulated trade
    odin_backtest_min_score: int = 40             # min conviction score
    odin_backtest_min_confidence: float = 0.50
    odin_backtest_min_rr: float = 1.5
    odin_backtest_balance: float = 1000.0
    odin_backtest_step: int = 6      # 4H bars between analysis (6 = 1 day)
    odin_backtest_window: int = 200  # lookback bars for SMC
