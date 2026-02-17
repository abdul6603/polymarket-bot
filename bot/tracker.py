from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

from bot.config import Config
from bot.http_session import get_session
from bot.signals import Signal
from bot.weight_learner import record_indicator_votes
from bot.v2_tools import generate_signal_rationale, push_trade_alert, format_trade_alert

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
TRADES_FILE = DATA_DIR / "trades.jsonl"


@dataclass
class TradeRecord:
    # Signal info
    trade_id: str
    timestamp: float
    asset: str
    timeframe: str
    direction: str          # "up" or "down"
    probability: float      # our predicted probability
    edge: float
    confidence: float
    token_id: str
    market_id: str
    question: str

    # Market context at signal time
    implied_up_price: float  # Polymarket implied prob of UP
    binance_price: float     # Binance spot at signal time

    # Indicator votes at signal time (for weight learning)
    indicator_votes: dict = field(default_factory=dict)

    # Market regime at signal time
    regime_label: str = ""      # "extreme_fear", "fear", "neutral", "greed", "extreme_greed"
    regime_fng: int = -1        # Fear & Greed value (0-100), -1 = unknown

    # Reward-to-Risk ratio at signal time
    reward_risk_ratio: float = 0.0

    # V2: Signal rationale (human-readable trade reasoning)
    signal_rationale: str = ""

    # Resolution (filled in later)
    resolved: bool = False
    outcome: str = ""       # "up" or "down" — actual market result
    won: bool = False
    resolve_time: float = 0.0
    market_end_time: float = 0.0  # when the candle expires

    # Live vs dry-run
    dry_run: bool = True


