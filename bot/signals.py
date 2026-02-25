from __future__ import annotations

import logging
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from bot.config import Config
from bot.pattern_gate import get_pattern_gate
from bot.regime import RegimeAdjustment
from bot.weight_learner import get_dynamic_weights
from bot.param_loader import get_live_params

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
    liquidation_heatmap_signal,
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
from bot.poly_flow import get_flow_tracker
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
    "flow": {"order_flow", "spot_depth", "orderbook", "liquidity", "poly_flow"},
    "external": {"open_interest", "long_short_ratio", "etf_flow", "whale_flow"},
    "price_action": {"temporal_arb", "price_div", "volume_spike"},
    "macro": {"news", "dxy_trend", "stablecoin_flow", "tvl_momentum", "mempool", "tavily_news", "atlas_market_intel"},
    "derivatives": {"funding_rate", "liquidation", "liq_heatmap"},
    "neural": {"lstm"},
}
MAX_GROUP_WEIGHT_FRACTION = 0.35  # No single group can contribute > 35% of total weight

# ── Consensus Clusters: de-duplicate correlated indicators ──
# These pairs agree 95-100% of the time. Counting both inflates consensus.
# When counting votes, only count ONE vote per cluster (highest confidence wins).
CONSENSUS_CLUSTERS = {
    "price_momentum": {"price_div", "temporal_arb", "heikin_ashi"},  # 100% agreement on 74 trades + heikin_ashi correlated
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
    "liq_heatmap": 1.2,   # Coinglass: price-level liquidation cluster proximity
    # MID TIER — above coin flip (55-60%)
    "temporal_arb": 1.8,
    "price_div": 1.4,
    "macd": 1.1,
    "order_flow": 2.0,  # Reduced 4.5→2.0: was dominating consensus. Flow is noisy in low-vol regimes.
    "news": 0.5,         # Re-enabled: LLM sentiment (Qwen 3B) replaces keyword matching. Expected 65-70% accuracy.
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
    # POLYMARKET FLOW — proprietary orderbook change tracking
    "poly_flow": 1.0,         # Polymarket book flow: depth velocity, spread compression, whale detection
    # NEURAL — PyTorch LSTM price direction predictor
    "lstm": 0.0,              # DISABLED — 52.5% accuracy = coin flip noise. Used as reversal sentinel instead.
    # TAVILY NEWS — Atlas Tavily crypto news sentiment (90-min cycles)
    "tavily_news": 0.7,       # Atlas-sourced Tavily sentiment. Macro-level, slow-moving signal.
    # ATLAS MARKET INTEL — Atlas market_intel.json keyword sentiment (90-min cycles)
    "atlas_market_intel": 1.0,  # Atlas DDG/Tavily news keyword analysis. Complementary to tavily_news.
}

