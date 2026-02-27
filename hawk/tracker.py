"""Position + P&L + Category Tracker for Hawk V2."""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from difflib import SequenceMatcher
from hawk.edge import TradeOpportunity

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
TRADES_FILE = DATA_DIR / "hawk_trades.jsonl"

ET = ZoneInfo("America/New_York")


class HawkTracker:
    """Track open positions, trade history, and per-category stats."""

    def __init__(self):
        DATA_DIR.mkdir(exist_ok=True)
        self._positions: list[dict] = []
        self._decision_ids: dict[str, str] = {}
        self._cancel_cooldowns: dict[str, float] = {}  # condition_id -> cancel timestamp
        self._load_positions()

    def _load_positions(self) -> None:
        """Load unresolved trades from disk on startup + rebuild decision_id map."""
        if not TRADES_FILE.exists():
            return
        try:
            with open(TRADES_FILE) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    # Rebuild decision_id mapping from persisted data
                    did = rec.get("decision_id", "")
                    if did:
                        cid = rec.get("condition_id") or rec.get("market_id", "")
                        if cid:
                            self._decision_ids[cid] = did
                    if not rec.get("resolved"):
                        self._positions.append(rec)
            log.info("Loaded %d open Hawk positions, %d decision IDs restored",
                     len(self._positions), len(self._decision_ids))
        except Exception:
            log.exception("Failed to load Hawk trade history")

    @property
    def open_positions(self) -> list[dict]:
        return list(self._positions)

    @property
    def total_exposure(self) -> float:
        return sum(p.get("size_usd", 0) for p in self._positions
                   if p.get("filled", True))

    @property
    def count(self) -> int:
        return sum(1 for p in self._positions if p.get("filled", True))

    def has_position_for_market(self, condition_id: str, question: str = "") -> bool:
        """Check by condition_id + question similarity (blocks near-duplicate bets)."""
        # Block recently-cancelled markets for 30 min to prevent ghost orders
        cooldown_ts = self._cancel_cooldowns.get(condition_id, 0)
        if cooldown_ts and (time.time() - cooldown_ts) < 1800:
            log.info("Cooldown active for %s (cancelled %.0fm ago)",
                     condition_id[:12], (time.time() - cooldown_ts) / 60)
            return True
        for p in self._positions:
            # Exact ID match
            if p.get("condition_id") == condition_id or p.get("market_id") == condition_id:
                return True
            # Question similarity check (blocks "BTC $66-68k" vs "BTC $68-70k" type dupes)
            if question and p.get("question"):
                ratio = SequenceMatcher(None, question.lower()[:80], p["question"].lower()[:80]).ratio()
                if ratio > 0.92:
                    log.warning("Similar market blocked (%.0f%% match): %s ~ %s",
                                ratio * 100, question[:50], p["question"][:50])
                    return True
        return False

    def record_trade(self, opp: TradeOpportunity, order_id: str,
                     order_placed_at: float = 0.0,
                     market_price_at_entry: float = 0.0,
                     filled: bool = True) -> None:
        """Append trade to JSONL and track in memory.

        V8: Accepts order_placed_at and market_price_at_entry for fill tracking.
        V8: Checks BOTH in-memory positions AND full JSONL history for duplicates.
        V10: filled=False for live orders until fill confirmed by check_fills().
        """
        if self.has_position_for_market(opp.market.condition_id, opp.market.question):
            log.warning("Duplicate trade blocked: already have position for %s", opp.market.condition_id[:12])
            return

        # V8: Also check full JSONL history — but only UNRESOLVED trades
        # Fix 7: Resolved trades must not block re-entry into a market
        all_trades = self._load_all_trades()
        for t in all_trades:
            if (t.get("condition_id") == opp.market.condition_id
                    and not t.get("resolved")):
                log.warning("Duplicate trade blocked (JSONL history): %s already exists (unresolved)", opp.market.condition_id[:12])
                return
        rec = {
            "trade_id": f"hawk_{opp.market.condition_id[:8]}_{int(time.time())}",
            "order_id": order_id,
            "condition_id": opp.market.condition_id,
            "question": opp.market.question[:200],
            "category": opp.market.category,
            "direction": opp.direction,
            "token_id": opp.token_id,
            "size_usd": opp.position_size_usd,
            "entry_price": _get_price(opp),
            "edge": opp.edge,
            "estimated_prob": opp.estimate.estimated_prob,
            "confidence": opp.estimate.confidence,
            "reasoning": opp.estimate.reasoning[:500],
            "kelly_fraction": opp.kelly_fraction,
            "expected_value": opp.expected_value,
            # V2 new fields
            "risk_score": opp.risk_score,
            "edge_source": opp.estimate.edge_source,
            "time_left_hours": opp.time_left_hours,
            "urgency_label": opp.urgency_label,
            "money_thesis": opp.estimate.money_thesis[:300],
            "news_factor": opp.estimate.news_factor[:300],
            # Market end date (ISO 8601)
            "end_date": opp.market.end_date,
            "event_slug": getattr(opp.market, 'event_slug', ''),
            # V8: Fill tracking fields
            "order_placed_at": order_placed_at or time.time(),
            "market_price_at_entry": market_price_at_entry,
            "game_id": self._compute_game_id(opp),
            # Timestamps
            "timestamp": time.time(),
            "opened_at": time.time(),
            "time_str": datetime.now(ET).strftime("%Y-%m-%d %I:%M%p"),
            # Brain tracking
            "decision_id": "",
            # Fill tracking
            "filled": filled,
            "original_size_usd": opp.position_size_usd,
            # Resolution fields
            "resolved": False,
            "outcome": "",
            "won": False,
            "resolve_time": 0.0,
            "pnl": 0.0,
        }
        self._positions.append(rec)
        self._append_to_file(rec)
        log.info(
            "Tracked Hawk trade: %s %s | $%.2f | edge=%.1f%% | risk=%d/10 | %s | %s",
            opp.direction.upper(), opp.market.condition_id[:12],
            opp.position_size_usd, opp.edge * 100,
            opp.risk_score, opp.urgency_label or "no-urgency", opp.market.category,
        )

    def set_decision_id(self, condition_id: str, decision_id: str) -> None:
        """Map a condition_id to a brain decision_id for outcome tracking.

        Persists to JSONL so decision IDs survive restarts.
        """
        self._decision_ids[condition_id] = decision_id
        self._update_trade_field(condition_id, "decision_id", decision_id)

    def get_decision_id(self, condition_id: str) -> str:
        """Get brain decision_id for a condition_id, or empty string."""
        return self._decision_ids.get(condition_id, "")

    @staticmethod
    def _compute_game_id(opp: TradeOpportunity) -> str:
        """V8: Compute game_id for correlation tracking."""
        try:
            from hawk.risk import extract_game_id
            return extract_game_id(opp.market.question, getattr(opp.market, 'event_slug', '')) or ""
        except Exception:
            return ""

    def add_cooldown(self, condition_id: str) -> None:
        """Block a market from new orders for 30 min after cancel."""
        self._cancel_cooldowns[condition_id] = time.time()
        # Cleanup expired cooldowns
        now = time.time()
        self._cancel_cooldowns = {
            k: v for k, v in self._cancel_cooldowns.items()
            if (now - v) < 1800
        }
        log.info("Cooldown set for %s (30 min)", condition_id[:12])

    def mark_filled(self, order_id: str) -> None:
        """Mark an order as filled (confirmed by CLOB API)."""
        for p in self._positions:
            if p.get("order_id") == order_id:
                p["filled"] = True
                self._update_trade_field(
                    p.get("condition_id", ""), "filled", True)
                log.info("Marked order %s as FILLED", order_id)
                return

    def remove_position(self, order_id: str) -> None:
        """Remove a position by order ID."""
        self._positions = [p for p in self._positions if p.get("order_id") != order_id]

    def cumulative_pnl(self) -> float:
        """Total realized P&L across all resolved trades (for compound bankroll)."""
        all_trades = self._load_all_trades()
        return sum(t.get("pnl", 0) for t in all_trades if t.get("resolved"))

    def category_stats(self) -> dict:
        """Returns {category: {wins, losses, pnl, win_rate}} for heatmap."""
        all_trades = self._load_all_trades()
        resolved = [t for t in all_trades if t.get("resolved")]
        cats: dict[str, dict] = {}
        for t in resolved:
            cat = t.get("category", "other")
            if cat not in cats:
                cats[cat] = {"wins": 0, "losses": 0, "pnl": 0.0}
            if t.get("won"):
                cats[cat]["wins"] += 1
            else:
                cats[cat]["losses"] += 1
            cats[cat]["pnl"] += t.get("pnl", 0.0)
        for cat in cats:
            total = cats[cat]["wins"] + cats[cat]["losses"]
            cats[cat]["win_rate"] = (cats[cat]["wins"] / total * 100) if total > 0 else 0
        return cats

    def summary(self) -> dict:
        """Overall stats for dashboard."""
        all_trades = self._load_all_trades()
        resolved = [t for t in all_trades if t.get("resolved") and t.get("outcome")]
        wins = sum(1 for t in resolved if t.get("won"))
        losses = len(resolved) - wins
        total_pnl = sum(t.get("pnl", 0) for t in resolved)
        wr = (wins / len(resolved) * 100) if resolved else 0
        return {
            "total_trades": len(all_trades),
            "resolved": len(resolved),
            "wins": wins,
            "losses": losses,
            "win_rate": round(wr, 1),
            "pnl": round(total_pnl, 2),
            "open_positions": self.count,
            "total_exposure": round(self.total_exposure, 2),
            "daily_pnl": self._daily_pnl(resolved),
            "cumulative_pnl": round(total_pnl, 2),
        }

    def _daily_pnl(self, resolved: list[dict]) -> float:
        """Calculate today's P&L."""
        today = datetime.now(ET).strftime("%Y-%m-%d")
        daily = [t for t in resolved if t.get("time_str", "").startswith(today)]
        return round(sum(t.get("pnl", 0) for t in daily), 2)

    def _load_all_trades(self) -> list[dict]:
        """Load all trades from JSONL file."""
        if not TRADES_FILE.exists():
            return []
        trades = []
        try:
            with open(TRADES_FILE) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        trades.append(json.loads(line))
        except Exception:
            log.exception("Failed to load Hawk trades")
        return trades

    def _update_trade_field(self, condition_id: str, field: str, value) -> None:
        """Update a field in the JSONL file for a specific condition_id."""
        if not TRADES_FILE.exists():
            return
        try:
            trades = []
            updated = False
            with open(TRADES_FILE) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    cid = rec.get("condition_id") or rec.get("market_id", "")
                    if cid == condition_id and not updated:
                        rec[field] = value
                        updated = True
                    trades.append(rec)
            if updated:
                tmp = TRADES_FILE.with_suffix(".tmp")
                with open(tmp, "w") as f:
                    for t in trades:
                        f.write(json.dumps(t) + "\n")
                os.replace(tmp, TRADES_FILE)
        except Exception:
            log.exception("Failed to update trade field %s for %s", field, condition_id[:12])

    def backfill_end_dates(self) -> int:
        """Backfill end_date for open positions missing it via CLOB API.

        Returns number of positions updated.
        """
        import urllib.request
        updated = 0
        for pos in self._positions:
            if pos.get("end_date"):
                continue
            cid = pos.get("condition_id", "")
            if not cid:
                continue
            try:
                url = f"https://clob.polymarket.com/markets/{cid}"
                req = urllib.request.Request(url, headers={"User-Agent": "Hawk/1.0"})
                with urllib.request.urlopen(req, timeout=8) as resp:
                    data = json.loads(resp.read().decode())
                end_date = data.get("end_date_iso") or ""
                if end_date and end_date != "None":
                    pos["end_date"] = end_date
                    self._update_trade_field(cid, "end_date", end_date)
                    updated += 1
                    log.info("Backfilled end_date for %s: %s", cid[:12], end_date)
            except Exception:
                log.warning("Failed to backfill end_date for %s", cid[:12])
        return updated

    def sync_with_onchain(self) -> int:
        """Reconcile internal tracker with on-chain positions.

        Queries the Polymarket data API directly (blockchain = truth).
        Writes fresh data to hawk_positions_onchain.json so dashboard stays current.
        Returns number of corrections made.
        """
        onchain_file = DATA_DIR / "hawk_positions_onchain.json"
        onchain_pos = self._fetch_onchain_positions()

        if onchain_pos is None:
            # API failed — fall back to cache file
            if onchain_file.exists():
                try:
                    data = json.loads(onchain_file.read_text())
                    onchain_pos = data.get("positions", []) if isinstance(data, dict) else data
                    log.info("[SYNC] API unreachable, using cached file (%d positions)", len(onchain_pos))
                except Exception:
                    return 0
            else:
                return 0

        # Write fresh data to cache file (dashboard + live_manager read this)
        try:
            cache_data = {"positions": onchain_pos, "live": True, "fetched_at": time.time()}
            onchain_file.write_text(json.dumps(cache_data, indent=2))
        except Exception:
            log.debug("[SYNC] Failed to write cache file (non-fatal)")

        corrections = 0
        onchain_cids = {p.get("condition_id", "") for p in onchain_pos if p.get("condition_id")}
        tracker_cids = {p.get("condition_id", "") for p in self._positions}

        # Remove phantom positions (in tracker but not on-chain)
        phantoms = tracker_cids - onchain_cids
        if phantoms:
            before = len(self._positions)
            self._positions = [
                p for p in self._positions
                if p.get("condition_id", "") not in phantoms
            ]
            removed = before - len(self._positions)
            if removed:
                corrections += removed
                log.warning("[SYNC] Removed %d phantom positions not found on-chain: %s",
                            removed, ", ".join(c[:12] for c in phantoms))

        # Update existing positions with fresh on-chain prices/sizes
        onchain_by_cid = {p["condition_id"]: p for p in onchain_pos if p.get("condition_id")}
        for pos in self._positions:
            cid = pos.get("condition_id", "")
            if cid in onchain_by_cid:
                oc = onchain_by_cid[cid]
                pos["cur_price"] = oc.get("cur_price", pos.get("cur_price", 0))
                pos["shares"] = oc.get("shares", pos.get("shares", 0))

        # Add missing on-chain positions (on-chain but not in tracker)
        missing = onchain_cids - tracker_cids
        for op in onchain_pos:
            cid = op.get("condition_id", "")
            if cid in missing:
                self._positions.append({
                    "condition_id": cid,
                    "question": op.get("question", op.get("title", "")),
                    "direction": op.get("direction", "yes"),
                    "entry_price": op.get("entry_price", 0.5),
                    "size_usd": op.get("size_usd", op.get("value", 0)),
                    "cur_price": op.get("cur_price", 0),
                    "shares": op.get("shares", 0),
                    "token_id": op.get("token_id", ""),
                    "category": op.get("category", "unknown"),
                    "filled": True,
                    "original_size_usd": op.get("size_usd", op.get("value", 0)),
                    "timestamp": time.time(),
                    "opened_at": time.time(),
                    "_from_onchain_sync": True,
                })
                corrections += 1
                log.info("[SYNC] Added missing on-chain position: %s", cid[:12])

        if corrections:
            log.info("[SYNC] Made %d corrections (tracker now has %d positions)",
                     corrections, len(self._positions))

        log.info("[SYNC] On-chain: %d positions, tracker: %d positions",
                 len(onchain_pos), len(self._positions))
        return corrections

    def _fetch_onchain_positions(self) -> list[dict] | None:
        """Query Polymarket data API for all on-chain positions.

        Returns processed position list, or None if API is unreachable.
        """
        import urllib.request

        wallet = os.getenv("HAWK_FUNDER_ADDRESS", "") or os.getenv("FUNDER_ADDRESS", "")
        if not wallet:
            log.warning("[SYNC] FUNDER_ADDRESS not set — cannot query on-chain")
            return None

        try:
            url = f"https://data-api.polymarket.com/positions?user={wallet.lower()}&limit=500"
            req = urllib.request.Request(url, headers={"User-Agent": "Hawk/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = json.loads(resp.read().decode())
        except Exception as e:
            log.warning("[SYNC] Polymarket API request failed: %s", str(e)[:100])
            return None

        if not isinstance(raw, list):
            return None

        # Group by conditionId, skip zero-size and crypto up/down (Garves territory)
        grouped: dict[str, list] = {}
        for pos in raw:
            size = float(pos.get("size", 0))
            if size <= 0:
                continue
            title = pos.get("title", pos.get("slug", ""))
            title_lower = title.lower()
            # Skip ALL crypto positions — that is Garves territory
            crypto_kw = (
                "up or down", "updown", "up/down",
                "bitcoin", "ethereum", "solana", "xrp", "cardano",
                "dogecoin", "bnb", "avalanche", "polkadot", "chainlink",
                "price of btc", "price of eth", "price of sol",
                "btc reach", "eth reach", "sol reach",
                "btc be above", "btc be below",
            )
            if any(kw in title_lower for kw in crypto_kw):
                continue
            cid = pos.get("conditionId", pos.get("asset", ""))
            grouped.setdefault(cid, []).append(pos)

        positions = []
        for cid, entries in grouped.items():
            total_size = sum(float(e.get("size", 0)) for e in entries)
            total_cost = sum(float(e.get("size", 0)) * float(e.get("avgPrice", 0)) for e in entries)
            cur_price = float(entries[0].get("curPrice", 0))
            if cur_price <= 0.001:
                continue

            total_value = total_size * cur_price
            avg_price = total_cost / total_size if total_size > 0 else 0
            pnl = total_value - total_cost
            pnl_pct = (pnl / total_cost * 100) if total_cost > 0 else 0
            title = entries[0].get("title", entries[0].get("slug", "Unknown"))
            outcome = entries[0].get("outcome", "")

            positions.append({
                "condition_id": cid,
                "question": title,
                "direction": outcome.lower() if outcome else "yes",
                "shares": round(total_size, 2),
                "size_usd": round(total_cost, 2),
                "entry_price": round(avg_price, 4),
                "cur_price": round(cur_price, 4),
                "value": round(total_value, 2),
                "pnl": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 1),
                "payout": round(total_size, 2),
                "est_return": round(total_size - total_cost, 2),
                "est_return_pct": round((total_size - total_cost) / total_cost * 100, 1) if total_cost > 0 else 0,
                "status": "won" if cur_price >= 0.999 else "active",
            })

        positions.sort(key=lambda x: -x["value"])
        return positions

    def _append_to_file(self, rec: dict) -> None:
        """Append a single trade record to the JSONL file."""
        try:
            with open(TRADES_FILE, "a") as f:
                f.write(json.dumps(rec) + "\n")
        except Exception:
            log.exception("Failed to write Hawk trade record")


_YES_OUTCOMES = {"yes", "up", "over"}
_NO_OUTCOMES = {"no", "down", "under"}


def _get_price(opp: TradeOpportunity) -> float:
    """Get entry price for the trade direction (handles Over/Under/team outcomes)."""
    target = _YES_OUTCOMES if opp.direction == "yes" else _NO_OUTCOMES
    for t in opp.market.tokens:
        tok_outcome = (t.get("outcome") or "").lower()
        if tok_outcome in target:
            try:
                return max(0.01, min(0.99, float(t.get("price", 0.5))))
            except (ValueError, TypeError):
                return 0.5
    # Fallback: first token = yes, second = no
    tokens = opp.market.tokens
    if len(tokens) == 2:
        idx = 0 if opp.direction == "yes" else 1
        try:
            return max(0.01, min(0.99, float(tokens[idx].get("price", 0.5))))
        except (ValueError, TypeError):
            return 0.5
    return 0.5
