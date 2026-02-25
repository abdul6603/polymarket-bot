"""Trade Journal + Learning — futures-specific wrapper around AgentMemory.

Records every trade decision with full context, tracks outcomes,
extracts patterns from historical performance.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from shared.agent_memory import AgentMemory

log = logging.getLogger("odin.journal")
ET = ZoneInfo("America/New_York")


class OdinJournal:
    """Futures-specific trade journal backed by AgentMemory('odin')."""

    def __init__(self):
        self._memory = AgentMemory("odin")
        self._trade_count = 0

    def record_trade_open(self, signal_dict: dict) -> str:
        """Log a trade decision with full context. Returns decision_id."""
        symbol = signal_dict.get("symbol", "UNKNOWN")
        direction = signal_dict.get("direction", "UNKNOWN")
        regime = signal_dict.get("regime", "neutral")
        conviction = signal_dict.get("conviction_score", 0)
        smc = signal_dict.get("smc_patterns", [])

        now = datetime.now(ET)
        hour_bucket = f"h{now.hour // 4 * 4}-{(now.hour // 4 + 1) * 4}"

        context = (
            f"{symbol} {direction} | regime={regime} | "
            f"conviction={conviction:.0f}/100 | "
            f"entry=${signal_dict.get('entry_price', 0):.2f} | "
            f"sl=${signal_dict.get('stop_loss', 0):.2f} | "
            f"tp=${signal_dict.get('take_profit', 0):.2f} | "
            f"smc={','.join(smc[:3]) if smc else 'none'}"
        )

        breakdown = signal_dict.get("conviction_breakdown", {})
        reasoning = json.dumps(breakdown, default=str) if breakdown else ""

        tags = [symbol, direction.lower(), regime.lower(), hour_bucket]

        decision_id = self._memory.record_decision(
            context=context,
            decision=f"{direction} {symbol} @ ${signal_dict.get('entry_price', 0):.2f}",
            reasoning=reasoning,
            confidence=min(conviction / 100.0, 1.0),
            tags=tags,
        )

        self._trade_count += 1
        log.info("[JOURNAL] Recorded open: %s %s (id=%s)", direction, symbol, decision_id)
        return decision_id

    def record_trade_close(self, decision_id: str, outcome_dict: dict) -> None:
        """Record trade outcome: PnL, exit reason, hold time."""
        pnl = outcome_dict.get("pnl_usd", 0)
        reason = outcome_dict.get("exit_reason", "")
        hold_h = outcome_dict.get("hold_hours", 0)

        outcome_text = (
            f"PnL=${pnl:+.2f} | reason={reason} | "
            f"hold={hold_h:.1f}h | exit=${outcome_dict.get('exit_price', 0):.2f}"
        )

        # Score: +1 for big win, +0.5 for small win, -0.5 for small loss, -1 for big loss
        if pnl > 20:
            score = 1.0
        elif pnl > 0:
            score = 0.5
        elif pnl > -20:
            score = -0.5
        else:
            score = -1.0

        self._memory.record_outcome(decision_id, outcome_text, score)
        log.info("[JOURNAL] Recorded close: %s (pnl=$%.2f)", decision_id, pnl)

        # Write to Excel progress sheet
        self._write_excel_progress(outcome_dict)

        self._maybe_extract_pattern(outcome_dict)

    def get_journal_fitness(
        self, symbol: str, direction: str, regime: str, hour: int
    ) -> dict:
        """Query historical win rate for this symbol/direction/regime/hour combo."""
        hour_bucket = f"h{hour // 4 * 4}-{(hour // 4 + 1) * 4}"

        # Search for decisions matching these tags
        decisions = self._memory.get_recent_decisions(limit=100, resolved_only=True)

        matching = []
        for d in decisions:
            tags = json.loads(d.get("tags", "[]")) if isinstance(d.get("tags"), str) else d.get("tags", [])
            tag_set = set(t.lower() for t in tags)

            # Must match symbol + direction at minimum
            if symbol.lower() not in tag_set or direction.lower() not in tag_set:
                continue
            matching.append(d)

        if not matching:
            return {
                "win_rate": 50.0,
                "sample_size": 0,
                "avg_pnl": 0.0,
                "recommendation": "insufficient_data",
            }

        wins = sum(1 for d in matching if d.get("outcome_score", 0) > 0)
        total = len(matching)
        wr = wins / total * 100 if total > 0 else 50.0

        # Calculate from outcomes
        pnls = []
        for d in matching:
            outcome = d.get("outcome", "")
            if "PnL=$" in outcome:
                try:
                    pnl_str = outcome.split("PnL=$")[1].split("|")[0].strip()
                    pnls.append(float(pnl_str))
                except (ValueError, IndexError):
                    pass
        avg_pnl = sum(pnls) / len(pnls) if pnls else 0.0

        # Recommendation
        if total < 5:
            rec = "insufficient_data"
        elif wr >= 65:
            rec = "favorable"
        elif wr >= 50:
            rec = "neutral"
        elif wr >= 35:
            rec = "cautious"
        else:
            rec = "avoid"

        return {
            "win_rate": round(wr, 1),
            "sample_size": total,
            "avg_pnl": round(avg_pnl, 2),
            "recommendation": rec,
        }

    def get_recent_trades(self, limit: int = 50) -> list[dict]:
        """Read last N trades from odin_trades.jsonl with full context."""
        trades_file = Path.home() / "odin" / "data" / "odin_trades.jsonl"
        if not trades_file.exists():
            return []
        lines = trades_file.read_text().strip().split("\n")
        trades = []
        for line in lines[-limit:]:
            try:
                trades.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return trades

    def get_relevant_context(self, situation: str) -> list[dict]:
        """Find similar past decisions via semantic + keyword search."""
        return self._memory.get_relevant_context(situation, limit=5)

    def _write_excel_progress(self, outcome: dict) -> None:
        """Write trade result to Excel progress sheet via shared/progress.py."""
        try:
            import sys
            sys.path.insert(0, str(Path.home() / "shared"))
            from progress import append_progress

            pnl = outcome.get("pnl_usd", 0)
            symbol = outcome.get("symbol", "")
            direction = outcome.get("direction", "")
            reason = outcome.get("exit_reason", "")
            status = "WIN" if pnl > 0 else "LOSS"

            append_progress(
                agent="Odin",
                change_type="Trade",
                feature=f"{direction} {symbol}",
                description=f"PnL=${pnl:+.2f} | {reason}",
                status=status,
            )
        except Exception as e:
            log.debug("[JOURNAL] Excel write error: %s", str(e)[:100])

    def get_lessons(self, limit: int = 10) -> list[dict]:
        """Extract lessons from recent patterns."""
        winning = self._memory.get_active_patterns("winning_combo")
        losing = self._memory.get_active_patterns("losing_combo")

        lessons = []
        for p in winning[:limit // 2]:
            lessons.append({
                "type": "winning",
                "pattern": p.get("description", ""),
                "confidence": p.get("confidence", 0),
            })
        for p in losing[:limit // 2]:
            lessons.append({
                "type": "losing",
                "pattern": p.get("description", ""),
                "confidence": p.get("confidence", 0),
            })
        return lessons

    def _maybe_extract_pattern(self, outcome: dict) -> None:
        """Every 10th trade, scan recent history for patterns."""
        self._trade_count += 1
        if self._trade_count % 10 != 0:
            return

        decisions = self._memory.get_recent_decisions(limit=30, resolved_only=True)
        if len(decisions) < 10:
            return

        # Group by symbol+direction
        combos: dict[str, list[float]] = {}
        for d in decisions:
            tags = json.loads(d.get("tags", "[]")) if isinstance(d.get("tags"), str) else d.get("tags", [])
            if len(tags) >= 2:
                key = f"{tags[0]}_{tags[1]}"
                combos.setdefault(key, []).append(d.get("outcome_score", 0))

        for combo, scores in combos.items():
            if len(scores) < 5:
                continue
            wins = sum(1 for s in scores if s > 0)
            wr = wins / len(scores) * 100

            if wr >= 65:
                self._memory.add_pattern(
                    pattern_type="winning_combo",
                    description=f"{combo}: {wr:.0f}% WR over {len(scores)} trades",
                    evidence_count=len(scores),
                    confidence=min(wr / 100, 0.95),
                    tags=[combo],
                )
                log.info("[JOURNAL] Pattern found: %s %.0f%% WR", combo, wr)
            elif wr <= 35:
                self._memory.add_pattern(
                    pattern_type="losing_combo",
                    description=f"{combo}: {wr:.0f}% WR over {len(scores)} trades — AVOID",
                    evidence_count=len(scores),
                    confidence=min((100 - wr) / 100, 0.95),
                    tags=[combo],
                )
                log.info("[JOURNAL] Bad pattern: %s %.0f%% WR — flagged", combo, wr)

    def get_stats(self) -> dict:
        """Memory stats + Odin-specific metrics."""
        stats = self._memory.get_stats()

        # Add winning/losing pattern counts
        winning = self._memory.get_active_patterns("winning_combo")
        losing = self._memory.get_active_patterns("losing_combo")
        stats["winning_patterns"] = len(winning)
        stats["losing_patterns"] = len(losing)
        stats["top_patterns"] = [
            {"combo": p["description"][:50], "confidence": p["confidence"]}
            for p in (winning[:3] + losing[:3])
        ]

        return stats
