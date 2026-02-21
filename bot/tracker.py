from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from dataclasses import asdict, dataclass, field, fields
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
DECISION_IDS_FILE = DATA_DIR / "decision_ids.json"


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

    # V3: Orderbook depth at execution time
    ob_liquidity_usd: float = 0.0
    ob_spread: float = 0.0
    ob_slippage_pct: float = 0.0

    # Execution data (for P&L tracking)
    size_usd: float = 0.0
    entry_price: float = 0.0
    pnl: float = 0.0

    # ML prediction at trade time
    ml_win_prob: float = 0.0

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

    def __init__(self, cfg: Config, position_tracker=None):
        self.cfg = cfg
        self._position_tracker = position_tracker  # PositionTracker to clean up on resolution
        self._total_resolved = 0  # lifetime counter for resolved trades
        DATA_DIR.mkdir(exist_ok=True)
        self._pending: dict[str, TradeRecord] = {}  # trade_id -> record
        self._decision_ids: dict[str, str] = {}  # trade_id -> brain decision_id
        self._load_decision_ids()
        self._load_pending()

    def _load_decision_ids(self) -> None:
        """Load persisted decision_id mappings from disk."""
        if not DECISION_IDS_FILE.exists():
            return
        try:
            with open(DECISION_IDS_FILE) as f:
                self._decision_ids = json.load(f)
            log.info("Loaded %d decision_id mappings from disk", len(self._decision_ids))
        except Exception:
            log.exception("Failed to load decision_ids")

    def _save_decision_ids(self) -> None:
        """Persist decision_id mappings to disk so they survive restarts."""
        try:
            with open(DECISION_IDS_FILE, "w") as f:
                json.dump(self._decision_ids, f)
        except Exception:
            log.exception("Failed to save decision_ids")

    def set_decision_id(self, trade_id: str, decision_id: str) -> None:
        """Map a trade_id to an AgentBrain decision_id for outcome tracking."""
        if decision_id:
            self._decision_ids[trade_id] = decision_id
            self._save_decision_ids()

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
                        # Filter to only known TradeRecord fields (handles schema changes)
                        valid_keys = {f.name for f in fields(TradeRecord)}
                        filtered = {k: v for k, v in rec.items() if k in valid_keys}
                        self._pending[rec["trade_id"]] = TradeRecord(**filtered)
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
        ob_liquidity_usd: float = 0.0,
        ob_spread: float = 0.0,
        ob_slippage_pct: float = 0.0,
        size_usd: float = 0.0,
        entry_price: float = 0.0,
        ml_win_prob: float = 0.0,
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
            ob_liquidity_usd=ob_liquidity_usd,
            ob_spread=ob_spread,
            ob_slippage_pct=ob_slippage_pct,
            size_usd=size_usd,
            entry_price=entry_price,
            ml_win_prob=ml_win_prob,
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
                # Timeframe-based timeout: longer markets get more time to resolve
                # Old: 30 min flat timeout caused 1h markets to be marked "unknown"
                timeout_map = {"5m": 600, "15m": 900, "1h": 7200, "4h": 18000, "weekly": 86400}
                timeout_s = timeout_map.get(rec.timeframe, 3600)
                if now > rec.market_end_time + timeout_s:
                    log.warning("Trade %s: market %s still unresolved after %dmin, marking stale",
                                trade_id, rec.market_id[:12], timeout_s // 60)
                    rec.resolved = True
                    rec.outcome = "unknown"
                    rec.resolve_time = now
                    resolved_ids.append(trade_id)
                continue

            rec.resolved = True
            rec.outcome = outcome
            rec.won = (rec.direction == outcome)
            rec.resolve_time = now
            self._total_resolved += 1
            if rec.entry_price > 0 and rec.size_usd > 0:
                shares = rec.size_usd / rec.entry_price
                if rec.won:
                    rec.pnl = round(shares * 1.0 - rec.size_usd, 2)
                else:
                    rec.pnl = round(-rec.size_usd, 2)
            resolved_ids.append(trade_id)

            result = "WIN" if rec.won else "LOSS"
            log.info(
                "RESOLVED %s: %s %s/%s predicted=%s actual=%s | %s",
                trade_id, rec.asset.upper(), rec.timeframe,
                result, rec.direction.upper(), outcome.upper(),
                rec.question[:50],
            )

            # Clean up resolved position from PositionTracker
            if self._position_tracker:
                self._position_tracker.remove_resolved_trade(trade_id)

            # Record indicator accuracy — but ONLY for actually resolved trades,
            # NOT for "unknown" outcomes (those would poison the weight learner)
            if rec.indicator_votes and rec.outcome in ("up", "down"):
                record_indicator_votes(rec, rec.indicator_votes)

            # V2: Push resolution alert to Shelby
            push_trade_alert(format_trade_alert(asdict(rec), "resolution"), "resolution")

            # Publish trade_resolved to shared event bus (enriched for Quant)
            try:
                from shared.events import publish as bus_publish
                bus_publish(
                    agent="garves",
                    event_type="trade_resolved",
                    data={
                        "trade_id": trade_id,
                        "asset": rec.asset,
                        "direction": rec.direction,
                        "timeframe": rec.timeframe,
                        "outcome": "win" if rec.won else "loss",
                        "actual_result": rec.outcome,
                        "won": rec.won,
                        "market_id": rec.market_id,
                        "indicator_votes": rec.indicator_votes,
                        "edge": rec.edge,
                        "confidence": rec.confidence,
                        "probability": rec.probability,
                        "pnl": rec.pnl,
                        "size_usd": rec.size_usd,
                        "entry_price": rec.entry_price,
                        "regime_label": rec.regime_label,
                        "regime_fng": rec.regime_fng,
                        "reward_risk_ratio": rec.reward_risk_ratio,
                        "implied_up_price": rec.implied_up_price,
                        "binance_price": rec.binance_price,
                        "ml_win_prob": rec.ml_win_prob,
                        "dry_run": rec.dry_run,
                    },
                    summary=f"{'WIN' if rec.won else 'LOSS'}: {rec.asset.upper()}/{rec.timeframe} predicted={rec.direction.upper()} actual={rec.outcome.upper()}",
                )
            except Exception:
                pass

            # Brain: record outcome
            try:
                import sys as _sys2
                _sys2.path.insert(0, str(Path.home() / "shared"))
                from agent_brain import AgentBrain
                _brain = AgentBrain("garves")
                _did = self._decision_ids.get(trade_id)
                if _did:
                    _score = 1.0 if rec.won else -1.0
                    _brain.remember_outcome(_did, f"{'WIN' if rec.won else 'LOSS'}: predicted={rec.direction} actual={outcome}", score=_score)
                    # Learn pattern from outcome
                    if rec.regime_label:
                        _brain.learn_pattern(
                            "trade_outcome",
                            f"{rec.asset}/{rec.timeframe} in {rec.regime_label}: {'WON' if rec.won else 'LOST'} predicting {rec.direction}",
                            evidence_count=1,
                            confidence=0.6 if rec.won else 0.4,
                        )
                    # Clean up resolved mapping
                    self._decision_ids.pop(trade_id, None)
                    self._save_decision_ids()
            except Exception:
                pass

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
        """Rewrite entire file with updated resolution data (atomic via temp file)."""
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

            # Atomic write: temp file + rename prevents data loss on crash
            import os
            tmp_path = TRADES_FILE.with_suffix(".jsonl.tmp")
            with open(tmp_path, "w") as f:
                for rec_dict in updated:
                    f.write(json.dumps(rec_dict) + "\n")
            os.replace(str(tmp_path), str(TRADES_FILE))
        except Exception:
            log.exception("Failed to rewrite trade file")

    def quick_stats(self) -> dict:
        """Compute quick stats from trades.jsonl for resolved trades."""
        wins = losses = total_pnl = 0
        if not TRADES_FILE.exists():
            return {"wins": 0, "losses": 0, "win_rate": 0.0, "pnl": 0.0, "total_resolved": self._total_resolved}
        try:
            with open(TRADES_FILE) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    if rec.get("resolved") and rec.get("outcome") in ("up", "down"):
                        if rec.get("won"):
                            wins += 1
                        else:
                            losses += 1
                        total_pnl += rec.get("pnl", 0.0)
        except Exception:
            pass
        total = wins + losses
        return {
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / total * 100, 1) if total else 0.0,
            "pnl": round(total_pnl, 2),
            "total_resolved": self._total_resolved,
        }

    @property
    def pending_count(self) -> int:
        return len(self._pending)
