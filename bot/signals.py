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
    ema_crossover,
    fear_greed_index,
    funding_rate_signal,
    get_params,
    heikin_ashi,
    liquidation_cascade_signal,
    liquidity_signal,
    macd,
    momentum,
    order_flow_delta,
    price_divergence,
    rsi,
    spot_depth_signal,
    temporal_arb,
    volume_spike,
    vwap,
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
}

# Indicator weights for the ensemble
# Updated by Quant backtest (Feb 17, 127 trades):
#   Baseline 60.0% WR → Optimized 65.3% WR (+5.3pp)
#   Changes: ema 1.1→0.6, order_flow 0.5→1.0, news 2.5→1.0
#   news demoted: only 3/11 correct in recent trades (27.3%)
#   ema demoted: 55.2% accuracy, marginal contributor
#   order_flow promoted: doubled weight based on weight_v171 sweep
WEIGHTS = {
    # TOP TIER — proven edge (>70% accuracy)
    "volume_spike": 2.5,
    "liquidation": 2.0,
    # MID TIER — above coin flip (55-60%)
    "temporal_arb": 1.8,
    "price_div": 1.4,
    "macd": 1.1,
    "order_flow": 2.0,  # Quant V2: 1.0→2.0 (60.2% accuracy, weight_v220)
    "news": 1.0,         # Quant: 2.5→1.0 (27.3% recent accuracy, 11 votes)
    # LOW TIER — marginal (50-55%)
    "momentum": 0.6,
    "ema": 0.6,           # Quant: 1.1→0.6 (weight_v171)
    "heikin_ashi": 0.5,
    "spot_depth": 0.0,  # Quant V2: disabled (insufficient data, weight_v220)
    "orderbook": 0.5,
    "liquidity": 0.4,
    # DISABLED — below coin flip (harmful)
    "bollinger": 0.0,
    "rsi": 0.0,
    "funding_rate": 0.0,
}

# Timeframe-dependent weight scaling
# Short timeframes: order flow / arb matters more; long: TA matters more
TF_WEIGHT_SCALE = {
    "5m":  {"order_flow": 1.8, "orderbook": 2.0, "temporal_arb": 2.5, "price_div": 2.0,
            "rsi": 0.6, "macd": 0.6, "heikin_ashi": 0.5,
            "spot_depth": 1.5, "liquidation": 1.8, "funding_rate": 0.5},
    "15m": {"order_flow": 1.5, "orderbook": 1.8, "temporal_arb": 2.0, "price_div": 1.5,
            "rsi": 0.8, "macd": 0.9,
            "spot_depth": 1.3, "liquidation": 1.5, "funding_rate": 0.8},
    "1h":  {"order_flow": 1.0, "orderbook": 1.2, "rsi": 1.0, "macd": 1.1,
            "funding_rate": 1.2, "liquidation": 1.0},
    "4h":  {"order_flow": 0.8, "orderbook": 0.8, "price_div": 0.7,
            "rsi": 1.2, "macd": 1.3, "heikin_ashi": 1.3,
            "funding_rate": 1.5, "liquidation": 0.8},
}

MIN_CANDLES = 30
MIN_CONSENSUS = 7  # at least 7 indicators must agree (data: 7+ = 62% WR, 5-6 = 15% WR)
MIN_ATR_THRESHOLD = 0.00005  # skip if volatility below this (0.005% of price)
MIN_CONFIDENCE = 0.25  # reject weak signals (avg was 0.178, most were losers)

# Directional bias: UP predictions have 47.3% WR vs DOWN 63% — require higher confidence for UP
UP_CONFIDENCE_PREMIUM = 0.08  # add 8% to confidence floor for UP bets

# Time-of-day filter: block hours with <30% WR across 140+ trades
# Good hours: 00,02,10,12,16,17 (79.5% WR combined)
# Bad hours: 05 (0%), 18 (17%), 19 (29%), 20 (13%), 21 (0%), 22 (12%), 23 (22%)
AVOID_HOURS_ET = {1, 3, 4, 5, 6, 7, 23}  # 7 dead hours — trade 8AM-10PM ET + keep 0,2 (79.5% WR)

# Timeframe-specific minimum edge — must exceed estimated fees
# Data: 0-8% edge = 20% WR, 8-11% = 62.5% WR — 8% is the breakeven floor
MIN_EDGE_BY_TF = {
    "5m": 0.08,   # 8% — raised from 6% (below 8% = 20% WR)
    "15m": 0.08,  # 8% — raised from 9% regime-adjusted (0.7x was dropping to 6.3%)
    "1h": 0.05,   # 5% — raised from 3%
    "4h": 0.04,   # 4% — raised from 3%
}

# Hard floor — regime adjustments cannot lower edge below this
MIN_EDGE_ABSOLUTE = 0.08  # 8% — never trade below this regardless of regime

