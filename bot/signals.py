from __future__ import annotations

import logging
from dataclasses import dataclass

from bot.config import Config
from bot.regime import RegimeAdjustment
from bot.weight_learner import get_dynamic_weights
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

# Indicator weights for the ensemble
# Updated by Quant backtest (Feb 17, 127 trades, weight_v149):
#   Baseline 60.0% WR → Optimized 65.3% WR (+5.3pp)
#   Feb 17 tuning: order_flow 3.0→4.0 (every top-20 config uses 4.0, catches reversals via volume delta)
#                  momentum 1.0→1.2 (#1 config uses 1.2, 63.2% accuracy)
#   news demoted: only 3/11 correct in recent trades (27.3%)
#   ema demoted: 55.2% accuracy, marginal contributor
WEIGHTS = {
    # TOP TIER — proven edge (>70% accuracy)
    "volume_spike": 2.5,
    "liquidation": 2.0,
    # MID TIER — above coin flip (55-60%)
    "temporal_arb": 1.8,
    "price_div": 1.4,
    "macd": 1.1,
    "order_flow": 2.5,  # Rebalanced: was 4.0 (dominated 40-50% of ensemble). 60.4% accuracy doesn't justify 4x weight
    "news": 0.4,         # Re-enabled: live accuracy 58.6% (140 samples). Was 0.0 from stale 47% data, weight_learner auto-adjusts
    # LOW TIER — marginal (50-55%)
    "momentum": 1.2,    # Quant V4: 1.0→1.2 (#1 config uses 1.2, 63.2% accuracy)
    "ema": 0.6,           # Quant: 1.1→0.6 (weight_v171)
    "heikin_ashi": 0.5,
    "spot_depth": 0.6,  # Re-enabled: 63.0% accuracy on 54 votes (was wrongly disabled for "insufficient data")
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
CONSENSUS_FLOOR = 3     # Lowered 4→3: old floor forced 4/4 unanimous with few indicators. R:R 1.2+ is the real guard now.
MIN_CONSENSUS = CONSENSUS_FLOOR  # backward compat for backtest/quant
MIN_ATR_THRESHOLD = 0.00005  # skip if volatility below this (0.005% of price)
MIN_CONFIDENCE = 0.55  # DATA: conf>=55% = 82.9% WR, conf>=60% = 91.7% WR. Old 0.25 let through garbage.

# Directional bias: UP predictions have 47.3% WR vs DOWN 63% — require higher confidence for UP
UP_CONFIDENCE_PREMIUM = 0.12  # Quant optimal: 0.12. UP bets need higher bar — forces only high-conviction UP trades through.

# NY market open manipulation window: 9:30-10:15 AM ET — high manipulation, skip
NY_OPEN_AVOID_START = (9, 30)   # 9:30 AM ET
NY_OPEN_AVOID_END = (10, 15)    # 10:15 AM ET

# Timeframe-specific minimum edge — must exceed estimated fees
# Data: 0-8% edge = 20% WR, 8-11% = 62.5% WR — 8% is the breakeven floor
MIN_EDGE_BY_TF = {
    "5m": 0.08,     # 8% — raised from 6% (below 8% = 20% WR)
    "15m": 0.08,    # 8% — raised from 9% regime-adjusted (0.7x was dropping to 6.3%)
    "1h": 0.05,     # 5% — raised from 3%
    "4h": 0.04,     # 4% — raised from 3%
    "weekly": 0.03, # 3% — lowest edge floor for longest timeframe
}

# Hard floor — regime adjustments cannot lower edge below this
MIN_EDGE_ABSOLUTE = 0.08  # 8% — never trade below this regardless of regime

# Reward-to-Risk ratio filter
# R:R = ((1-P) * 0.98) / P  where P = token price, 0.98 = payout after 2% winner fee
MIN_RR_RATIO = 1.2  # reject signals where R:R < 1.2 (only bet when win > loss)
MAX_TOKEN_PRICE = 0.50  # Never buy tokens above $0.50 — forces cheap side with R:R > 0.96

# Minimum confidence to count toward consensus vote
# Indicators below this confidence are basically guessing — don't let them inflate head count
# XRP loss analysis: 4/6 "DOWN" voters had <10% confidence, outvoted MACD at 81% UP
MIN_VOTE_CONFIDENCE = 0.15  # 15% — below this, vote doesn't count for consensus

# Reversal Sentinel — disabled indicators act as reversal canaries
# When 2+ disabled indicators disagree with majority, raise the bar
REVERSAL_SENTINEL_INDICATORS = {"rsi", "bollinger", "heikin_ashi", "funding_rate"}
REVERSAL_SENTINEL_THRESHOLD = 2   # how many dissenters needed to trigger
REVERSAL_SENTINEL_PENALTY = 0.10  # +10% confidence floor when triggered

# Asset-specific edge premium — weaker assets need higher edge to trade
ASSET_EDGE_PREMIUM = {
    "bitcoin": 1.5,    # raised 1.0→1.5: 40% WR, -$221 PnL — must prove high edge to trade
    "ethereum": 1.3,   # raised 0.9→1.3: 54% WR but -$15 PnL — needs higher bar
    "solana": 1.5,     # raised 1.2→1.5: 33% WR, -$179 PnL — hardest bar
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

        # ── NY Open Manipulation Window: 9:30-10:15 AM ET ──
        from datetime import datetime as _dt
        from zoneinfo import ZoneInfo
        _now_et = _dt.now(ZoneInfo("America/New_York"))
        current_hour_et = _now_et.hour
        current_min_et = _now_et.minute
        now_hm = (current_hour_et, current_min_et)
        if NY_OPEN_AVOID_START <= now_hm <= NY_OPEN_AVOID_END:
            log.info("[%s/%s] NY open manipulation window (%d:%02d ET), skipping",
                     asset.upper(), timeframe, current_hour_et, current_min_et)
            return None

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

        # ── Binance Spot Order Book Depth ──
        if spot_depth:
            votes["spot_depth"] = spot_depth_signal(
                bids=spot_depth.get("bids", []),
                asks=spot_depth.get("asks", []),
            )
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

        # Filter to non-None votes
        active: dict[str, IndicatorVote] = {
            k: v for k, v in votes.items() if v is not None
        }

        if len(active) < 3:
            log.info("[%s/%s] Too few indicators fired (%d)", asset.upper(), timeframe, len(active))
            return None

        # ── Weighted Ensemble Score (with timeframe scaling) ──
        dynamic_weights = get_dynamic_weights(WEIGHTS)
        tf_scale = TF_WEIGHT_SCALE.get(timeframe, {})
        weighted_sum = 0.0
        weight_total = 0.0
        up_count = 0
        down_count = 0

        disabled = []
        for name, vote in active.items():
            base_w = dynamic_weights.get(name, 1.0)

            # Skip indicators disabled by weight learner (accuracy < 40%)
            if base_w <= 0:
                disabled.append(name)
                continue

            # Apply timeframe-specific weight scaling
            scale = tf_scale.get(name, 1.0)
            w = base_w * scale

            sign = 1.0 if vote.direction == "up" else -1.0
            weighted_sum += w * vote.confidence * sign
            weight_total += w  # FIX: normalize by raw weights, not confidence-weighted

            # Only count toward consensus if confidence is meaningful
            # Low-confidence votes (<15%) still contribute to weighted score but
            # don't inflate head count — prevents noise from outvoting strong signals
            if vote.confidence >= MIN_VOTE_CONFIDENCE:
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

        # ── Map Score → Probability ──
        lo, hi = PROB_CLAMP.get(timeframe, (0.30, 0.70))
        raw_prob = 0.5 + score * 0.25
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

        # ── Safety: reject signals that contradict strong market odds ──
        if implied_up_price is not None and 0.01 < implied_up_price < 0.99:
            market_dir = "up" if implied_up_price > 0.5 else "down"
            market_strength = abs(implied_up_price - 0.5)

            if consensus_dir != market_dir and market_strength > 0.15:
                # Our indicators disagree with a strong market lean (>65%)
                # Require much higher consensus to go against the market
                contrarian_min = max(effective_consensus + 1, int(active_count * 0.80))
                if agree_count < contrarian_min:
                    log.info(
                        "[%s/%s] Market safety filter: consensus=%s but market=%s (%.0f%%), "
                        "need %d/%d agree (have %d), skipping",
                        asset.upper(), timeframe, consensus_dir.upper(),
                        market_dir.upper(), implied_up_price * 100,
                        contrarian_min, total_indicators, agree_count,
                    )
                    return None

        # ── Timeframe-Specific Minimum Edge (regime-adjusted + asset premium) ──
        asset_premium = ASSET_EDGE_PREMIUM.get(asset, 1.0)
        min_edge = MIN_EDGE_BY_TF.get(timeframe, 0.05) * (regime.edge_multiplier if regime else 1.0) * asset_premium
        # Hard floor — regime cannot lower edge below absolute minimum
        min_edge = max(min_edge, MIN_EDGE_ABSOLUTE)
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
            effective_conf_floor += 0.03  # +3% for ETH — 9 of 12 losses were ETH
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
