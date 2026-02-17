"""Quant — The Strategy Alchemist. Main loop."""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from quant.config import QuantConfig
from quant.data_loader import load_all_candles, load_all_trades, load_indicator_accuracy
from quant.backtester import replay_historical_trades, backtest_candle_indicators
from quant.optimizer import run_optimization, get_live_params
from quant.reporter import (
    write_status, write_results, write_recommendations,
    write_hawk_review, publish_events,
)
from quant.scorer import score_result

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


class QuantBot:
    """Main Quant agent loop."""

    def __init__(self, cfg: QuantConfig | None = None):
        self.cfg = cfg or QuantConfig()
        self.cycle = 0

    async def run(self):
        """Run backtesting cycles forever."""
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [QUANT] %(levelname)s %(message)s",
            datefmt="%H:%M:%S",
        )
        log.info("Quant — The Strategy Alchemist starting up")
        log.info("Config: cycle=%dm, max_combos=%d, min_trades=%d",
                 self.cfg.cycle_minutes, self.cfg.max_combinations,
                 self.cfg.min_trades_for_significance)

        while True:
            self.cycle += 1
            try:
                await self._run_cycle()
            except Exception:
                log.exception("Cycle %d failed", self.cycle)

            log.info("Sleeping %d minutes until next cycle...", self.cfg.cycle_minutes)
            await asyncio.sleep(self.cfg.cycle_minutes * 60)

    async def _run_cycle(self):
        """Single backtest cycle."""
        log.info("=== Cycle %d starting ===", self.cycle)

        # 1. Load data
        log.info("Loading historical data...")
        trades = load_all_trades()
        candles = load_all_candles()
        accuracy = load_indicator_accuracy()

        candle_counts = {asset: len(c) for asset, c in candles.items()}
        log.info("Data: %d trades, candles: %s", len(trades), candle_counts)

        if len(trades) < self.cfg.min_trades_for_significance:
            log.warning("Only %d trades — need %d for significance. Running baseline only.",
                        len(trades), self.cfg.min_trades_for_significance)

        # 2. Run optimization (baseline + parameter sweep)
        baseline, scored = run_optimization(
            trades=trades,
            max_combinations=self.cfg.max_combinations,
            min_trades=self.cfg.min_trades_for_significance,
        )

        # 3. Load Hawk trades for calibration review
        hawk_trades = self._load_hawk_trades()

        # 4. Write all reports
        write_status(self.cycle, baseline, len(scored), len(trades), candle_counts)
        write_results(baseline, scored)
        write_recommendations(baseline, scored)
        if self.cfg.hawk_review:
            write_hawk_review(hawk_trades)

        # 5. Publish to event bus
        publish_events(baseline, scored)

        # 6. Log summary
        best_wr = scored[0][1].win_rate if scored and scored[0][1].total_signals >= 20 else 0
        log.info("=== Cycle %d complete ===", self.cycle)
        log.info("Baseline: WR=%.1f%% (%d signals)", baseline.win_rate, baseline.total_signals)
        log.info("Best found: WR=%.1f%% | Combos tested: %d", best_wr, len(scored))

    def _load_hawk_trades(self) -> list[dict]:
        """Load Hawk trades for calibration review."""
        hawk_file = DATA_DIR / "hawk_trades.jsonl"
        if not hawk_file.exists():
            return []
        trades = []
        try:
            with open(hawk_file) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        trades.append(json.loads(line))
        except Exception:
            pass
        return trades


def run_single_backtest(progress_callback=None) -> dict:
    """Run a single backtest cycle (called from dashboard API).

    Returns summary dict for the API response.
    """
    trades = load_all_trades()
    candles = load_all_candles()

    baseline, scored = run_optimization(
        trades=trades,
        max_combinations=500,
        min_trades=20,
        progress_callback=progress_callback,
    )

    # Write reports
    candle_counts = {asset: len(c) for asset, c in candles.items()}
    write_status(0, baseline, len(scored), len(trades), candle_counts)
    write_results(baseline, scored)
    write_recommendations(baseline, scored)

    # Hawk review
    hawk_file = DATA_DIR / "hawk_trades.jsonl"
    hawk_trades = []
    if hawk_file.exists():
        try:
            with open(hawk_file) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        hawk_trades.append(json.loads(line))
        except Exception:
            pass
    write_hawk_review(hawk_trades)

    publish_events(baseline, scored)

    best = scored[0][1] if scored and scored[0][1].total_signals >= 20 else baseline
    return {
        "baseline_wr": round(baseline.win_rate, 1),
        "best_wr": round(best.win_rate, 1),
        "combos_tested": len(scored),
        "trades_used": len(trades),
    }