# Timeframe-dependent weight scaling
# Short timeframes: order flow / arb matters more; long: TA matters more
TF_WEIGHT_SCALE = {
    "5m":  {"order_flow": 1.8, "orderbook": 2.0, "temporal_arb": 2.5, "price_div": 2.0,
            "rsi": 0.6, "macd": 0.6, "heikin_ashi": 0.5,
            "spot_depth": 1.5, "liquidation": 1.8, "liq_heatmap": 1.5, "funding_rate": 0.5,
            # External: slow data gets low weight on fast timeframes
            "open_interest": 0.4, "long_short_ratio": 0.3, "etf_flow": 0.3,
            "dxy_trend": 0.2, "stablecoin_flow": 0.2, "tvl_momentum": 0.2,
            "mempool": 0.5, "whale_flow": 0.5, "tavily_news": 0.3, "atlas_market_intel": 0.3},
    "15m": {"order_flow": 1.5, "orderbook": 1.8, "temporal_arb": 2.0, "price_div": 1.5,
            "rsi": 0.8, "macd": 0.9,
            "spot_depth": 1.3, "liquidation": 1.5, "liq_heatmap": 1.3, "funding_rate": 0.8,
            "open_interest": 0.6, "long_short_ratio": 0.5, "etf_flow": 0.5,
            "dxy_trend": 0.3, "stablecoin_flow": 0.3, "tvl_momentum": 0.3,
            "mempool": 0.6, "whale_flow": 0.6, "tavily_news": 0.5, "atlas_market_intel": 0.5},
    "1h":  {"order_flow": 1.0, "orderbook": 1.2, "rsi": 1.0, "macd": 1.1,
            "funding_rate": 1.2, "liquidation": 1.0, "liq_heatmap": 1.0,
            "open_interest": 1.0, "long_short_ratio": 0.8, "etf_flow": 0.8,
            "dxy_trend": 0.6, "stablecoin_flow": 0.6, "tvl_momentum": 0.6,
            "mempool": 0.8, "whale_flow": 1.0, "tavily_news": 0.9, "atlas_market_intel": 0.9},
    "4h":  {"order_flow": 0.8, "orderbook": 0.8, "price_div": 0.7,
            "rsi": 1.2, "macd": 1.3, "heikin_ashi": 1.3,
            "funding_rate": 1.5, "liquidation": 0.8, "liq_heatmap": 0.7,
            "open_interest": 1.3, "long_short_ratio": 1.2, "etf_flow": 1.2,
            "dxy_trend": 1.0, "stablecoin_flow": 1.0, "tvl_momentum": 1.0,
            "mempool": 0.6, "whale_flow": 1.2, "tavily_news": 1.0, "atlas_market_intel": 1.0},
    "weekly": {"rsi": 1.5, "macd": 1.5, "heikin_ashi": 1.5, "ema": 1.5,
               "momentum": 1.3, "bollinger": 1.2, "order_flow": 0.5,
               "orderbook": 0.5, "temporal_arb": 0.5, "price_div": 0.5,
               "funding_rate": 1.5, "liquidation": 0.5, "liq_heatmap": 0.4,
               "open_interest": 1.5, "long_short_ratio": 1.5, "etf_flow": 1.5,
               "dxy_trend": 1.3, "stablecoin_flow": 1.3, "tvl_momentum": 1.3,
               "mempool": 0.4, "whale_flow": 1.3, "tavily_news": 1.0, "atlas_market_intel": 1.0},
}

MIN_CANDLES = 30
CONSENSUS_RATIO = 0.70  # 70% of active indicators must agree
CONSENSUS_FLOOR = 2     # Lowered 7→2: cluster de-duplication + disabled anti-signals reduce active to 2-4 votes. Floor must be ≤ min active count. Edge floor (8%) + weight_learner are the real quality gates.
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
    "5m": 0.02,     # 2% — brain data: low edge (<10%) wins 56% on 79 trades. Consensus gates quality.
    "15m": 0.02,    # 2% — any positive edge after fees is tradeable. Confidence is the real gate.
    "1h": 0.02,     # 2% — position sizing limits risk, not edge floors.
    "4h": 0.02,     # 2% — RE-ENABLED. Brain says 4h is best: 74% WR on 19 trades.
    "weekly": 0.02, # 2% — uniform floor, let other gates do their job.
}

# Dynamic edge floor — regime-aware. Uniform 2% — consensus, confidence, and sizing are the real guards.
# Brain data proves low-edge trades are profitable. Edge floors were causing total paralysis.
MIN_EDGE_ABSOLUTE = 0.02  # 2% — lowered from 12% which blocked ALL trades during 11% rallies
MIN_EDGE_BY_REGIME = {
    "extreme_fear": 0.02,
    "fear": 0.02,
    "neutral": 0.02,
    "greed": 0.02,
    "extreme_greed": 0.02,
}
MIN_EDGE_HARD_FLOOR = 0.02  # absolute minimum — 2% after fees still profitable

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
REVERSAL_SENTINEL_PENALTY = 0.03  # Lowered 0.15→0.03: RSI+Bollinger are ALWAYS disabled & always disagree with UP during rallies. 15% penalty was blocking every UP signal systematically.

