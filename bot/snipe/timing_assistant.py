"""Entry Timing & Confidence Assistant — unified timing oracle.

Runs inside the snipe engine every 2s tick. Evaluates 5 weighted components
into a 0-100 timing_score, then broadcasts recommendations to all crypto agents
via JSON file + event bus.

Thresholds:
  score >= 80 → AUTO-EXECUTE (100% size)
  65 <= score < 80 → CONSERVATIVE (70% size)
  score < 65 → AUTO-SKIP

Consumers: Garves snipe (direct), Garves taker (JSON), Odin (JSON+events), Hawk (JSON)
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

from bot.snipe.timing_learner import TimingLearner

log = logging.getLogger("garves.snipe")

ASSIST_FILE = Path(__file__).parent.parent.parent / "data" / "snipe_assist.json"
OVERRIDE_FILE = Path(__file__).parent.parent.parent / "data" / "snipe_assist_override.json"

# Scoring thresholds
THRESHOLD_AUTO = 80
THRESHOLD_CONSERVATIVE = 65

# Component weights (sum = 100)
WEIGHTS = {
    "snipe_signal_strength": 40,
    "liquidity_quality": 20,
    "time_positioning": 15,
    "regime_alignment": 10,
    "historical_accuracy": 15,
}


@dataclass
class TimingRecommendation:
    timing_score: float = 0.0
    action: str = "auto_skip"       # auto_execute | conservative | auto_skip
    direction: str = ""             # up | down
    recommended_size_pct: float = 0.0
    optimal_entry_window_s: float = 0.0
    confidence_factors: dict = field(default_factory=dict)
    agent_overrides: dict = field(default_factory=dict)
    expires_at: float = 0.0
    timestamp: float = 0.0


class TimingAssistant:
    """Core timing oracle — evaluates every snipe tick and broadcasts."""

    def __init__(self, learner: TimingLearner):
        self._learner = learner
        self._last_rec: TimingRecommendation | None = None
        self._fear_greed: float = 50.0  # Updated externally by main loop
        self._thresholds = {
            "auto": THRESHOLD_AUTO,
            "conservative": THRESHOLD_CONSERVATIVE,
        }

    def set_fear_greed(self, value: float) -> None:
        """Update Fear & Greed index from Garves main loop."""
        self._fear_greed = value

    def set_thresholds(self, auto: int | None = None, conservative: int | None = None) -> None:
        """Dynamically adjust thresholds from dashboard."""
        if auto is not None:
            self._thresholds["auto"] = max(50, min(100, auto))
        if conservative is not None:
            self._thresholds["conservative"] = max(30, min(self._thresholds["auto"] - 1, conservative))

    def evaluate(self, snipe_state: dict) -> TimingRecommendation:
        """Evaluate timing from current snipe state. Called every tick.

        snipe_state keys:
            score_result: ScoreResult from SignalScorer (or None)
            clob_book: target CLOB orderbook dict
            remaining_s: seconds remaining in window
            direction: "up" or "down"
            regime: Fear & Greed regime label
            implied_price: CLOB implied price
        """
        score_result = snipe_state.get("score_result")
        clob_book = snipe_state.get("clob_book") or {}
        remaining_s = snipe_state.get("remaining_s", 0)
        direction = snipe_state.get("direction", "")
        regime = snipe_state.get("regime", "neutral")

        factors = {}
        total = 0.0

        # 1. Snipe Signal Strength (40%) — from the 10-component scorer
        if score_result:
            raw = score_result.total_score / 100.0  # Normalize 0-100 → 0-1
        else:
            raw = 0.0
        weighted = raw * WEIGHTS["snipe_signal_strength"]
        total += weighted
        factors["snipe_signal_strength"] = {"raw": round(raw, 3), "weighted": round(weighted, 1)}

        # 2. Liquidity Quality (20%) — CLOB spread compression + ask depth + buy pressure
        liq_raw = self._score_liquidity(clob_book)
        liq_weighted = liq_raw * WEIGHTS["liquidity_quality"]
        total += liq_weighted
        factors["liquidity_quality"] = {"raw": round(liq_raw, 3), "weighted": round(liq_weighted, 1)}

        # 3. Time Positioning (15%) — sweet spot detection
        time_raw = self._score_time_positioning(remaining_s)
        time_weighted = time_raw * WEIGHTS["time_positioning"]
        total += time_weighted
        factors["time_positioning"] = {"raw": round(time_raw, 3), "weighted": round(time_weighted, 1)}

        # 4. Regime Alignment (10%) — Fear & Greed index
        regime_raw = self._score_regime(direction)
        regime_weighted = regime_raw * WEIGHTS["regime_alignment"]
        total += regime_weighted
        factors["regime_alignment"] = {"raw": round(regime_raw, 3), "weighted": round(regime_weighted, 1)}

        # 5. Historical Accuracy (15%) — self-learning WR
        hist = self._learner.get_accuracy(
            direction=direction or None,
            regime=regime if regime != "neutral" else None,
            window=50,
        )
        hist_raw = hist["win_rate"] if hist["total"] >= 5 else 0.5
        hist_weighted = hist_raw * WEIGHTS["historical_accuracy"]
        total += hist_weighted
        factors["historical_accuracy"] = {
            "raw": round(hist_raw, 3), "weighted": round(hist_weighted, 1),
            "total_samples": hist["total"],
        }

        timing_score = round(total, 1)

        # Check for dashboard override
        override_action = self._check_override()

        # Determine action
        if override_action:
            action = override_action
        elif timing_score >= self._thresholds["auto"]:
            action = "auto_execute"
        elif timing_score >= self._thresholds["conservative"]:
            action = "conservative"
        else:
            action = "auto_skip"

        # Recommended size
        if action == "auto_execute":
            size_pct = 1.0
        elif action == "conservative":
            size_pct = 0.70
        else:
            size_pct = 0.0

        # Optimal entry window from self-learning
        optimal = self._learner.get_optimal_timing_range(direction=direction or None)

        # Agent-specific overrides
        agent_overrides = {
            "garves_snipe": {"action": action, "size_pct": size_pct},
            "garves_taker": {"action": action, "size_pct": size_pct},
            "odin": self._odin_override(direction, timing_score, action, size_pct),
            "hawk": self._hawk_override(timing_score, action, size_pct),
        }

        rec = TimingRecommendation(
            timing_score=timing_score,
            action=action,
            direction=direction,
            recommended_size_pct=size_pct,
            optimal_entry_window_s=optimal.get("best_range_s", (30, 120))[0],
            confidence_factors=factors,
            agent_overrides=agent_overrides,
            expires_at=time.time() + 120,  # Valid for 2 minutes
            timestamp=time.time(),
        )

        self._last_rec = rec
        self._write_status(rec)
        self._publish_recommendation(rec)

        if direction:
            log.info(
                "[TIMING] score=%.0f | %s | %s %s | size=%.0f%% | regime=%s | hist_wr=%.0f%% (%d)",
                timing_score, action.upper(), direction.upper(),
                "override" if override_action else "",
                size_pct * 100, regime, hist_raw * 100, hist["total"],
            )

        return rec

    def get_recommendation_for_agent(self, agent: str) -> dict:
        """Get agent-specific recommendation from last evaluation."""
        if not self._last_rec:
            return {"action": "auto_skip", "size_pct": 0.0, "timing_score": 0}
        overrides = self._last_rec.agent_overrides.get(agent, {})
        return {
            "action": overrides.get("action", self._last_rec.action),
            "size_pct": overrides.get("size_pct", self._last_rec.recommended_size_pct),
            "timing_score": self._last_rec.timing_score,
            "direction": self._last_rec.direction,
            "expires_at": self._last_rec.expires_at,
        }

    def record_outcome(
        self,
        agent: str,
        direction: str,
        won: bool,
        timing_score: float,
        size_pct: float,
        pnl_usd: float = 0.0,
        window_remaining_s: float = 0.0,
    ) -> str:
        """Record trade outcome for self-learning. Returns record ID."""
        record_id = self._learner.record(
            agent=agent,
            direction=direction,
            timing_score=int(timing_score),
            action=self._action_for_score(timing_score),
            size_pct=size_pct,
            window_remaining_s=window_remaining_s,
        )
        if record_id and won is not None:
            self._learner.resolve(record_id, won, pnl_usd)
        return record_id

    def get_last_recommendation(self) -> TimingRecommendation | None:
        return self._last_rec

    # ── Scoring helpers ──

    def _score_liquidity(self, book: dict) -> float:
        """Score CLOB liquidity quality 0-1."""
        if not book:
            return 0.0
        spread = book.get("spread", 1.0)
        buy_pressure = book.get("buy_pressure", 0)
        sell_pressure = book.get("sell_pressure", 0)
        best_ask = book.get("best_ask", 1.0)

        score = 0.0
        # Spread compression (tight = good)
        if spread < 0.10:
            score += 0.4
        elif spread < 0.30:
            score += 0.25
        elif spread < 0.50:
            score += 0.1

        # Ask depth at reasonable prices
        if best_ask < 0.65 and sell_pressure > 0:
            depth = sell_pressure / max(best_ask, 0.01)
            if depth > 50:
                score += 0.3
            elif depth > 20:
                score += 0.2
            elif depth > 5:
                score += 0.1

        # Buy pressure activity
        if buy_pressure > 0:
            score += 0.15
            if buy_pressure > sell_pressure * 0.5:
                score += 0.15

        return min(1.0, score)

    def _score_time_positioning(self, remaining_s: float) -> float:
        """Score time position 0-1. Sweet spot is 30-90s before close."""
        if remaining_s <= 0 or remaining_s > 240:
            return 0.0
        # Optimal window: 30-90s
        if 30 <= remaining_s <= 90:
            return 1.0
        # Good: 15-30s or 90-120s
        if 15 <= remaining_s < 30:
            return 0.7
        if 90 < remaining_s <= 120:
            return 0.7
        # Decent: 5-15s or 120-180s
        if 5 <= remaining_s < 15:
            return 0.4
        if 120 < remaining_s <= 180:
            return 0.5
        # Early: 180-240s
        return 0.2

    def _score_regime(self, direction: str) -> float:
        """Score regime alignment 0-1 based on Fear & Greed."""
        fg = self._fear_greed
        if not direction:
            return 0.5
        if direction == "up":
            # Greed favors UP
            if fg >= 70:
                return 0.9
            if fg >= 55:
                return 0.7
            if fg >= 40:
                return 0.5
            return 0.3  # Fear against UP
        else:
            # Fear favors DOWN
            if fg <= 30:
                return 0.9
            if fg <= 45:
                return 0.7
            if fg <= 60:
                return 0.5
            return 0.3  # Greed against DOWN

    def _odin_override(self, direction: str, score: float, action: str, size_pct: float) -> dict:
        """Odin-specific: translate to LONG/SHORT, add direction confirmation."""
        odin_dir = "LONG" if direction == "up" else "SHORT" if direction == "down" else ""
        return {
            "action": action,
            "size_pct": size_pct,
            "direction_hint": odin_dir,
            "confirmation": score >= self._thresholds["conservative"],
        }

    def _hawk_override(self, score: float, action: str, size_pct: float) -> dict:
        """Hawk: only applies to crypto-adjacent markets."""
        return {
            "action": action,
            "size_pct": size_pct,
            "crypto_only": True,
        }

    def _check_override(self) -> str | None:
        """Read dashboard override file. Returns forced action or None."""
        try:
            if OVERRIDE_FILE.exists():
                data = json.loads(OVERRIDE_FILE.read_text())
                expires = data.get("expires_at", 0)
                if time.time() < expires:
                    return data.get("action")
                # Expired — clean up
                OVERRIDE_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        return None

    def _action_for_score(self, score: float) -> str:
        if score >= self._thresholds["auto"]:
            return "auto_execute"
        if score >= self._thresholds["conservative"]:
            return "conservative"
        return "auto_skip"

    def _write_status(self, rec: TimingRecommendation) -> None:
        """Atomic write to snipe_assist.json for cross-process consumers."""
        try:
            ASSIST_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = asdict(rec)
            data["thresholds"] = self._thresholds.copy()
            data["learner"] = self._learner.get_status()
            tmp = ASSIST_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2, default=str))
            os.rename(str(tmp), str(ASSIST_FILE))
        except Exception as e:
            log.debug("[TIMING] Write status error: %s", str(e)[:100])

    def _publish_recommendation(self, rec: TimingRecommendation) -> None:
        """Publish to shared event bus for async notification."""
        if not rec.direction:
            return  # Don't publish idle states
        try:
            from shared.events import publish
            publish(
                agent="garves",
                event_type="snipe_timing_recommendation",
                data={
                    "timing_score": rec.timing_score,
                    "action": rec.action,
                    "direction": rec.direction,
                    "size_pct": rec.recommended_size_pct,
                    "expires_at": rec.expires_at,
                },
                summary=f"Timing: {rec.action.upper()} {rec.direction.upper()} score={rec.timing_score:.0f}",
            )
        except Exception:
            pass

    def get_status(self) -> dict:
        """Dashboard-friendly status."""
        rec = self._last_rec
        if not rec:
            return {"active": False, "timing_score": 0, "action": "idle"}
        return {
            "active": True,
            "timing_score": rec.timing_score,
            "action": rec.action,
            "direction": rec.direction,
            "size_pct": rec.recommended_size_pct,
            "factors": rec.confidence_factors,
            "agent_overrides": rec.agent_overrides,
            "thresholds": self._thresholds.copy(),
            "learner": self._learner.get_status(),
            "expires_at": rec.expires_at,
            "timestamp": rec.timestamp,
        }
