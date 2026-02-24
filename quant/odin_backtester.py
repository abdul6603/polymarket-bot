"""Odin strategy backtester — replays SMC + regime + conviction on historical candles.

Walk a 200-candle window across 4H candles (primary TF).
At each step:
  1. Resample to 1D (HTF) and build from 4H (MTF) windows
  2. Run SMC engine on each TF → detect patterns
  3. Run MultiTimeframeAnalyzer → generate signal
  4. Classify regime from price data (no CoinGlass)
  5. Score conviction (simplified — no journal/brotherhood)
  6. Simulate position entry + exit (trailing SL, partial TPs)
  7. Track PnL per trade

Data: Binance klines downloaded via quant/bulk_download.py (JSONL).
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# We import Odin modules at runtime to avoid hard dependency
_odin_loaded = False
_SMCEngine = None
_MultiTimeframeAnalyzer = None
_Direction = None


def _ensure_odin():
    """Lazy-load Odin strategy modules."""
    global _odin_loaded, _SMCEngine, _MultiTimeframeAnalyzer, _Direction
    if _odin_loaded:
        return
    import sys
    odin_dir = str(Path.home() / "odin")
    if odin_dir not in sys.path:
        sys.path.insert(0, odin_dir)
    from odin.strategy.smc_engine import SMCEngine, Direction
    from odin.strategy.multi_tf import MultiTimeframeAnalyzer
    _SMCEngine = SMCEngine
    _MultiTimeframeAnalyzer = MultiTimeframeAnalyzer
    _Direction = Direction
    _odin_loaded = True


# ── Data Structures ──

@dataclass
class BacktestTrade:
    """A single simulated trade."""
    symbol: str
    direction: str          # "LONG" or "SHORT"
    entry_price: float
    entry_time: float       # Unix timestamp
    exit_price: float = 0.0
    exit_time: float = 0.0
    exit_reason: str = ""   # "sl", "tp1", "tp2", "tp3", "time", "end"
    qty: float = 0.0
    risk_usd: float = 0.0
    pnl_usd: float = 0.0
    pnl_pct: float = 0.0
    r_multiple: float = 0.0
    stop_loss: float = 0.0
    take_profit_1: float = 0.0
    regime: str = ""
    conviction: float = 0.0
    confidence: float = 0.0
    is_win: bool = False
    hold_hours: float = 0.0

    # Partial TP tracking
    partial_exits: list = field(default_factory=list)


@dataclass
class OdinBacktestResult:
    """Full backtest summary."""
    symbol: str
    timeframe: str
    candles_used: int = 0
    trades: list[BacktestTrade] = field(default_factory=list)
    signals_generated: int = 0
    signals_filtered: int = 0

    # Aggregate stats (computed by scorer)
    total_pnl: float = 0.0
    win_count: int = 0
    loss_count: int = 0
    win_rate: float = 0.0
    avg_r: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe: float = 0.0
    profit_factor: float = 0.0
    elapsed_seconds: float = 0.0


# ── Exit Simulation ──

@dataclass
class _ExitState:
    """Tracks trailing SL + partial TP state during simulation."""
    highest: float = 0.0
    lowest: float = float("inf")
    current_sl: float = 0.0
    original_sl: float = 0.0
    entry_price: float = 0.0
    r_distance: float = 0.0
    remaining_frac: float = 1.0  # Fraction of position still open
    tp1_hit: bool = False
    tp2_hit: bool = False
    weighted_exit: float = 0.0   # Accumulated weighted exit price
    weight_sum: float = 0.0


def _simulate_exit(
    candles_after: pd.DataFrame,
    direction: str,
    entry_price: float,
    stop_loss: float,
    take_profit_1: float,
    entry_time: float,
    max_bars: int = 200,
) -> tuple[float, float, str, list]:
    """Walk forward through candles to simulate exit.

    Returns (exit_price, exit_time, reason, partial_exits).
    Simulates:
      - SL hit
      - TP1 at 1.5R (close 50%)
      - TP2 at 2.5R (close 30%)
      - TP3 at 4.0R (close remaining 20%)
      - Time exit at max_bars
      - Trailing SL after 1R
    """
    if len(candles_after) == 0 or stop_loss <= 0:
        return entry_price, entry_time, "no_data", []

    r_dist = abs(entry_price - stop_loss)
    if r_dist <= 0:
        return entry_price, entry_time, "zero_risk", []

    state = _ExitState(
        highest=entry_price,
        lowest=entry_price,
        current_sl=stop_loss,
        original_sl=stop_loss,
        entry_price=entry_price,
        r_distance=r_dist,
    )

    partials = []
    bars = min(max_bars, len(candles_after))
    highs = candles_after["high"].values[:bars]
    lows = candles_after["low"].values[:bars]
    closes = candles_after["close"].values[:bars]
    times = candles_after.index.values[:bars] if hasattr(candles_after.index, "values") else list(range(bars))

    for i in range(bars):
        h, l, c = float(highs[i]), float(lows[i]), float(closes[i])
        t = float(times[i]) if isinstance(times[i], (int, float, np.integer, np.floating)) else entry_time + i * 14400

        # Update high/low water marks
        state.highest = max(state.highest, h)
        state.lowest = min(state.lowest, l)

        # Check SL hit first (uses candle low for LONG, high for SHORT)
        if direction == "LONG" and l <= state.current_sl:
            _record_partial(state, partials, state.current_sl, t, "sl", state.remaining_frac)
            return _weighted_exit(state), t, "sl", partials
        elif direction == "SHORT" and h >= state.current_sl:
            _record_partial(state, partials, state.current_sl, t, "sl", state.remaining_frac)
            return _weighted_exit(state), t, "sl", partials

        # Current R-multiple
        if direction == "LONG":
            current_r = (c - entry_price) / r_dist
            best_r = (state.highest - entry_price) / r_dist
        else:
            current_r = (entry_price - c) / r_dist
            best_r = (entry_price - state.lowest) / r_dist

        # Partial TP checks
        if not state.tp1_hit and current_r >= 1.5:
            _record_partial(state, partials, c, t, "tp1", 0.50)
            state.tp1_hit = True
            # Move SL to breakeven
            state.current_sl = entry_price

        if not state.tp2_hit and state.tp1_hit and current_r >= 2.5:
            frac = min(0.60, state.remaining_frac)  # 30% of original = 60% of remaining after TP1
            _record_partial(state, partials, c, t, "tp2", frac)
            state.tp2_hit = True

        if state.tp2_hit and current_r >= 4.0:
            _record_partial(state, partials, c, t, "tp3", state.remaining_frac)
            return _weighted_exit(state), t, "tp3", partials

        # Trailing SL (activate after 1R, trail at 1.5 * r_dist)
        if current_r >= 1.0:
            trail_dist = r_dist * 1.5
            if direction == "LONG":
                new_sl = state.highest - trail_dist
            else:
                new_sl = state.lowest + trail_dist

            if direction == "LONG" and new_sl > state.current_sl:
                state.current_sl = new_sl
            elif direction == "SHORT" and new_sl < state.current_sl:
                state.current_sl = new_sl

    # Time exit at max bars — close at last close
    final_price = float(closes[-1])
    final_time = float(times[-1]) if isinstance(times[-1], (int, float, np.integer, np.floating)) else entry_time + bars * 14400
    if state.remaining_frac > 0:
        _record_partial(state, partials, final_price, final_time, "time", state.remaining_frac)
    return _weighted_exit(state), final_time, "time", partials


def _record_partial(state: _ExitState, partials: list, price: float,
                    t: float, reason: str, frac: float):
    """Record a partial exit and update state."""
    actual_frac = min(frac, state.remaining_frac)
    if actual_frac <= 0:
        return
    state.weighted_exit += price * actual_frac
    state.weight_sum += actual_frac
    state.remaining_frac -= actual_frac
    partials.append({
        "price": round(price, 2),
        "fraction": round(actual_frac, 3),
        "reason": reason,
        "time": t,
    })


def _weighted_exit(state: _ExitState) -> float:
    """Calculate volume-weighted average exit price."""
    if state.weight_sum > 0:
        return state.weighted_exit / state.weight_sum
    return state.entry_price


# ── Candle Resampling ──

def _resample_to_daily(df_4h: pd.DataFrame) -> pd.DataFrame:
    """Resample 4H candles to daily."""
    if len(df_4h) < 6:
        return df_4h
    df = df_4h.copy()
    # Group by date (every 6 candles ≈ 1 day for 4H)
    groups = []
    for i in range(0, len(df), 6):
        chunk = df.iloc[i:i+6]
        if len(chunk) == 0:
            continue
        groups.append({
            "open": chunk["open"].iloc[0],
            "high": chunk["high"].max(),
            "low": chunk["low"].min(),
            "close": chunk["close"].iloc[-1],
            "volume": chunk["volume"].sum(),
        })
    return pd.DataFrame(groups)


def _load_candle_jsonl(path: Path) -> pd.DataFrame:
    """Load candles from JSONL file into a DataFrame."""
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df


# ── Simplified Conviction Scoring (no journal/brotherhood) ──

def _score_conviction(
    signal_confidence: float,
    regime: dict,
    direction: str,
    smc_pattern_count: int,
    risk_reward: float,
) -> tuple[float, float]:
    """Simplified conviction scoring for backtest.

    Returns (score 0-100, risk_multiplier 0-1).
    Uses 6 of the 10 components (no journal, brotherhood, sentiment, funding).
    """
    score = 0.0

    # 1. Regime alignment (15 pts)
    regime_bias = regime.get("direction_bias", "NONE")
    if regime_bias == direction:
        score += 15
    elif regime_bias == "NONE":
        score += 7.5

    # 2. SMC quality (15 pts)
    if smc_pattern_count >= 3:
        score += 15
    elif smc_pattern_count == 2:
        score += 11
    elif smc_pattern_count == 1:
        score += 7.5

    # 3. Multi-TF agreement (12 pts) — proxy from confidence
    score += signal_confidence * 12

    # 4. Macro support (10 pts) — from regime score
    regime_score = regime.get("score", 50)
    if regime_score >= 70:
        score += 10
    elif regime_score >= 50:
        score += 5
    else:
        score += 2

    # 5. Volume confirmation (8 pts) — from regime vol_zscore
    vol_z = regime.get("volume_zscore", 0)
    if vol_z > 2:
        score += 8
    elif vol_z > 1:
        score += 5
    else:
        score += 2.5

    # 6. R:R quality (5 pts)
    if risk_reward >= 3.0:
        score += 5
    elif risk_reward >= 2.0:
        score += 3
    elif risk_reward >= 1.5:
        score += 1.5

    # Neutral fill for missing components: journal(10), sentiment(8),
    # funding(10), brother(7) — all get 50% (neutral assumption)
    score += 0.5 * (10 + 8 + 10 + 7)  # +17.5

    # Risk multiplier tiers
    if score >= 70:
        mult = 1.0
    elif score >= 55:
        mult = 0.75
    elif score >= 40:
        mult = 0.50
    elif score >= 20:
        mult = 0.25
    else:
        mult = 0.0

    return round(score, 1), mult


# ── Main Backtester ──

def run_odin_backtest(
    symbol: str,
    candle_path: Path,
    risk_per_trade_usd: float = 15.0,
    min_trade_score: int = 40,
    min_confidence: float = 0.50,
    min_rr: float = 1.5,
    balance: float = 1000.0,
    step_size: int = 6,
    window_size: int = 200,
    max_trades: int = 500,
    progress_callback=None,
) -> OdinBacktestResult:
    """Run Odin strategy backtest on a single asset.

    Args:
        symbol: Asset symbol (e.g. "BTCUSDT")
        candle_path: Path to 4H JSONL candle file
        risk_per_trade_usd: $ risk per trade (Odin's current: $15)
        min_trade_score: Minimum conviction score to trade
        min_confidence: Minimum signal confidence
        min_rr: Minimum risk:reward ratio
        balance: Starting balance for sizing
        step_size: Bars to advance between analysis windows (6 = 1 day)
        window_size: Candle lookback for SMC analysis
        max_trades: Cap trades to prevent runaway
        progress_callback: Optional fn(pct) for dashboard

    Returns:
        OdinBacktestResult with all trades and stats.
    """
    _ensure_odin()
    from quant.odin_regime_proxy import classify_regime

    t0 = time.time()
    result = OdinBacktestResult(symbol=symbol, timeframe="4h")

    # Load candles
    df = _load_candle_jsonl(candle_path)
    if len(df) < window_size + 100:
        log.warning("[ODIN-BT] Not enough candles for %s: %d (need %d)",
                    symbol, len(df), window_size + 100)
        return result

    result.candles_used = len(df)
    log.info("[ODIN-BT] %s: %d candles loaded, window=%d, step=%d",
             symbol, len(df), window_size, step_size)

    # Initialize engines
    smc = _SMCEngine()
    mtf_analyzer = _MultiTimeframeAnalyzer(htf_label="1D", mtf_label="4H", ltf_label="15m")

    # Walk through candles
    open_position = None
    total_steps = (len(df) - window_size - 50) // step_size
    current_balance = balance

    for step_idx, i in enumerate(range(window_size, len(df) - 50, step_size)):
        # Progress
        if progress_callback and total_steps > 0:
            progress_callback(int(step_idx / total_steps * 100))

        # Skip if we already have a position (single position at a time)
        if open_position is not None:
            continue

        if len(result.trades) >= max_trades:
            break

        # Extract windows
        mtf_window = df.iloc[i - window_size:i]  # 4H (200 bars = ~33 days)
        htf_window = _resample_to_daily(mtf_window)  # ~33 daily bars

        # For LTF, we use the most recent 50 bars of 4H as a proxy
        # (no 15m data in this dataset)
        ltf_window = df.iloc[max(0, i - 50):i]

        current_price = float(df.iloc[i]["close"])
        current_time = float(df.iloc[i]["timestamp"])

        # 1. Classify regime from price data
        regime = classify_regime(mtf_window, lookback=50)

        # 2. Run multi-TF SMC analysis
        try:
            signal = mtf_analyzer.analyze(
                htf_df=htf_window,
                mtf_df=mtf_window,
                ltf_df=ltf_window,
                current_price=current_price,
            )
        except Exception as e:
            log.debug("[ODIN-BT] SMC error at bar %d: %s", i, str(e)[:80])
            continue

        result.signals_generated += 1

        # 3. Check signal quality
        if signal.direction == _Direction.NEUTRAL:
            result.signals_filtered += 1
            continue
        if signal.confidence < min_confidence:
            result.signals_filtered += 1
            continue
        if signal.risk_reward < min_rr:
            result.signals_filtered += 1
            continue

        direction = "LONG" if signal.direction == _Direction.BULLISH else "SHORT"

        # Count SMC patterns
        pattern_count = 0
        if signal.mtf and signal.mtf.structure:
            s = signal.mtf.structure
            pattern_count = len(s.active_obs) + len(s.active_fvgs) + len(s.liquidity_zones)

        # 4. Score conviction
        conv_score, risk_mult = _score_conviction(
            signal.confidence, regime, direction, pattern_count, signal.risk_reward,
        )

        if conv_score < min_trade_score:
            result.signals_filtered += 1
            continue

        # 5. Calculate position sizing
        risk = risk_per_trade_usd * risk_mult
        if risk < 1.0:
            result.signals_filtered += 1
            continue

        sl = signal.stop_loss
        if sl <= 0:
            result.signals_filtered += 1
            continue

        sl_dist = abs(current_price - sl)
        if sl_dist <= 0:
            result.signals_filtered += 1
            continue

        qty = risk / sl_dist

        # 6. Simulate entry + exit
        candles_after = df.iloc[i + 1:]
        exit_price, exit_time, exit_reason, partials = _simulate_exit(
            candles_after, direction, current_price, sl,
            signal.take_profit_1, current_time,
            max_bars=200,  # ~33 days max hold
        )

        # 7. Calculate PnL
        if direction == "LONG":
            pnl = (exit_price - current_price) * qty
        else:
            pnl = (current_price - exit_price) * qty

        r_mult = pnl / risk if risk > 0 else 0
        pnl_pct = pnl / current_balance * 100 if current_balance > 0 else 0
        hold_hours = (exit_time - current_time) / 3600

        trade = BacktestTrade(
            symbol=symbol,
            direction=direction,
            entry_price=round(current_price, 2),
            entry_time=current_time,
            exit_price=round(exit_price, 2),
            exit_time=exit_time,
            exit_reason=exit_reason,
            qty=round(qty, 6),
            risk_usd=round(risk, 2),
            pnl_usd=round(pnl, 2),
            pnl_pct=round(pnl_pct, 2),
            r_multiple=round(r_mult, 2),
            stop_loss=round(sl, 2),
            take_profit_1=round(signal.take_profit_1, 2),
            regime=regime.get("regime", "unknown"),
            conviction=conv_score,
            confidence=round(signal.confidence, 3),
            is_win=pnl > 0,
            hold_hours=round(hold_hours, 1),
            partial_exits=partials,
        )

        result.trades.append(trade)
        current_balance += pnl

        log.debug(
            "[ODIN-BT] Trade #%d: %s %s entry=$%.2f exit=$%.2f pnl=$%.2f (%.1fR) %s",
            len(result.trades), direction, symbol,
            current_price, exit_price, pnl, r_mult, exit_reason,
        )

    result.elapsed_seconds = time.time() - t0
    log.info(
        "[ODIN-BT] %s complete: %d trades (%d signals, %d filtered) in %.1fs",
        symbol, len(result.trades), result.signals_generated,
        result.signals_filtered, result.elapsed_seconds,
    )

    return result


def run_multi_asset_backtest(
    candle_dir: Path,
    symbols: list[str] | None = None,
    **kwargs,
) -> dict[str, OdinBacktestResult]:
    """Run backtest across multiple assets.

    Args:
        candle_dir: Directory containing {asset}.jsonl files
        symbols: List of assets to test (default: all .jsonl files)
        **kwargs: Passed to run_odin_backtest

    Returns:
        Dict of {symbol: OdinBacktestResult}
    """
    results = {}

    if symbols is None:
        symbols = [f.stem.upper() + "USDT" for f in candle_dir.glob("*.jsonl")]

    for symbol in symbols:
        # Map symbol to filename (BTCUSDT -> bitcoin.jsonl)
        asset_map = {
            "BTCUSDT": "bitcoin",
            "ETHUSDT": "ethereum",
            "SOLUSDT": "solana",
            "XRPUSDT": "xrp",
        }
        fname = asset_map.get(symbol, symbol.lower().replace("usdt", ""))
        path = candle_dir / f"{fname}.jsonl"

        if not path.exists():
            log.warning("[ODIN-BT] No candle data for %s at %s", symbol, path)
            continue

        results[symbol] = run_odin_backtest(symbol=symbol, candle_path=path, **kwargs)

    return results
