"""Garves V2 — Post-Trade Analysis + Mistake-to-Rule Engine.

After EVERY resolved trade, analyzes what went right/wrong.
If a mistake repeats 2x → auto-creates a blocking rule.

Mistake types:
- model: probability estimate was wrong
- execution: slippage/fill issues
- sizing: position too large/small for the edge
- thesis: the agreeing indicators were wrong
- regime: traded in unfamiliar regime
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
ANALYSIS_FILE = DATA_DIR / "post_trade_analysis.jsonl"
AUTO_RULES_FILE = DATA_DIR / "auto_rules.json"
LESSONS_FILE = DATA_DIR / "lessons.json"
TRADES_FILE = DATA_DIR / "trades.jsonl"

# Rule engine settings
MISTAKE_THRESHOLD = 2       # Create rule after 2 repeated mistakes
MAX_ACTIVE_RULES = 10       # Cap on auto-generated rules
RULE_EXPIRY_DAYS = 7        # Auto-rules expire after 7 days


@dataclass
class TradeAnalysis:
    """Result of post-trade analysis."""
    trade_id: str
    timestamp: float = field(default_factory=time.time)
    model_correct: bool = True
    execution_quality: str = "acceptable"   # "good" / "acceptable" / "poor"
    sizing_appropriate: bool = True
    thesis_correct: bool = True
    ev_predicted: float = 0.0
    ev_captured: float = 0.0
    ev_capture_pct: float = 0.0
    slippage_cost_usd: float = 0.0
    indicator_accuracy: dict = field(default_factory=dict)
    mistake_type: str = "none"
    mistake_detail: str = ""
    actionable: bool = False


class PostTradeAnalyzer:
    """Analyzes every resolved trade and auto-creates rules for repeated mistakes.

    Usage:
        analyzer = PostTradeAnalyzer()
        analysis = analyzer.analyze(trade_record)
        rule = analyzer.maybe_create_rule(analysis)
    """

    def __init__(self):
        self._mistake_counts: dict[str, int] = {}
        self._load_mistake_counts()

    def _load_mistake_counts(self) -> None:
        """Load running mistake counts from existing analyses."""
        if not ANALYSIS_FILE.exists():
            return
        try:
            for line in ANALYSIS_FILE.read_text().splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                mtype = rec.get("mistake_type", "none")
                mdetail = rec.get("mistake_detail", "")
                if mtype != "none" and mdetail:
                    key = f"{mtype}:{mdetail}"
                    self._mistake_counts[key] = self._mistake_counts.get(key, 0) + 1
        except Exception as e:
            log.debug("Failed to load mistake counts: %s", str(e)[:100])

    def analyze(self, trade: dict) -> TradeAnalysis:
        """Analyze a resolved trade record.

        Args:
            trade: Dict from trades.jsonl with resolution data.

        Returns:
            TradeAnalysis with breakdown of what went right/wrong.
        """
        analysis = TradeAnalysis(trade_id=trade.get("trade_id", ""))
        won = trade.get("won", False)
        direction = trade.get("direction", "")
        outcome = trade.get("outcome", "")
        asset = trade.get("asset", "")
        timeframe = trade.get("timeframe", "")
        regime = trade.get("regime_label", "")

        # EV analysis
        ev_predicted = trade.get("ev_predicted", 0.0)
        pnl = trade.get("pnl", 0.0)
        size_usd = trade.get("size_usd", 10.0)

        analysis.ev_predicted = ev_predicted
        analysis.ev_captured = pnl
        if ev_predicted > 0:
            analysis.ev_capture_pct = pnl / ev_predicted
        elif ev_predicted < 0:
            analysis.ev_capture_pct = 0.0
        else:
            analysis.ev_capture_pct = 1.0 if won else 0.0

        # Model accuracy — did our probability direction match reality?
        analysis.model_correct = won

        # Execution quality — estimate from slippage data
        ob_slippage = trade.get("ob_slippage_pct", 0.0)
        analysis.slippage_cost_usd = ob_slippage * size_usd
        if ob_slippage <= 0.01:
            analysis.execution_quality = "good"
        elif ob_slippage <= 0.03:
            analysis.execution_quality = "acceptable"
        else:
            analysis.execution_quality = "poor"

        # Sizing — was the edge large enough for the size?
        edge = trade.get("edge", 0.0)
        if not won and size_usd > 12.0 and edge < 0.12:
            analysis.sizing_appropriate = False

        # Thesis — check indicator votes accuracy
        votes = trade.get("indicator_votes", {})
        for name, vote_data in votes.items():
            vote_dir = vote_data if isinstance(vote_data, str) else vote_data.get("direction", "")
            correct = (vote_dir == outcome) if outcome in ("up", "down") else None
            if correct is not None:
                analysis.indicator_accuracy[name] = correct

        # Count how many indicators got it right
        if analysis.indicator_accuracy:
            correct_count = sum(1 for v in analysis.indicator_accuracy.values() if v)
            total_count = len(analysis.indicator_accuracy)
            analysis.thesis_correct = correct_count > total_count / 2

        # Determine mistake type
        if won:
            analysis.mistake_type = "none"
        elif not analysis.model_correct and not analysis.thesis_correct:
            analysis.mistake_type = "thesis"
            analysis.mistake_detail = f"{asset}_{timeframe}_{direction}"
            analysis.actionable = True
        elif analysis.execution_quality == "poor":
            analysis.mistake_type = "execution"
            analysis.mistake_detail = f"slippage_{asset}_{timeframe}"
            analysis.actionable = True
        elif not analysis.sizing_appropriate:
            analysis.mistake_type = "sizing"
            analysis.mistake_detail = f"oversized_{asset}_{timeframe}"
            analysis.actionable = True
        elif not analysis.model_correct:
            analysis.mistake_type = "model"
            analysis.mistake_detail = f"{asset}_{timeframe}_{regime}"
            analysis.actionable = True

        # Save analysis
        self._save_analysis(analysis)

        # Track mistake
        if analysis.mistake_type != "none" and analysis.mistake_detail:
            key = f"{analysis.mistake_type}:{analysis.mistake_detail}"
            self._mistake_counts[key] = self._mistake_counts.get(key, 0) + 1
            log.info(
                "[POST-TRADE] %s: mistake=%s detail=%s (count=%d)",
                analysis.trade_id[:12], analysis.mistake_type,
                analysis.mistake_detail, self._mistake_counts[key],
            )

        return analysis

    def maybe_create_rule(self, analysis: TradeAnalysis) -> dict | None:
        """If a mistake has repeated >= MISTAKE_THRESHOLD times, auto-create a rule.

        Returns:
            The new rule dict if created, else None.
        """
        if analysis.mistake_type == "none" or not analysis.mistake_detail:
            return None

        key = f"{analysis.mistake_type}:{analysis.mistake_detail}"
        count = self._mistake_counts.get(key, 0)
        if count < MISTAKE_THRESHOLD:
            return None

        # Load existing rules
        rules = self._load_rules()

        # Check if rule already exists
        for r in rules:
            if r.get("key") == key and not r.get("expired"):
                return None  # Already have this rule

        # Check rule cap
        active_rules = [r for r in rules if not r.get("expired")]
        if len(active_rules) >= MAX_ACTIVE_RULES:
            # Expire oldest rule to make room
            oldest = min(active_rules, key=lambda r: r.get("created_at", 0))
            oldest["expired"] = True
            log.info("[AUTO-RULE] Expired oldest rule to make room: %s", oldest.get("key"))

        # Create new rule based on mistake type
        rule = {
            "key": key,
            "mistake_type": analysis.mistake_type,
            "detail": analysis.mistake_detail,
            "count": count,
            "created_at": time.time(),
            "expires_at": time.time() + RULE_EXPIRY_DAYS * 86400,
            "expired": False,
            "action": self._determine_action(analysis),
        }

        rules.append(rule)
        self._save_rules(rules)
        self._save_lesson(analysis, rule)

        log.info(
            "[AUTO-RULE] Created: %s -> %s (mistake repeated %dx)",
            key, rule["action"], count,
        )
        return rule

    @staticmethod
    def _determine_action(analysis: TradeAnalysis) -> dict:
        """Determine the corrective action for a repeated mistake."""
        parts = analysis.mistake_detail.split("_")
        asset = parts[0] if parts else ""
        timeframe = parts[1] if len(parts) > 1 else ""

        if analysis.mistake_type == "model":
            return {
                "type": "raise_edge_floor",
                "asset": asset,
                "timeframe": timeframe,
                "edge_floor_boost": 0.03,  # +3% edge required
            }
        if analysis.mistake_type == "execution":
            return {
                "type": "tighten_spread",
                "asset": asset,
                "timeframe": timeframe,
                "max_spread_reduction": 0.01,  # Reduce max spread by 1 cent
            }
        if analysis.mistake_type == "sizing":
            return {
                "type": "cap_conviction",
                "asset": asset,
                "timeframe": timeframe,
                "conviction_cap": 60,  # Cap conviction at 60/100
            }
        if analysis.mistake_type == "thesis":
            return {
                "type": "flag_indicators",
                "asset": asset,
                "timeframe": timeframe,
                "description": "Multiple indicators consistently wrong for this combo",
            }
        return {"type": "warning", "description": analysis.mistake_detail}

    @staticmethod
    def _load_rules() -> list[dict]:
        """Load auto-rules from disk."""
        if not AUTO_RULES_FILE.exists():
            return []
        try:
            return json.loads(AUTO_RULES_FILE.read_text())
        except Exception:
            return []

    @staticmethod
    def _save_rules(rules: list[dict]) -> None:
        """Save auto-rules to disk."""
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        AUTO_RULES_FILE.write_text(json.dumps(rules, indent=2))

    @staticmethod
    def _save_analysis(analysis: TradeAnalysis) -> None:
        """Append analysis to JSONL file."""
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(ANALYSIS_FILE, "a") as f:
            f.write(json.dumps(asdict(analysis)) + "\n")

    @staticmethod
    def _save_lesson(analysis: TradeAnalysis, rule: dict) -> None:
        """Save lesson learned from repeated mistake."""
        lessons = []
        if LESSONS_FILE.exists():
            try:
                lessons = json.loads(LESSONS_FILE.read_text())
            except Exception:
                lessons = []

        lessons.append({
            "timestamp": time.time(),
            "mistake_type": analysis.mistake_type,
            "detail": analysis.mistake_detail,
            "rule_created": rule.get("action", {}),
            "lesson": f"Repeated {analysis.mistake_type} mistake on {analysis.mistake_detail} "
                      f"({rule.get('count', 0)}x) — auto-rule created.",
        })

        LESSONS_FILE.write_text(json.dumps(lessons, indent=2))

    @classmethod
    def get_active_rules(cls) -> list[dict]:
        """Return currently active (non-expired) auto-rules."""
        rules = cls._load_rules()
        now = time.time()
        active = []
        for r in rules:
            if r.get("expired"):
                continue
            if r.get("expires_at", 0) < now:
                r["expired"] = True
                continue
            active.append(r)
        return active

    @classmethod
    def get_analysis_summary(cls, limit: int = 20) -> dict:
        """Return summary of recent post-trade analyses."""
        if not ANALYSIS_FILE.exists():
            return {"total": 0, "recent": []}

        analyses = []
        try:
            for line in ANALYSIS_FILE.read_text().splitlines():
                if not line.strip():
                    continue
                analyses.append(json.loads(line))
        except Exception:
            pass

        recent = analyses[-limit:]
        total = len(analyses)
        mistakes = [a for a in analyses if a.get("mistake_type") != "none"]
        by_type = {}
        for m in mistakes:
            mt = m.get("mistake_type", "unknown")
            by_type[mt] = by_type.get(mt, 0) + 1

        avg_ev_capture = 0.0
        ev_analyses = [a for a in analyses if a.get("ev_predicted", 0) > 0]
        if ev_analyses:
            avg_ev_capture = sum(a.get("ev_capture_pct", 0) for a in ev_analyses) / len(ev_analyses)

        return {
            "total": total,
            "mistakes": len(mistakes),
            "by_type": by_type,
            "avg_ev_capture_pct": round(avg_ev_capture * 100, 1),
            "active_rules": len(cls.get_active_rules()),
            "recent": recent[-10:],
        }
