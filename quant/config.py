"""Quant configuration."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class QuantConfig:
    cycle_minutes: int = 60
    max_combinations: int = 500
    min_trades_for_significance: int = 20
    assets: list[str] = field(default_factory=lambda: ["bitcoin", "ethereum", "solana"])
    timeframes: list[str] = field(default_factory=lambda: ["5m", "15m", "1h", "4h"])
    hawk_review: bool = True
    event_poll_interval: int = 30       # seconds between event bus polls
    mini_opt_threshold: int = 10        # trades studied before auto mini-optimization
