"""OdinBrain — LLM-powered trade analysis replacing rule-based SMC engine.

Architecture:
  1. Data filter (no LLM): CoinGlass regime + price action check per symbol
  2. Shared LLM router (local Qwen default) full analysis for filtered symbols → JSON decision
  3. Parse JSON into TradeSignal with validation

The brain thinks like a trader: structure + regime + macro must align.
Safety rails (PortfolioGuard, CircuitBreaker) remain untouched downstream.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Optional

import pandas as pd

from odin.config import OdinConfig
from odin.strategy.signals import TradeSignal

log = logging.getLogger("odin.llm_brain")

# ── System Prompt (Odin's Persona) ──

SYSTEM_PROMPT = """\
You are ODIN — the All-Seeing Regime God. Professional crypto futures swing \
trader on Hyperliquid.

## Identity
- Smart Money Concepts: structure (BOS, CHOCH), order blocks, FVGs, liquidity
- Macro-aware: SPX + NASDAQ bullish = crypto bullish bias. ALWAYS check
- Liquidity hunter: trade the direction smart money moves AFTER sweeping stops
- High-conviction only: structure + regime + macro must align
- Multi-TF: Daily = bias, 4H = structure, 15m = entry timing
- Dual mode: SCALP (2-20 min, tight SL, quick profit) + SWING (hours/days, wider SL)

## Critical Rules
1. NEVER flip on one 15m candle after clear 4H breakout. Pullbacks are retests.
2. 4H close above resistance = BULLISH until multiple 4H candles close back below.
3. Price above bullish OB = bullish. One wick doesn't invalidate — need a CLOSE.
4. Regime BULL + macro confirms = strong LONG bias. SMC pullbacks = entries.
5. Volume confirms: big moves on high volume are real. Low vol pullbacks = noise.
6. Learn from mistakes. Your past lessons are provided — apply them.

## Decision Framework (Step by Step)
1. NEWS: Read headlines first. Tariffs, Fed, geopolitical = MASSIVE impact on crypto. \
A 7% bounce during tariff fears could be a dead cat bounce. News overrides technicals.
2. MACRO: SPX/NASDAQ bullish? VIX calm? Gold/silver ripping = flight to safety = bearish crypto.
3. REGIME: CoinGlass? Funding? OI? L/S ratio?
4. STRUCTURE: Daily trend? 4H BOS/CHOCH? Key OBs? 15m trigger?
5. CONFLUENCE: 2/3 macro+regime+structure agree = tradeable. 1/3 = flat.
6. ENTRY: Nearest OB/FVG? Logical SL below structure?
7. CONVICTION: 0-100 honest. 70+ = high. 50-69 = moderate. <50 = don't trade.
8. TRADE TYPE: Decide if this is a SCALP or SWING:
   - SCALP: Price at OB/FVG zone, clear 15m trigger, quick 0.3-1% move expected.
     Tight SL (0.3-1.5%), TP at 0.5-1.5%. Hold 2-20 minutes. Big notional, fast profit.
   - SWING: Multi-TF alignment, regime confirms, hold for hours/days.
     Wider SL (1-5%), TP at 2-5%+. Smaller notional, bigger R:R.
9. RISK SIZING: Decide risk_usd ($5-$100) based on conviction + setup quality:
   - A+ setup (80+ conviction, 3/3 alignment, clean structure): $60-100
   - Good setup (65-79, 2/3 alignment): $30-60
   - Moderate setup (50-64, mixed signals): $5-25
   - Scalps: higher risk (quick resolution). Swings: moderate risk (longer exposure).

## News Rules
- If negative macro news (tariffs, rate hikes, sanctions): reduce conviction by 15-25 points
- If market is bouncing on BAD news: be skeptical — dead cat bounces are traps
- If positive news (rate cuts, stimulus, regulatory clarity): boost confidence in LONGs
- No news = rely on technicals. Bad news + bullish technicals = FLAT or reduced size.

