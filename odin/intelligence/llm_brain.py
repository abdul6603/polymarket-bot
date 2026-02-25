"""OdinBrain — LLM-powered trade analysis replacing rule-based SMC engine.

Architecture:
  1. Data filter (no LLM): CoinGlass regime + price action check per symbol
  2. Claude Opus 4.6 full analysis for filtered symbols → JSON decision
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
trader on Hyperliquid, $200 capital.

## Identity
- Smart Money Concepts: structure (BOS, CHOCH), order blocks, FVGs, liquidity
- Macro-aware: SPX + NASDAQ bullish = crypto bullish bias. ALWAYS check
- Liquidity hunter: trade the direction smart money moves AFTER sweeping stops
- High-conviction only: structure + regime + macro must align
- Multi-TF: Daily = bias, 4H = structure, 15m = entry timing

## Critical Rules
1. NEVER flip on one 15m candle after clear 4H breakout. Pullbacks are retests.
2. 4H close above resistance = BULLISH until multiple 4H candles close back below.
3. Price above bullish OB = bullish. One wick doesn't invalidate — need a CLOSE.
4. Regime BULL + macro confirms = strong LONG bias. SMC pullbacks = entries.
5. Volume confirms: big moves on high volume are real. Low vol pullbacks = noise.
6. Learn from mistakes. Your past lessons are provided — apply them.

## Decision Framework (Step by Step)
1. MACRO: SPX/NASDAQ bullish? VIX calm? Base bias.
2. REGIME: CoinGlass? Funding? OI? L/S ratio?
3. STRUCTURE: Daily trend? 4H BOS/CHOCH? Key OBs? 15m trigger?
4. CONFLUENCE: 2/3 macro+regime+structure agree = tradeable. 1/3 = flat.
5. ENTRY: Nearest OB/FVG? Logical SL below structure?
6. CONVICTION: 0-100 honest. 70+ = high. 50-69 = moderate. <50 = don't trade.

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
        """Filter symbols worth sending to Opus. Returns list of symbol strings.

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

    # ── Analyst (Claude Opus 4.6 — the brain) ──

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

        # Call Claude Opus 4.6 directly (not through shared router — needs specific model)
        try:
            raw, in_tok, out_tok = _call_opus(
                model=self._cfg.llm_analyst_model,
                system=SYSTEM_PROMPT,
                user=user_prompt,
                max_tokens=self._cfg.llm_max_tokens_analyze,
                temperature=self._cfg.llm_temperature,
            )
            # Log cost via shared tracker
            try:
                from shared.llm_client import _log_cost
                # Opus pricing: $15/M input, $75/M output
                cost = (in_tok * 15.0 + out_tok * 75.0) / 1_000_000
                _log_cost(
                    agent="odin", provider="anthropic", model=self._cfg.llm_analyst_model,
                    task_type="analyst", input_tokens=in_tok, output_tokens=out_tok,
                    latency_ms=0, cost_usd=cost,
                )
            except Exception:
                pass
        except Exception as e:
            log.error("[LLM_BRAIN] Opus call failed: %s", str(e)[:200])
            return None

        if not raw:
            log.warning("[LLM_BRAIN] Empty response from Opus")
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

        # Garves alignment
        parts.append(_format_garves(brotherhood, symbol))

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

        # Required fields
        action = data.get("action", "").upper()
        conviction = data.get("conviction", 0)
        stop_loss = data.get("stop_loss", 0)
        tp1 = data.get("take_profit_1", 0)
        tp2 = data.get("take_profit_2", 0)
        entry = data.get("entry_price", current_price)
        rr = data.get("risk_reward", 0)
        reasoning = data.get("reasoning", [])

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

        # SL distance: 0.5% to 5% from entry
        sl_dist_pct = abs(stop_loss - entry) / entry * 100
        if sl_dist_pct < 0.5 or sl_dist_pct > 5.0:
            log.warning("[LLM_BRAIN] SL distance %.2f%% outside 0.5-5.0%% range", sl_dist_pct)
            return None

        # Calculate R:R if not provided
        if rr <= 0 and tp1 > 0:
            sl_dist = abs(entry - stop_loss)
            tp_dist = abs(tp1 - entry)
            rr = round(tp_dist / sl_dist, 2) if sl_dist > 0 else 0

        # Minimum R:R
        if rr < 1.5:
            log.info("[LLM_BRAIN] %s R:R=%.1f too low", symbol, rr)
            return None

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
            confidence=conviction / 100.0,
            entry_price=entry,
            entry_zone_top=entry,
            entry_zone_bottom=entry,
            stop_loss=stop_loss,
            take_profit_1=tp1 if tp1 > 0 else entry + (entry - stop_loss) * 2 * (1 if action == "LONG" else -1),
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
            atr=atr_value,
            entry_reason=f"LLM Brain: {action} conv={conviction}",
            reasons=reasoning if isinstance(reasoning, list) else [str(reasoning)],
        )

        log.info("[LLM_BRAIN] %s → %s conv=%d SL=$%.2f TP=$%.2f R:R=%.1f",
                 symbol, action, conviction, stop_loss, tp1, rr)
        return signal

    @property
    def last_reasoning(self) -> list[str]:
        return self._last_reasoning


# ── Anthropic Direct Call (Opus 4.6) ──

def _call_opus(
    model: str, system: str, user: str,
    max_tokens: int, temperature: float,
) -> tuple[str, int, int]:
    """Call Anthropic Claude Opus directly. Returns (text, in_tokens, out_tokens)."""
    import os
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        temperature=temperature,
    )
    text = resp.content[0].text.strip()
    return text, resp.usage.input_tokens, resp.usage.output_tokens


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