class PerformanceTracker:
    """Records every signal prediction and checks market resolution to compute win rate."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        DATA_DIR.mkdir(exist_ok=True)
        self._pending: dict[str, TradeRecord] = {}  # trade_id -> record
        self._load_pending()

    def _load_pending(self) -> None:
        """Load unresolved trades from disk on startup."""
        if not TRADES_FILE.exists():
            return
        try:
            with open(TRADES_FILE) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    if not rec.get("resolved"):
                        self._pending[rec["trade_id"]] = TradeRecord(**rec)
            log.info("Loaded %d pending trades to resolve", len(self._pending))
        except Exception:
            log.exception("Failed to load trade history")

    def record_signal(
        self,
        signal: Signal,
        market_id: str,
        question: str,
        implied_up_price: float,
        binance_price: float,
        market_end_time: float,
        indicator_votes: dict | None = None,
        regime_label: str = "",
        regime_fng: int = -1,
    ) -> None:
        """Record a new signal prediction."""
        trade_id = f"{market_id[:12]}_{int(time.time())}"
        # V2: Generate signal rationale
        rationale = generate_signal_rationale(
            direction=signal.direction,
            indicator_votes=indicator_votes or {},
            edge=signal.edge,
            confidence=signal.confidence,
            regime_label=regime_label,
            regime_fng=regime_fng,
            asset=signal.asset,
            timeframe=signal.timeframe,
            implied_up_price=implied_up_price,
        )

        rec = TradeRecord(
            trade_id=trade_id,
            timestamp=time.time(),
            asset=signal.asset,
            timeframe=signal.timeframe,
            direction=signal.direction,
            probability=signal.probability,
            edge=signal.edge,
            confidence=signal.confidence,
            token_id=signal.token_id,
            market_id=market_id,
            question=question,
            implied_up_price=implied_up_price,
            binance_price=binance_price,
            indicator_votes=indicator_votes or {},
            market_end_time=market_end_time,
            regime_label=regime_label,
            regime_fng=regime_fng,
            reward_risk_ratio=getattr(signal, "reward_risk_ratio", 0.0) or 0.0,
            signal_rationale=rationale,
            dry_run=self.cfg.dry_run,
        )
        self._pending[trade_id] = rec
        self._append_to_file(rec)
        log.info(
            "Tracked signal: %s %s/%s %s (prob=%.1f%%, edge=%.1f%%) expires=%s",
            trade_id, signal.asset.upper(), signal.timeframe,
            signal.direction.upper(), signal.probability * 100, signal.edge * 100,
            datetime.fromtimestamp(market_end_time, tz=ZoneInfo("America/New_York")).strftime("%I:%M%p ET"),
        )
        log.info("Rationale: %s", rationale)

        # V2: Push new trade alert to Shelby
        push_trade_alert(format_trade_alert(asdict(rec), "new_trade"), "new_trade")

    def check_resolutions(self) -> None:
        """Poll CLOB API to check if any pending markets have resolved."""
        now = time.time()
        resolved_ids = []

        for trade_id, rec in list(self._pending.items()):
            # Only check after market end time + small buffer
            if now < rec.market_end_time + 30:
                continue

            outcome = self._fetch_resolution(rec.market_id)
            if outcome is None:
                # Not resolved yet — if it's been more than 30 min past end, skip
                if now > rec.market_end_time + 1800:
                    log.warning("Trade %s: market %s still unresolved after 30min, marking stale", trade_id, rec.market_id[:12])
                    rec.resolved = True
                    rec.outcome = "unknown"
                    rec.resolve_time = now
                    resolved_ids.append(trade_id)
                continue

            rec.resolved = True
            rec.outcome = outcome
            rec.won = (rec.direction == outcome)
            rec.resolve_time = now
            resolved_ids.append(trade_id)

            result = "WIN" if rec.won else "LOSS"
            log.info(
                "RESOLVED %s: %s %s/%s predicted=%s actual=%s | %s",
                trade_id, rec.asset.upper(), rec.timeframe,
                result, rec.direction.upper(), outcome.upper(),
                rec.question[:50],
            )

            # Record indicator accuracy for dynamic weight learning
            if rec.indicator_votes:
                record_indicator_votes(rec, rec.indicator_votes)

            # V2: Push resolution alert to Shelby
            push_trade_alert(format_trade_alert(asdict(rec), "resolution"), "resolution")

            # macOS notification (Feature 2: Trade Alerts)
            try:
                subprocess.run(
                    [
                        "osascript", "-e",
                        f'display notification "{result}: {rec.asset.upper()}/{rec.timeframe} {rec.direction}" with title "GARVES" sound name "Glass"',
                    ],
                    capture_output=True,
                )
            except Exception:
                log.debug("Failed to send macOS notification")

        # Collect resolved records BEFORE removing from pending
        if resolved_ids:
            resolved_records = {tid: self._pending[tid] for tid in resolved_ids}
            for tid in resolved_ids:
                del self._pending[tid]
            self._rewrite_file(resolved_records)

    def _fetch_resolution(self, market_id: str) -> str | None:
        """Check if a Polymarket condition has resolved. Returns 'up', 'down', or None."""
        try:
            resp = get_session().get(
                f"{self.cfg.clob_host}/markets/{market_id}",
                timeout=10,
            )
            if resp.status_code != 200:
                return None

            data = resp.json()

            # Check if market is resolved
            if not data.get("closed"):
                return None

            # Find which token won (price ~1.0 after resolution)
            tokens = data.get("tokens", [])
            for t in tokens:
                outcome_label = (t.get("outcome") or "").lower()
                winner = t.get("winner", False)
                if winner:
                    if outcome_label in ("up", "yes"):
                        return "up"
                    elif outcome_label in ("down", "no"):
                        return "down"

            # Fallback: check final prices
            for t in tokens:
                outcome_label = (t.get("outcome") or "").lower()
                price = float(t.get("price", 0))
                if price > 0.9:
                    if outcome_label in ("up", "yes"):
                        return "up"
                    elif outcome_label in ("down", "no"):
                        return "down"

            return None
        except Exception:
            log.debug("Could not fetch resolution for %s", market_id[:12])
            return None

    def _append_to_file(self, rec: TradeRecord) -> None:
        """Append a single trade record to the JSONL file."""
        try:
            with open(TRADES_FILE, "a") as f:
                f.write(json.dumps(asdict(rec)) + "\n")
        except Exception:
            log.exception("Failed to write trade record")

    def _rewrite_file(self, resolved_records: dict[str, "TradeRecord"] | None = None) -> None:
        """Rewrite entire file with updated resolution data."""
        try:
            all_records = []
            if TRADES_FILE.exists():
                with open(TRADES_FILE) as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        all_records.append(json.loads(line))

            # Build map of records to update (resolved ones)
            update_map = {}
            if resolved_records:
                for tid, rec in resolved_records.items():
                    update_map[tid] = asdict(rec)

            # Deduplicate and apply updates
            updated = []
            seen = set()
            for rec_dict in all_records:
                tid = rec_dict["trade_id"]
                if tid in seen:
                    continue
                seen.add(tid)
                if tid in update_map:
                    updated.append(update_map[tid])
                else:
                    updated.append(rec_dict)

            with open(TRADES_FILE, "w") as f:
                for rec_dict in updated:
                    f.write(json.dumps(rec_dict) + "\n")
        except Exception:
            log.exception("Failed to rewrite trade file")

    @property
    def pending_count(self) -> int:
        return len(self._pending)
