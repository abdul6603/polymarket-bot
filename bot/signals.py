from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from bot.config import Config
from bot.pattern_gate import get_pattern_gate
from bot.regime import RegimeAdjustment
from bot.weight_learner import get_dynamic_weights

# ── Shared Intelligence Layer (MLX routing for signal synthesis) ──
_USE_SHARED_LLM = False
_shared_llm_call = None
try:
    sys.path.insert(0, str(Path.home() / "shared"))
    from llm_client import llm_call as _llm_call
    _shared_llm_call = _llm_call
    _USE_SHARED_LLM = True
except ImportError:
    pass
from bot.indicators import (
    IndicatorVote,
    atr,
    bollinger_bands,
    dxy_trend_signal,
    ema_crossover,
    etf_flow_signal,
    fear_greed_index,
    funding_rate_signal,
    get_params,
    heikin_ashi,
    liquidation_cascade_signal,
    liquidity_signal,
    long_short_ratio_signal,
    macd,
    mempool_congestion_signal,
    momentum,
    open_interest_signal,
    order_flow_delta,
    price_divergence,
    rsi,
    spot_depth_signal,
    stablecoin_flow_signal,
    temporal_arb,
    tvl_momentum_signal,
    volume_spike,
    whale_flow_signal,
)
from bot.news_feed import get_news_feed
from bot.price_cache import PriceCache

log = logging.getLogger(__name__)

# Timeframe-specific probability clamps
# Wider clamps let strong consensus push probability further,
# preventing contrarian signals where edge_down > edge_up
# even though all indicators say UP.
PROB_CLAMP = {
    "5m": (0.30, 0.70),
    "15m": (0.25, 0.75),
    "1h": (0.20, 0.80),
    "4h": (0.15, 0.85),
    "weekly": (0.10, 0.90),
}

# ── Indicator Groups: prevent correlated indicators from dominating ──
# Trend indicators (MACD, EMA, momentum, heikin_ashi) all measure price trend.
# In extreme fear they ALL herd bearish, creating fake "strong consensus"
# from one signal counted 4 times. Cap each group's weight contribution.
INDICATOR_GROUPS = {
    "trend": {"macd", "ema", "heikin_ashi", "momentum"},
    "flow": {"order_flow", "spot_depth", "orderbook", "liquidity"},
    "external": {"open_interest", "long_short_ratio", "etf_flow", "whale_flow"},
    "price_action": {"temporal_arb", "price_div", "volume_spike"},
    "macro": {"news", "dxy_trend", "stablecoin_flow", "tvl_momentum", "mempool"},
    "derivatives": {"funding_rate", "liquidation"},
    "neural": {"lstm"},
}
MAX_GROUP_WEIGHT_FRACTION = 0.35  # No single group can contribute > 35% of total weight

# ── Consensus Clusters: de-duplicate correlated indicators ──
# These pairs agree 95-100% of the time. Counting both inflates consensus.
# When counting votes, only count ONE vote per cluster (highest confidence wins).
CONSENSUS_CLUSTERS = {
    "price_momentum": {"price_div", "temporal_arb"},  # 100% agreement on 74 trades
    "trend_slope": {"ema", "momentum"},               # 95% agreement
}

# ── Regime-Aware Indicator Scaling ──
# In extreme fear, trend-following indicators measure the fear itself, not predict the future.
# Demote them. In extreme greed, same logic — trend follows euphoria.
REGIME_INDICATOR_SCALE = {
    "extreme_fear": {
        "macd": 0.5, "ema": 0.5, "momentum": 0.5, "heikin_ashi": 0.5,
        "volume_spike": 1.2, "order_flow": 1.0, "liquidation": 1.2,
    },
    "fear": {
        "macd": 0.7, "ema": 0.7, "momentum": 0.7, "heikin_ashi": 0.7,
    },
    "extreme_greed": {
        "macd": 0.5, "ema": 0.5, "momentum": 0.5, "heikin_ashi": 0.5,
        "volume_spike": 1.2, "order_flow": 1.0,
    },
    "greed": {
        "macd": 0.7, "ema": 0.7, "momentum": 0.7, "heikin_ashi": 0.7,
    },
}

# Indicator weights for the ensemble
# Updated by Quant backtest (Feb 17, 127 trades, weight_v149):
#   Baseline 60.0% WR → Optimized 65.3% WR (+5.3pp)
#   Feb 17 tuning: order_flow 3.0→4.0 (every top-20 config uses 4.0, catches reversals via volume delta)
#                  momentum 1.0→1.2 (#1 config uses 1.2, 63.2% accuracy)
#   news demoted: only 3/11 correct in recent trades (27.3%)
#   ema demoted: 55.2% accuracy, marginal contributor
WEIGHTS = {
    # TOP TIER — proven edge (>70% accuracy)
    # Feb 20 Quant moderate tune: volume_spike 2.5→4.5, order_flow 2.5→4.5, momentum 1.2→2.0
    "volume_spike": 4.5,
    "liquidation": 2.0,
    # MID TIER — above coin flip (55-60%)
    "temporal_arb": 1.8,
    "price_div": 1.4,
    "macd": 1.1,
    "order_flow": 4.5,  # Quant moderate: 2.5→4.5. Volume+flow are strongest leading signals. Was 4.0→2.5→4.5.
    "news": 0.0,         # Disabled: 20% accuracy in 155-trade analysis (Feb 19). Was 0.4.
    # LOW TIER — marginal (50-55%)
    "momentum": 2.0,    # Quant moderate: 1.2→2.0. Strong confirming signal per backtest.
    "ema": 0.6,           # Quant: 1.1→0.6 (weight_v171)
    "heikin_ashi": 0.5,
    "spot_depth": 0.5,  # Re-enabled: 60.1% accuracy over 148 votes (longer window). Noise-filtered: only signals when depth > $500K.
    "orderbook": 0.5,
    "liquidity": 0.4,
    # DISABLED — below coin flip (harmful)
    "bollinger": 0.0,
    "rsi": 0.0,
    "funding_rate": 0.0,
    # EXTERNAL DATA — Phase 1 Multi-API indicators (start conservative, weight_learner adjusts)
    "open_interest": 0.8,     # Coinglass: cross-exchange OI trend
    "long_short_ratio": 0.6,  # Coinglass: contrarian L/S ratio
    "etf_flow": 0.7,          # Coinglass: BTC ETF institutional flows
    "dxy_trend": 0.5,         # FRED: USD index inverse correlation
    "stablecoin_flow": 0.5,   # DeFiLlama: stablecoin market cap changes
    "tvl_momentum": 0.4,      # DeFiLlama: DeFi TVL shifts
    "mempool": 0.5,           # Mempool.space: BTC network congestion
    "whale_flow": 0.7,        # Whale Alert: large exchange flows
    # NEURAL — PyTorch LSTM price direction predictor
    "lstm": 0.0,              # DISABLED — 52.5% accuracy = coin flip noise. Used as reversal sentinel instead.
}

