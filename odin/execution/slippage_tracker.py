"""Execution quality tracking — per-symbol slippage and latency.

Principle: Edge must be measurable. Tracks actual execution costs
to build a dynamic fee model that replaces the static paper_fee_rate.
"""
from __future__ import annotations

import json
import logging
import statistics
import time
from pathlib import Path

log = logging.getLogger("odin.execution.slippage")


class SlippageTracker:
    """Records execution quality per symbol for dynamic fee modeling."""

    def __init__(self, data_dir: Path | None = None):
        self._data_dir = data_dir or Path(__file__).parent.parent / "data"
        self._file = self._data_dir / "slippage.jsonl"
        self._cache: dict[str, list[dict]] = {}  # symbol -> records

    def record(
        self,
        symbol: str,
        signal_price: float,
        fill_price: float,
        signal_ts: float,
        fill_ts: float,
        order_type: str = "market",
    ) -> None:
        """Record execution quality for one fill."""
        if signal_price <= 0 or fill_price <= 0:
            return

        slippage_pct = (fill_price - signal_price) / signal_price * 100
        latency_ms = (fill_ts - signal_ts) * 1000 if fill_ts > signal_ts else 0

        record = {
            "symbol": symbol,
            "signal_price": signal_price,
            "fill_price": fill_price,
            "slippage_pct": round(slippage_pct, 5),
            "latency_ms": round(latency_ms, 1),
            "order_type": order_type,
            "timestamp": time.time(),
        }

        # Append to file
        try:
            with open(self._file, "a") as f:
                f.write(json.dumps(record) + "\n")
        except Exception:
            pass

        # Update cache
        self._cache.setdefault(symbol, []).append(record)
        # Keep last 200 per symbol
        if len(self._cache[symbol]) > 200:
            self._cache[symbol] = self._cache[symbol][-200:]

        log.debug(
            "[SLIPPAGE] %s: signal=$%.2f fill=$%.2f slip=%.4f%% lat=%.0fms",
            symbol, signal_price, fill_price, slippage_pct, latency_ms,
        )

    def get_symbol_stats(self, symbol: str) -> dict:
        """Per-symbol execution statistics."""
        records = self._get_records(symbol)
        if not records:
            return {"avg_slippage_pct": 0, "avg_latency_ms": 0, "sample_size": 0}

        slippages = [r["slippage_pct"] for r in records]
        latencies = [r["latency_ms"] for r in records]

        return {
            "avg_slippage_pct": round(statistics.mean(slippages), 5),
            "median_slippage_pct": round(statistics.median(slippages), 5),
            "avg_latency_ms": round(statistics.mean(latencies), 1),
            "sample_size": len(records),
            "worst_slippage_pct": round(max(abs(s) for s in slippages), 5),
        }

    def get_dynamic_fee_rate(self, symbol: str, static_fee_rate: float = 0.0017) -> float:
        """Per-symbol round-trip cost including measured slippage.

        Falls back to static fee_rate if < 5 samples.
        Returns rate as decimal (0.0017 = 0.17%).
        """
        stats = self.get_symbol_stats(symbol)
        if stats["sample_size"] < 5:
            return static_fee_rate

        # Round-trip: 2x avg abs slippage + base exchange fee (0.07% for HL taker)
        avg_abs_slip = abs(stats["avg_slippage_pct"]) / 100
        base_fee = 0.0007  # Hyperliquid taker fee per side
        dynamic = (avg_abs_slip + base_fee) * 2

        # Never go below exchange minimum
        return max(dynamic, 0.0014)

    def get_all_stats(self) -> dict:
        """Stats for all tracked symbols."""
        symbols = set()
        if self._file.exists():
            for line in self._file.read_text().strip().split("\n"):
                try:
                    symbols.add(json.loads(line)["symbol"])
                except (json.JSONDecodeError, KeyError):
                    continue

        return {sym: self.get_symbol_stats(sym) for sym in symbols}

    def _get_records(self, symbol: str, limit: int = 100) -> list[dict]:
        """Get recent records for a symbol."""
        if symbol in self._cache and self._cache[symbol]:
            return self._cache[symbol][-limit:]

        # Load from file
        if not self._file.exists():
            return []

        records = []
        for line in self._file.read_text().strip().split("\n"):
            try:
                r = json.loads(line)
                if r.get("symbol") == symbol:
                    records.append(r)
            except json.JSONDecodeError:
                continue

        self._cache[symbol] = records[-200:]
        return records[-limit:]