# Asset-specific edge premium — weaker assets need higher edge to trade
ASSET_EDGE_PREMIUM = {
    "bitcoin": 1.0,
    "ethereum": 1.0,
    "solana": 1.0,     # Lowered 1.3→1.0: old WR was pre-weight-learner. Fresh epoch, no penalty.
    "xrp": 0.9,        # Best performer, give room.
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


def _estimate_fees(timeframe: str, implied_price: float | None, is_maker: bool = False) -> float:
    """Estimate total Polymarket fees as fraction to subtract from edge.

    - 2% winner fee (always, on payout)
    - Taker fee: Polymarket's quadratic formula = 0.25 * (p * (1-p))^2
      Peaks at ~1.56% for 50/50, drops to near-zero at extreme prices.
    - Maker orders: zero taker fee (only winner fee applies)
    """
    winner_fee = 0.02

    if is_maker:
        return winner_fee

    ip = implied_price if implied_price is not None else 0.5
    ip = max(0.01, min(0.99, ip))
    taker_fee = 0.25 * (ip * (1 - ip)) ** 2

    return winner_fee + taker_fee


# ── Tavily News Sentiment (Atlas-sourced) ──
_TAVILY_SENTIMENT_FILE = Path.home() / "atlas" / "data" / "news_sentiment.json"
_tavily_cache: dict = {"data": None, "loaded_at": 0.0}
_TAVILY_CACHE_TTL = 120  # 2 minutes
_TAVILY_ASSET_MAP = {
    "bitcoin": "BTC", "ethereum": "ETH", "solana": "SOL", "xrp": "XRP",
}
_TAVILY_STALENESS = 7200  # 2 hours — ignore data older than this

# ── Atlas Market Intel (market_intel.json from Atlas data feed) ──
_ATLAS_INTEL_FILE = Path.home() / "polymarket-bot" / "data" / "market_intel.json"
_atlas_intel_cache: dict = {"data": None, "loaded_at": 0.0}
_ATLAS_INTEL_CACHE_TTL = 120  # 2 minutes
_ATLAS_INTEL_STALENESS = 10800  # 3 hours


def _get_tavily_sentiment_vote(asset: str) -> IndicatorVote | None:
    """Read Atlas Tavily news sentiment and return a vote for the given asset.

    Returns IndicatorVote if sentiment exceeds +/-0.15 threshold and data is fresh.
    Returns None if neutral, stale (>2h), or file missing.
    """
    now = time.time()

    # In-memory cache (2 min TTL)
    if _tavily_cache["data"] is not None and (now - _tavily_cache["loaded_at"]) < _TAVILY_CACHE_TTL:
        data = _tavily_cache["data"]
    else:
        if not _TAVILY_SENTIMENT_FILE.exists():
            return None
        try:
            import json as _json
            data = _json.loads(_TAVILY_SENTIMENT_FILE.read_text())
            _tavily_cache["data"] = data
            _tavily_cache["loaded_at"] = now
        except Exception:
            return None

    # Map asset name to ticker
    ticker = _TAVILY_ASSET_MAP.get(asset)
    if not ticker:
        return None

    assets = data.get("assets", {})
    asset_data = assets.get(ticker)
    if not asset_data:
        return None

    # Check staleness — data older than 2h is unreliable
    ts_str = asset_data.get("timestamp") or data.get("updated_at", "")
    if ts_str:
        try:
            from datetime import datetime as _dt, timezone as _tz
            # Parse ISO timestamp
            ts_str_clean = ts_str.replace("Z", "+00:00")
            ts_dt = _dt.fromisoformat(ts_str_clean)
            age_s = (now - ts_dt.timestamp())
            if age_s > _TAVILY_STALENESS:
                log.debug("[%s] Tavily sentiment stale (%.0fh old)", asset.upper(), age_s / 3600)
                return None
        except Exception:
            pass  # Can't parse timestamp — use data anyway

    sentiment = asset_data.get("sentiment", 0)

    # Threshold: must exceed +/-0.15 to generate a vote
    if abs(sentiment) <= 0.15:
        return None

    direction = "up" if sentiment > 0 else "down"
    confidence = min(abs(sentiment), 0.8)

    log.debug(
        "[%s] Tavily news sentiment: %s (%.2f, conf=%.2f, %d headlines)",
        asset.upper(), direction.upper(), sentiment, confidence,
        asset_data.get("headline_count", 0),
    )

    return IndicatorVote(
        direction=direction,
        confidence=confidence,
        raw_value=sentiment * 100,
    )


def _get_atlas_market_intel_vote(asset: str) -> IndicatorVote | None:
    """Read Atlas market_intel.json and return a vote based on news sentiment.

    Counts bullish vs bearish keywords in news titles/snippets.
    Returns IndicatorVote if >= 3 mentions lean the same direction.
    """
    now = time.time()

    # In-memory cache (2 min TTL)
    if _atlas_intel_cache["data"] is not None and (now - _atlas_intel_cache["loaded_at"]) < _ATLAS_INTEL_CACHE_TTL:
        data = _atlas_intel_cache["data"]
    else:
        if not _ATLAS_INTEL_FILE.exists():
            return None
        try:
            import json as _json
            data = _json.loads(_ATLAS_INTEL_FILE.read_text())
            _atlas_intel_cache["data"] = data
            _atlas_intel_cache["loaded_at"] = now
        except Exception:
            return None

    # Check staleness
    ts_str = data.get("scanned_at", "")
    if ts_str:
        try:
            from datetime import datetime as _dt
            ts_dt = _dt.fromisoformat(ts_str)
            if ts_dt.tzinfo is None:
                from zoneinfo import ZoneInfo
                ts_dt = ts_dt.replace(tzinfo=ZoneInfo("America/New_York"))
            age_s = now - ts_dt.timestamp()
            if age_s > _ATLAS_INTEL_STALENESS:
                return None
        except Exception:
            pass

    # Count bullish vs bearish keywords across all news items
    bullish_kw = {"bullish", "surge", "rally", "breakout", "soar", "pump", "gain", "rise", "upgrade", "accumulate"}
    bearish_kw = {"bearish", "crash", "dump", "plunge", "drop", "sell-off", "selloff", "decline", "downgrade", "liquidat"}

    asset_kw = {"bitcoin": {"bitcoin", "btc"}, "ethereum": {"ethereum", "eth"},
                "solana": {"solana", "sol"}, "xrp": {"xrp", "ripple"}}.get(asset, set())

    bullish = 0
    bearish = 0
    for item in data.get("news", []) + data.get("sentiment", []):
        text = (item.get("title", "") + " " + item.get("snippet", "")).lower()
        # Only count if relevant to this asset (or general crypto)
        if asset_kw and not any(kw in text for kw in asset_kw) and "crypto" not in text:
            continue
        if any(kw in text for kw in bullish_kw):
            bullish += 1
        if any(kw in text for kw in bearish_kw):
            bearish += 1

    # Need >= 3 mentions in one direction with clear lean
    total = bullish + bearish
    if total < 3:
        return None
    if bullish == bearish:
        return None

    direction = "up" if bullish > bearish else "down"
    strength = abs(bullish - bearish) / max(total, 1)
    confidence = min(strength, 0.7)

    log.debug("[%s] Atlas market intel: %s (bull=%d, bear=%d, conf=%.2f)",
              asset.upper(), direction.upper(), bullish, bearish, confidence)

    return IndicatorVote(direction=direction, confidence=confidence,
                         raw_value=(bullish - bearish) * 10)


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

        # ── Load Quant-validated param overrides (cached 60s, fallback to defaults) ──
        _live = get_live_params({
            "min_confidence": MIN_CONFIDENCE,
            "up_confidence_premium": UP_CONFIDENCE_PREMIUM,
            "min_edge_absolute": MIN_EDGE_ABSOLUTE,
            "consensus_floor": CONSENSUS_FLOOR,
            "consensus_ratio": CONSENSUS_RATIO,
        })
        _min_confidence = _live["min_confidence"]
        _up_confidence_premium = _live["up_confidence_premium"]
        _min_edge_absolute = _live["min_edge_absolute"]
        _consensus_floor = _live["consensus_floor"]
        _consensus_ratio = _live["consensus_ratio"]

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

        # ── Polymarket Order Book Flow (depth velocity, spread compression, whales) ──
        try:
            _flow_tracker = get_flow_tracker()
            # Try both up and down token IDs — use whichever has flow data
            _flow_vote = _flow_tracker.get_signal(up_token_id)
            if _flow_vote is None:
                _flow_vote = _flow_tracker.get_signal(down_token_id)
            votes["poly_flow"] = _flow_vote
        except Exception:
            votes["poly_flow"] = None

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
            try:
                if cg.liq_heatmap_available:
                    votes["liq_heatmap"] = liquidation_heatmap_signal(
                        cg.liq_cluster_above_usd, cg.liq_cluster_below_usd,
                        cg.liq_nearest_above_pct, cg.liq_nearest_below_pct,
                    )
                else:
                    votes["liq_heatmap"] = None
            except Exception:
                votes["liq_heatmap"] = None
        else:
            votes["open_interest"] = None
            votes["long_short_ratio"] = None
            votes["etf_flow"] = None
            votes["liq_heatmap"] = None

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

        # ── Tavily News Sentiment (Atlas-sourced, 90-min cycle) ──
        votes["tavily_news"] = _get_tavily_sentiment_vote(asset)

        # ── Atlas Market Intel (DDG/Tavily keyword sentiment, 90-min cycle) ──
        votes["atlas_market_intel"] = _get_atlas_market_intel_vote(asset)

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
        # Neutral in extreme regimes — let indicators decide direction naturally.
        # Old +0.08 UP bias in extreme_fear forced bullish in crashes, preventing DOWN trades.
        # Mild bias in fear/greed only (contrarian lean without overriding reality).
        if regime:
            regime_bias = {"extreme_fear": 0.0, "fear": 0.02,
                           "neutral": 0.0,
                           "greed": -0.02, "extreme_greed": 0.0}.get(regime.label, 0.0)
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

        # ── Adaptive Consensus Engine ──
        # Dynamic consensus that adapts to regime + active indicator count.
        # Hard cap: consensus_floor can NEVER exceed MAX_VIABLE_CONSENSUS in live mode.
        # This prevents optimizer-induced paralysis (e.g., Quant sets floor=7 with only 5 active).
        MAX_VIABLE_CONSENSUS = 4  # Hard ceiling — even Quant can't exceed this

        majority_dir = "up" if up_count >= down_count else "down"
        agree_count = max(up_count, down_count)
        active_count = up_count + down_count  # non-disabled indicators only
        total_indicators = len(active)

        # Clamp consensus_floor to MAX_VIABLE_CONSENSUS — prevents paralysis
        safe_floor = min(_consensus_floor, MAX_VIABLE_CONSENSUS)

        # Regime-aware dynamic consensus — extreme regimes cap at 4 to prevent paralysis.
        # Indicators herd in extreme regimes; requiring 60%+ of 8-10 is impossible.
        EXTREME_MAX_CONSENSUS = 4  # hard cap in extreme regimes — never require more than 4
        if regime and regime.label in ("extreme_fear", "extreme_greed"):
            regime_consensus = max(safe_floor, min(math.ceil(active_count * 0.50), EXTREME_MAX_CONSENSUS))
        elif regime and regime.label in ("fear", "greed"):
            regime_consensus = max(safe_floor, math.ceil(active_count * 0.55))
        else:
            # Normal: standard 70% ratio
            regime_consensus = max(safe_floor, int(active_count * _consensus_ratio))

        effective_consensus = min(regime_consensus, active_count)  # can't require more than available
        if regime and regime.consensus_offset:
            effective_consensus += regime.consensus_offset
            effective_consensus = min(effective_consensus, active_count)

        # Extreme fear + DOWN: further lower to 45% (bearish momentum is natural in fear)
        if regime and regime.label == "extreme_fear" and majority_dir == "down":
            fear_consensus = max(safe_floor, min(math.ceil(active_count * 0.45), EXTREME_MAX_CONSENSUS))
            fear_consensus = min(fear_consensus, active_count)
            if fear_consensus < effective_consensus:
                effective_consensus = fear_consensus

        # ── High-Volume Override — strong volume/liquidation signals relax consensus by 1 ──
        vol_vote = votes.get("volume_spike")
        liq_vote = votes.get("liquidation")
        oi_vote = votes.get("open_interest")

        volume_override = False
        if vol_vote is not None and vol_vote.confidence >= 0.70:
            volume_override = True
        elif (liq_vote is not None and oi_vote is not None
              and liq_vote.direction == oi_vote.direction
              and liq_vote.confidence >= 0.50):
            volume_override = True

        if volume_override and effective_consensus > safe_floor:
            effective_consensus -= 1
            log.info("[%s/%s] HIGH-VOLUME OVERRIDE: consensus lowered by 1 to %d",
                     asset.upper(), timeframe, effective_consensus)

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
            # Penalty only — never hard block. Raises confidence floor to make trade harder, not impossible.
            reversal_penalty = REVERSAL_SENTINEL_PENALTY
            log.info(
                "[%s/%s] REVERSAL SENTINEL: %d disabled indicators (%s) disagree with %s — "
                "confidence penalty +%.0f%% applied (agree %d/%d)",
                asset.upper(), timeframe, len(sentinel_dissenters),
                "+".join(sentinel_dissenters), majority_dir.upper(),
                reversal_penalty * 100, agree_count, effective_consensus,
            )

        # ── Trend Filter — anti-trend signals need stronger consensus ──
        if len(closes) >= 50:
            short_trend = sum(closes[-10:]) / 10
            long_trend = sum(closes[-50:]) / 50
            trend_dir = "up" if short_trend > long_trend else "down"

            if majority_dir != trend_dir:
                # Going against the trend — require 70% of active indicators to agree
                anti_trend_min = max(effective_consensus + 1, int(active_count * 0.70))
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

        # ── V2: Auto-rules from post-trade analysis ──
        try:
            from bot.post_trade_analyzer import PostTradeAnalyzer
            _auto_rules = PostTradeAnalyzer.get_active_rules()
            for _rule in _auto_rules:
                _action = _rule.get("action", {})
                _rule_asset = _action.get("asset", "")
                _rule_tf = _action.get("timeframe", "")
                if _rule_asset and _rule_asset != asset:
                    continue
                if _rule_tf and _rule_tf != timeframe:
                    continue
                _atype = _action.get("type", "")
                if _atype == "raise_edge_floor":
                    _boost = _action.get("edge_floor_boost", 0.03)
                    _min_edge_absolute = max(_min_edge_absolute, _min_edge_absolute + _boost)
                    log.info("[AUTO-RULE] Raised edge floor by +%.0f%% for %s/%s", _boost * 100, asset, timeframe)
                elif _atype == "flag_indicators":
                    log.info("[AUTO-RULE] WARNING: indicators unreliable for %s/%s — %s",
                             asset, timeframe, _action.get("description", ""))
        except Exception:
            pass  # Auto-rules never block trading on errors

        # ── Timeframe-Specific Minimum Edge (regime-adjusted + asset premium) ──
        asset_premium = ASSET_EDGE_PREMIUM.get(asset, 1.0)
        min_edge = MIN_EDGE_BY_TF.get(timeframe, 0.05) * (regime.edge_multiplier if regime else 1.0) * asset_premium
        # Dynamic edge floor — regime-aware instead of rigid 12%
        regime_edge_floor = MIN_EDGE_BY_REGIME.get(regime.label, MIN_EDGE_ABSOLUTE) if regime else _min_edge_absolute
        regime_edge_floor = max(regime_edge_floor, MIN_EDGE_HARD_FLOOR)
        min_edge = max(min_edge, regime_edge_floor)
        # Pattern gate edge adjustment: raise bar for losing combos, lower for winning ones
        if _gate_decision.edge_adjustment > 0:
            min_edge = max(min_edge, _gate_decision.edge_adjustment)
            log.info("[%s/%s] Pattern gate raised edge to %.0f%% (%.0f%% WR over %d trades)",
                     asset.upper(), timeframe, min_edge * 100,
                     _gate_decision.win_rate * 100, _gate_decision.sample_size)
        elif _gate_decision.edge_adjustment < 0:
            min_edge = max(0.02, min_edge + _gate_decision.edge_adjustment)  # Never below 2%
        if consensus_edge < min_edge:
            log.info(
                "[%s/%s] Edge too low: %.3f < %.3f (asset_premium=%.1fx)",
                asset.upper(), timeframe, consensus_edge, min_edge, asset_premium,
            )
            return None

        # ── Confidence Floor: MAX of regime floor and MIN_CONFIDENCE (never let regime lower it) ──
        regime_floor = regime.confidence_floor if regime else _min_confidence
        effective_conf_floor = max(regime_floor, _min_confidence)
        if consensus_dir == "up":
            up_premium = _up_confidence_premium
            # Halve UP penalty in fear — contrarian buying IS the strategy in fear regimes
            if regime and regime.label in ("extreme_fear", "fear"):
                up_premium *= 0.5
            effective_conf_floor += up_premium
        # ETH confidence premium removed — old WR data was pre-weight-learner. Fresh epoch, no per-asset penalty.
        # Reversal sentinel penalty: raise floor when disabled indicators signal reversal
        effective_conf_floor += reversal_penalty
        if confidence < effective_conf_floor:
            log.info(
                "[%s/%s] Confidence too low for %s: %.3f < %.3f (UP premium applied: %s)",
                asset.upper(), timeframe, consensus_dir.upper(),
                confidence, effective_conf_floor, consensus_dir == "up",
            )
            return None

        # Build indicator vote snapshot for weight learning (rich format)
        ind_votes = {
            name: {"direction": vote.direction, "confidence": vote.confidence, "raw_value": vote.raw_value}
            for name, vote in active.items()
        }

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

            # ── ML Veto Gate ──
            # RF model can't cause a trade, but CAN prevent one.
            # If ML predicts <40% win probability, block the trade.
            try:
                from bot.ml_predictor import GarvesV2MLPredictor
                _ml = getattr(self, "_ml_veto", None)
                if _ml is None:
                    _ml = GarvesV2MLPredictor()
                    self._ml_veto = _ml
                if _ml._model is not None:
                    # Build a minimal signal-like object for prediction
                    _temp_signal = Signal(
                        direction=consensus_dir, edge=consensus_edge,
                        probability=consensus_prob, token_id=consensus_token,
                        confidence=confidence, timeframe=timeframe, asset=asset,
                        indicator_votes=ind_votes, atr_value=atr_val,
                        reward_risk_ratio=rr_ratio,
                    )
                    from bot.conviction import AssetSignalSnapshot
                    _temp_snap = AssetSignalSnapshot(
                        asset=asset, direction=consensus_dir,
                        consensus_count=agree_count, total_indicators=active_count,
                        edge=consensus_edge, confidence=confidence,
                        has_volume_spike=False, has_temporal_arb=False,
                        indicator_votes=ind_votes,
                    )
                    _ml_prob = _ml.predict(_temp_signal, _temp_snap)
                    if _ml_prob is not None and _ml_prob < 0.40:
                        log.info(
                            "[%s/%s] ML VETO: win_prob=%.3f < 0.40, blocking trade",
                            asset.upper(), timeframe, _ml_prob,
                        )
                        return None
                    if _ml_prob is not None:
                        log.info("[%s/%s] ML gate passed: win_prob=%.3f", asset.upper(), timeframe, _ml_prob)
            except Exception:
                pass  # ML failure never blocks trading

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