# Timeframe-dependent weight scaling
# Short timeframes: order flow / arb matters more; long: TA matters more
TF_WEIGHT_SCALE = {
    "5m":  {"order_flow": 1.8, "orderbook": 2.0, "temporal_arb": 2.5, "price_div": 2.0,
            "rsi": 0.6, "macd": 0.6, "heikin_ashi": 0.5,
            "spot_depth": 1.5, "liquidation": 1.8, "funding_rate": 0.5,
            # External: slow data gets low weight on fast timeframes
            "open_interest": 0.4, "long_short_ratio": 0.3, "etf_flow": 0.3,
            "dxy_trend": 0.2, "stablecoin_flow": 0.2, "tvl_momentum": 0.2,
            "mempool": 0.5, "whale_flow": 0.5},
    "15m": {"order_flow": 1.5, "orderbook": 1.8, "temporal_arb": 2.0, "price_div": 1.5,
            "rsi": 0.8, "macd": 0.9,
            "spot_depth": 1.3, "liquidation": 1.5, "funding_rate": 0.8,
            "open_interest": 0.6, "long_short_ratio": 0.5, "etf_flow": 0.5,
            "dxy_trend": 0.3, "stablecoin_flow": 0.3, "tvl_momentum": 0.3,
            "mempool": 0.6, "whale_flow": 0.6},
    "1h":  {"order_flow": 1.0, "orderbook": 1.2, "rsi": 1.0, "macd": 1.1,
            "funding_rate": 1.2, "liquidation": 1.0,
            "open_interest": 1.0, "long_short_ratio": 0.8, "etf_flow": 0.8,
            "dxy_trend": 0.6, "stablecoin_flow": 0.6, "tvl_momentum": 0.6,
            "mempool": 0.8, "whale_flow": 1.0},
    "4h":  {"order_flow": 0.8, "orderbook": 0.8, "price_div": 0.7,
            "rsi": 1.2, "macd": 1.3, "heikin_ashi": 1.3,
            "funding_rate": 1.5, "liquidation": 0.8,
            "open_interest": 1.3, "long_short_ratio": 1.2, "etf_flow": 1.2,
            "dxy_trend": 1.0, "stablecoin_flow": 1.0, "tvl_momentum": 1.0,
            "mempool": 0.6, "whale_flow": 1.2},
    "weekly": {"rsi": 1.5, "macd": 1.5, "heikin_ashi": 1.5, "ema": 1.5,
               "momentum": 1.3, "bollinger": 1.2, "order_flow": 0.5,
               "orderbook": 0.5, "temporal_arb": 0.5, "price_div": 0.5,
               "funding_rate": 1.5, "liquidation": 0.5,
               "open_interest": 1.5, "long_short_ratio": 1.5, "etf_flow": 1.5,
               "dxy_trend": 1.3, "stablecoin_flow": 1.3, "tvl_momentum": 1.3,
               "mempool": 0.4, "whale_flow": 1.3},
}

MIN_CANDLES = 30
CONSENSUS_RATIO = 0.70  # Relaxed 0.78→0.70: R:R 1.2+ filter guards quality, let more trades through (7/10 instead of 8/10)
CONSENSUS_FLOOR = 3     # Raised 2→3: Quant suggested 7 (too aggressive), tried 4 but caused 100% unanimity requirement on low-indicator pairs (SOL/15m has only 4 active). 3 = meaningful filter without deadlock.
MIN_CONSENSUS = CONSENSUS_FLOOR  # backward compat for backtest/quant
MIN_ATR_THRESHOLD = 0.00005  # skip if volatility below this (0.005% of price)
MIN_CONFIDENCE = 0.20  # Lowered 0.60→0.20: Quant backtest shows 61.4% WR at 0.2. Consensus=7 and edge=8% are the real quality gates; 0.60 was filtering out winners.

# Directional bias: UP predictions have 47.3% WR vs DOWN 63% — require higher confidence for UP
UP_CONFIDENCE_PREMIUM = 0.0  # Removed 0.06→0.0: Top 16 backtest combos all have premium=0. No value when consensus is strict (7/10).

# NY market open manipulation window: 9:30-10:15 AM ET — high manipulation, skip
NY_OPEN_AVOID_START = (9, 30)   # 9:30 AM ET
NY_OPEN_AVOID_END = (10, 15)    # 10:15 AM ET

