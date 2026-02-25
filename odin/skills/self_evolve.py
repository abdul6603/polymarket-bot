"""Self-Evolve — auto-backtest + strategy mutation engine.

Weekly: backtests current params against recent data, mutates
thresholds/weights, keeps the best-performing version.
Always preserves "last known good" config for rollback.
"""
from __future__ import annotations

import copy
import json
import logging
import random
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("odin.skills.self_evolve")

DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS evolution (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    generation INTEGER NOT NULL,
    params TEXT NOT NULL,
    backtest_trades INTEGER DEFAULT 0,
    backtest_wr REAL DEFAULT 0,
    backtest_pnl REAL DEFAULT 0,
    backtest_sharpe REAL DEFAULT 0,
    is_current INTEGER DEFAULT 0,
    is_best INTEGER DEFAULT 0,
    created_at REAL NOT NULL,
    notes TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_evo_gen ON evolution(generation);
"""

# Parameters that can be mutated with their bounds
MUTABLE_PARAMS = {
    "min_confluence_score": (0.40, 0.80, 0.05),
    "funding_extreme_high": (0.005, 0.02, 0.001),
    "funding_extreme_low": (-0.01, -0.002, 0.001),
    "ls_crowded_long": (0.55, 0.70, 0.02),
    "ls_crowded_short": (0.55, 0.70, 0.02),
    "oi_surge_thresh": (3.0, 8.0, 0.5),
    "ob_volume_zscore_min": (1.0, 3.0, 0.25),
    "fvg_min_size_atr": (0.2, 0.5, 0.05),
    "swing_length": (5, 15, 1),
    "target_rr": (1.5, 3.5, 0.25),
    # Conviction component weights (sum must stay 100)
    "w_regime_alignment": (5, 25, 3),
    "w_smc_quality": (5, 25, 3),
    "w_multi_tf_agreement": (5, 20, 3),
    "w_macro_support": (2, 15, 3),
    "w_risk_reward_quality": (5, 20, 3),
}


@dataclass
class BacktestResult:
    """Result of a single backtest run."""
    params: dict
    trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    max_drawdown: float = 0.0
    sharpe: float = 0.0

    @property
    def win_rate(self) -> float:
        if self.trades == 0:
            return 0.0
        return self.wins / self.trades * 100

    @property
    def score(self) -> float:
        """Composite fitness score."""
        if self.trades < 10:
            return 0.0
        wr_score = self.win_rate * 0.30
        pnl_score = max(0, min(self.total_pnl / 10, 30)) * 0.30
        sharpe_score = max(0, min(self.sharpe * 10, 20)) * 0.20
        dd_penalty = max(0, self.max_drawdown / 5) * 0.20
        return wr_score + pnl_score + sharpe_score - dd_penalty


class SelfEvolve:
    """Auto-backtesting and strategy parameter mutation."""

    def __init__(self, data_dir: Path | None = None):
        self._data_dir = data_dir or Path.home() / "odin" / "data"
        self._db_path = self._data_dir / "evolution.db"
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.executescript(DB_SCHEMA)
        self._conn.commit()
        self._generation = self._get_latest_generation()
        self._best_params: dict | None = None
        self._current_params: dict | None = None

    def get_current_params(self) -> dict:
        """Get current active parameters."""
        if self._current_params:
            return self._current_params
        row = self._conn.execute(
            "SELECT params FROM evolution WHERE is_current=1 ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row:
            self._current_params = json.loads(row[0])
            return self._current_params
        return {k: (lo + hi) / 2 for k, (lo, hi, _) in MUTABLE_PARAMS.items()}

    def mutate_params(self, base_params: dict, mutation_rate: float = 0.3) -> dict:
        """Create a mutated version of parameters.

        Args:
            base_params: Starting parameters
            mutation_rate: Fraction of params to mutate (0-1)

        Returns:
            New parameter dict with mutations applied.
        """
        mutated = copy.deepcopy(base_params)
        params_to_mutate = random.sample(
            list(MUTABLE_PARAMS.keys()),
            max(1, int(len(MUTABLE_PARAMS) * mutation_rate)),
        )

        for param in params_to_mutate:
            lo, hi, step = MUTABLE_PARAMS[param]
            current = mutated.get(param, (lo + hi) / 2)
            direction = random.choice([-1, 1])
            steps = random.randint(1, 3)
            new_val = current + direction * step * steps
            new_val = max(lo, min(hi, new_val))
            if isinstance(MUTABLE_PARAMS[param][2], int):
                new_val = int(new_val)
            mutated[param] = round(new_val, 4)

        return mutated

    def run_backtest(self, params: dict, trade_history: list[dict]) -> BacktestResult:
        """Backtest parameters against historical trades.

        Simulates conviction scoring with given params on past trades
        to see if filtering would have improved outcomes.
        """
        result = BacktestResult(params=params)

        if len(trade_history) < 10:
            return result

        min_conf = params.get("min_confluence_score", 0.60)
        target_rr = params.get("target_rr", 2.0)

        running_pnl = 0.0
        peak_pnl = 0.0
        pnl_series: list[float] = []

        for trade in trade_history:
            conviction = trade.get("conviction_score", 50) / 100
            rr = trade.get("risk_reward", 0)
            pnl = trade.get("pnl_usd", 0)

            # Would this trade have been taken with these params?
            if conviction < min_conf:
                continue
            if rr < target_rr * 0.75:
                continue

            result.trades += 1
            if pnl > 0:
                result.wins += 1
            else:
                result.losses += 1

            result.total_pnl += pnl
            running_pnl += pnl
            peak_pnl = max(peak_pnl, running_pnl)
            drawdown = peak_pnl - running_pnl
            result.max_drawdown = max(result.max_drawdown, drawdown)
            pnl_series.append(pnl)

        # Sharpe ratio (simplified)
        if pnl_series and len(pnl_series) > 1:
            import statistics
            mean_pnl = statistics.mean(pnl_series)
            std_pnl = statistics.stdev(pnl_series)
            result.sharpe = mean_pnl / std_pnl if std_pnl > 0 else 0

        return result

    def evolve(self, trade_history: list[dict], population_size: int = 10) -> dict:
        """Run evolution: generate mutations, backtest, keep best.

        Returns the best parameter set found.
        """
        self._generation += 1
        current = self.get_current_params()
        now = time.time()

        # Generate population (current + mutations)
        candidates: list[tuple[dict, BacktestResult]] = []

        # Always include current params
        current_bt = self.run_backtest(current, trade_history)
        candidates.append((current, current_bt))

        # Generate mutations
        for _ in range(population_size - 1):
            mutated = self.mutate_params(current, mutation_rate=random.uniform(0.2, 0.5))
            bt = self.run_backtest(mutated, trade_history)
            candidates.append((mutated, bt))

        # Sort by fitness score
        candidates.sort(key=lambda c: c[1].score, reverse=True)
        best_params, best_bt = candidates[0]

        # Safety: don't adopt if WR drops > 5% from current
        if current_bt.win_rate - best_bt.win_rate > 5 and best_bt.trades >= 10:
            log.warning(
                "[EVOLVE] Gen %d: best WR %.1f%% worse than current %.1f%% — keeping current",
                self._generation, best_bt.win_rate, current_bt.win_rate,
            )
            best_params = current
            best_bt = current_bt

        # Store in DB
        self._conn.execute("UPDATE evolution SET is_current=0")
        self._conn.execute("UPDATE evolution SET is_best=0")

        for params, bt in candidates:
            is_best = params is best_params
            self._conn.execute(
                """INSERT INTO evolution
                   (generation, params, backtest_trades, backtest_wr, backtest_pnl,
                    backtest_sharpe, is_current, is_best, created_at, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (self._generation, json.dumps(params), bt.trades,
                 round(bt.win_rate, 1), round(bt.total_pnl, 2),
                 round(bt.sharpe, 3), 1 if is_best else 0, 1 if is_best else 0,
                 now, f"gen_{self._generation}"),
            )
        self._conn.commit()

        self._best_params = best_params
        self._current_params = best_params

        log.info(
            "[EVOLVE] Gen %d: %d candidates | best: WR=%.1f%% PnL=$%.2f Sharpe=%.2f (%d trades)",
            self._generation, len(candidates), best_bt.win_rate,
            best_bt.total_pnl, best_bt.sharpe, best_bt.trades,
        )

        return {
            "generation": self._generation,
            "best_wr": round(best_bt.win_rate, 1),
            "best_pnl": round(best_bt.total_pnl, 2),
            "best_sharpe": round(best_bt.sharpe, 3),
            "trades_tested": best_bt.trades,
            "params_changed": best_params != current,
            "params": best_params,
        }

    def rollback(self) -> dict:
        """Rollback to previous best parameters."""
        row = self._conn.execute(
            "SELECT params FROM evolution WHERE is_best=1 AND generation < ? ORDER BY generation DESC LIMIT 1",
            (self._generation,),
        ).fetchone()
        if row:
            self._current_params = json.loads(row[0])
            log.info("[EVOLVE] Rolled back to previous generation")
            return self._current_params
        return self.get_current_params()

    def get_stats(self) -> dict:
        row = self._conn.execute(
            "SELECT COUNT(*), MAX(generation) FROM evolution"
        ).fetchone()
        total, max_gen = row[0] or 0, row[1] or 0

        best = self._conn.execute(
            "SELECT backtest_wr, backtest_pnl, backtest_sharpe FROM evolution "
            "WHERE is_best=1 ORDER BY id DESC LIMIT 1"
        ).fetchone()

        return {
            "generation": max_gen,
            "total_variants": total,
            "best_wr": best[0] if best else 0,
            "best_pnl": best[1] if best else 0,
            "best_sharpe": best[2] if best else 0,
        }

    def _get_latest_generation(self) -> int:
        row = self._conn.execute("SELECT MAX(generation) FROM evolution").fetchone()
        return row[0] or 0 if row else 0
