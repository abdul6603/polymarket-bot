"""OmniCoin Analyzer — deep analysis of any coin using ALL Odin skills.

When triggered (via dashboard button or command), runs every available
skill on the target coin and produces a comprehensive report with
Odin's honest assessment and genuine 1-10 confidence score.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger("odin.skills.omnicoin")
ET = ZoneInfo("America/New_York")


@dataclass
class OmniCoinReport:
    """Full analysis report for a coin."""
    symbol: str
    confidence: int                # 1-10 honest score
    bias: str                      # LONG, SHORT, NEUTRAL
    summary: str                   # Odin's honest take
    sections: dict = field(default_factory=dict)
    timestamp: float = 0.0
    analysis_time_ms: int = 0

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "confidence": self.confidence,
            "bias": self.bias,
            "summary": self.summary,
            "sections": self.sections,
            "timestamp": self.timestamp,
            "timestamp_et": datetime.fromtimestamp(self.timestamp, ET).strftime(
                "%Y-%m-%d %H:%M:%S"
            ) if self.timestamp else "",
            "analysis_time_ms": self.analysis_time_ms,
        }


class OmniCoinAnalyzer:
    """Runs ALL Odin skills on any coin for comprehensive analysis."""

    def __init__(self, skills_registry=None, data_dir: Path | None = None):
        self._skills = skills_registry
        self._data_dir = data_dir or Path.home() / "odin" / "data"
        self._last_report: OmniCoinReport | None = None
        self._analysis_count = 0

    def analyze(
        self,
        symbol: str,
        chart_image: str | bytes | None = None,
        coinglass_data: dict | None = None,
        candle_data: dict | None = None,
        regime_data: dict | None = None,
        macro_data: dict | None = None,
    ) -> OmniCoinReport:
        """Run full deep analysis on a coin.

        Args:
            symbol: Coin symbol (e.g., "BTCUSDT" or "BTC")
            chart_image: Optional chart screenshot path/bytes for Eye-Vision
            coinglass_data: Optional CoinGlass metrics
            candle_data: Optional dict with "htf", "mtf", "ltf" DataFrames
            regime_data: Optional regime state dict
            macro_data: Optional macro data dict
        """
        start = time.time()
        self._analysis_count += 1
        sections: dict[str, dict] = {}

        # Normalize symbol
        sym = symbol.upper().replace("USDT", "")
        full_sym = f"{sym}USDT" if not symbol.endswith("USDT") else symbol

        log.info("[OMNICOIN] Starting analysis for %s...", full_sym)

        # 1. Eye-Vision (if chart provided)
        if chart_image:
            sections["eye_vision"] = self._run_eye_vision(chart_image)

        # 2. Regime Detection
        sections["regime"] = self._run_regime(full_sym, regime_data, coinglass_data)

        # 3. SMC Structure (if candle data available)
        if candle_data:
            sections["smc_structure"] = self._run_smc(full_sym, candle_data)

        # 4. OB Memory — historical zones
        sections["ob_memory"] = self._run_ob_memory(full_sym)

        # 5. Liquidity Raid Predictions
        sections["liquidity_raids"] = self._run_liquidity_raid(
            full_sym, candle_data, coinglass_data
        )

        # 6. Sentiment Fusion
        sections["sentiment"] = self._run_sentiment(full_sym, coinglass_data)

        # 7. Cross-Chain Arb Check
        sections["arb_check"] = self._run_arb_check(sym)

        # 8. Stop Hunt Simulation (if candle data)
        if candle_data:
            sections["stop_hunt_sim"] = self._run_stop_hunt(full_sym, candle_data)

        # 9. Self-Evolve fitness
        sections["evolution"] = self._run_evolution_check()

        # 10. Journal Fitness (historical performance)
        sections["journal"] = self._run_journal_check(full_sym)

        # 11. Brotherhood Intel
        sections["brotherhood"] = self._run_brotherhood(full_sym)

        # Calculate honest confidence (1-10)
        confidence, bias, summary = self._synthesize(full_sym, sections)

        elapsed = int((time.time() - start) * 1000)

        report = OmniCoinReport(
            symbol=full_sym,
            confidence=confidence,
            bias=bias,
            summary=summary,
            sections=sections,
            timestamp=time.time(),
            analysis_time_ms=elapsed,
        )

        self._last_report = report
        self._save_report(report)

        log.info(
            "[OMNICOIN] %s: conf=%d/10 bias=%s (%dms) — %s",
            full_sym, confidence, bias, elapsed, summary[:80],
        )

        return report

    # ── Individual Skill Runners ──

    def _run_eye_vision(self, image: str | bytes) -> dict:
        try:
            eye = self._get_skill("eye_vision")
            if eye:
                result = eye.analyze_chart(image)
                return {"available": True, **result}
        except Exception as e:
            log.debug("[OMNICOIN] Eye-Vision error: %s", str(e)[:100])
        return {"available": False, "error": "Eye-Vision not available"}

    def _run_regime(self, symbol: str, regime: dict | None, cg: dict | None) -> dict:
        if regime:
            return {
                "available": True,
                "regime": regime.get("regime", "neutral"),
                "score": regime.get("global_score", 50),
                "bias": regime.get("direction_bias", "NONE"),
            }
        return {"available": bool(regime), "regime": "unknown"}

    def _run_smc(self, symbol: str, candle_data: dict) -> dict:
        try:
            from odin.strategy.smc_engine import SMCEngine
            engine = SMCEngine()

            results = {}
            for tf_name in ["mtf", "ltf"]:
                df = candle_data.get(tf_name)
                if df is not None and len(df) >= 50:
                    ms = engine.analyze(df)
                    results[tf_name] = {
                        "trend": ms.trend.name,
                        "active_obs": len(ms.active_obs),
                        "active_fvgs": len(ms.active_fvgs),
                        "liquidity_zones": len(ms.liquidity_zones),
                        "last_bos": ms.last_bos.direction.name if ms.last_bos else None,
                        "last_choch": ms.last_choch.direction.name if ms.last_choch else None,
                    }
            return {"available": True, **results}
        except Exception as e:
            return {"available": False, "error": str(e)[:100]}

    def _run_ob_memory(self, symbol: str) -> dict:
        try:
            ob_mem = self._get_skill("ob_memory")
            if ob_mem:
                stats = ob_mem.get_stats()
                active = ob_mem.get_active_zones(symbol)
                return {
                    "available": True,
                    "active_zones": len(active),
                    "total_stored": stats.get("total_zones", 0),
                    "hit_rate": stats.get("overall_hit_rate", 0),
                    "zones": [
                        {"type": z.zone_type, "dir": z.direction,
                         "level": z.price_level, "strength": z.strength}
                        for z in active[:5]
                    ],
                }
        except Exception as e:
            log.debug("[OMNICOIN] OB Memory error: %s", str(e)[:100])
        return {"available": False}

    def _run_liquidity_raid(self, symbol: str, candles: dict | None, cg: dict | None) -> dict:
        try:
            raid = self._get_skill("liquidity_raid")
            if raid and candles:
                from odin.strategy.smc_engine import SMCEngine
                engine = SMCEngine()
                df = candles.get("mtf")
                if df is not None and len(df) >= 50:
                    ms = engine.analyze(df)
                    smc_dict = {
                        "liquidity_zones": [
                            {"price_level": z.price_level, "direction": z.direction,
                             "strength": z.strength, "details": z.details}
                            for z in ms.liquidity_zones
                        ],
                        "active_obs": [
                            {"price_level": o.price_level, "strength": o.strength}
                            for o in ms.active_obs
                        ],
                    }
                    current_price = float(df["close"].iloc[-1])
                    preds = raid.predict_raids(symbol, current_price, smc_dict, cg)
                    return {
                        "available": True,
                        "predictions": [
                            {"level": p.level, "dir": p.direction,
                             "prob": p.probability, "type": p.target_type}
                            for p in preds[:5]
                        ],
                    }
        except Exception as e:
            log.debug("[OMNICOIN] Raid error: %s", str(e)[:100])
        return {"available": False}

    def _run_sentiment(self, symbol: str, cg: dict | None) -> dict:
        try:
            sent = self._get_skill("sentiment_fusion")
            if sent:
                signal = sent.analyze(symbol, cg)
                return {
                    "available": True,
                    "direction": signal.direction,
                    "strength": signal.strength,
                    "horizon_min": signal.horizon_minutes,
                    "reasons": signal.reasons[:3],
                }
        except Exception as e:
            log.debug("[OMNICOIN] Sentiment error: %s", str(e)[:100])
        return {"available": False}

    def _run_arb_check(self, asset: str) -> dict:
        try:
            arb = self._get_skill("cross_chain_arb")
            if arb:
                opps = arb.scan_all([asset])
                return {
                    "available": True,
                    "opportunities": len(opps),
                    "details": [
                        {"buy": o.buy_exchange, "sell": o.sell_exchange, "edge": o.edge_pct}
                        for o in opps[:3]
                    ],
                }
        except Exception as e:
            log.debug("[OMNICOIN] Arb error: %s", str(e)[:100])
        return {"available": False}

    def _run_stop_hunt(self, symbol: str, candle_data: dict) -> dict:
        try:
            sim = self._get_skill("stop_hunt_sim")
            if sim:
                df = candle_data.get("mtf")
                if df is not None and len(df) >= 50:
                    price = float(df["close"].iloc[-1])
                    atr_pct = 0.5
                    sl_long = price * (1 - atr_pct / 100)
                    result = sim.simulate(price, sl_long, "LONG", candle_df=df)
                    return {
                        "available": True,
                        "survival_rate": result.survival_rate,
                        "recommendation": result.recommendation,
                        "wick_p90": result.wick_stats.get("p90", 0),
                        "wick_max": result.wick_stats.get("max_wick_pct", 0),
                    }
        except Exception as e:
            log.debug("[OMNICOIN] SL Sim error: %s", str(e)[:100])
        return {"available": False}

    def _run_evolution_check(self) -> dict:
        try:
            evo = self._get_skill("self_evolve")
            if evo:
                return {"available": True, **evo.get_stats()}
        except Exception:
            pass
        return {"available": False}

    def _run_journal_check(self, symbol: str) -> dict:
        try:
            reporter = self._get_skill("auto_reporter")
            if reporter:
                stats = reporter.get_journal_stats()
                return {"available": True, **stats}
        except Exception:
            pass
        return {"available": False}

    def _run_brotherhood(self, symbol: str) -> dict:
        try:
            brotherhood = self._get_skill("brotherhood")
            if brotherhood:
                return {"available": True, **brotherhood.get_status()}
        except Exception:
            pass
        return {"available": False}

    # ── Synthesis (The Brain) ──

    def _synthesize(self, symbol: str, sections: dict) -> tuple[int, str, str]:
        """Synthesize all skill outputs into honest confidence + bias + summary.

        Returns (confidence 1-10, bias, summary_text).
        """
        scores: list[float] = []  # -1 to +1 per section
        insights: list[str] = []

        # Eye-Vision
        ev = sections.get("eye_vision", {})
        if ev.get("available"):
            ev_conf = ev.get("confidence", 0.5)
            ev_bias = ev.get("bias", "neutral")
            if ev_bias == "long":
                scores.append(ev_conf)
            elif ev_bias == "short":
                scores.append(-ev_conf)
            else:
                scores.append(0)
            insights.append(f"Chart: {ev.get('narrative', 'N/A')[:80]}")

        # Regime
        reg = sections.get("regime", {})
        if reg.get("available"):
            regime_score = reg.get("score", 50)
            scores.append((regime_score - 50) / 50)
            insights.append(f"Regime: {reg.get('regime', '?')} (score={regime_score})")

        # SMC Structure
        smc = sections.get("smc_structure", {})
        if smc.get("available"):
            for tf in ["mtf", "ltf"]:
                if tf in smc:
                    trend = smc[tf].get("trend", "NEUTRAL")
                    if trend == "BULLISH":
                        scores.append(0.5)
                    elif trend == "BEARISH":
                        scores.append(-0.5)
                    insights.append(f"SMC {tf}: {trend}, {smc[tf].get('active_obs', 0)} OBs")

        # Sentiment
        sent = sections.get("sentiment", {})
        if sent.get("available"):
            sent_dir = sent.get("direction", "NEUTRAL")
            sent_str = sent.get("strength", 0) / 100
            if sent_dir == "LONG":
                scores.append(sent_str)
            elif sent_dir == "SHORT":
                scores.append(-sent_str)
            insights.append(f"Sentiment: {sent_dir} (str={sent.get('strength', 0):.0f})")

        # Liquidity Raids
        raids = sections.get("liquidity_raids", {})
        if raids.get("available") and raids.get("predictions"):
            top_raid = raids["predictions"][0]
            insights.append(
                f"Raid risk: {top_raid['type']} {top_raid['dir']} "
                f"@ ${top_raid['level']:.0f} ({top_raid['prob']:.0f}%)"
            )

        # OB Memory
        ob = sections.get("ob_memory", {})
        if ob.get("available") and ob.get("active_zones", 0) > 0:
            insights.append(f"OB Memory: {ob['active_zones']} active zones")

        # Calculate composite
        if not scores:
            return 5, "NEUTRAL", f"Insufficient data for {symbol}. Need more sources."

        avg_score = sum(scores) / len(scores)

        # Honest confidence: based on agreement between sources
        agreement = 1 - (max(scores) - min(scores)) / 2 if len(scores) > 1 else 0.5
        raw_confidence = abs(avg_score) * 0.6 + agreement * 0.4
        confidence = max(1, min(10, int(raw_confidence * 10 + 0.5)))

        # Bias
        if avg_score > 0.15:
            bias = "LONG"
        elif avg_score < -0.15:
            bias = "SHORT"
        else:
            bias = "NEUTRAL"

        # Honest summary
        num_sources = len(scores)
        summary = (
            f"{symbol}: {bias} bias with {confidence}/10 confidence "
            f"({num_sources} sources analyzed). "
            + " | ".join(insights[:3])
        )

        return confidence, bias, summary

    def _save_report(self, report: OmniCoinReport) -> None:
        """Save report to JSON file for dashboard consumption."""
        try:
            out_path = self._data_dir / "omnicoin_analysis.json"
            out_path.write_text(json.dumps(report.to_dict(), indent=2, default=str))
        except Exception as e:
            log.debug("[OMNICOIN] Save error: %s", str(e)[:100])

    def _get_skill(self, name: str):
        """Get a skill from the registry."""
        if self._skills:
            return self._skills.get(name)
        return None

    def get_status(self) -> dict:
        return {
            "analyses_done": self._analysis_count,
            "last_symbol": self._last_report.symbol if self._last_report else None,
            "last_confidence": self._last_report.confidence if self._last_report else None,
            "last_bias": self._last_report.bias if self._last_report else None,
        }
