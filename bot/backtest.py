"""
GARVES — Backtesting Engine
Replays historical Binance price data through the signal engine and simulates
Polymarket-style "up or down" markets to estimate win rate and PnL.

Usage:
    python -m bot.backtest                              # 7-day BTC backtest
    python -m bot.backtest --asset ethereum --days 14   # 14-day ETH
    python -m bot.backtest --sweep                      # parameter sweep
    python -m bot.backtest --export results.csv         # export trades
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import requests as http_requests

from bot.indicators import (
    IndicatorVote,
    atr,
    bollinger_bands,
    ema_crossover,
    get_params,
    heikin_ashi,
    macd,
    momentum,
    order_flow_delta,
    price_divergence,
    rsi,
    temporal_arb,
    volume_spike,
    vwap,
)
from bot.price_cache import Candle, PriceCache
from bot.signals import (
    WEIGHTS,
    TF_WEIGHT_SCALE,
    PROB_CLAMP,
    MIN_CANDLES,
    MIN_CONSENSUS,
    MIN_ATR_THRESHOLD,
    MIN_CONFIDENCE,
    MIN_EDGE_BY_TF,
    _estimate_fees,
)
from bot.weight_learner import get_dynamic_weights

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
CANDLE_DIR = DATA_DIR / "candles"
CANDLE_DIR.mkdir(parents=True, exist_ok=True)

BINANCE_KLINES_URL = "https://api.binance.us/api/v3/klines"
SYMBOL_MAP = {"bitcoin": "BTCUSDT", "ethereum": "ETHUSDT", "solana": "SOLUSDT", "xrp": "XRPUSDT"}
TIMEFRAME_MINUTES = {"5m": 5, "15m": 15, "1h": 60, "4h": 240}


# ── Data structures ──

@dataclass
class BacktestTrade:
    timestamp: float
    asset: str
    timeframe: str
    direction: str       # predicted
    probability: float
    edge: float
    confidence: float
    outcome: str         # actual: "up" or "down"
    won: bool
    pnl: float
    entry_price: float
    indicator_votes: dict = field(default_factory=dict)


@dataclass
class TimeframeStats:
    timeframe: str
    trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    net_pnl: float = 0.0
    avg_edge: float = 0.0


@dataclass
class BacktestResult:
    asset: str
    timeframes: list[str]
    period_days: int
    total_candles: int
    total_signals: int
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    gross_pnl: float
    net_pnl: float
    avg_edge: float
    avg_confidence: float
    max_drawdown: float
    profit_factor: float
    sharpe_ratio: float
    trades_per_day: float
    tf_stats: dict[str, TimeframeStats] = field(default_factory=dict)
    indicator_accuracy: dict[str, dict] = field(default_factory=dict)
    trades: list[BacktestTrade] = field(default_factory=list)


# ── Historical data fetching from Binance ──

def fetch_binance_klines(asset: str, days: int, interval: str = "1m") -> list[Candle]:
    """Download historical klines from Binance REST API (public, no key needed)."""
    symbol = SYMBOL_MAP.get(asset)
    if not symbol:
        log.error("Unknown asset: %s", asset)
        return []

    end_ms = int(time.time() * 1000)
    start_ms = end_ms - (days * 86400 * 1000)
    all_candles: list[Candle] = []
    cursor = start_ms
    request_count = 0

    log.info("Fetching %d days of %s klines for %s...", days, interval, symbol)

    while cursor < end_ms:
        try:
            resp = http_requests.get(
                BINANCE_KLINES_URL,
                params={
                    "symbol": symbol,
                    "interval": interval,
                    "startTime": cursor,
                    "endTime": end_ms,
                    "limit": 1000,
                },
                timeout=15,
            )
            if resp.status_code == 429:
                log.warning("Rate limited, waiting 10s...")
                time.sleep(10)
                continue
            resp.raise_for_status()
            data = resp.json()
            if not data:
                break

            for k in data:
                all_candles.append(Candle(
                    timestamp=k[0] / 1000.0,
                    open=float(k[1]),
                    high=float(k[2]),
                    low=float(k[3]),
                    close=float(k[4]),
                    volume=float(k[5]),
                ))

            # Advance cursor past the last candle's open time
            # For 1m candles: +60s; for other intervals, use the interval gap
            last_open_ms = int(data[-1][0])
            if len(data) >= 2:
                gap = int(data[-1][0]) - int(data[-2][0])
                cursor = last_open_ms + max(gap, 60000)
            else:
                cursor = last_open_ms + 60000
            request_count += 1
            if request_count % 10 == 0:
                log.info("  ... %d candles fetched", len(all_candles))
            time.sleep(0.1)

        except http_requests.RequestException as e:
            log.error("Binance API error: %s, retrying in 5s...", e)
            time.sleep(5)

    all_candles.sort(key=lambda c: c.timestamp)
    log.info("Fetched %d candles (%d requests)", len(all_candles), request_count)
    return all_candles


def save_backtest_candles(asset: str, candles: list[Candle]) -> None:
    """Save fetched candles to disk for reuse."""
    fpath = CANDLE_DIR / f"{asset}_backtest.jsonl"
    existing: dict[float, dict] = {}
    if fpath.exists():
        try:
            with open(fpath) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        c = json.loads(line)
                        existing[c["timestamp"]] = c
        except Exception as e:
            log.warning("Failed to read existing backtest candles for %s: %s", asset, e)
    for c in candles:
        existing[c.timestamp] = {
            "timestamp": c.timestamp, "open": c.open, "high": c.high,
            "low": c.low, "close": c.close, "volume": c.volume,
        }
    sorted_c = sorted(existing.values(), key=lambda x: x["timestamp"])
    try:
        with open(fpath, "w") as f:
            for c in sorted_c:
                f.write(json.dumps(c) + "\n")
        log.info("Saved %d candles to %s", len(sorted_c), fpath)
    except Exception as e:
        log.error("Failed to write backtest candles for %s: %s", asset, e)


def load_backtest_candles(asset: str) -> list[Candle]:
    """Load backtest candles from disk."""
    fpath = CANDLE_DIR / f"{asset}_backtest.jsonl"
    if not fpath.exists():
        return []
    candles = []
    try:
        with open(fpath) as f:
            for line in f:
                line = line.strip()
                if line:
                    d = json.loads(line)
                    candles.append(Candle(**d))
    except Exception as e:
        log.warning("Failed to load backtest candles for %s: %s", asset, e)
        return []
    return sorted(candles, key=lambda c: c.timestamp)


# ── Signal engine replay (standalone, no network) ──

def _build_order_flow(candles: list[Candle], window: int = 30) -> tuple[float, float]:
    """Approximate buy/sell volume from candle direction (tick rule)."""
    recent = candles[-window:]
    buy_vol = sum(c.volume for c in recent if c.close >= c.open)
    sell_vol = sum(c.volume for c in recent if c.close < c.open)
    return buy_vol, sell_vol


def generate_backtest_signal(
    candles: list[Candle],
    asset: str,
    timeframe: str,
    implied_up_price: float = 0.50,
    weight_overrides: dict | None = None,
    min_consensus_override: int | None = None,
    min_confidence_override: float | None = None,
    min_edge_overrides: dict | None = None,
) -> tuple[dict | None, dict]:
    """Run the signal engine on a candle window. Returns (signal_dict, all_votes)."""
    closes = [c.close for c in candles]
    if len(closes) < MIN_CANDLES:
        return None, {}

    atr_val = atr(candles)
    if atr_val is not None and atr_val < MIN_ATR_THRESHOLD:
        return None, {}

    p = get_params(timeframe)

    votes: dict[str, IndicatorVote | None] = {
        "rsi": rsi(closes, period=p["rsi_period"]),
        "macd": macd(closes, fast=p["macd_fast"], slow=p["macd_slow"],
                      signal_period=p["macd_signal"]),
        "ema": ema_crossover(closes, fast=p["ema_fast"], slow=p["ema_slow"]),
        "heikin_ashi": heikin_ashi(candles),
        "bollinger": bollinger_bands(closes, period=p["bb_period"]),
        "momentum": momentum(closes, short_window=p["mom_short"],
                              long_window=p["mom_long"]),
        "vwap": vwap(candles),
        "volume_spike": volume_spike(candles),
    }

    # Order flow (approximated)
    buy_vol, sell_vol = _build_order_flow(candles)
    votes["order_flow"] = order_flow_delta(buy_vol, sell_vol)

    # No orderbook/liquidity/sentiment in backtest
    votes["orderbook"] = None
    votes["liquidity"] = None
    votes["sentiment"] = None

    # Price divergence (use candle data as proxy)
    binance_price = closes[-1]
    price_3m_ago = closes[-4] if len(closes) >= 4 else None
    if binance_price and price_3m_ago:
        votes["price_div"] = price_divergence(binance_price, price_3m_ago, implied_up_price)
        votes["temporal_arb"] = temporal_arb(binance_price, price_3m_ago, implied_up_price, timeframe)
    else:
        votes["price_div"] = None
        votes["temporal_arb"] = None

    active: dict[str, IndicatorVote] = {k: v for k, v in votes.items() if v is not None}
    if len(active) < 3:
        return None, {k: v.direction for k, v in active.items()}

    # Weighted ensemble
    base_weights = weight_overrides if weight_overrides else get_dynamic_weights(WEIGHTS)
    tf_scale = TF_WEIGHT_SCALE.get(timeframe, {})
    weighted_sum = 0.0
    weight_total = 0.0
    up_count = 0
    down_count = 0

    for name, vote in active.items():
        base_w = base_weights.get(name, 1.0)
        scale = tf_scale.get(name, 1.0)
        w = base_w * scale
        sign = 1.0 if vote.direction == "up" else -1.0
        weighted_sum += w * vote.confidence * sign
        weight_total += w
        if vote.direction == "up":
            up_count += 1
        else:
            down_count += 1

    if weight_total == 0:
        return None, {k: v.direction for k, v in active.items()}

    score = weighted_sum / weight_total

    # Consensus filter
    min_cons = min_consensus_override if min_consensus_override is not None else MIN_CONSENSUS
    majority_dir = "up" if up_count >= down_count else "down"
    agree_count = max(up_count, down_count)
    if agree_count < min_cons:
        return None, {k: v.direction for k, v in active.items()}

    # Anti-trend filter
    if len(closes) >= 50:
        short_trend = sum(closes[-10:]) / 10
        long_trend = sum(closes[-50:]) / 50
        trend_dir = "up" if short_trend > long_trend else "down"
        if majority_dir != trend_dir:
            anti_trend_min = max(min_cons + 2, int(len(active) * 0.7))
            if agree_count < anti_trend_min:
                return None, {k: v.direction for k, v in active.items()}

    # Probability
    lo, hi = PROB_CLAMP.get(timeframe, (0.30, 0.70))
    raw_prob = 0.5 + score * 0.25
    prob_up = max(lo, min(hi, raw_prob))
    confidence = min(abs(score), 1.0)

    min_conf = min_confidence_override if min_confidence_override is not None else MIN_CONFIDENCE
    if confidence < min_conf:
        return None, {k: v.direction for k, v in active.items()}

    # Edge
    if 0.01 < implied_up_price < 0.99:
        edge_up = prob_up - implied_up_price
        edge_down = (1 - prob_up) - (1 - implied_up_price)
    else:
        edge_up = prob_up - 0.50
        edge_down = (1 - prob_up) - 0.50

    fees = _estimate_fees(timeframe, implied_up_price)
    edge_up -= fees
    edge_down -= fees

    edge_map = min_edge_overrides if min_edge_overrides else MIN_EDGE_BY_TF
    min_edge = edge_map.get(timeframe, 0.03)
    if max(edge_up, edge_down) < min_edge:
        return None, {k: v.direction for k, v in active.items()}

    ind_votes = {name: vote.direction for name, vote in active.items()}

    if edge_up > edge_down and edge_up > 0:
        return {"direction": "up", "edge": edge_up, "probability": prob_up, "confidence": confidence}, ind_votes
    elif edge_down > 0:
        return {"direction": "down", "edge": edge_down, "probability": 1 - prob_up, "confidence": confidence}, ind_votes

    return None, ind_votes


# ── Backtest engine ──

class BacktestEngine:
    """Replay historical candles through the signal engine."""

    def __init__(
        self,
        asset: str = "bitcoin",
        timeframes: list[str] | None = None,
        days: int = 7,
        order_size: float = 10.0,
        max_concurrent: int = 2,
        weight_overrides: dict | None = None,
        min_consensus: int | None = None,
        min_confidence: float | None = None,
        min_edge_overrides: dict | None = None,
    ):
        self.asset = asset
        self.timeframes = timeframes or ["5m", "15m"]
        self.days = days
        self.order_size = order_size
        self.max_concurrent = max_concurrent
        self.weight_overrides = weight_overrides
        self.min_consensus = min_consensus
        self.min_confidence = min_confidence
        self.min_edge_overrides = min_edge_overrides

    def _load_or_fetch(self) -> list[Candle]:
        """Load cached candles or fetch from Binance."""
        cached = load_backtest_candles(self.asset)
        now = time.time()
        need_start = now - (self.days * 86400)

        if cached:
            first_ts = cached[0].timestamp
            last_ts = cached[-1].timestamp
            if first_ts <= need_start + 3600 and last_ts >= now - 3600:
                log.info("Using cached data: %d candles", len(cached))
                return [c for c in cached if c.timestamp >= need_start]

        candles = fetch_binance_klines(self.asset, self.days)
        if candles:
            save_backtest_candles(self.asset, candles)
        return candles

    def run(self) -> BacktestResult:
        """Execute the backtest."""
        candles = self._load_or_fetch()
        if not candles:
            log.error("No candle data for %s", self.asset)
            return self._empty_result()

        log.info("Backtesting %s | %s | %d candles (%d days)",
                 self.asset.upper(), ", ".join(self.timeframes), len(candles), self.days)

        trades: list[BacktestTrade] = []
        total_signals = 0
        ind_stats: dict[str, dict] = defaultdict(lambda: {"total": 0, "correct": 0})
        tf_trades: dict[str, list[BacktestTrade]] = defaultdict(list)

        for tf in self.timeframes:
            window_min = TIMEFRAME_MINUTES[tf]
            warmup = max(MIN_CANDLES, 60)

            if len(candles) < warmup + window_min:
                log.warning("Not enough data for %s", tf)
                continue

            step = window_min
            open_count = 0
            open_close_times: list[float] = []

            i = warmup
            while i + window_min < len(candles):
                # Expire open positions
                current_ts = candles[i].timestamp
                while open_close_times and open_close_times[0] <= current_ts:
                    open_close_times.pop(0)
                    open_count = max(0, open_count - 1)

                history = candles[max(0, i - 200):i]
                window_start = candles[i].open
                window_end = candles[min(i + window_min - 1, len(candles) - 1)].close
                actual = "up" if window_end > window_start else "down"

                signal, votes = generate_backtest_signal(
                    history, self.asset, tf,
                    implied_up_price=0.50,
                    weight_overrides=self.weight_overrides,
                    min_consensus_override=self.min_consensus,
                    min_confidence_override=self.min_confidence,
                    min_edge_overrides=self.min_edge_overrides,
                )

                if signal is not None:
                    total_signals += 1

                    if open_count >= self.max_concurrent:
                        i += step
                        continue

                    won = signal["direction"] == actual
                    entry_price = signal["probability"]

                    if won:
                        pnl = (1.0 - entry_price) * self.order_size - 0.02 * self.order_size
                    else:
                        pnl = -entry_price * self.order_size

                    trade = BacktestTrade(
                        timestamp=candles[i].timestamp,
                        asset=self.asset,
                        timeframe=tf,
                        direction=signal["direction"],
                        probability=signal["probability"],
                        edge=signal["edge"],
                        confidence=signal["confidence"],
                        outcome=actual,
                        won=won,
                        pnl=pnl,
                        entry_price=entry_price,
                        indicator_votes=votes,
                    )
                    trades.append(trade)
                    tf_trades[tf].append(trade)
                    open_count += 1
                    open_close_times.append(current_ts + window_min * 60)

                    for ind_name, ind_dir in votes.items():
                        ind_stats[ind_name]["total"] += 1
                        if ind_dir == actual:
                            ind_stats[ind_name]["correct"] += 1

                i += step

        return self._compile(candles, trades, total_signals, tf_trades, ind_stats)

    def _compile(self, candles, trades, total_signals, tf_trades, ind_stats) -> BacktestResult:
        if not trades:
            return self._empty_result(len(candles))

        wins = sum(1 for t in trades if t.won)
        losses = len(trades) - wins
        win_rate = wins / len(trades)
        winning_pnl = sum(t.pnl for t in trades if t.won)
        losing_pnl = abs(sum(t.pnl for t in trades if not t.won))
        net_pnl = sum(t.pnl for t in trades)
        avg_edge = sum(t.edge for t in trades) / len(trades)
        avg_conf = sum(t.confidence for t in trades) / len(trades)

        # Max drawdown
        equity = []
        running = 0.0
        for t in trades:
            running += t.pnl
            equity.append(running)
        peak = 0.0
        max_dd = 0.0
        for eq in equity:
            peak = max(peak, eq)
            max_dd = max(max_dd, peak - eq)

        profit_factor = winning_pnl / losing_pnl if losing_pnl > 0 else float("inf")

        # Sharpe
        daily_pnl: dict[str, float] = defaultdict(float)
        for t in trades:
            day = datetime.fromtimestamp(t.timestamp, tz=timezone.utc).strftime("%Y-%m-%d")
            daily_pnl[day] += t.pnl
        if len(daily_pnl) > 1:
            rets = list(daily_pnl.values())
            sharpe = float(np.mean(rets) / np.std(rets) * np.sqrt(365)) if np.std(rets) > 0 else 0.0
        else:
            sharpe = 0.0

        span = (candles[-1].timestamp - candles[0].timestamp) / 86400 if candles else 1
        trades_per_day = len(trades) / max(span, 1)

        tf_stats = {}
        for tf, tlist in tf_trades.items():
            if not tlist:
                continue
            tw = sum(1 for t in tlist if t.won)
            tf_stats[tf] = TimeframeStats(
                timeframe=tf, trades=len(tlist), wins=tw, losses=len(tlist) - tw,
                win_rate=tw / len(tlist),
                net_pnl=sum(t.pnl for t in tlist),
                avg_edge=sum(t.edge for t in tlist) / len(tlist),
            )

        ind_acc = {}
        for name, s in ind_stats.items():
            ind_acc[name] = {"total": s["total"], "correct": s["correct"],
                             "accuracy": s["correct"] / s["total"] if s["total"] > 0 else 0.0}

        return BacktestResult(
            asset=self.asset, timeframes=self.timeframes, period_days=self.days,
            total_candles=len(candles), total_signals=total_signals, total_trades=len(trades),
            wins=wins, losses=losses, win_rate=win_rate,
            gross_pnl=winning_pnl, net_pnl=net_pnl, avg_edge=avg_edge,
            avg_confidence=avg_conf, max_drawdown=max_dd, profit_factor=profit_factor,
            sharpe_ratio=sharpe, trades_per_day=trades_per_day,
            tf_stats=tf_stats, indicator_accuracy=ind_acc, trades=trades,
        )

    def _empty_result(self, n: int = 0) -> BacktestResult:
        return BacktestResult(
            asset=self.asset, timeframes=self.timeframes, period_days=self.days,
            total_candles=n, total_signals=0, total_trades=0, wins=0, losses=0,
            win_rate=0.0, gross_pnl=0.0, net_pnl=0.0, avg_edge=0.0, avg_confidence=0.0,
            max_drawdown=0.0, profit_factor=0.0, sharpe_ratio=0.0, trades_per_day=0.0,
        )


# ── Parameter sweep ──

def run_sweep(asset: str, days: int) -> list[tuple[str, BacktestResult]]:
    """Test multiple parameter configurations and rank by net PnL."""
    configs: list[tuple[str, dict]] = [
        ("baseline", {}),
        ("consensus_5", {"min_consensus": 5}),
        ("consensus_6", {"min_consensus": 6}),
        ("confidence_0.30", {"min_confidence": 0.30}),
        ("confidence_0.35", {"min_confidence": 0.35}),
        ("edge_5m=0.07", {"min_edge_overrides": {**MIN_EDGE_BY_TF, "5m": 0.07}}),
        ("edge_5m=0.10", {"min_edge_overrides": {**MIN_EDGE_BY_TF, "5m": 0.10}}),
        ("15m_only", {"timeframes": ["15m"]}),
        ("5m_only", {"timeframes": ["5m"]}),
        ("boost_arb", {"weight_overrides": {**WEIGHTS, "temporal_arb": 3.0, "price_div": 2.0}}),
        ("boost_orderflow", {"weight_overrides": {**WEIGHTS, "order_flow": 2.0}}),
        ("reduce_momentum", {"weight_overrides": {**WEIGHTS, "momentum": 0.5, "ema": 0.6}}),
        ("aggressive", {"min_consensus": 3, "min_confidence": 0.20,
                        "min_edge_overrides": {**MIN_EDGE_BY_TF, "5m": 0.03, "15m": 0.02}}),
        ("conservative", {"min_consensus": 6, "min_confidence": 0.35,
                          "min_edge_overrides": {**MIN_EDGE_BY_TF, "5m": 0.08, "15m": 0.05}}),
    ]

    results: list[tuple[str, BacktestResult]] = []
    for name, params in configs:
        # Copy params to avoid mutating the config dicts
        params = dict(params)
        tfs = params.pop("timeframes", None)
        engine = BacktestEngine(
            asset=asset, timeframes=tfs or ["5m", "15m"], days=days,
            weight_overrides=params.get("weight_overrides"),
            min_consensus=params.get("min_consensus"),
            min_confidence=params.get("min_confidence"),
            min_edge_overrides=params.get("min_edge_overrides"),
        )
        result = engine.run()
        results.append((name, result))
        log.info("Sweep [%s]: %d trades, WR=%.1f%%, PnL=$%.2f",
                 name, result.total_trades, result.win_rate * 100, result.net_pnl)

    results.sort(key=lambda x: x[1].net_pnl, reverse=True)
    return results


# ── Reporting ──

def print_report(result: BacktestResult) -> None:
    """Print a comprehensive backtest report."""
    G = "\033[92m"  # green
    Y = "\033[93m"  # yellow
    R = "\033[91m"  # red
    B = "\033[1m"   # bold
    D = "\033[2m"   # dim
    X = "\033[0m"   # reset

    print(f"\n{B}{'=' * 60}")
    print(f"  GARVES BACKTEST: {result.asset.upper()}")
    print(f"  {result.period_days} days | {', '.join(result.timeframes)}")
    print(f"{'=' * 60}{X}")

    print(f"\n  Candles:           {result.total_candles:,}")
    print(f"  Signals generated: {result.total_signals:,}")
    print(f"  Trades executed:   {result.total_trades:,}")
    print(f"  Trades/day:        {result.trades_per_day:.1f}")

    print(f"\n  {'-' * 40}")
    print(f"  Wins:     {result.wins}")
    print(f"  Losses:   {result.losses}")
    wr_c = G if result.win_rate >= 0.55 else Y if result.win_rate >= 0.50 else R
    print(f"  Win Rate: {wr_c}{B}{result.win_rate:.1%}{X}")

    print(f"\n  {'-' * 40}")
    pnl_c = G if result.net_pnl >= 0 else R
    print(f"  Gross PnL:      ${result.gross_pnl:+.2f}")
    print(f"  Net PnL:        {pnl_c}{B}${result.net_pnl:+.2f}{X}")
    print(f"  Max Drawdown:   ${result.max_drawdown:.2f}")
    print(f"  Profit Factor:  {result.profit_factor:.2f}")
    print(f"  Sharpe Ratio:   {result.sharpe_ratio:.2f}")
    print(f"  Avg Edge:       {result.avg_edge:.1%}")
    print(f"  Avg Confidence: {result.avg_confidence:.2f}")

    if result.tf_stats:
        print(f"\n  {'-' * 40}")
        print(f"  {B}PER-TIMEFRAME:{X}")
        print(f"  {'TF':<6} {'Trades':>7} {'Wins':>5} {'WR':>7} {'PnL':>9} {'Edge':>7}")
        for tf, s in sorted(result.tf_stats.items()):
            c = G if s.win_rate >= 0.55 else Y if s.win_rate >= 0.50 else R
            print(f"  {s.timeframe:<6} {s.trades:>7} {s.wins:>5} {c}{s.win_rate:>6.1%}{X} ${s.net_pnl:>+8.2f} {s.avg_edge:>6.1%}")

    if result.indicator_accuracy:
        print(f"\n  {'-' * 40}")
        print(f"  {B}INDICATOR ACCURACY:{X}")
        print(f"  {'Name':<16} {'Votes':>6} {'Correct':>8} {'Accuracy':>9}")
        for name, s in sorted(result.indicator_accuracy.items(), key=lambda x: x[1]["accuracy"], reverse=True):
            c = G if s["accuracy"] >= 0.55 else Y if s["accuracy"] >= 0.50 else R
            print(f"  {name:<16} {s['total']:>6} {s['correct']:>8} {c}{s['accuracy']:>8.1%}{X}")

    if result.trades:
        print(f"\n  {'-' * 40}")
        print(f"  {B}LAST 10 TRADES:{X}")
        print(f"  {'Time':<18} {'TF':<5} {'Dir':>5} {'Result':>7} {'PnL':>8} {'Edge':>6}")
        for t in result.trades[-10:]:
            ts = datetime.fromtimestamp(t.timestamp, tz=timezone.utc).strftime("%m-%d %H:%M")
            rc = G if t.won else R
            print(f"  {ts:<18} {t.timeframe:<5} {t.direction:>5} {rc}{'WIN' if t.won else 'LOSS':>7}{X} ${t.pnl:>+7.2f} {t.edge:>5.1%}")

        # Equity curve
        print(f"\n  {'-' * 40}")
        print(f"  {B}EQUITY CURVE:{X}")
        cum = 0.0
        step = max(1, len(result.trades) // 15)
        for i, t in enumerate(result.trades):
            cum += t.pnl
            if i % step == 0 or i == len(result.trades) - 1:
                bar_len = int(abs(cum) / max(result.max_drawdown, 1) * 20)
                c = G if cum >= 0 else R
                bar = "#" * min(bar_len, 30)
                print(f"  {D}Trade {i + 1:>4d}:{X} {c}${cum:>+8.2f} {bar}{X}")

    print(f"\n{B}{'=' * 60}{X}\n")


def print_sweep_report(results: list[tuple[str, BacktestResult]]) -> None:
    """Print parameter sweep comparison."""
    B = "\033[1m"
    G = "\033[92m"
    R = "\033[91m"
    X = "\033[0m"

    print(f"\n{B}{'=' * 80}")
    print("  PARAMETER SWEEP RESULTS")
    print(f"{'=' * 80}{X}")
    print(f"  {'Config':<24} {'Trades':>7} {'WR':>7} {'Net PnL':>10} {'Sharpe':>8} {'MaxDD':>8} {'PF':>6}")
    print(f"  {'-' * 72}")

    for name, r in results:
        c = G if r.net_pnl >= 0 else R
        print(f"  {name:<24} {r.total_trades:>7} {r.win_rate:>6.1%} {c}${r.net_pnl:>+9.2f}{X} {r.sharpe_ratio:>8.2f} ${r.max_drawdown:>7.2f} {r.profit_factor:>5.2f}")

    if results:
        best_name, best = results[0]
        print(f"\n  {B}BEST: {G}{best_name}{X}")
        print(f"  WR: {best.win_rate:.1%} | PnL: ${best.net_pnl:+.2f} | Sharpe: {best.sharpe_ratio:.2f}")

    print(f"{B}{'=' * 80}{X}\n")


def export_trades(trades: list[BacktestTrade], filepath: str) -> None:
    """Export trades to CSV."""
    try:
        with open(filepath, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "timestamp", "datetime", "asset", "timeframe", "direction",
                "probability", "edge", "confidence", "outcome", "won", "pnl",
                "entry_price", "indicator_votes",
            ])
            writer.writeheader()
            for t in trades:
                writer.writerow({
                    "timestamp": t.timestamp,
                    "datetime": datetime.fromtimestamp(t.timestamp, tz=timezone.utc).isoformat(),
                    "asset": t.asset, "timeframe": t.timeframe, "direction": t.direction,
                    "probability": f"{t.probability:.4f}", "edge": f"{t.edge:.4f}",
                    "confidence": f"{t.confidence:.4f}", "outcome": t.outcome,
                    "won": t.won, "pnl": f"{t.pnl:.4f}", "entry_price": f"{t.entry_price:.4f}",
                    "indicator_votes": json.dumps(t.indicator_votes),
                })
        print(f"\nExported {len(trades)} trades to {filepath}")
    except Exception as e:
        log.error("Failed to export trades to %s: %s", filepath, e)
        print(f"\nError exporting trades to {filepath}: {e}")


# ── CLI ──

def main() -> None:
    parser = argparse.ArgumentParser(description="Garves Backtesting Engine")
    parser.add_argument("--asset", default="bitcoin", choices=["bitcoin", "ethereum", "solana"])
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--timeframes", nargs="+", default=["5m", "15m"],
                        choices=["5m", "15m", "1h", "4h"])
    parser.add_argument("--sweep", action="store_true", help="Run parameter sweep")
    parser.add_argument("--export", type=str, help="Export trades to CSV")
    parser.add_argument("--order-size", type=float, default=10.0)
    parser.add_argument("--max-concurrent", type=int, default=2)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.sweep:
        results = run_sweep(args.asset, args.days)
        print_sweep_report(results)
        if results:
            print_report(results[0][1])
    else:
        engine = BacktestEngine(
            asset=args.asset, timeframes=args.timeframes, days=args.days,
            order_size=args.order_size, max_concurrent=args.max_concurrent,
        )
        result = engine.run()
        print_report(result)
        if args.export and result.trades:
            export_trades(result.trades, args.export)


if __name__ == "__main__":
    main()