# Asset-specific edge premium — weaker assets need higher edge to trade
ASSET_EDGE_PREMIUM = {
    "bitcoin": 1.0,    # baseline (33.9% WR — needs filtering not premium)
    "ethereum": 0.9,   # slight discount (best performer: 41.3% WR)
    "solana": 1.5,     # +50% edge required (31.6% WR — worst performer)
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
    tf_multiplier = {"5m": 1.0, "15m": 1.0, "1h": 0.8, "4h": 0.6}.get(timeframe, 0.8)
    taker_fee = base_taker * tf_multiplier

    return winner_fee + taker_fee


class SignalEngine:
    """Ensemble signal engine with fee awareness, ATR filter, and timeframe-specific params."""

    def __init__(self, cfg: Config, price_cache: PriceCache):
        self.cfg = cfg
        self._cache = price_cache
        # Cross-timeframe signal cache: (asset, timeframe) -> (direction, timestamp)
        self._signal_history: dict[tuple[str, str], tuple[str, float]] = {}
        self._CROSS_TF_MAX_AGE = 600  # 10 min — 15m signal must be recent

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
    ) -> Signal | None:
        """Generate a signal from the weighted ensemble of all indicators."""

        closes = self._cache.get_closes(asset, 200)
        if len(closes) < MIN_CANDLES:
            log.info(
                "Warming up [%s/%s] (%d/%d candles)",
                asset.upper(), timeframe, len(closes), MIN_CANDLES,
            )
            return None

        # ── Time-of-Day Filter: skip dead zone hours (06-07 ET = 0-33% WR) ──
        from datetime import datetime as _dt
        from zoneinfo import ZoneInfo
        current_hour_et = _dt.now(ZoneInfo("America/New_York")).hour
        if current_hour_et in AVOID_HOURS_ET:
            log.info("[%s/%s] Time-of-day filter: hour %d ET is in dead zone, skipping",
                     asset.upper(), timeframe, current_hour_et)
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

        # ── Consensus Filter ──
        majority_dir = "up" if up_count >= down_count else "down"
        agree_count = max(up_count, down_count)
        total_indicators = len(active)

        # Apply regime adjustment to consensus requirement
        effective_consensus = MIN_CONSENSUS + (regime.consensus_offset if regime else 0)
        effective_consensus = max(MIN_CONSENSUS, effective_consensus)  # never below MIN_CONSENSUS (7)

        if agree_count < effective_consensus:
            log.info(
                "[%s/%s] Consensus filter: %d/%d agree on %s (need %d%s), skipping",
                asset.upper(), timeframe, agree_count, total_indicators,
                majority_dir, effective_consensus,
                f" regime={regime.label}" if regime else "",
            )
            return None

        # ── Trend Filter — anti-trend signals need stronger consensus ──
        if len(closes) >= 50:
            short_trend = sum(closes[-10:]) / 10
            long_trend = sum(closes[-50:]) / 50
            trend_dir = "up" if short_trend > long_trend else "down"

            if majority_dir != trend_dir:
                # Going against the trend — require 70% of indicators to agree
                anti_trend_min = max(MIN_CONSENSUS + 2, int(total_indicators * 0.7))
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
            edge_up = prob_up - 0.50
            edge_down = (1 - prob_up) - 0.50

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
            "[%s/%s] Consensus: %d/%d agree | Binance: $%s | Poly implied: %s | edge_up=%.3f edge_down=%.3f (after fees)",
            asset.upper(), timeframe, agree_count, total_indicators,
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
                contrarian_min = max(MIN_CONSENSUS + 2, int(total_indicators * 0.75))
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

        # ── Directional Bias: UP predictions need higher confidence (47.3% WR vs 63% DOWN) ──
        effective_conf_floor = regime.confidence_floor if regime else MIN_CONFIDENCE
        if consensus_dir == "up":
            effective_conf_floor += UP_CONFIDENCE_PREMIUM
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

            # ── Cross-Timeframe Validation ──
            # 5m signals must agree with most recent 15m signal for the same asset.
            # Prevents noise-driven 5m trades that contradict the broader trend.
            if timeframe == "5m":
                key_15m = (asset, "15m")
                cached = self._signal_history.get(key_15m)
                if cached is not None:
                    cached_dir, cached_ts = cached
                    age = now_ts - cached_ts
                    if age < self._CROSS_TF_MAX_AGE and cached_dir != consensus_dir:
                        log.info(
                            "[%s/5m] Cross-TF filter: 5m=%s but 15m=%s (%.0fs ago), SKIPPING",
                            asset.upper(), consensus_dir.upper(), cached_dir.upper(), age,
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
            )

        log.info(
            "[%s/%s] No edge after fees (prob_up=%.3f, implied=%.3f, fees=%.3f)",
            asset.upper(), timeframe, prob_up, implied_up_price or 0.5, fees,
        )
        return None