# Timeframe-specific minimum edge — must exceed estimated fees
# Data: 0-8% edge = 20% WR, 8-11% = 62.5% WR — 8% is the breakeven floor
MIN_EDGE_BY_TF = {
    "5m": 0.08,     # 8% — raised from 6% (below 8% = 20% WR)
    "15m": 0.08,    # 8% — raised from 9% regime-adjusted (0.7x was dropping to 6.3%)
    "1h": 0.99,     # DISABLED — 14% WR (2W/12L), broken resolution. Effectively unreachable edge.
    "4h": 0.99,     # DISABLED — 27.7% WR across 83 trades. Effectively unreachable edge.
    "weekly": 0.03, # 3% — lowest edge floor for longest timeframe
}

# Hard floor — regime adjustments cannot lower edge below this
MIN_EDGE_ABSOLUTE = 0.08  # 8% — Data: edge<8% has 6-27% WR (79 trades, only 18 wins). 8% is the breakeven floor.

# Reward-to-Risk ratio filter
# R:R = ((1-P) * 0.98) / P  where P = token price, 0.98 = payout after 2% winner fee
MIN_RR_RATIO = 1.0  # Lowered 1.2→1.0: old 1.2 required token≤$0.45, but most crypto markets price $0.47-0.50. Edge+confidence are the real guards.
MAX_TOKEN_PRICE = 0.50  # Never buy tokens above $0.50 — forces cheap side with R:R > 0.96

# Minimum confidence to count toward consensus vote
# Indicators below this confidence are basically guessing — don't let them inflate head count
# XRP loss analysis: 4/6 "DOWN" voters had <10% confidence, outvoted MACD at 81% UP
MIN_VOTE_CONFIDENCE = 0.15  # 15% — below this, vote doesn't count for consensus

# Reversal Sentinel — disabled indicators act as reversal canaries
# When 2+ disabled indicators disagree with majority, raise the bar
REVERSAL_SENTINEL_INDICATORS = {"rsi", "bollinger", "heikin_ashi", "funding_rate", "lstm"}
REVERSAL_SENTINEL_THRESHOLD = 2   # how many dissenters needed to trigger
REVERSAL_SENTINEL_PENALTY = 0.10  # +10% confidence floor when triggered

# Asset-specific edge premium — weaker assets need higher edge to trade
ASSET_EDGE_PREMIUM = {
    "bitcoin": 1.5,    # raised 1.0→1.5: 40% WR, -$221 PnL — must prove high edge to trade
    "ethereum": 1.3,   # raised 0.9→1.3: 54% WR but -$15 PnL — needs higher bar
    "solana": 2.0,     # raised 1.5→2.0: SOL/15m has 43% WR — requires 16% edge to trade
    "xrp": 0.9,        # slight discount: 61% WR, best performer — give it more room
}


@dataclass
class Signal:
    direction: str        # "up" or "down"
    edge: float           # expected edge as fraction (e.g. 0.05 = 5%)
    probability: float    # estimated probability of "up"
    token_id: str         # token to buy
    confidence: float
    timeframe: str        # "5m", "15m", "1h", "4h"
    asset: str            # "bitcoin", "ethereum", "solana"
    indicator_votes: dict | None = None  # indicator_name -> direction at signal time
    atr_value: float | None = None       # ATR as fraction of price (for conviction engine)
    reward_risk_ratio: float | None = None  # R:R = ((1-P)*0.98)/P


def _estimate_fees(timeframe: str, implied_price: float | None) -> float:
    """Estimate total Polymarket fees as fraction to subtract from edge.

    - 2% winner fee (always, on payout)
    - Up to 3% taker fee on ALL timeframes (peaks at 50/50 odds, scales with proximity)
      Shorter timeframes get slightly higher taker fees due to wider spreads.
    """
    winner_fee = 0.02

    ip = implied_price if implied_price is not None else 0.5
    distance = abs(ip - 0.5)
    # Base taker fee peaks at 3% for 50/50, drops to 0% at extreme prices
    base_taker = 0.03 * max(1.0 - distance * 2, 0)
    # Shorter timeframes have wider spreads -> slightly higher effective taker fee
    tf_multiplier = {"5m": 1.0, "15m": 1.0, "1h": 0.8, "4h": 0.6, "weekly": 0.4}.get(timeframe, 0.8)
    taker_fee = base_taker * tf_multiplier

    return winner_fee + taker_fee