Respond with ONLY a JSON object. No other text."""


class OdinBrain:
    """LLM-powered trade analyst — replaces rule-based SMC + conviction engine."""

    def __init__(self, cfg: OdinConfig):
        self._cfg = cfg
        self._lessons: list[str] = []
        self.last_candle_summary: str = ""
        self._last_reasoning: list[str] = []
        log.info("[LLM_BRAIN] Initialized | model=%s min_conv=%d temp=%.2f",
                 cfg.llm_analyst_model, cfg.llm_min_conviction, cfg.llm_temperature)

    def set_lessons(self, lessons: list[str]) -> None:
        """Inject lessons from ReflectionEngine."""
        self._lessons = lessons

    # ── Data Filter (No LLM — instant, free) ──

    def screen(
        self,
        symbols: list[tuple[str, str]],
        candle_dfs: dict[str, pd.DataFrame],
        regime: object,
        macro: object,
    ) -> list[str]:
        """Filter symbols worth sending to LLM. Returns list of symbol strings.

        A symbol passes if ANY of:
        1. CoinGlass regime score >= threshold (bull or bear — direction exists)
        2. Price moved > move_pct% in last 4H candle
        3. Volume > vol_mult × average
        4. Price within 1% of a known OB/FVG zone (checked by caller)

        If no regime data → pass ALL (safe fallback).
        """
        if not regime or not hasattr(regime, "opportunities"):
            log.info("[FILTER] No regime data — passing all %d symbols", len(symbols))
            return [sym for sym, _ in symbols]

        passed: list[str] = []
        for sym, _direction in symbols:
            reasons: list[str] = []
            bare = sym.replace("USDT", "")

            # 1. CoinGlass regime score
            for opp in regime.opportunities:
                if opp.symbol == bare and opp.score >= self._cfg.screen_regime_threshold:
                    reasons.append(f"regime={opp.score:.0f}")
                    break

            # 2. Price movement in recent candles
            mtf_df = candle_dfs.get(sym)
            if mtf_df is not None and len(mtf_df) >= 2:
                last_close = float(mtf_df["close"].iloc[-1])
                prev_close = float(mtf_df["close"].iloc[-2])
                if prev_close > 0:
                    move_pct = abs(last_close - prev_close) / prev_close * 100
                    if move_pct >= self._cfg.screen_move_pct:
                        reasons.append(f"move={move_pct:.1f}%")

                # 3. Volume spike
                if len(mtf_df) >= 10:
                    avg_vol = float(mtf_df["volume"].iloc[-10:].mean())
                    last_vol = float(mtf_df["volume"].iloc[-1])
                    if avg_vol > 0 and last_vol >= avg_vol * self._cfg.screen_volume_mult:
                        reasons.append(f"vol={last_vol / avg_vol:.1f}x")

            if reasons:
                passed.append(sym)
                log.info("[FILTER] PASS %s: %s", sym, ", ".join(reasons))
            else:
                log.debug("[FILTER] SKIP %s: no triggers", sym)

        if not passed and symbols:
            # If filter killed everything, pass the top regime pick
            top = symbols[0][0]
            passed.append(top)
            log.info("[FILTER] All filtered — forcing top pick: %s", top)

        return passed

    # ── Analyst (shared LLM router — local Qwen default) ──

    def analyze(
        self,
        symbol: str,
        htf_df: pd.DataFrame,
        mtf_df: pd.DataFrame,
        ltf_df: pd.DataFrame,
        current_price: float,
        regime: object,
        macro: object,
        zones: list,
        brotherhood: object,
        balance: float,
        open_positions: int,
    ) -> Optional[TradeSignal]:
        """Run full LLM analysis for one symbol. Returns TradeSignal or None."""
        # Build the data prompt
        user_prompt = self._build_prompt(
            symbol, htf_df, mtf_df, ltf_df, current_price,
            regime, macro, zones, brotherhood, balance, open_positions,
        )

        # Route through shared LLM client (local Qwen → cloud fallback)
        try:
            from shared.llm_client import llm_call
            raw = llm_call(
                system=SYSTEM_PROMPT,
                user=user_prompt,
                agent="odin",
                task_type="analyst",
                max_tokens=self._cfg.llm_max_tokens_analyze,
                temperature=self._cfg.llm_temperature,
            )
        except Exception as e:
            log.error("[LLM_BRAIN] LLM call failed: %s", str(e)[:200])
            return None

        if not raw:
            log.warning("[LLM_BRAIN] Empty response from LLM")
            return None

        log.info("[LLM_BRAIN] %s raw response (%d chars): %.200s...",
                 symbol, len(raw), raw)

        # Write full response for debugging
        try:
            from pathlib import Path
            debug_file = Path(self._cfg.data_dir) / "llm_last_response.json"
            debug_file.write_text(raw)
        except Exception:
            pass

        # Parse and validate
        signal = self._parse_decision(raw, symbol, current_price, macro)
        if signal:
            self._last_reasoning = signal.reasons
        return signal

    def _build_prompt(
        self,
        symbol: str,
        htf_df: pd.DataFrame,
        mtf_df: pd.DataFrame,
        ltf_df: pd.DataFrame,
        current_price: float,
        regime: object,
        macro: object,
        zones: list,
        brotherhood: object,
        balance: float,
        open_positions: int,
    ) -> str:
        """Format all data into a single text prompt for the analyst."""
        parts: list[str] = []

        parts.append(f"=== {symbol} Analysis @ ${current_price:,.2f} ===")
        parts.append(f"Account: ${balance:,.0f} | Open positions: {open_positions}/2")
        parts.append("")

        # Candles (3 timeframes)
        parts.append(_format_candles(htf_df, self._cfg.htf, n=15))
        parts.append(_format_candles(mtf_df, self._cfg.mtf, n=15))
        parts.append(_format_candles(ltf_df, self._cfg.ltf, n=15))

        # Regime (CoinGlass)
        parts.append(_format_regime(regime, symbol))

        # Macro (SPY/VIX/BTC.D/USDT.D)
        parts.append(_format_macro(macro))

        # Structure zones (OBs/FVGs near price)
        parts.append(_format_zones(zones, current_price))

        # Funding income opportunity
        parts.append(_format_funding(regime, symbol))

        # Garves alignment
        parts.append(_format_garves(brotherhood, symbol))

        # Atlas news sentiment (what's driving the market)
        parts.append(_format_news(brotherhood, symbol))

        # Lessons
        if self._lessons:
            parts.append(_format_lessons(self._lessons))

        prompt = "\n".join(parts)

        # Cache for reflection logging
        self.last_candle_summary = _format_candles(mtf_df, self._cfg.mtf, n=5)

        return prompt

    def _parse_decision(
        self,
        raw_text: str,
        symbol: str,
        current_price: float,
        macro: object,
    ) -> Optional[TradeSignal]:
        """Extract JSON from LLM response, validate, return TradeSignal or None."""
        data = _extract_json(raw_text)
        if not data:
            log.warning("[LLM_BRAIN] Failed to parse JSON from response")
            return None

        # Required fields — handle multiple naming conventions from LLM
        action = (data.get("action") or data.get("decision") or data.get("direction") or "").upper()
        conviction = data.get("conviction") or data.get("conviction_score") or data.get("confidence") or 0
        stop_loss = data.get("stop_loss") or 0
        tp1 = data.get("take_profit_1") or data.get("take_profit") or 0
        tp2 = data.get("take_profit_2") or 0
        entry = data.get("entry_price") or data.get("entry") or current_price
        rr = data.get("risk_reward") or 0
        llm_risk = data.get("risk_usd") or data.get("risk_amount") or data.get("position_size_usd") or 0
        reasoning = data.get("reasoning", [])
        # If reasoning is a dict (structured), flatten to list of strings
        if isinstance(reasoning, dict):
            reasoning = [f"{k}: {v}" if isinstance(v, str) else f"{k}: {json.dumps(v)}"
                         for k, v in reasoning.items()]

        # Validate action
        if action not in ("LONG", "SHORT", "FLAT"):
            log.warning("[LLM_BRAIN] Invalid action: %s", action)
            return None

        # FLAT or low conviction → no trade
        if action == "FLAT":
            log.info("[LLM_BRAIN] %s → FLAT (conviction=%d)", symbol, conviction)
            return None

        if not isinstance(conviction, (int, float)):
            log.warning("[LLM_BRAIN] Invalid conviction type: %s", type(conviction))
            return None
        conviction = int(min(max(conviction, 0), 100))

        if conviction < self._cfg.llm_min_conviction:
            log.info("[LLM_BRAIN] %s → %s but conv=%d < min=%d",
                     symbol, action, conviction, self._cfg.llm_min_conviction)
            return None

        # Validate stop loss exists
        if stop_loss <= 0:
            log.warning("[LLM_BRAIN] No stop loss provided")
            return None

        # Price consistency: LONG → SL < entry < TP; SHORT → reverse
        if action == "LONG":
            if not (stop_loss < entry):
                log.warning("[LLM_BRAIN] LONG but SL(%.2f) >= entry(%.2f)", stop_loss, entry)
                return None
            if tp1 > 0 and tp1 <= entry:
                log.warning("[LLM_BRAIN] LONG but TP1(%.2f) <= entry(%.2f)", tp1, entry)
                return None
        else:  # SHORT
            if not (stop_loss > entry):
                log.warning("[LLM_BRAIN] SHORT but SL(%.2f) <= entry(%.2f)", stop_loss, entry)
                return None
            if tp1 > 0 and tp1 >= entry:
                log.warning("[LLM_BRAIN] SHORT but TP1(%.2f) >= entry(%.2f)", tp1, entry)
                return None

        # Determine trade type from LLM output
        trade_type = (data.get("trade_type") or data.get("type") or "swing").lower()
        if trade_type not in ("scalp", "swing"):
            trade_type = "swing"

        # SL distance bounds depend on trade type
        sl_dist_pct = abs(stop_loss - entry) / entry * 100
        if trade_type == "scalp":
            sl_min, sl_max = 0.2, 2.0
        else:
            sl_min, sl_max = 0.5, 5.0
        if sl_dist_pct < sl_min or sl_dist_pct > sl_max:
            log.warning("[LLM_BRAIN] SL distance %.2f%% outside %.1f-%.1f%% range (%s)",
                        sl_dist_pct, sl_min, sl_max, trade_type)
            return None

        # Compute TP fallback BEFORE R:R check (so R:R can use fallback TP)
        if tp1 <= 0:
            sl_dist = abs(entry - stop_loss)
            tp1 = entry + sl_dist * 2 * (1 if action == "LONG" else -1)

        # Calculate R:R if not provided
        if rr <= 0 and tp1 > 0:
            sl_dist = abs(entry - stop_loss)
            tp_dist = abs(tp1 - entry)
            rr = round(tp_dist / sl_dist, 2) if sl_dist > 0 else 0

        # Minimum R:R (scalps can be lower since they're fast)
        min_rr = 1.0 if trade_type == "scalp" else 1.5
        if rr < min_rr:
            log.info("[LLM_BRAIN] %s R:R=%.1f too low (min=%.1f for %s)",
                     symbol, rr, min_rr, trade_type)
            return None

        # Clamp LLM risk to valid range ($5-$100)
        max_risk = self._cfg.llm_max_risk_usd
        min_risk = self._cfg.llm_min_risk_usd
        if isinstance(llm_risk, (int, float)) and llm_risk > 0:
            llm_risk = float(min(max(llm_risk, min_risk), max_risk))
        else:
            # Default: scale from conviction
            llm_risk = max(min_risk, min(max_risk, conviction * 1.0))

        # Compute ATR from mtf if available (not passed directly, use entry context)
        atr_value = abs(entry - stop_loss)  # Rough proxy

        # Determine macro context
        macro_regime = "neutral"
        macro_score = 50
        if macro:
            macro_regime = getattr(macro, "regime", type("", (), {"value": "neutral"})()).value
            macro_score = getattr(macro, "score", 50)

        # Build TradeSignal
        signal = TradeSignal(
            symbol=symbol,
            direction=action,
            trade_type=trade_type,
            confidence=conviction / 100.0,
            entry_price=entry,
            entry_zone_top=entry,
            entry_zone_bottom=entry,
            stop_loss=stop_loss,
            take_profit_1=tp1,
            take_profit_2=tp2 if tp2 > 0 else 0,
            risk_reward=rr,
            macro_multiplier=1.0,
            macro_regime=macro_regime,
            macro_score=macro_score,
            conviction_score=float(conviction),
            conviction_breakdown={
                "llm_conviction": conviction,
                "macro_alignment": data.get("macro_alignment", ""),
                "regime_alignment": data.get("regime_alignment", ""),
                "structure_bias": data.get("structure_bias", ""),
                "tf_agreement": data.get("timeframe_agreement", ""),
                "volume_confirms": data.get("volume_confirms", False),
            },
            risk_multiplier=conviction / 100.0,
            llm_risk_usd=llm_risk,
            atr=atr_value,
            entry_reason=f"LLM Brain: {action} conv={conviction} risk=${llm_risk:.0f}",
            reasons=reasoning if isinstance(reasoning, list) else [str(reasoning)],
        )

        log.info("[LLM_BRAIN] %s → %s %s conv=%d risk=$%.0f SL=$%.2f TP=$%.2f R:R=%.1f",
                 symbol, action, trade_type.upper(), conviction, llm_risk, stop_loss, tp1, rr)
        return signal

    @property
    def last_reasoning(self) -> list[str]:
        return self._last_reasoning


# ── Data Formatting Helpers ──

def _format_candles(df: pd.DataFrame, tf_label: str, n: int = 15) -> str:
    """Format OHLCV table + EMAs + ATR for the prompt."""
    if df is None or df.empty:
        return f"=== {tf_label} Candles ===\nNo data\n"

    tail = df.tail(n).copy()
    lines = [f"=== {tf_label} Candles (last {len(tail)}) ==="]
    lines.append("  Open       High       Low        Close      Volume")

    for _, row in tail.iterrows():
        lines.append(
            f"  {row['open']:<10.2f} {row['high']:<10.2f} "
            f"{row['low']:<10.2f} {row['close']:<10.2f} {row['volume']:<10.0f}"
        )

    # EMAs
    if len(df) >= 50:
        ema20 = float(df["close"].ewm(span=20).mean().iloc[-1])
        ema50 = float(df["close"].ewm(span=50).mean().iloc[-1])
        lines.append(f"EMA20={ema20:.2f}  EMA50={ema50:.2f}")
        if len(df) >= 200:
            ema200 = float(df["close"].ewm(span=200).mean().iloc[-1])
            lines.append(f"EMA200={ema200:.2f}")

    # ATR
    if len(df) >= 14:
        tr = pd.concat([
            df["high"] - df["low"],
            (df["high"] - df["close"].shift()).abs(),
            (df["low"] - df["close"].shift()).abs(),
        ], axis=1).max(axis=1)
        atr = float(tr.rolling(14).mean().iloc[-1])
        lines.append(f"ATR14={atr:.2f}")

    lines.append("")
    return "\n".join(lines)


def _format_regime(regime: object, symbol: str) -> str:
    """Format CoinGlass regime summary."""
    if not regime or not hasattr(regime, "regime"):
        return "=== Regime ===\nNo CoinGlass data\n"

    lines = ["=== CoinGlass Regime ==="]
    lines.append(f"Global: {regime.regime.value} (score={regime.global_score:.0f})")
    lines.append(f"Direction bias: {regime.direction_bias.value}")

    bare = symbol.replace("USDT", "")
    for opp in getattr(regime, "opportunities", []):
        if opp.symbol == bare:
            lines.append(
                f"{bare}: score={opp.score:.0f} dir={opp.direction.value} "
                f"funding={opp.funding_signal:+.2f} OI={opp.oi_signal:+.2f} "
                f"LS={opp.ls_signal:+.2f} liq={opp.liq_signal:+.2f}"
            )
            if opp.reasons:
                lines.append(f"  Reasons: {'; '.join(opp.reasons[:3])}")
            break

    lines.append("")
    return "\n".join(lines)


def _format_funding(regime: object, symbol: str) -> str:
    """Format funding income opportunity for the analyst."""
    if not regime or not hasattr(regime, "funding_arbs"):
        return ""

    arbs = getattr(regime, "funding_arbs", {})
    if not arbs:
        return ""

    active = [(sym, fa) for sym, fa in arbs.items() if fa.active]
    if not active:
        return ""

    bare = symbol.replace("USDT", "")
    lines = ["=== FUNDING INCOME OPPORTUNITY ==="]
    for sym, fa in active:
        side_label = "LONGs collect" if fa.collect_side == "LONG" else "SHORTs collect"
        lines.append(
            f"- {sym}: Funding rate {fa.rate_8h:+.4%}/8h -> {side_label} "
            f"${fa.daily_income_est:.2f}/day (annual {fa.annualized_pct:.1f}%)"
        )

    this_arb = arbs.get(bare)
    if this_arb and this_arb.active:
        lines.append(
            f"- PREFERENCE: If your analysis is neutral/weak, lean toward "
            f"{this_arb.collect_side} to collect funding income"
        )
    lines.append("")
    return "\n".join(lines)


def _format_macro(macro: object) -> str:
    """Format SPY/VIX/BTC.D/USDT.D macro summary."""
    if not macro:
        return "=== Macro ===\nNo macro data\n"

    lines = ["=== Macro (SPY/VIX/BTC.D/USDT.D) ==="]
    lines.append(f"Regime: {macro.regime.value} (score={macro.score})")
    lines.append(f"SPY: ${macro.spy_price:.2f} ({macro.spy_change_pct:+.1f}%)")
    lines.append(f"  Above 50MA: {macro.spy_above_50ma} | Above 200MA: {macro.spy_above_200ma}")
    lines.append(f"VIX: {macro.vix:.1f}")
    lines.append(f"BTC.D: {macro.btc_dominance:.1f}% ({macro.btc_d_trend})")
    lines.append(f"USDT.D: {macro.usdt_dominance:.1f}% ({macro.usdt_d_trend})")
    if macro.reasons:
        lines.append(f"Macro reasons: {'; '.join(macro.reasons[:3])}")
    lines.append("")
    return "\n".join(lines)


def _format_zones(zones: list, current_price: float) -> str:
    """Format active OBs/FVGs near current price."""
    if not zones:
        return "=== Structure Zones ===\nNo active OB/FVG zones nearby\n"

    lines = ["=== Structure Zones (OBs/FVGs near price) ==="]
    for z in zones[:6]:
        dist_pct = (z.price_level - current_price) / current_price * 100
        lines.append(
            f"  {z.zone_type} {z.direction} @ ${z.price_level:.2f} "
            f"(strength={z.strength:.0f}, dist={dist_pct:+.2f}%)"
        )
    lines.append("")
    return "\n".join(lines)


def _format_garves(brotherhood: object, symbol: str) -> str:
    """Format Garves alignment info."""
    if not brotherhood:
        return "=== Garves ===\nNo data\n"

    try:
        # Use the alignment method if available
        alignment = brotherhood.get_brother_alignment(symbol, "LONG")
        garves_dir = alignment.get("garves_direction", "N/A")
        garves_wr = alignment.get("garves_wr", "N/A")
        reason = alignment.get("reason", "no_data")

        lines = ["=== Garves (Brother Agent) ==="]
        lines.append(f"Direction: {garves_dir} | WR: {garves_wr}% | Status: {reason}")
        lines.append("")
        return "\n".join(lines)
    except Exception:
        return "=== Garves ===\nNo data\n"


def _format_news(brotherhood: object, symbol: str) -> str:
    """Format Atlas news sentiment + headlines for context."""
    if not brotherhood:
        return "=== News / Sentiment ===\nNo data\n"

    try:
        sentiment = brotherhood.get_atlas_sentiment(symbol)
        direction = sentiment.get("direction", "NEUTRAL")
        score = sentiment.get("score", 0)
        headlines = sentiment.get("headlines", [])

        # Also read the full news file for broader market context
        import json
        from pathlib import Path
        news_file = Path.home() / "atlas" / "data" / "news_sentiment.json"
        all_headlines: list[str] = []
        if news_file.exists():
            try:
                data = json.loads(news_file.read_text())
                # Get headlines from all assets for macro context
                for asset, info in data.items():
                    for h in info.get("headlines", [])[:2]:
                        title = h if isinstance(h, str) else h.get("title", "")
                        if title:
                            all_headlines.append(f"[{asset}] {title}")
            except Exception:
                pass

        lines = ["=== News & Sentiment (Atlas Intelligence) ==="]
        bare = symbol.replace("USDT", "")
        lines.append(f"{bare} sentiment: {direction} (score={score:+.2f})")

        if headlines:
            lines.append(f"{bare} headlines:")
            for h in headlines[:3]:
                title = h if isinstance(h, str) else h.get("title", "")
                if title:
                    lines.append(f"  - {title}")

        # Broader market headlines (tariffs, Fed, geopolitical)
        if all_headlines:
            lines.append("Market-wide headlines:")
            seen = set()
            for h in all_headlines[:6]:
                if h not in seen:
                    seen.add(h)
                    lines.append(f"  - {h}")

        if not headlines and not all_headlines:
            lines.append("No recent news available")

        lines.append("")
        return "\n".join(lines)
    except Exception:
        return "=== News / Sentiment ===\nNo data\n"


def _format_lessons(lessons: list[str]) -> str:
    """Format lessons for injection into prompt."""
    if not lessons:
        return ""
    lines = ["=== Your Past Lessons (Apply These) ==="]
    for i, lesson in enumerate(lessons, 1):
        lines.append(f"{i}. {lesson}")
    lines.append("")
    return "\n".join(lines)


def _extract_json(text: str) -> Optional[dict]:
    """Extract JSON from LLM response, handling markdown fences and trailing text."""
    if not text:
        return None

    # Try direct parse first
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try extracting from markdown code fences
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Try finding first { ... } block
    brace_start = text.find("{")
    if brace_start >= 0:
        depth = 0
        for i in range(brace_start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[brace_start:i + 1])
                    except json.JSONDecodeError:
                        break

    return None
