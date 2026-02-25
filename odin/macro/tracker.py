"""MacroDominanceTracker — 4-pillar macro scoring system.

Pillars:
  1. Equity Trend (SPY vs 50/200 MA)  — 0-30 points
  2. VIX Fear Gauge                    — 0-20 points
  3. USDT Dominance trend              — 0-25 points
  4. BTC Dominance regime              — 0-25 points

Total: 0-100 macro health score → regime classification → position multiplier.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import numpy as np
import requests

log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

_CACHE_TTL_DOM = 600      # 10 min
_CACHE_TTL_EQ = 300       # 5 min market hours
_CACHE_TTL_EQ_OFF = 1800  # 30 min off hours


class MacroRegime(Enum):
    STRONG_BULL = "strong_bull"
    BULL = "bull"
    NEUTRAL = "neutral"
    BEAR = "bear"
    CRISIS = "crisis"


@dataclass
class MacroSignal:
    """Unified macro output for the trading engine."""
    regime: MacroRegime = MacroRegime.NEUTRAL
    score: int = 50
    position_multiplier: float = 0.6
    allow_longs: bool = True
    allow_shorts: bool = True
    direction_bias: str = "neutral"

    equity_score: int = 15
    vix_score: int = 10
    usdt_d_score: int = 12
    btc_d_score: int = 15

    spy_price: float = 0.0
    spy_change_pct: float = 0.0
    spy_above_50ma: bool = True
    spy_above_200ma: bool = True
    vix: float = 18.0

    btc_dominance: float = 0.0
    usdt_dominance: float = 0.0
    btc_d_trend: str = ""
    usdt_d_trend: str = ""
    crypto_regime: str = ""

    reasons: list[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)


class MacroDominanceTracker:
    """
    Combined macro tracker for Odin.

    Data sources (all free):
    - CoinGecko /global → BTC.D + USDT.D (1 call)
    - yfinance → SPY, VIX, ES=F
    """

    def __init__(self, data_dir: Optional[Path] = None):
        self._cg_key = os.environ.get("COINGECKO_API_KEY", "")
        self._data_dir = data_dir or Path(__file__).parent.parent / "data" / "macro"
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._dom_history = self._data_dir / "dominance_history.jsonl"
        self._regime_log = self._data_dir / "macro_regime_log.jsonl"

        # Caches: (data, timestamp)
        self._dom_cache: Optional[tuple[dict, float]] = None
        self._eq_cache: Optional[tuple[dict, float]] = None

    def get_signal(self) -> MacroSignal:
        """Generate unified macro signal. Call every 5-10 minutes."""
        dom = self._fetch_dominance()
        eq = self._fetch_equity()

        eq_score, eq_reasons = self._score_equity(eq)
        vix_score, vix_reasons = self._score_vix(eq)
        usdt_score, usdt_reasons = self._score_usdt_d(dom)
        btcd_score, crypto_regime, btcd_reasons = self._score_btc_d(dom)

        total = eq_score + vix_score + usdt_score + btcd_score
        all_reasons = eq_reasons + vix_reasons + usdt_reasons + btcd_reasons

        # Classify regime
        if total >= 80:
            regime, mult, bias = MacroRegime.STRONG_BULL, 1.0, "long"
            longs, shorts = True, True
        elif total >= 60:
            regime, mult, bias = MacroRegime.BULL, 0.8, "long"
            longs, shorts = True, True
        elif total >= 40:
            regime, mult, bias = MacroRegime.NEUTRAL, 0.6, "neutral"
            longs, shorts = True, True
        elif total >= 20:
            regime, mult, bias = MacroRegime.BEAR, 0.4, "short"
            longs, shorts = False, True
        else:
            regime, mult, bias = MacroRegime.CRISIS, 0.1, "short"
            longs, shorts = False, True
            all_reasons.append("CRISIS: ALL SIGNALS RED — shorts only, minimal size")

        # Overrides — never block shorts (shorts profit in downturns)
        vix_val = eq.get("vix", 0)
        spy_chg = eq.get("spy_change_pct", 0)
        if vix_val > 30:
            regime, mult, longs = MacroRegime.CRISIS, 0.1, False
            all_reasons.append(f"VIX OVERRIDE: {vix_val:.1f} > 30")
        if spy_chg < -3.0:
            regime, mult, longs = MacroRegime.CRISIS, 0.1, False
            all_reasons.append(f"SPY CRASH: {spy_chg:.1f}%")
        if crypto_regime == "capitulation":
            regime, mult, longs = MacroRegime.CRISIS, 0.1, False
            all_reasons.append("CAPITULATION: BTC.D + price both falling — shorts only")

        signal = MacroSignal(
            regime=regime,
            score=total,
            position_multiplier=round(mult, 2),
            allow_longs=longs,
            allow_shorts=shorts,
            direction_bias=bias,
            equity_score=eq_score,
            vix_score=vix_score,
            usdt_d_score=usdt_score,
            btc_d_score=btcd_score,
            spy_price=eq.get("spy_price", 0),
            spy_change_pct=eq.get("spy_change_pct", 0),
            spy_above_50ma=eq.get("above_50ma", True),
            spy_above_200ma=eq.get("above_200ma", True),
            vix=vix_val,
            btc_dominance=dom.get("btc_d", 0),
            usdt_dominance=dom.get("usdt_d", 0),
            btc_d_trend=dom.get("btc_d_trend", ""),
            usdt_d_trend=dom.get("usdt_d_trend", ""),
            crypto_regime=crypto_regime,
            reasons=all_reasons[:8],
        )

        self._log_regime(signal)
        log.info(
            "[MACRO] %s score=%d mult=%.2f | eq=%d vix=%d usdt=%d btcd=%d",
            regime.value, total, mult, eq_score, vix_score, usdt_score, btcd_score,
        )
        return signal

    # ── Data Fetchers ──

    def _fetch_dominance(self) -> dict:
        """Fetch BTC.D + USDT.D from CoinGecko /global."""
        now = time.time()
        if self._dom_cache and now - self._dom_cache[1] < _CACHE_TTL_DOM:
            return self._dom_cache[0]

        try:
            headers = {"accept": "application/json"}
            if self._cg_key:
                headers["x-cg-demo-api-key"] = self._cg_key
            resp = requests.get(
                "https://api.coingecko.com/api/v3/global",
                headers=headers, timeout=10,
            )
            if resp.status_code == 429:
                log.warning("[MACRO] CoinGecko rate limited")
                return self._dom_cache[0] if self._dom_cache else {}
            resp.raise_for_status()

            mcp = resp.json().get("data", {}).get("market_cap_percentage", {})
            mcap_chg = resp.json().get("data", {}).get(
                "market_cap_change_percentage_24h_usd", 0
            )

            data = {
                "btc_d": round(mcp.get("btc", 0), 4),
                "usdt_d": round(mcp.get("usdt", 0), 4),
                "eth_d": round(mcp.get("eth", 0), 4),
                "mcap_chg_24h": mcap_chg,
                "btc_d_trend": "",
                "usdt_d_trend": "",
            }

            # Calculate trends from history
            data = self._calc_trends(data)
            self._store_snapshot(data)
            self._dom_cache = (data, now)
            return data

        except Exception as e:
            log.error("[MACRO] CoinGecko error: %s", str(e)[:150])
            return self._dom_cache[0] if self._dom_cache else {}

    def _fetch_equity(self) -> dict:
        """Fetch SPY, VIX from yfinance."""
        now = time.time()
        hour_et = datetime.now(ET).hour
        is_mkt = 9 <= hour_et < 16 and datetime.now(ET).weekday() < 5
        ttl = _CACHE_TTL_EQ if is_mkt else _CACHE_TTL_EQ_OFF

        if self._eq_cache and now - self._eq_cache[1] < ttl:
            return self._eq_cache[0]

        data: dict = {
            "spy_price": 0, "spy_change_pct": 0, "spy_5d_ret": 0,
            "spy_ma50": 0, "spy_ma200": 0, "above_50ma": True,
            "above_200ma": True, "vix": 18, "vix_change_pct": 0,
        }

        try:
            import yfinance as yf

            spy = yf.Ticker("SPY")
            hist = spy.history(period="1y")
            if not hist.empty and len(hist) >= 200:
                c = hist["Close"]
                data["spy_price"] = round(float(c.iloc[-1]), 2)
                data["spy_ma50"] = round(float(c.rolling(50).mean().iloc[-1]), 2)
                data["spy_ma200"] = round(float(c.rolling(200).mean().iloc[-1]), 2)
                data["above_50ma"] = data["spy_price"] > data["spy_ma50"]
                data["above_200ma"] = data["spy_price"] > data["spy_ma200"]
                if len(c) >= 2:
                    data["spy_change_pct"] = round(
                        (float(c.iloc[-1]) / float(c.iloc[-2]) - 1) * 100, 3
                    )
                if len(c) >= 5:
                    data["spy_5d_ret"] = round(
                        (float(c.iloc[-1]) / float(c.iloc[-5]) - 1) * 100, 3
                    )

            vix = yf.Ticker("^VIX")
            vh = vix.history(period="5d")
            if not vh.empty:
                data["vix"] = round(float(vh["Close"].iloc[-1]), 2)
                if len(vh) >= 2:
                    data["vix_change_pct"] = round(
                        (float(vh["Close"].iloc[-1]) / float(vh["Close"].iloc[-2]) - 1) * 100, 1
                    )

        except ImportError:
            log.warning("[MACRO] yfinance not installed — equity data unavailable")
        except Exception as e:
            log.error("[MACRO] Equity fetch error: %s", str(e)[:150])

        self._eq_cache = (data, now)
        return data

    # ── Scoring ──

    def _score_equity(self, eq: dict) -> tuple[int, list[str]]:
        reasons = []
        spy = eq.get("spy_price", 0)
        if spy == 0:
            return 15, ["Equity data unavailable"]

        chg = eq.get("spy_change_pct", 0)
        above50 = eq.get("above_50ma", True)
        above200 = eq.get("above_200ma", True)
        ret5d = eq.get("spy_5d_ret", 0)

        if above50 and above200 and ret5d > 0:
            score = 30
            reasons.append(f"SPY ${spy:.0f} above 50/200 MA, 5d +{ret5d:.1f}%")
        elif above50 and above200:
            score = 25
            reasons.append("SPY above both MAs, momentum mixed")
        elif above200:
            score = 18
            reasons.append(f"SPY above 200MA, below 50MA ({chg:+.1f}%)")
        else:
            score = 0
            reasons.append("SPY BELOW 200MA — bear territory")

        if chg < -3.0:
            score = max(0, score - 20)
            reasons.append(f"SPY CRASH {chg:.1f}%")
        elif chg < -1.0:
            score = max(0, score - 5)
            reasons.append(f"SPY weak {chg:.1f}%")

        return score, reasons

    def _score_vix(self, eq: dict) -> tuple[int, list[str]]:
        vix = eq.get("vix", 18)
        if vix == 0:
            return 10, ["VIX unavailable"]

        if vix < 15:
            return 20, [f"VIX={vix:.1f} complacency"]
        if vix < 20:
            return 15, [f"VIX={vix:.1f} normal"]
        if vix < 25:
            return 8, [f"VIX={vix:.1f} elevated fear"]
        if vix < 30:
            return 3, [f"VIX={vix:.1f} HIGH FEAR"]
        return 0, [f"VIX={vix:.1f} PANIC"]

    def _score_usdt_d(self, dom: dict) -> tuple[int, list[str]]:
        usdt_d = dom.get("usdt_d", 0)
        trend = dom.get("usdt_d_trend", "flat")
        chg = dom.get("usdt_d_change_7d", 0)
        if usdt_d == 0:
            return 12, ["USDT.D unavailable"]

        if trend == "falling" and chg < -0.3:
            score, msg = 25, f"USDT.D={usdt_d:.2f}% FALLING FAST — capital into crypto"
        elif trend == "falling":
            score, msg = 18, f"USDT.D={usdt_d:.2f}% falling — mild risk-on"
        elif trend == "rising" and chg > 0.3:
            score, msg = 0, f"USDT.D={usdt_d:.2f}% RISING FAST — flight to stables"
        elif trend == "rising":
            score, msg = 5, f"USDT.D={usdt_d:.2f}% rising — caution"
        else:
            score, msg = 12, f"USDT.D={usdt_d:.2f}% flat"

        if usdt_d > 6.0:
            score = max(0, score - 5)
        elif usdt_d < 4.0:
            score = min(25, score + 3)

        return score, [msg]

    def _score_btc_d(self, dom: dict) -> tuple[int, str, list[str]]:
        btc_d = dom.get("btc_d", 0)
        btc_d_trend = dom.get("btc_d_trend", "flat")
        mcap_chg = dom.get("mcap_chg_24h", 0)
        if btc_d == 0:
            return 15, "unknown", ["BTC.D unavailable"]

        price_trend = "rising" if mcap_chg > 0.5 else ("falling" if mcap_chg < -0.5 else "flat")

        if btc_d_trend == "rising" and price_trend in ("rising", "flat"):
            return 25, "btc_season", [f"BTC.D={btc_d:.1f}% rising + price up = BTC SEASON"]
        if btc_d_trend == "falling" and price_trend in ("rising", "flat"):
            return 20, "alt_season", [f"BTC.D={btc_d:.1f}% falling + price up = ALT SEASON"]
        if btc_d_trend == "flat":
            return 15, "consolidation", [f"BTC.D={btc_d:.1f}% flat"]
        if btc_d_trend == "rising" and price_trend == "falling":
            return 5, "crypto_bear", [f"BTC.D={btc_d:.1f}% rising + price down = CRYPTO BEAR"]
        if btc_d_trend == "falling" and price_trend == "falling":
            return 0, "capitulation", [f"BTC.D={btc_d:.1f}% falling + price down = CAPITULATION"]
        return 12, "mixed", [f"BTC.D={btc_d:.1f}% mixed"]

    # ── Trend / History ──

    def _calc_trends(self, data: dict) -> dict:
        if not self._dom_history.exists():
            data["btc_d_trend"] = "flat"
            data["usdt_d_trend"] = "flat"
            return data

        try:
            cutoff = time.time() - 7 * 86400
            history = []
            with open(self._dom_history) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    if rec.get("ts", 0) >= cutoff:
                        history.append(rec)

            if len(history) < 3:
                data["btc_d_trend"] = "flat"
                data["usdt_d_trend"] = "flat"
                return data

            history.sort(key=lambda x: x["ts"])
            oldest = history[0]

            btc_chg = data["btc_d"] - oldest.get("btc_d", data["btc_d"])
            data["btc_d_change_7d"] = round(btc_chg, 4)
            data["btc_d_trend"] = "rising" if btc_chg > 0.5 else ("falling" if btc_chg < -0.5 else "flat")

            usdt_chg = data["usdt_d"] - oldest.get("usdt_d", data["usdt_d"])
            data["usdt_d_change_7d"] = round(usdt_chg, 4)
            data["usdt_d_trend"] = "rising" if usdt_chg > 0.15 else ("falling" if usdt_chg < -0.15 else "flat")

        except Exception as e:
            log.debug("[MACRO] Trend calc error: %s", e)
            data["btc_d_trend"] = "flat"
            data["usdt_d_trend"] = "flat"
        return data

    def _store_snapshot(self, data: dict) -> None:
        try:
            if self._dom_history.exists():
                with open(self._dom_history) as f:
                    lines = f.readlines()
                if lines:
                    last = json.loads(lines[-1])
                    if time.time() - last.get("ts", 0) < 3600:
                        return
            rec = {
                "btc_d": data["btc_d"], "usdt_d": data["usdt_d"],
                "eth_d": data.get("eth_d", 0), "ts": time.time(),
                "date": datetime.now(ET).strftime("%Y-%m-%d %H:%M"),
            }
            with open(self._dom_history, "a") as f:
                f.write(json.dumps(rec) + "\n")
        except Exception:
            pass

    def _log_regime(self, sig: MacroSignal) -> None:
        try:
            rec = {
                "regime": sig.regime.value, "score": sig.score,
                "mult": sig.position_multiplier, "crypto": sig.crypto_regime,
                "spy": sig.spy_price, "vix": sig.vix,
                "btc_d": sig.btc_dominance, "usdt_d": sig.usdt_dominance,
                "ts": time.time(),
                "date": datetime.now(ET).strftime("%Y-%m-%d %H:%M"),
            }
            with open(self._regime_log, "a") as f:
                f.write(json.dumps(rec) + "\n")
        except Exception:
            pass
