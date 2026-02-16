# Garves Bot Research Report - Upgrade Plan

**Date:** 2026-02-14
**Objective:** Identify actionable improvements to the Garves Polymarket trading bot based on web research, code analysis, and market intelligence.

---

## Table of Contents

1. [Current Architecture Assessment](#1-current-architecture-assessment)
2. [Critical Issues Found in Code](#2-critical-issues-found-in-code)
3. [New Indicators to Add](#3-new-indicators-to-add)
4. [Ensemble Weighting Improvements](#4-ensemble-weighting-improvements)
5. [Market Microstructure & Polymarket-Specific Insights](#5-market-microstructure--polymarket-specific-insights)
6. [Risk Management Improvements](#6-risk-management-improvements)
7. [Timing Optimizations](#7-timing-optimizations)
8. [Execution & Latency Improvements](#8-execution--latency-improvements)
9. [ML-Based Signal Enhancement](#9-ml-based-signal-enhancement)
10. [Polymarket API Quirks & Advantages](#10-polymarket-api-quirks--advantages)
11. [Priority Implementation Roadmap](#11-priority-implementation-roadmap)

---

## 1. Current Architecture Assessment

### What the bot does well:
- Multi-asset (BTC/ETH/SOL) across 4 timeframes (5m/15m/1h/4h)
- Real-time Binance WebSocket trade feed with tick-rule order flow classification
- Polymarket WebSocket for orderbook depth and implied prices
- Consensus filter requiring 4+ indicators to agree
- Timeframe-specific probability clamping to prevent overconfidence
- Clean signal-to-execution pipeline with risk checks

### What the bot is missing:
- **No adaptive weights** -- static indicator weights regardless of market regime
- **No volatility awareness** -- same signals in calm vs volatile markets
- **No CVD divergence detection** -- order flow is used but not for divergence
- **No rate-of-change on indicators** -- treating indicators as point-in-time snapshots
- **No timeframe-specific indicator parameters** -- RSI(14), MACD(12,26,9) used for all timeframes including 5m
- **No Kelly criterion position sizing** -- fixed order sizes regardless of edge magnitude
- **No historical performance feedback** -- weights don't adapt based on what's actually working
- **No fee awareness** -- Polymarket introduced dynamic taker fees on 15-minute markets (Feb 2026)
- **All implied_up_price values are 0.5** -- suggests the WS feed may not be populating real implied prices for many markets, meaning edge calculations are often wrong

---

## 2. Critical Issues Found in Code

### 2a. Implied price always 0.5
Looking at `trades.jsonl`, every single trade has `implied_up_price: 0.5`. This means either:
- The WebSocket feed is not receiving price data before signals fire
- Market subscriptions happen too late (tokens are subscribed in the same tick they are evaluated)

**Fix:** Add a warmup period. After subscribing to new tokens, wait at least 1 tick (30s) before generating signals. Alternatively, fetch the current book/price via REST API as a fallback when WS price is missing.

### 2b. Price divergence indicator is backwards
In `price_divergence()`, the function fades the market (bets against the direction the market is leaning). But the research shows the profitable strategy is the opposite: **when Binance confirms directional momentum and Polymarket hasn't caught up yet, bet WITH the Binance direction**. The current implementation measures "staleness" but then bets against the prevailing Polymarket direction, which is wrong.

**Fix:** Rewrite `price_divergence()` to compare the short-term Binance price change (e.g., last 5 minutes) against the Polymarket implied probability. If Binance shows +0.5% move but Polymarket implied UP is still 0.50, that's a BUY UP signal (not a fade).

### 2c. Indicator parameters not adapted for timeframe
RSI(14) on 1-minute candles covers 14 minutes of data. On a 5-minute market, this is fine. On a 4-hour market, RSI(14) is still only 14 minutes of data -- far too short.

**Fix:** Scale indicator parameters with timeframe:
```
5m:  RSI(7),  MACD(6,12,6),  EMA(5,13),  BB(10)
15m: RSI(14), MACD(12,26,9), EMA(8,21),  BB(20)
1h:  RSI(21), MACD(12,26,9), EMA(12,26), BB(20)
4h:  RSI(28), MACD(24,52,18),EMA(20,50), BB(40)
```

### 2d. Edge calculation when implied_up_price is 0.5
When implied price is 0.5, the edge calculation reduces to `prob_up - 0.5` for the up side. Combined with probability clamping, this means:
- 5m: max edge = 0.65 - 0.50 = 0.15 (15%)
- 15m: max edge = 0.70 - 0.50 = 0.20 (20%)

This is unrealistically high. The real edge at these markets is 2-5%. The bot is likely overestimating its edge on every trade.

**Fix:** When implied price is unknown, use the market's actual last traded price (even via REST fallback). Never default to 0.5.

---

## 3. New Indicators to Add

### 3a. Stochastic RSI (StochRSI)
StochRSI applies a stochastic oscillator to RSI values, making it more sensitive to momentum changes on short timeframes. Research shows it outperforms standard RSI on 5-minute and 15-minute charts for crypto.

**Parameters:** StochRSI(14, 14, 3, 3) for 15m+ ; StochRSI(7, 7, 3, 3) for 5m.

```python
def stoch_rsi(closes, rsi_period=14, stoch_period=14, k=3, d=3):
    # Calculate RSI series
    rsi_values = [rsi_value(closes[:i+1], rsi_period) for i in range(len(closes))]
    # Apply stochastic formula to RSI
    rsi_arr = np.array(rsi_values[-stoch_period:])
    lowest = np.min(rsi_arr)
    highest = np.max(rsi_arr)
    if highest == lowest:
        return None
    k_val = (rsi_arr[-1] - lowest) / (highest - lowest)
    # k_val < 0.2 = oversold (UP), k_val > 0.8 = overbought (DOWN)
```

### 3b. KDJ Indicator
KDJ is widely recommended for crypto short-term trading (2025-2026 research). It generates Golden Cross (buy) and Death Cross (sell) signals with faster responsiveness than RSI.

**Parameters:** KDJ(5, 3, 3) for 5m charts; KDJ(9, 3, 3) for 15m+.

### 3c. ATR (Average True Range) -- Volatility Filter
Not a directional indicator but a meta-filter. Use ATR to:
1. **Filter signals in low volatility** -- if ATR is below 20th percentile of its own history, signals are likely noise
2. **Scale position sizes** -- higher ATR = smaller positions
3. **Adjust probability clamps** -- widen clamps in high volatility, tighten in low

```python
def atr(candles, period=14):
    trs = []
    for i in range(1, len(candles)):
        tr = max(
            candles[i].high - candles[i].low,
            abs(candles[i].high - candles[i-1].close),
            abs(candles[i].low - candles[i-1].close),
        )
        trs.append(tr)
    return np.mean(trs[-period:])
```

### 3d. CVD Divergence (upgrade existing order_flow_delta)
Currently, `order_flow_delta` just computes net buy vs sell volume. The research shows the real edge is in **CVD divergence**: when price makes a new high/low but CVD does not follow.

**Implementation:**
```python
def cvd_divergence(candles, buy_volumes, sell_volumes, lookback=20):
    # Build cumulative delta series
    deltas = [b - s for b, s in zip(buy_volumes, sell_volumes)]
    cvd = np.cumsum(deltas)

    # Check for divergence
    price_new_high = candles[-1].close > max(c.close for c in candles[-lookback:-1])
    cvd_lower = cvd[-1] < max(cvd[-lookback:-1])
    # Bearish divergence: price new high but CVD failing -> DOWN
    if price_new_high and cvd_lower:
        return IndicatorVote(direction="down", confidence=0.7, raw_value=...)

    price_new_low = candles[-1].close < min(c.close for c in candles[-lookback:-1])
    cvd_higher = cvd[-1] > min(cvd[-lookback:-1])
    # Bullish divergence: price new low but CVD rising -> UP
    if price_new_low and cvd_higher:
        return IndicatorVote(direction="up", confidence=0.7, raw_value=...)
```

### 3e. Volume Spike Detection
Research on the "spike bot" approach shows that sudden volume spikes on Binance (>2x average) within a 5-minute window are strong directional signals.

```python
def volume_spike(candles, threshold=2.0, lookback=20):
    avg_vol = np.mean([c.volume for c in candles[-lookback-1:-1]])
    current_vol = candles[-1].volume
    if current_vol > avg_vol * threshold:
        direction = "up" if candles[-1].close > candles[-1].open else "down"
        conf = min((current_vol / avg_vol - 1) / 3, 1.0)
        return IndicatorVote(direction=direction, confidence=conf, ...)
```

### 3f. RSI + MACD Divergence Confirmation
Instead of treating RSI and MACD independently, add a combined divergence detector:
- If RSI shows bullish divergence AND MACD histogram is turning positive -> strong UP signal with boosted confidence
- This "dual confirmation" filtering reduces false signals by 30-40% per research

---

## 4. Ensemble Weighting Improvements

### 4a. Adaptive Weights Based on Recent Performance
Track which indicators are actually predicting correctly over the last N trades. Upweight winners, downweight losers.

```python
class AdaptiveWeights:
    def __init__(self, decay=0.95):
        self.accuracy = {name: 0.5 for name in WEIGHTS}  # start at 50%
        self.decay = decay

    def update(self, indicator_name, was_correct):
        old = self.accuracy[indicator_name]
        self.accuracy[indicator_name] = self.decay * old + (1 - self.decay) * (1.0 if was_correct else 0.0)

    def get_weight(self, name):
        base = WEIGHTS[name]
        # Scale by accuracy: indicators at 70% get 1.4x, at 30% get 0.6x
        return base * (self.accuracy[name] * 2)
```

### 4b. Regime-Dependent Weights
Detect market regime (trending vs ranging) and apply different weight profiles:

```python
REGIME_WEIGHTS = {
    "trending": {
        "ema": 1.5, "macd": 1.3, "momentum": 1.2, "heikin_ashi": 1.1,
        "rsi": 0.5, "bollinger": 0.6, "vwap": 0.7,
        "order_flow": 1.3, "orderbook": 1.5, "price_div": 1.0,
    },
    "ranging": {
        "rsi": 1.4, "bollinger": 1.3, "vwap": 1.0,
        "ema": 0.6, "macd": 0.7, "momentum": 0.5, "heikin_ashi": 0.7,
        "order_flow": 1.3, "orderbook": 1.5, "price_div": 1.0,
    },
}
```

**Regime detection:** Compare ATR(14) to ATR(50). If ATR(14)/ATR(50) > 1.3, market is trending. If < 0.7, market is ranging.

### 4c. Timeframe-Dependent Weights
Order flow and price divergence matter much more for 5m/15m (where latency arbitrage exists) than for 4h (where TA matters more):

```python
TF_WEIGHT_SCALE = {
    "5m":  {"order_flow": 1.8, "orderbook": 2.0, "price_div": 2.0, "rsi": 0.5, "macd": 0.5},
    "15m": {"order_flow": 1.5, "orderbook": 1.8, "price_div": 1.5, "rsi": 0.8, "macd": 0.9},
    "1h":  {"order_flow": 1.0, "orderbook": 1.2, "price_div": 1.0, "rsi": 1.0, "macd": 1.1},
    "4h":  {"order_flow": 0.8, "orderbook": 0.8, "price_div": 0.7, "rsi": 1.2, "macd": 1.3},
}
```

### 4d. Confidence-Weighted Score Improvement
Current formula: `weighted_sum / weight_total` where both include confidence. This double-counts confidence. Consider:

```python
# Current (problematic): score = sum(w * conf * sign) / sum(w * conf)
# Better: separate weight from confidence
score = sum(w * conf * sign) / sum(w)  # normalize by raw weights only
```

---

## 5. Market Microstructure & Polymarket-Specific Insights

### 5a. Polymarket Dynamic Taker Fees (NEW - Feb 2026)
Polymarket now charges dynamic taker fees on 15-minute crypto markets:
- **Up to 3% fee** when odds are near 50/50
- **Drops toward 0%** as odds approach 0% or 100%
- Fees fund the Maker Rebates Program (redistributed to market makers in USDC daily)
- **Only affects 15-minute markets** -- other timeframes remain fee-free for now

**Impact on the bot:**
- On 15m markets, subtract the taker fee from edge before deciding to trade
- Prefer trading when odds have moved away from 50% (lower fees)
- Consider becoming a market maker on 15m markets (earn rebates instead of paying fees)
- 5m markets appear to have no taker fees yet -- may be a temporary advantage window

```python
def polymarket_taker_fee(implied_price, timeframe):
    """Estimate the Polymarket taker fee for a given market."""
    if timeframe != "15m":
        return 0.0
    # Fee peaks at 50% odds, drops to 0 at extremes
    distance_from_center = abs(implied_price - 0.5)
    # Approximate: 3% at 0.50, ~0% at 0.0/1.0
    fee = 0.03 * (1.0 - distance_from_center * 2)
    return max(fee, 0.0)
```

### 5b. The "Gabagool" Strategy (Latency Arbitrage)
The most profitable known Polymarket bot strategy:
- Monitors Binance/Coinbase for confirmed directional momentum
- When spot price has already moved significantly but Polymarket odds still lag at ~50/50
- Buys the "sure thing" side before Polymarket catches up
- 98% win rate with $4,000-$5,000 bets
- Generated $40M+ in arbitrage profits ecosystem-wide between Apr 2024 - Apr 2025

**How to implement in Garves:**
- Track short-term (1-3 minute) Binance price change
- If Binance price moved >0.1% in a direction but Polymarket implied is still near 0.50
- This represents a temporal arbitrage window
- Signal should be very high confidence (0.9+) since price has already confirmed

```python
def temporal_arb(binance_price, price_3m_ago, polymarket_implied, timeframe):
    """Detect when Binance has moved but Polymarket hasn't caught up."""
    if timeframe not in ("5m", "15m"):
        return None
    pct_move = (binance_price - price_3m_ago) / price_3m_ago
    # If Binance moved >0.1% but Polymarket is still near 0.5
    if abs(pct_move) > 0.001 and abs(polymarket_implied - 0.5) < 0.05:
        direction = "up" if pct_move > 0 else "down"
        conf = min(abs(pct_move) * 500, 0.95)  # 0.2% move -> 100% conf
        return IndicatorVote(direction=direction, confidence=conf, raw_value=pct_move * 100)
    return None
```

### 5c. Orderbook Imbalance Enhancement
Current implementation only uses top 5 levels. Research suggests:
- Use **all available levels** for imbalance calculation
- Weight closer levels exponentially more (level 1 = 5x weight of level 5)
- Track **rate of change** in orderbook imbalance -- rapid shifts are more predictive than static imbalance
- Incorporate bid/ask **count** (number of orders) not just total size -- many small orders vs one large order have different meaning

### 5d. Spread as a Signal Filter
Current `liquidity_signal` uses spread as a confidence modifier. But spread is also an information signal:
- Tight spread (< 2 cents) -> market is efficient, harder to find edge
- Wide spread (> 5 cents) -> market may be stale/illiquid, easier to find edge but harder to execute
- Rapid spread tightening -> informed traders entering, momentum likely to follow

---

## 6. Risk Management Improvements

### 6a. Kelly Criterion Position Sizing
Replace fixed `order_size_usd` with Kelly-optimal sizing:

```python
def kelly_size(edge, probability, bankroll, max_fraction=0.25):
    """Kelly criterion for binary outcomes."""
    # Binary market: pay probability p, win 1/p with probability p_true
    # Kelly fraction f* = (p_true * odds - 1) / (odds - 1)
    # Simplified for binary: f* = edge / (1 - probability)
    if probability >= 1.0 or edge <= 0:
        return 0
    kelly_f = edge / (1 - probability)
    # Half-Kelly for safety (research standard)
    half_kelly = kelly_f / 2
    clamped = min(half_kelly, max_fraction)
    return bankroll * max(clamped, 0)
```

### 6b. Daily Loss Limit
Add a daily P&L tracker. If the bot loses more than X% of bankroll in a day, stop trading until the next day.

```python
# In Config:
max_daily_loss_pct: float = 10.0  # stop after 10% daily loss

# Track in PerformanceTracker:
def daily_pnl(self) -> float:
    today = datetime.now().date()
    return sum(
        rec.edge * cfg.order_size_usd * (1 if rec.won else -1)
        for rec in self._all_records
        if datetime.fromtimestamp(rec.timestamp).date() == today and rec.resolved
    )
```

### 6c. Drawdown-Based Position Scaling
Scale position sizes inversely with recent drawdown:

```python
def drawdown_scale(recent_losses, recent_wins, base_size):
    """Reduce size during losing streaks."""
    net = recent_wins - recent_losses
    if net < -3:  # 3+ net losses
        return base_size * 0.5
    elif net < -1:
        return base_size * 0.75
    return base_size
```

### 6d. Correlation Risk
Avoid taking the same directional bet on BTC and SOL simultaneously since crypto assets are highly correlated. If already long BTC-UP, don't also go long SOL-UP on the same timeframe.

```python
def check_correlation_risk(tracker, signal):
    """Prevent correlated positions in same direction."""
    for pos in tracker.open_positions:
        if pos.direction == signal.direction and pos.direction != "neutral":
            if abs(pos.opened_at - time.time()) < 600:  # within 10 min
                return False, "Correlated position already open"
    return True, "ok"
```

### 6e. Minimum Edge After Fees
Adjust min_edge_pct to account for Polymarket fees:

```python
def adjusted_min_edge(base_min_edge, taker_fee, polymarket_winner_fee=0.02):
    """Effective minimum edge after all fees."""
    return base_min_edge + taker_fee + polymarket_winner_fee
```

Polymarket charges a 2% winner fee. So with a 3% taker fee on 15m markets, the effective minimum edge needs to be 5%+ to be profitable.

---

## 7. Timing Optimizations

### 7a. When Markets Are Most Predictable
Based on research:
- **Early in the candle** (first 20% of time): Prices haven't moved much, Polymarket odds are near 50/50. This is when the temporal arbitrage window is open. Best time for order flow-based signals.
- **Mid-candle** (20-80%): TA indicators have enough data to generate signals. Best time for ensemble-based signals.
- **Late in the candle** (last 20%): Polymarket prices are well-calibrated (95.4% accuracy 4 hours before resolution). Avoid trading unless edge is very large. Prices are near 0/1 for obvious outcomes.

**Implement:**
```python
def time_in_candle(remaining_s, total_candle_s):
    """Returns 0.0 (start of candle) to 1.0 (end of candle)."""
    elapsed = total_candle_s - remaining_s
    return elapsed / total_candle_s

# Apply:
progress = time_in_candle(remaining_s, tf.max_remaining_s)
if progress > 0.8:
    min_edge *= 2.0  # require double edge late in candle
elif progress < 0.2 and temporal_arb_signal:
    min_edge *= 0.5  # lower bar early when arb window is open
```

### 7b. Session-Based Filtering
Crypto markets have distinct periods:
- **Asian session** (00:00-08:00 UTC): Lower volume, more ranging
- **European session** (08:00-16:00 UTC): Medium volume, trend initiation
- **US session** (14:00-22:00 UTC): Highest volume, strongest trends
- **Overlap** (14:00-16:00 UTC): Most volatile, best for momentum signals

The bot should weight momentum/trend indicators higher during US/overlap sessions and mean-reversion indicators (RSI, Bollinger) higher during Asian session.

### 7c. Avoid Trading During Major News Events
BTC often spikes +-2% on CPI, FOMC, and other macro events. These spikes can trigger false TA signals. Consider:
- Maintain a simple schedule of known event times
- Pause trading for 15 minutes before/after major macro events

---

## 8. Execution & Latency Improvements

### 8a. Use FOK or IOC Orders Instead of GTC
Currently the bot places GTC (Good Till Cancelled) limit orders. For short-duration markets (5m, 15m), GTC orders may sit unfilled. Consider:
- **FOK (Fill Or Kill)**: Either fill immediately and completely, or cancel
- **IOC (Immediate Or Cancel)**: Fill what you can immediately, cancel the rest
- GTC is fine for 1h/4h markets where there's time to wait

### 8b. Aggressive Pricing for High-Edge Signals
Currently: `price = round(signal.probability, 2)`. This places a limit order at our fair value, which may not get filled.

For high-edge signals (edge > 5%):
```python
# Pay slightly more than fair value to ensure fill
if signal.edge > 0.05:
    fill_premium = min(signal.edge * 0.3, 0.03)  # give up 30% of edge for fill certainty
    price = round(signal.probability + fill_premium, 2)
```

### 8c. Batch Orders
Polymarket now supports batch order placement (up to 15 orders per call). When the bot has signals for multiple markets in the same tick, batch them together for faster execution.

### 8d. Pre-Sign Orders
The HMAC-SHA256 signing is a bottleneck (~1s per order in Python). Pre-compute signatures for common price/size combinations to reduce execution latency.

---

## 9. ML-Based Signal Enhancement

### 9a. XGBoost Meta-Learner (Recommended First ML Addition)
Use the 11 indicator values as features and train a binary classifier on historical outcomes:

```python
# Features per trade:
features = [
    rsi_value, macd_histogram, ema_gap, ha_streak,
    bollinger_position, momentum_value, vwap_diff,
    order_flow_delta, orderbook_imbalance, liquidity_imbalance,
    price_divergence,
    # Meta features:
    time_in_candle, atr_value, volume_vs_avg,
    num_indicators_agreeing, weighted_score,
]
# Target: 1 if UP won, 0 if DOWN won
```

Research shows LSTM+XGBoost hybrid achieves 90.4% accuracy for crypto price prediction. Even simple XGBoost with proper features achieves 10-15% improvement over naive ensemble.

### 9b. Online Learning with SGD
Instead of full retraining, use online logistic regression that updates after each resolved trade:

```python
from sklearn.linear_model import SGDClassifier

model = SGDClassifier(loss='log_loss', learning_rate='adaptive')
# After each resolved trade:
model.partial_fit(features, [outcome])
```

### 9c. Feature Engineering for ML
Beyond raw indicator values, add derived features:
- Indicator agreement count / total count
- Weighted score (current ensemble score)
- Rate of change of each indicator (delta from 2 candles ago)
- Cross-indicator signals (RSI oversold + MACD crossover = strong combo)
- Time features: hour of day, day of week, minutes remaining in candle

---

## 10. Polymarket API Quirks & Advantages

### 10a. Known API Behaviors
- **Rate limits:** 100 requests/minute for public API, 60 orders/minute for trading, burst of 500/s on /order endpoint
- **Batch orders:** Up to 15 orders per batch call (increased from 5 in 2025)
- **WebSocket heartbeat:** If client disconnects, all open orders are cancelled automatically
- **Market resolution:** Uses Chainlink Data Streams (sub-second pricing) for settlement of 5m/15m markets
- **Winner fee:** 2% on winning positions

### 10b. REST Fallback for Prices
When WebSocket price is not available, fetch via REST:
```
GET https://clob.polymarket.com/markets/{condition_id}
```
This returns current token prices, which can be used as fallback for `implied_up_price`.

### 10c. Maker vs Taker Advantage
- Maker orders (providing liquidity) earn rebates on 15m markets
- Consider posting limit orders slightly inside the spread to act as a maker
- This turns the 3% fee into a rebate

### 10d. Market Discovery Optimization
The current binary search + scan approach for finding markets is slow (multiple REST calls). Consider:
- Use the Gamma API for faster market search: `https://gamma-api.polymarket.com/markets?tag=crypto`
- Cache market condition IDs and only refresh every 5-10 minutes
- The current code already does this but the scan window (30,000 markets) could be reduced with better targeting

---

## 11. Priority Implementation Roadmap

### TONIGHT (Highest Impact, Lowest Effort):

1. **Fix implied price fallback** -- Add REST API fallback when WS price is None. This alone will dramatically improve edge calculation accuracy.

2. **Fix price divergence indicator** -- Rewrite to compare Binance price momentum vs Polymarket implied (not fade). Add short-term Binance price history (last 3-5 minutes) to PriceCache.

3. **Add timeframe-specific indicator parameters** -- Scale RSI, MACD, EMA, Bollinger parameters with timeframe. Biggest impact on 5m (currently using 14-period RSI on 1-min candles = only 14 minutes of history).

4. **Add Polymarket fee awareness** -- Subtract estimated taker fee and 2% winner fee from edge before risk check. Without this, the bot thinks it has edge when it doesn't on 15m markets.

5. **Add ATR volatility filter** -- Skip low-volatility signals. Use ATR for dynamic position sizing.

### THIS WEEK (Medium Impact):

6. **Implement temporal arbitrage signal** -- Track 3-minute Binance price change, generate high-confidence signal when Polymarket lags. This is the known highest-edge strategy.

7. **Add CVD divergence** -- Upgrade order_flow_delta to detect CVD divergence patterns (price vs volume divergence).

8. **Adaptive weights** -- Track per-indicator accuracy over a rolling window, scale weights by recent performance.

9. **Add StochRSI and KDJ** -- More sensitive momentum indicators for short timeframes.

10. **Kelly criterion sizing** -- Replace fixed order size with edge-proportional sizing (half-Kelly).

### NEXT WEEK (Higher Effort, Large Impact):

11. **XGBoost meta-learner** -- Train on accumulated trade history. Use indicator values + meta-features as input.

12. **Regime detection** -- Trending vs ranging weight profiles based on ATR ratio.

13. **Correlation risk management** -- Prevent correlated BTC/ETH/SOL positions.

14. **Session-based filtering** -- Different indicator weights for Asian/European/US sessions.

15. **Maker order strategy** -- On 15m markets, post limit orders as maker to earn rebates instead of paying 3% taker fee.

---

## Sources

- [Polymarket HFT: AI Arbitrage and Mispricing (QuantVPS)](https://www.quantvps.com/blog/polymarket-hft-traders-use-ai-arbitrage-mispricing)
- [Polymarket Strategies: 2026 Guide (CryptoNews)](https://cryptonews.com/cryptocurrency/polymarket-strategies/)
- [Inside the Mind of a Polymarket BOT (CoinsBench)](https://coinsbench.com/inside-the-mind-of-a-polymarket-bot-3184e9481f0a)
- [Automated Trading on Polymarket (QuantVPS)](https://www.quantvps.com/blog/automated-trading-polymarket)
- [Arbitrage Bots Dominate Polymarket (Yahoo Finance)](https://finance.yahoo.com/news/arbitrage-bots-dominate-polymarket-millions-100000888.html)
- [7 Polymarket Arbitrage Strategies (Medium/Dexoryn)](https://medium.com/@dexoryn/7-polymarket-arbitrage-strategies-every-trader-should-know-6d74b615b86e)
- [Systematic Edges in Prediction Markets (QuantPedia)](https://quantpedia.com/systematic-edges-in-prediction-markets/)
- [Mathematical Execution Behind Prediction Market Alpha (Substack)](https://navnoorbawa.substack.com/p/the-mathematical-execution-behind)
- [CryptoPulse: Short-Term Crypto Forecasting with Dual-Prediction (arXiv)](https://arxiv.org/html/2502.19349v3)
- [Short-term Crypto Price Forecasting Based on News Headlines (Frontiers)](https://www.frontiersin.org/journals/blockchain/articles/10.3389/fbloc.2025.1627769/full)
- [LSTM+XGBoost Crypto Price Prediction (arXiv)](https://arxiv.org/html/2506.22055v1)
- [How Latency Impacts Polymarket Bot Performance (QuantVPS)](https://www.quantvps.com/blog/how-latency-impacts-polymarket-trading-performance)
- [Polymarket Introduces Dynamic Fees (FinanceMagnates)](https://www.financemagnates.com/cryptocurrency/polymarket-introduces-dynamic-fees-to-curb-latency-arbitrage-in-short-term-crypto-markets/)
- [Polymarket Taker Fees on 15-Minute Markets (TradingView)](https://www.tradingview.com/news/cointelegraph:e59c32089094b:0-polymarket-quietly-introduces-taker-fees-on-15-minute-crypto-markets/)
- [Polymarket Taker Fees (The Block)](https://www.theblock.co/post/384461/polymarket-adds-taker-fees-to-15-minute-crypto-markets-to-fund-liquidity-rebates)
- [CVD Trading Strategy (Bookmap)](https://bookmap.com/blog/how-cumulative-volume-delta-transform-your-trading-strategy)
- [Order Flow Trading Guide (CMC Markets)](https://www.cmcmarkets.com/en/trading-strategy/order-flow-trading)
- [Polymarket 5-Minute Markets (CoinMarketCap)](https://coinmarketcap.com/academy/article/polymarket-debuts-5-minute-bitcoin-prediction-markets-with-instant-settlement)
- [Kelly Criterion for Trading Systems (QuantConnect)](https://www.quantconnect.com/research/18312/kelly-criterion-applications-in-trading-systems/)
- [Adaptive Kelly Criterion (TradingOnramp)](https://tradingonramp.com/my-crypto-risk-reassessed-adopting-the-adaptive-kelly-criterion/)
- [RSI + MACD Divergence Mastering (Kavout)](https://www.kavout.com/market-lens/unlock-peak-profits-mastering-rsi-and-macd-divergence-in-crypto-and-forex)
- [MACD, RSI, KDJ for Crypto 2026 (Gate.io)](https://dex.gate.com/crypto-wiki/article/how-to-use-macd-rsi-and-kdj-technical-indicators-for-crypto-price-prediction-in-2026-20260207)
- [Polymarket CLOB Documentation](https://docs.polymarket.com/developers/CLOB/introduction)
- [Polymarket Maker Rebates Program](https://docs.polymarket.com/developers/market-makers/maker-rebates-program)
- [Polyfill-rs: Fastest Polymarket Rust Client](https://github.com/floor-licker/polyfill-rs)
- [Polymarket-Kalshi BTC Arbitrage Bot (GitHub)](https://github.com/CarlosIbCu/polymarket-kalshi-btc-arbitrage-bot)
- [ATR Volatility Indicator Guide (BingX)](https://bingx.com/en/learn/article/what-is-average-true-range-atr-volatility-indicator-in-crypto-trading)
- [Polymarket Chainlink 5-Minute Markets (CryptoTimes)](https://www.cryptotimes.io/2026/02/14/polymarket-launches-5-minute-crypto-trades-via-chainlink/)
