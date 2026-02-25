"""ReflectionEngine — post-trade review and lesson extraction for OdinBrain.

After every N trades (default 5), reviews recent outcomes and extracts
actionable lessons. Lessons are injected into every future LLM analysis call.

Uses local Qwen 14B for reflection (free, no API cost).
Stores lessons in AgentMemory patterns table with type "llm_lesson".
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from shared.agent_memory import AgentMemory

log = logging.getLogger("odin.reflection")

REFLECTION_PROMPT = """\
You are reviewing your recent trades as Odin, a crypto futures swing trader. \
Analyze what you did right and wrong. Extract 1-3 SPECIFIC, ACTIONABLE lessons. \
Be brutally honest.

Each lesson must be one clear sentence that a trader can immediately apply.

Respond with ONLY a JSON object:
{
  "lessons": [
    {"lesson": "...", "source": "WIN|LOSS", "confidence": 0.0-1.0, "action": "add|reinforce|deprecate"}
  ],
  "overall_assessment": "1-2 sentence summary"
}"""


class ReflectionEngine:
    """Post-trade review: logs context, reflects every N trades, extracts lessons."""

    def __init__(self, journal, brain):
        self._journal = journal
        self._brain = brain
        self._memory = AgentMemory("odin")
        self._trade_count = 0
        self._reflect_every_n = 5
        self._max_lessons = 20
        self._log_file = Path.home() / "odin" / "data" / "reflection_log.jsonl"
        self._log_file.parent.mkdir(parents=True, exist_ok=True)
        log.info("[REFLECTION] Initialized | reflect_every=%d max_lessons=%d",
                 self._reflect_every_n, self._max_lessons)

    def configure(self, reflect_every_n: int = 5, max_lessons: int = 20) -> None:
        """Update config from OdinConfig."""
        self._reflect_every_n = reflect_every_n
        self._max_lessons = max_lessons

    def log_trade_context(
        self,
        decision_id: str,
        signal,
        regime: object,
        macro: object,
        candle_summary: str,
    ) -> None:
        """Write full trade context to reflection log for later review."""
        entry = {
            "decision_id": decision_id,
            "symbol": signal.symbol,
            "direction": signal.direction,
            "entry_price": signal.entry_price,
            "stop_loss": signal.stop_loss,
            "take_profit_1": signal.take_profit_1,
            "conviction": signal.conviction_score,
            "risk_reward": signal.risk_reward,
            "regime": regime.regime.value if regime and hasattr(regime, "regime") else "neutral",
            "macro_score": getattr(macro, "score", 0) if macro else 0,
            "llm_reasoning": signal.reasons[:6] if signal.reasons else [],
            "lessons_used": list(self._brain._lessons[:5]) if self._brain._lessons else [],
            "candle_context": candle_summary[:500],
            "timestamp": time.time(),
        }
        try:
            with open(self._log_file, "a") as f:
                f.write(json.dumps(entry) + "\n")
            log.info("[REFLECTION] Logged context for %s %s (dec=%s)",
                     signal.direction, signal.symbol, decision_id)
        except Exception as e:
            log.warning("[REFLECTION] Log write error: %s", str(e)[:100])

    def log_trade_close(
        self,
        decision_id: str,
        symbol: str,
        direction: str,
        entry_price: float,
        exit_price: float,
        pnl_usd: float,
        exit_reason: str,
    ) -> None:
        """Append close data to the most recent reflection log entry."""
        self._trade_count += 1

        # Append close info to the log
        close_entry = {
            "decision_id": decision_id,
            "event": "close",
            "symbol": symbol,
            "direction": direction,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "pnl_usd": pnl_usd,
            "exit_reason": exit_reason,
            "timestamp": time.time(),
        }
        try:
            with open(self._log_file, "a") as f:
                f.write(json.dumps(close_entry) + "\n")
        except Exception:
            pass

        log.info("[REFLECTION] Trade #%d closed: %s %s PnL=$%.2f",
                 self._trade_count, direction, symbol, pnl_usd)

    def maybe_reflect(self) -> None:
        """Check if it's time to run reflection (every N trades)."""
        if self._trade_count > 0 and self._trade_count % self._reflect_every_n == 0:
            log.info("[REFLECTION] Trigger: %d trades completed — running reflection",
                     self._trade_count)
            try:
                lessons = self.run_reflection()
                if lessons:
                    log.info("[REFLECTION] Extracted %d lessons", len(lessons))
            except Exception as e:
                log.warning("[REFLECTION] Reflection failed: %s", str(e)[:200])

    def run_reflection(self) -> list[dict]:
        """Review recent trades and extract lessons using local LLM."""
        # Read recent closed trades from log
        trades = self._read_recent_closes(10)
        if len(trades) < 3:
            log.info("[REFLECTION] Not enough closed trades (%d) for reflection", len(trades))
            return []

        # Format trades for the LLM
        trade_text = self._format_trades_for_review(trades)
        current_lessons = self.get_active_lessons(limit=10)

        user_prompt = f"""{trade_text}

=== Current Active Lessons ===
{chr(10).join(current_lessons) if current_lessons else "No lessons yet."}

Review these trades. What did you learn?"""

        # Call local Qwen 14B (free)
        try:
            from shared.llm_client import llm_call
            raw = llm_call(
                system=REFLECTION_PROMPT,
                user=user_prompt,
                agent="odin",
                task_type="reflection",
                max_tokens=500,
                temperature=0.3,
            )
        except Exception as e:
            log.warning("[REFLECTION] LLM call failed: %s", str(e)[:200])
            return []

        if not raw:
            return []

        # Parse response
        try:
            # Try to extract JSON
            data = None
            raw = raw.strip()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                import re
                match = re.search(r"\{.*\}", raw, re.DOTALL)
                if match:
                    data = json.loads(match.group())

            if not data or "lessons" not in data:
                log.warning("[REFLECTION] No lessons in response")
                return []

            lessons = data["lessons"]
            assessment = data.get("overall_assessment", "")
            if assessment:
                log.info("[REFLECTION] Assessment: %s", assessment[:200])

            # Store lessons in AgentMemory
            for lesson_data in lessons:
                lesson_text = lesson_data.get("lesson", "")
                action = lesson_data.get("action", "add")
                confidence = lesson_data.get("confidence", 0.5)
                source = lesson_data.get("source", "")

                if not lesson_text:
                    continue

                if action == "deprecate":
                    # Find and deactivate matching lesson
                    self._deprecate_lesson(lesson_text)
                else:
                    # Add or reinforce
                    self._memory.add_pattern(
                        pattern_type="llm_lesson",
                        description=lesson_text,
                        evidence_count=1,
                        confidence=min(max(confidence, 0.1), 0.95),
                        tags=[source.lower(), "reflection"],
                    )
                    log.info("[REFLECTION] Lesson %s: %s (conf=%.2f, src=%s)",
                             action, lesson_text[:80], confidence, source)

            # Prune if too many
            self._prune_lessons()

            # Refresh brain's lessons
            self._brain.set_lessons(self.get_active_lessons())

            return lessons

        except Exception as e:
            log.warning("[REFLECTION] Parse error: %s", str(e)[:200])
            return []

    def get_active_lessons(self, limit: int = 5) -> list[str]:
        """Get formatted lessons for injection into analyst prompt."""
        patterns = self._memory.get_active_patterns(
            pattern_type="llm_lesson",
            min_confidence=0.4,
        )
        result = []
        for p in patterns[:limit]:
            desc = p.get("description", "")
            conf = p.get("confidence", 0)
            evidence = p.get("evidence_count", 0)
            result.append(f'"{desc}" (conf={conf:.2f}, {evidence} trades)')
        return result

    def _read_recent_closes(self, limit: int = 10) -> list[dict]:
        """Read recent closed trades from reflection log."""
        if not self._log_file.exists():
            return []
        closes = []
        try:
            lines = self._log_file.read_text().strip().split("\n")
            for line in reversed(lines):
                try:
                    entry = json.loads(line)
                    if entry.get("event") == "close":
                        closes.append(entry)
                        if len(closes) >= limit:
                            break
                except json.JSONDecodeError:
                    continue
        except Exception:
            pass
        return list(reversed(closes))

    def _format_trades_for_review(self, trades: list[dict]) -> str:
        """Format closed trades as text for the reflection LLM."""
        lines = ["=== Recent Closed Trades ==="]
        for i, t in enumerate(trades, 1):
            pnl = t.get("pnl_usd", 0)
            result = "WIN" if pnl > 0 else "LOSS"
            lines.append(
                f"{i}. {result}: {t.get('direction', '?')} {t.get('symbol', '?')} "
                f"entry=${t.get('entry_price', 0):.2f} exit=${t.get('exit_price', 0):.2f} "
                f"PnL=${pnl:+.2f} reason={t.get('exit_reason', '?')}"
            )
        return "\n".join(lines)

    def _deprecate_lesson(self, lesson_text: str) -> None:
        """Find and deactivate a lesson matching the text."""
        patterns = self._memory.get_active_patterns(pattern_type="llm_lesson")
        for p in patterns:
            if lesson_text.lower() in p.get("description", "").lower():
                self._memory.deactivate_pattern(p["id"])
                log.info("[REFLECTION] Deprecated lesson: %s", p["description"][:80])
                return

    def _prune_lessons(self) -> None:
        """Keep max N active lessons, deactivate lowest confidence."""
        patterns = self._memory.get_active_patterns(pattern_type="llm_lesson")
        if len(patterns) <= self._max_lessons:
            return

        # Already sorted by confidence desc — deactivate the tail
        for p in patterns[self._max_lessons:]:
            self._memory.deactivate_pattern(p["id"])
            log.info("[REFLECTION] Pruned low-conf lesson: %s (conf=%.2f)",
                     p["description"][:60], p["confidence"])
