"""Arbiter Tracker — track arb positions, P&L, resolution."""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
POSITIONS_FILE = DATA_DIR / "arbiter_positions.json"
TRADES_FILE = DATA_DIR / "arbiter_trades.jsonl"


class ArbiterTracker:
    """Track active arb sets, completed trades, and P&L."""

    def __init__(self):
        DATA_DIR.mkdir(exist_ok=True)
        self._active_arbs: list[dict] = []
        self._load_positions()

    def _load_positions(self) -> None:
        """Load active arb positions from disk."""
        if not POSITIONS_FILE.exists():
            return
        try:
            data = json.loads(POSITIONS_FILE.read_text())
            self._active_arbs = data.get("arbs", [])
            log.info("Loaded %d active arbiter positions", len(self._active_arbs))
        except Exception:
            log.exception("Failed to load arbiter positions")

    def _save_positions(self) -> None:
        """Persist active positions to disk."""
        try:
            POSITIONS_FILE.write_text(json.dumps({
                "arbs": self._active_arbs,
                "updated": time.time(),
            }, indent=2))
        except Exception:
            log.exception("Failed to save arbiter positions")

    @property
    def active_arbs(self) -> list[dict]:
        return list(self._active_arbs)

    @property
    def active_count(self) -> int:
        return len(self._active_arbs)

    def has_arb_for_event(self, event_slug: str) -> bool:
        """Check if we already have an active arb for this event."""
        return any(a.get("event_slug") == event_slug for a in self._active_arbs)

    def can_open(self, event_slug: str, max_concurrent: int) -> bool:
        """Check if we can open a new arb."""
        if self.has_arb_for_event(event_slug):
            log.info("Already have arb for %s — skipping", event_slug)
            return False
        if self.active_count >= max_concurrent:
            log.info("Max concurrent arbs (%d) reached — skipping", max_concurrent)
            return False
        return True

    def record_arb(self, event_slug: str, event_title: str, arb_type: str,
                   legs: list[dict], order_ids: list[str],
                   total_cost: float, expected_profit_pct: float,
                   deviation_pct: float, status: str) -> None:
        """Record a new arb position."""
        rec = {
            "event_slug": event_slug,
            "event_title": event_title[:200],
            "arb_type": arb_type,
            "legs": legs,
            "order_ids": order_ids,
            "total_cost": total_cost,
            "expected_profit_pct": expected_profit_pct,
            "deviation_pct": deviation_pct,
            "status": status,
            "opened_at": time.time(),
            "resolved": False,
            "pnl": 0.0,
        }
        self._active_arbs.append(rec)
        self._save_positions()
        self._append_trade(rec)
        log.info("Recorded arb: %s | %s | dev=%.1f%% | profit=%.1f%%",
                 arb_type, event_slug, deviation_pct, expected_profit_pct)

    def check_resolutions(self) -> list[dict]:
        """Check if any active arbs have resolved.

        Queries CLOB API for market resolution status.
        Returns list of resolved arbs.
        """
        import urllib.request

        resolved = []
        for arb in list(self._active_arbs):
            if arb.get("resolved"):
                continue

            all_resolved = True
            any_won = False
            for leg in arb.get("legs", []):
                cid = leg.get("condition_id", "")
                if not cid:
                    continue
                try:
                    url = f"https://clob.polymarket.com/markets/{cid}"
                    req = urllib.request.Request(url, headers={"User-Agent": "Arbiter/1.0"})
                    with urllib.request.urlopen(req, timeout=8) as resp:
                        data = json.loads(resp.read().decode())
                    if not data.get("closed", False):
                        all_resolved = False
                        break
                    # Check which outcome won
                    tokens = data.get("tokens", [])
                    for tok in tokens:
                        if tok.get("winner") and tok.get("token_id") == leg.get("token_id"):
                            any_won = True
                except Exception:
                    all_resolved = False
                    break

            if all_resolved:
                arb["resolved"] = True
                # For sum arbs: guaranteed $1 payout if all legs filled
                if arb["status"] in ("success", "dry_run_success"):
                    arb["pnl"] = arb.get("expected_profit_pct", 0) / 100 * arb.get("total_cost", 0)
                else:
                    arb["pnl"] = 0.0  # Scratched
                resolved.append(arb)
                log.info("Arb resolved: %s | %s | P&L: $%.2f",
                         arb["arb_type"], arb["event_slug"], arb["pnl"])

        if resolved:
            # Remove resolved from active
            self._active_arbs = [a for a in self._active_arbs if not a.get("resolved")]
            self._save_positions()
            for arb in resolved:
                self._append_trade(arb)

        return resolved

    def total_exposure(self) -> float:
        """Total USD committed to active arbs."""
        return sum(a.get("total_cost", 0) for a in self._active_arbs)

    def summary(self) -> dict:
        """Overall stats for dashboard."""
        all_trades = self._load_all_trades()
        resolved = [t for t in all_trades if t.get("resolved")]
        total_pnl = sum(t.get("pnl", 0) for t in resolved)
        successful = [t for t in resolved if t.get("status") in ("success", "dry_run_success")]

        return {
            "active_arbs": self.active_count,
            "total_exposure": round(self.total_exposure(), 2),
            "total_trades": len(all_trades),
            "resolved": len(resolved),
            "successful": len(successful),
            "total_pnl": round(total_pnl, 2),
            "last_update": time.time(),
        }

    def _load_all_trades(self) -> list[dict]:
        """Load all trades from JSONL."""
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
            log.exception("Failed to load arbiter trades")
        return trades

    def _append_trade(self, rec: dict) -> None:
        """Append a trade record to JSONL."""
        try:
            with open(TRADES_FILE, "a") as f:
                f.write(json.dumps(rec) + "\n")
        except Exception:
            log.exception("Failed to write arbiter trade record")