class SignalEngine:
    """Ensemble signal engine with fee awareness, ATR filter, and timeframe-specific params."""

    def __init__(self, cfg: Config, price_cache: PriceCache):
        self.cfg = cfg
        self._cache = price_cache
        # Cross-timeframe signal cache: (asset, timeframe) -> (direction, timestamp)
        self._signal_history: dict[tuple[str, str], tuple[str, float]] = {}

    def generate_signal(
        self,
        up_token_id: str,
        down_token_id: str,
        asset: str = "bitcoin",
        timeframe: str = "5m",
        implied_up_price: float | None = None,
        orderbook: object | None = None,
        regime: RegimeAdjustment | None = None,
        derivatives_data: dict | None = None,
        spot_depth: dict | None = None,
        external_data: dict | None = None,
    ) -> Signal | None:
        """Generate a signal from the weighted ensemble of all indicators."""

        closes = self._cache.get_closes(asset, 200)
        if len(closes) < MIN_CANDLES:
            log.info(
                "Warming up [%s/%s] (%d/%d candles)",
                asset.upper(), timeframe, len(closes), MIN_CANDLES,
            )
            return None

        # Time-of-day blocks removed — let signal quality filter trades at all hours

        candles = self._cache.get_candles(asset, 200)
        binance_price = self._cache.get_price(asset)
        price_3m_ago = self._cache.get_price_ago(asset, 3)

        # ── ATR Volatility Filter ──
        atr_val = atr(candles)
        if atr_val is not None and atr_val < MIN_ATR_THRESHOLD:
            log.debug(
                "[%s/%s] ATR filter: %.6f < %.6f, skipping (flat market)",
                asset.upper(), timeframe, atr_val, MIN_ATR_THRESHOLD,
            )
            return None

        # ── Get timeframe-specific indicator params ──
        p = get_params(timeframe)

        # ── Technical Analysis Indicators (with timeframe-tuned params) ──
        votes: dict[str, IndicatorVote | None] = {
            "rsi": rsi(closes, period=p["rsi_period"]),
            "macd": macd(closes, fast=p["macd_fast"], slow=p["macd_slow"],
                         signal_period=p["macd_signal"]),
            "ema": ema_crossover(closes, fast=p["ema_fast"], slow=p["ema_slow"]),
            "heikin_ashi": heikin_ashi(candles),
            "bollinger": bollinger_bands(closes, period=p["bb_period"]),
            "momentum": momentum(closes, short_window=p["mom_short"],
                                 long_window=p["mom_long"]),
            # REMOVED: vwap — 46.8% accuracy (worse than random)
        }

        # ── Order Flow / Delta Metrics ──
        buy_vol, sell_vol = self._cache.get_order_flow(asset, window=30)
        votes["order_flow"] = order_flow_delta(buy_vol, sell_vol)

        # Polymarket orderbook imbalance
        if orderbook is not None:
            bp = getattr(orderbook, "buy_pressure", 0)
            sp = getattr(orderbook, "sell_pressure", 0)
            total = bp + sp
            if total > 0:
                imbalance = (bp - sp) / total
                ob_dir = "up" if imbalance > 0 else "down"
                ob_conf = min(abs(imbalance), 1.0)
                votes["orderbook"] = IndicatorVote(
                    direction=ob_dir, confidence=ob_conf, raw_value=imbalance * 100,
                )
            else:
                votes["orderbook"] = None

            # Polymarket Liquidity Signal
            spread = getattr(orderbook, "spread", 0)
            if bp > 0 or sp > 0:
                votes["liquidity"] = liquidity_signal(bp, sp, spread)
            else:
                votes["liquidity"] = None
        else:
            votes["orderbook"] = None
            votes["liquidity"] = None

        # ── Price Divergence: Binance momentum vs Polymarket (FIXED) ──
        if binance_price and price_3m_ago:
            votes["price_div"] = price_divergence(
                binance_price, price_3m_ago, implied_up_price,
            )
        else:
            votes["price_div"] = None

        # ── Temporal Arbitrage (Gabagool strategy) ──
        if binance_price and price_3m_ago:
            votes["temporal_arb"] = temporal_arb(
                binance_price, price_3m_ago, implied_up_price, timeframe,
            )
        else:
            votes["temporal_arb"] = None

        # ── Volume Spike Detection ──
        votes["volume_spike"] = volume_spike(candles)

        # ── Crypto News Sentiment (24/7 RSS feed) ──
        try:
            news = get_news_feed().get_sentiment(asset)
            if news is not None and news.headline_count >= 2:
                news_dir = "up" if news.sentiment > 0 else "down"
                news_conf = min(abs(news.sentiment), 1.0)
                votes["news"] = IndicatorVote(
                    direction=news_dir,
                    confidence=news_conf,
                    raw_value=news.sentiment * 100,
                )
                log.debug("[%s/%s] News: %s (%.0f%%, %d headlines) — %s",
                          asset.upper(), timeframe, news_dir.upper(),
                          news_conf * 100, news.headline_count, news.top_headline[:60])
        except Exception:
            votes["news"] = None

        # REMOVED: sentiment (Fear & Greed) — 47.6% accuracy (worse than random)
        # FnG is still used for regime detection, just not as a voting indicator

        # ── Derivatives Intelligence (Binance Futures: funding rates + liquidations) ──
        if derivatives_data:
            # Funding rate signal
            fr = derivatives_data.get("funding_rates", {}).get(asset, {})
            if fr:
                votes["funding_rate"] = funding_rate_signal(fr.get("rate", 0))
            else:
                votes["funding_rate"] = None

            # Liquidation cascade signal
            liq = derivatives_data.get("liquidations", {}).get(asset, {})
            if liq:
                votes["liquidation"] = liquidation_cascade_signal(
                    long_liq_usd=liq.get("long_liq_usd_5m", 0),
                    short_liq_usd=liq.get("short_liq_usd_5m", 0),
                    cascade_detected=liq.get("cascade_detected", False),
                    cascade_direction=liq.get("cascade_direction", ""),
                )
            else:
                votes["liquidation"] = None
        else:
            votes["funding_rate"] = None
            votes["liquidation"] = None

        # ── Binance Spot Order Book Depth (noise-filtered: $500K+ total depth only) ──
        if spot_depth:
            _bids = spot_depth.get("bids", [])
            _asks = spot_depth.get("asks", [])
            # Only signal when orderbook is deep enough to be meaningful
            try:
                _total_depth = (
                    sum(float(b[0]) * float(b[1]) for b in _bids)
                    + sum(float(a[0]) * float(a[1]) for a in _asks)
                )
            except (ValueError, TypeError, IndexError):
                _total_depth = 0
            if _total_depth >= 500_000:  # $500K minimum depth
                votes["spot_depth"] = spot_depth_signal(bids=_bids, asks=_asks)
            else:
                votes["spot_depth"] = None
        else:
            votes["spot_depth"] = None

        # ── External Data Indicators (Phase 1 — Multi-API Integration) ──
        ext = external_data or {}

        # Coinglass: OI, Long/Short, ETF flow
        cg = ext.get("coinglass")
        if cg:
            try:
                votes["open_interest"] = open_interest_signal(
                    cg.oi_change_1h_pct, cg.oi_change_4h_pct,
                )
            except Exception:
                votes["open_interest"] = None
            try:
                votes["long_short_ratio"] = long_short_ratio_signal(cg.long_short_ratio)
            except Exception:
                votes["long_short_ratio"] = None
            try:
                if cg.etf_available and asset == "bitcoin":
                    votes["etf_flow"] = etf_flow_signal(cg.etf_net_flow_usd)
                else:
                    votes["etf_flow"] = None
            except Exception:
                votes["etf_flow"] = None
        else:
            votes["open_interest"] = None
            votes["long_short_ratio"] = None
            votes["etf_flow"] = None

        # FRED Macro: DXY trend
        macro = ext.get("macro")
        if macro and macro.dxy_trend:
            try:
                votes["dxy_trend"] = dxy_trend_signal(macro.dxy_trend, macro.dxy_change_pct)
            except Exception:
                votes["dxy_trend"] = None
        else:
            votes["dxy_trend"] = None

        # DeFiLlama: stablecoin flow + TVL momentum
        defi = ext.get("defi")
        if defi:
            try:
                votes["stablecoin_flow"] = stablecoin_flow_signal(
                    defi.stablecoin_change_7d_usd, defi.stablecoin_change_7d_pct,
                )
            except Exception:
                votes["stablecoin_flow"] = None
            try:
                votes["tvl_momentum"] = tvl_momentum_signal(
                    defi.tvl_change_24h_pct, defi.tvl_change_7d_pct,
                )
            except Exception:
                votes["tvl_momentum"] = None
        else:
            votes["stablecoin_flow"] = None
            votes["tvl_momentum"] = None

        # Mempool.space: BTC congestion (BTC only)
        mempool_data = ext.get("mempool")
        if mempool_data and asset == "bitcoin":
            try:
                votes["mempool"] = mempool_congestion_signal(
                    mempool_data.fee_ratio_vs_baseline,
                    mempool_data.tx_count,
                    mempool_data.congestion_level,
                )
            except Exception:
                votes["mempool"] = None
        else:
            votes["mempool"] = None

        # Whale Alert: exchange flow
        whale = ext.get("whale")
        if whale:
            try:
                votes["whale_flow"] = whale_flow_signal(
                    whale.deposits_usd, whale.withdrawals_usd, whale.tx_count,
                )
            except Exception:
                votes["whale_flow"] = None
        else:
            votes["whale_flow"] = None

        # ── LSTM Neural Predictor ──
        try:
            from bot.lstm_predictor import predict_direction
            lstm_result = predict_direction(asset, candles)
            if lstm_result and lstm_result["confidence"] >= 0.55:
                votes["lstm"] = IndicatorVote(
                    direction=lstm_result["direction"],
                    confidence=lstm_result["confidence"],
                    raw_value=lstm_result["raw_prob"] * 100,
                )
            else:
                votes["lstm"] = None
        except Exception:
            votes["lstm"] = None

        # Filter to non-None votes
        active: dict[str, IndicatorVote] = {
            k: v for k, v in votes.items() if v is not None
        }

        if len(active) < 3:
            log.info("[%s/%s] Too few indicators fired (%d)", asset.upper(), timeframe, len(active))
            return None

        # ── Weighted Ensemble Score (with timeframe + regime + group cap scaling) ──
        dynamic_weights = get_dynamic_weights(WEIGHTS)
        tf_scale = TF_WEIGHT_SCALE.get(timeframe, {})
        regime_scale = REGIME_INDICATOR_SCALE.get(regime.label, {}) if regime else {}

        # Pass 1: compute raw weights for each indicator
        indicator_weights: dict[str, float] = {}
        disabled = []
        for name, vote in active.items():
            base_w = dynamic_weights.get(name, 1.0)
            if base_w <= 0:
                disabled.append(name)
                continue
            w = base_w * tf_scale.get(name, 1.0) * regime_scale.get(name, 1.0)
            indicator_weights[name] = w

        # Pass 2: apply indicator group caps — no single group > 35% of total
        total_raw = sum(indicator_weights.values())
        if total_raw > 0:
            # Find which group each indicator belongs to
            ind_to_group = {}
            for grp, members in INDICATOR_GROUPS.items():
                for m in members:
                    ind_to_group[m] = grp

            # Compute group totals
            group_totals: dict[str, float] = {}
            for name, w in indicator_weights.items():
                grp = ind_to_group.get(name, "other")
                group_totals[grp] = group_totals.get(grp, 0) + w

            # Cap groups that exceed MAX_GROUP_WEIGHT_FRACTION
            for grp, grp_total in group_totals.items():
                fraction = grp_total / total_raw
                if fraction > MAX_GROUP_WEIGHT_FRACTION:
                    scale_down = (MAX_GROUP_WEIGHT_FRACTION * total_raw) / grp_total
                    for name in indicator_weights:
                        if ind_to_group.get(name, "other") == grp:
                            indicator_weights[name] *= scale_down
                    log.info("[%s/%s] Group cap: '%s' was %.0f%% of total, scaled to %.0f%%",
                             asset.upper(), timeframe, grp, fraction * 100, MAX_GROUP_WEIGHT_FRACTION * 100)

        # Pass 3: compute weighted ensemble score
        weighted_sum = 0.0
        weight_total = 0.0
        up_count = 0
        down_count = 0

        # Build reverse map: indicator -> cluster name (if any)
        ind_to_cluster = {}
        for cluster_name, members in CONSENSUS_CLUSTERS.items():
            for m in members:
                ind_to_cluster[m] = cluster_name

        # Track which clusters already voted (for consensus de-duplication)
        cluster_voted: dict[str, tuple[str, float]] = {}  # cluster -> (direction, confidence)

        for name, vote in active.items():
            if name in disabled or name not in indicator_weights:
                continue
            w = indicator_weights[name]
            sign = 1.0 if vote.direction == "up" else -1.0
            weighted_sum += w * vote.confidence * sign
            weight_total += w

            # De-duplicated consensus: if this indicator belongs to a cluster,
            # only count the FIRST vote (highest confidence wins since we process all)
            if vote.confidence >= MIN_VOTE_CONFIDENCE:
                cluster = ind_to_cluster.get(name)
                if cluster:
                    if cluster not in cluster_voted:
                        cluster_voted[cluster] = (vote.direction, vote.confidence)
                        if vote.direction == "up":
                            up_count += 1
                        else:
                            down_count += 1
                    elif vote.confidence > cluster_voted[cluster][1]:
                        # Higher confidence — replace the cluster's vote
                        old_dir = cluster_voted[cluster][0]
                        if old_dir == "up":
                            up_count -= 1
                        else:
                            down_count -= 1
                        cluster_voted[cluster] = (vote.direction, vote.confidence)
                        if vote.direction == "up":
                            up_count += 1
                        else:
                            down_count += 1
                    # else: lower confidence, skip (cluster already voted)
                else:
                    # Not in any cluster — count normally
                    if vote.direction == "up":
                        up_count += 1
                    else:
                        down_count += 1

        if disabled:
            log.info("[%s/%s] Disabled anti-signals (accuracy <40%%): %s",
                     asset.upper(), timeframe, ", ".join(disabled))

        if weight_total == 0:
            return None

        score = weighted_sum / weight_total  # -1 to +1

        # ── Regime Directional Bias ──
        # Extreme fear = oversold = bias toward UP (bounces likely)
        # Extreme greed = overbought = bias toward DOWN (corrections likely)
        # This treats regime as a first-class directional signal, not just a threshold modifier.
        if regime:
            regime_bias = {"extreme_fear": 0.08, "fear": 0.03,
                           "neutral": 0.0,
                           "greed": -0.03, "extreme_greed": -0.08}.get(regime.label, 0.0)
            if regime_bias != 0.0:
                score += regime_bias
                score = max(-1.0, min(1.0, score))  # keep in bounds
                log.info("[%s/%s] Regime bias: %+.2f (%s FnG=%d)",
                         asset.upper(), timeframe, regime_bias, regime.label, regime.fng_value)

        # ── LLM Signal Synthesis (close calls only — margin < 3 votes) ──
        _llm_adj = 0.0
        vote_margin = abs(up_count - down_count)
        active_count = up_count + down_count
        if _USE_SHARED_LLM and _shared_llm_call and vote_margin < 3 and active_count >= 5:
            try:
                _t0 = time.time()
                _vote_summary = ", ".join(
                    f"{n}={v.direction}({v.confidence:.2f})" for n, v in active.items()
                    if n not in disabled
                )[:500]
                _regime_str = f"{regime.label} (FnG={regime.fng_value})" if regime else "unknown"
                _result = _shared_llm_call(
                    system=(
                        "You analyze conflicting trading indicator signals. "
                        "Reply with ONLY a number from -0.10 to +0.10 representing your score adjustment. "
                        "Positive = lean UP, negative = lean DOWN, 0 = no opinion."
                    ),
                    user=(
                        f"Asset: {asset.upper()}/{timeframe}, Regime: {_regime_str}, Score: {score:.3f}\n"
                        f"Votes UP: {up_count}, DOWN: {down_count} (margin: {vote_margin})\n"
                        f"Indicators: {_vote_summary}"
                    ),
                    agent="garves",
                    task_type="analysis",
                    max_tokens=15,
                    temperature=0.1,
                )
                _elapsed = time.time() - _t0
                if _result and _elapsed < 3.0:  # Guard: skip if too slow
                    try:
                        _llm_adj = float(_result.strip())
                        _llm_adj = max(-0.10, min(0.10, _llm_adj))
                        if _llm_adj != 0.0:
                            score += _llm_adj
                            score = max(-1.0, min(1.0, score))
                            log.info("[%s/%s] LLM synthesis: %+.3f adjustment (margin=%d, %.1fs)",
                                     asset.upper(), timeframe, _llm_adj, vote_margin, _elapsed)
                    except (ValueError, TypeError):
                        pass
                elif _elapsed >= 3.0:
                    log.debug("[%s/%s] LLM synthesis skipped: too slow (%.1fs)", asset.upper(), timeframe, _elapsed)
            except Exception:
                pass  # LLM failure never blocks trading

        # ── Consensus Filter (proportional) ──
        majority_dir = "up" if up_count >= down_count else "down"
        agree_count = max(up_count, down_count)
        active_count = up_count + down_count  # non-disabled indicators only
        total_indicators = len(active)

        # Proportional consensus: 70% of active indicators must agree, floor of 3
        effective_consensus = max(CONSENSUS_FLOOR, int(active_count * CONSENSUS_RATIO))
        effective_consensus = min(effective_consensus, active_count)  # can't require more than available
        if regime and regime.consensus_offset:
            effective_consensus += regime.consensus_offset

        if agree_count < effective_consensus:
            log.info(
                "[%s/%s] Consensus filter: %d/%d agree on %s (need %d of %d active%s), skipping",
                asset.upper(), timeframe, agree_count, active_count,
                majority_dir, effective_consensus, active_count,
                f" regime={regime.label}" if regime else "",
            )
            return None

        # ── Reversal Sentinel — disabled indicators as reversal canaries ──
        # RSI, Bollinger, Heikin Ashi, funding_rate are computed but disabled (weight=0).
        # When 2+ of them disagree with majority, it signals a potential reversal.
        reversal_penalty = 0.0
        sentinel_dissenters = []
        for ind_name in REVERSAL_SENTINEL_INDICATORS:
            vote = votes.get(ind_name)
            if vote is not None and vote.direction != majority_dir:
                sentinel_dissenters.append(ind_name)

        if len(sentinel_dissenters) >= REVERSAL_SENTINEL_THRESHOLD:
            # Raise consensus requirement by +1
            sentinel_consensus = effective_consensus + 1
            if agree_count < sentinel_consensus:
                log.info(
                    "[%s/%s] REVERSAL SENTINEL: %d disabled indicators (%s) disagree with %s, "
                    "raised consensus to %d but only %d agree — BLOCKED",
                    asset.upper(), timeframe, len(sentinel_dissenters),
                    "+".join(sentinel_dissenters), majority_dir.upper(),
                    sentinel_consensus, agree_count,
                )
                return None
            reversal_penalty = REVERSAL_SENTINEL_PENALTY
            log.info(
                "[%s/%s] REVERSAL SENTINEL: %d disabled indicators (%s) disagree with %s — "
                "confidence penalty +%.0f%% applied (passed consensus %d/%d)",
                asset.upper(), timeframe, len(sentinel_dissenters),
                "+".join(sentinel_dissenters), majority_dir.upper(),
                reversal_penalty * 100, agree_count, sentinel_consensus,
            )

        # ── Trend Filter — anti-trend signals need stronger consensus ──
        if len(closes) >= 50:
            short_trend = sum(closes[-10:]) / 10
            long_trend = sum(closes[-50:]) / 50
            trend_dir = "up" if short_trend > long_trend else "down"

            if majority_dir != trend_dir:
                # Going against the trend — require 80% of active indicators to agree
                anti_trend_min = max(effective_consensus + 1, int(active_count * 0.80))
                if agree_count < anti_trend_min:
                    log.info(
                        "[%s/%s] Anti-trend filter: signal=%s but trend=%s, need %d/%d (have %d)",
                        asset.upper(), timeframe, majority_dir.upper(), trend_dir.upper(),
                        anti_trend_min, total_indicators, agree_count,
                    )
                    return None

        # ── Map Score → Probability (blended with market) ──
        lo, hi = PROB_CLAMP.get(timeframe, (0.30, 0.70))
        model_prob = 0.5 + score * 0.25

        # Bayesian blend: market gets more weight when it has strong conviction.
        # This prevents phantom contrarian edges — the old code generated fake 45%
        # edges when our model (capped at 0.75) disagreed with market at 0.82.
        if implied_up_price is not None and 0.05 < implied_up_price < 0.95:
            market_conviction = abs(implied_up_price - 0.5)
            market_weight = 0.3 + market_conviction  # 0.30 at 50/50, 0.80 at extremes
            market_weight = min(market_weight, 0.80)  # cap market influence
            model_weight = 1.0 - market_weight
            raw_prob = model_weight * model_prob + market_weight * implied_up_price
        else:
            raw_prob = model_prob

        prob_up = max(lo, min(hi, raw_prob))

        confidence = min(abs(score), 1.0)

        # NOTE: Confidence check moved below edge calculation — the second check
        # handles both directions (UP gets premium) so the first was redundant.

        # ── Edge Calculation ──
        if implied_up_price is not None and 0.01 < implied_up_price < 0.99:
            edge_up = prob_up - implied_up_price
            edge_down = (1 - prob_up) - (1 - implied_up_price)
        else:
            # No orderbook price — can't calculate real edge, skip this market
            log.info("[%s/%s] No implied price — skipping (can't calculate edge)", asset.upper(), timeframe)
            return None

        # ── Subtract Fees from Edge ──
        fees = _estimate_fees(timeframe, implied_up_price)
        edge_up -= fees
        edge_down -= fees

        # ── Log: TA Prediction + All Indicator Breakdown ──
        pred_dir = "UP" if prob_up >= 0.5 else "DOWN"
        pred_pct = prob_up * 100 if prob_up >= 0.5 else (1 - prob_up) * 100

        vote_strs = []
        for k, v in active.items():
            vote_strs.append(f"{k}={v.direction[0].upper()}({v.confidence:.0%}|{v.raw_value:+.1f})")

        log.info(
            "[%s/%s] TA Prediction: %s %.1f%% | score=%.3f | fees=%.1f%% | ATR=%.4f%% | %s",
            asset.upper(), timeframe, pred_dir, pred_pct, score,
            fees * 100, (atr_val or 0) * 100,
            " ".join(vote_strs),
        )
        log.info(
            "[%s/%s] Consensus: %d/%d agree (%d active) | Binance: $%s | Poly implied: %s | edge_up=%.3f edge_down=%.3f (after fees)",
            asset.upper(), timeframe, agree_count, active_count, total_indicators,
            f"{binance_price:,.2f}" if binance_price else "N/A",
            f"{implied_up_price:.3f}" if implied_up_price else "N/A",
            edge_up, edge_down,
        )

        # ── Consensus-Driven Direction ──
        # Use indicator consensus as primary direction, not edge math.
        # Edge math with implied=0.5 default creates contrarian signals
        # that bet AGAINST the consensus when market odds are extreme.
        consensus_dir = majority_dir  # "up" or "down"
        consensus_edge = edge_up if consensus_dir == "up" else edge_down
        consensus_prob = prob_up if consensus_dir == "up" else (1 - prob_up)
        consensus_token = up_token_id if consensus_dir == "up" else down_token_id

        # ── Safety: graduated filter for contrarian signals ──
        # When betting against the market, require progressively stronger consensus.
        # Old filter: only triggered at 15% lean, easily passed by correlated indicators.
        # New: graduated from 5% lean, with escalating consensus requirements.
        if implied_up_price is not None and 0.01 < implied_up_price < 0.99:
            market_dir = "up" if implied_up_price > 0.5 else "down"
            market_strength = abs(implied_up_price - 0.5)

            if consensus_dir != market_dir and market_strength > 0.05:
                # Graduated: 5-10% lean = +1, 10-20% = +2, 20%+ = +3
                extra_needed = 1 + int(market_strength * 10)
                contrarian_min = effective_consensus + extra_needed
                contrarian_min = min(contrarian_min, active_count)  # can't require more than available
                if agree_count < contrarian_min:
                    log.info(
                        "[%s/%s] Market safety filter: consensus=%s but market=%s (%.0f%%), "
                        "need %d/%d agree (have %d), lean=%.1f%% extra_needed=%d, skipping",
                        asset.upper(), timeframe, consensus_dir.upper(),
                        market_dir.upper(), implied_up_price * 100,
                        contrarian_min, active_count, agree_count,
                        market_strength * 100, extra_needed,
                    )
                    return None

        # ── Historical Pattern Gate ──
        # Block combos with proven losing track records
        _gate = get_pattern_gate()
        _gate_decision = _gate.evaluate(asset, timeframe, consensus_dir)
        if not _gate_decision.allowed:
            log.info(
                "[%s/%s] PATTERN GATE BLOCKED: %s (%.0f%% WR over %d trades)",
                asset.upper(), timeframe, _gate_decision.reason,
                _gate_decision.win_rate * 100, _gate_decision.sample_size,
            )
            return None

        # ── Timeframe-Specific Minimum Edge (regime-adjusted + asset premium) ──
        asset_premium = ASSET_EDGE_PREMIUM.get(asset, 1.0)
        min_edge = MIN_EDGE_BY_TF.get(timeframe, 0.05) * (regime.edge_multiplier if regime else 1.0) * asset_premium
        # Hard floor — regime cannot lower edge below absolute minimum
        min_edge = max(min_edge, MIN_EDGE_ABSOLUTE)
        # Pattern gate edge adjustment: raise bar for losing combos, lower for winning ones
        if _gate_decision.edge_adjustment > 0:
            min_edge = max(min_edge, _gate_decision.edge_adjustment)
            log.info("[%s/%s] Pattern gate raised edge to %.0f%% (%.0f%% WR over %d trades)",
                     asset.upper(), timeframe, min_edge * 100,
                     _gate_decision.win_rate * 100, _gate_decision.sample_size)
        elif _gate_decision.edge_adjustment < 0:
            min_edge = max(0.05, min_edge + _gate_decision.edge_adjustment)  # Never below 5%
        if consensus_edge < min_edge:
            log.info(
                "[%s/%s] Edge too low: %.3f < %.3f (asset_premium=%.1fx)",
                asset.upper(), timeframe, consensus_edge, min_edge, asset_premium,
            )
            return None

        # ── Confidence Floor: MAX of regime floor and MIN_CONFIDENCE (never let regime lower it) ──
        regime_floor = regime.confidence_floor if regime else MIN_CONFIDENCE
        effective_conf_floor = max(regime_floor, MIN_CONFIDENCE)
        if consensus_dir == "up":
            up_premium = UP_CONFIDENCE_PREMIUM
            # Halve UP penalty in fear — contrarian buying IS the strategy in fear regimes
            if regime and regime.label in ("extreme_fear", "fear"):
                up_premium *= 0.5
            effective_conf_floor += up_premium
        # ETH confidence premium: weakest asset (72% WR vs BTC 79%, XRP 100%)
        if asset == "ethereum":
            eth_penalty = 0.03
            if regime and regime.label in ("extreme_fear", "fear"):
                eth_penalty *= 0.5  # Halve ETH penalty in fear — contrarian buying shouldn't be penalized per-asset
            effective_conf_floor += eth_penalty
        # Reversal sentinel penalty: raise floor when disabled indicators signal reversal
        effective_conf_floor += reversal_penalty
        if confidence < effective_conf_floor:
            log.info(
                "[%s/%s] Confidence too low for %s: %.3f < %.3f (UP premium applied: %s)",
                asset.upper(), timeframe, consensus_dir.upper(),
                confidence, effective_conf_floor, consensus_dir == "up",
            )
            return None

        # Build indicator vote snapshot for weight learning
        ind_votes = {name: vote.direction for name, vote in active.items()}

        if consensus_edge > 0:
            import time as _time
            now_ts = _time.time()

            # ── Reward-to-Risk Ratio Filter ──
            # Buy token at price P: Win = (1-P)*0.98, Lose = P
            # R:R = ((1-P)*0.98) / P
            rr_ratio = None
            token_price = implied_up_price if consensus_dir == "up" else (1 - implied_up_price if implied_up_price is not None else None)
            if token_price is not None and 0.01 < token_price < 0.99:
                # ── Max Token Price Cap ──
                # Never buy tokens above $0.50 — guarantees R:R > 0.96
                # Forces us to bet the cheap (underdog) side with favorable payouts
                if token_price > MAX_TOKEN_PRICE:
                    log.info(
                        "[%s/%s] Token price cap: %.3f > %.2f, skipping (only bet cheap side)",
                        asset.upper(), timeframe, token_price, MAX_TOKEN_PRICE,
                    )
                    return None

                rr_ratio = ((1 - token_price) * 0.98) / token_price
                if rr_ratio < MIN_RR_RATIO:
                    log.info(
                        "[%s/%s] R:R filter: %.2f < %.2f (token_price=%.3f), skipping",
                        asset.upper(), timeframe, rr_ratio, MIN_RR_RATIO, token_price,
                    )
                    return None

            # Cache this signal direction for cross-TF lookups
            self._signal_history[(asset, timeframe)] = (consensus_dir, now_ts)

            return Signal(
                direction=consensus_dir,
                edge=consensus_edge,
                probability=consensus_prob,
                token_id=consensus_token,
                confidence=confidence,
                timeframe=timeframe,
                asset=asset,
                indicator_votes=ind_votes,
                atr_value=atr_val,
                reward_risk_ratio=rr_ratio,
            )

        log.info(
            "[%s/%s] No edge after fees (prob_up=%.3f, implied=%.3f, fees=%.3f)",
            asset.upper(), timeframe, prob_up, implied_up_price or 0.5, fees,
        )
        return None
