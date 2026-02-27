"""Paper trade tracker — logs simulated trades and computes running P&L."""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger("killshot.tracker")

_TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
_TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")

DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
PAPER_FILE = DATA_DIR / "killshot_paper.jsonl"
STATUS_FILE = DATA_DIR / "killshot_status.json"


@dataclass
class PaperTrade:
    """A single paper trade record."""

    timestamp: float
    asset: str
    market_id: str
    question: str
    direction: str        # "up" or "down"
    entry_price: float    # simulated maker order price (e.g. 0.87)
    size_usd: float       # dollar amount committed
    shares: float         # size_usd / entry_price
    window_end_ts: float  # when this 5m window closes
    spot_delta_pct: float # spot price change % that triggered this trade
    open_price: float     # asset open price at window start
    market_bid: float = 0.0   # CLOB best bid at entry time
    market_ask: float = 0.0   # CLOB best ask at entry time
    outcome: str = ""     # "win", "loss", or "expired" (empty while pending)
    pnl: float = 0.0
    resolved_at: float = 0.0


class PaperTracker:
    """Tracks paper trades, resolves at window close, computes stats."""

    def __init__(self):
        self._pending: list[PaperTrade] = []
        self._session_pnl: float = 0.0
        self._session_trades: int = 0
        self._session_wins: int = 0
        self._load_pending()

    def _load_pending(self) -> None:
        """Reload unresolved trades from disk (handles bot restarts)."""
        if not PAPER_FILE.exists():
            return
        now = time.time()
        with open(PAPER_FILE) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    if not d.get("outcome") and d.get("window_end_ts", 0) > now - 600:
                        trade = PaperTrade(
                            timestamp=d["timestamp"],
                            asset=d["asset"],
                            market_id=d["market_id"],
                            question=d.get("question", ""),
                            direction=d["direction"],
                            entry_price=d["entry_price"],
                            size_usd=d["size_usd"],
                            shares=d["shares"],
                            window_end_ts=d["window_end_ts"],
                            spot_delta_pct=d.get("spot_delta_pct", 0),
                            open_price=d["open_price"],
                        )
                        self._pending.append(trade)
                except Exception:
                    continue
        if self._pending:
            log.info("Loaded %d pending paper trades from disk", len(self._pending))

    def record_trade(self, trade: PaperTrade) -> None:
        """Log a new paper trade."""
        self._pending.append(trade)
        self._session_trades += 1
        self._append_to_file(trade)
        log.info(
            "[KILLSHOT] Paper trade: %s %s @ %.0f¢ ($%.2f, %.1f shares) | delta=%.3f%%",
            trade.direction.upper(), trade.asset, trade.entry_price * 100,
            trade.size_usd, trade.shares, trade.spot_delta_pct * 100,
        )

    def resolve_trades(self, price_cache) -> list[PaperTrade]:
        """Check pending trades — resolve any whose window has closed."""
        now = time.time()
        resolved = []
        still_pending = []

        for trade in self._pending:
            # Grace period: wait 10s after window close
            if now < trade.window_end_ts + 10:
                still_pending.append(trade)
                continue

            # Expire trades older than 10 minutes past close
            if now > trade.window_end_ts + 600:
                trade.outcome = "expired"
                trade.resolved_at = now
                resolved.append(trade)
                self._update_in_file(trade)
                log.warning(
                    "[KILLSHOT] Expired: %s %s (missed resolution window)",
                    trade.direction, trade.asset,
                )
                continue

            # Determine outcome from current spot price
            current_price = price_cache.get_price(trade.asset)
            if current_price is None:
                still_pending.append(trade)
                continue

            # Did price go up or down from open?
            went_up = current_price > trade.open_price

            if (trade.direction == "up" and went_up) or \
               (trade.direction == "down" and not went_up):
                trade.outcome = "win"
                trade.pnl = round(trade.shares * (1.0 - trade.entry_price), 4)
                self._session_wins += 1
            else:
                trade.outcome = "loss"
                trade.pnl = round(-trade.size_usd, 4)

            trade.resolved_at = now
            self._session_pnl += trade.pnl
            resolved.append(trade)
            self._update_in_file(trade)

            wr = (self._session_wins / max(self._session_trades, 1)) * 100
            log.info(
                "[KILLSHOT] %s: %s %s | P&L $%.2f | Session: $%.2f (WR %.0f%%)",
                trade.outcome.upper(), trade.direction.upper(), trade.asset,
                trade.pnl, self._session_pnl, wr,
            )
            emoji = "\u2705" if trade.outcome == "win" else "\u274c"
            sign = "+" if trade.pnl >= 0 else ""
            self._notify_tg(
                f"{emoji} <b>Killshot {trade.outcome.upper()}</b>\n"
                f"{trade.direction.upper()} {trade.asset.upper()} @ {trade.entry_price:.0%}\n"
                f"P&L: <b>{sign}${trade.pnl:.2f}</b>\n"
                f"Session: {sign if self._session_pnl >= 0 else ''}${self._session_pnl:.2f} | WR {wr:.0f}% ({self._session_trades} trades)"
            )

        self._pending = still_pending
        return resolved

    @staticmethod
    def _notify_tg(text: str) -> None:
        if not _TG_TOKEN or not _TG_CHAT:
            return
        try:
            import requests
            requests.post(
                f"https://api.telegram.org/bot{_TG_TOKEN}/sendMessage",
                json={"chat_id": _TG_CHAT, "text": text, "parse_mode": "HTML"},
                timeout=5,
            )
        except Exception:
            pass

    # ── File I/O ────────────────────────────────────────────────

    def _append_to_file(self, trade: PaperTrade) -> None:
        with open(PAPER_FILE, "a") as f:
            f.write(json.dumps(asdict(trade)) + "\n")

    def _update_in_file(self, trade: PaperTrade) -> None:
        """Rewrite the resolved trade's line in the JSONL file."""
        if not PAPER_FILE.exists():
            return
        lines = []
        updated = False
        with open(PAPER_FILE) as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    d = json.loads(stripped)
                    if (d.get("market_id") == trade.market_id
                            and d.get("timestamp") == trade.timestamp
                            and not updated):
                        lines.append(json.dumps(asdict(trade)))
                        updated = True
                    else:
                        lines.append(stripped)
                except Exception:
                    lines.append(stripped)
        if not updated:
            lines.append(json.dumps(asdict(trade)))
        tmp = PAPER_FILE.with_suffix(".jsonl.tmp")
        with open(tmp, "w") as f:
            f.write("\n".join(lines) + "\n")
        tmp.replace(PAPER_FILE)

    # ── Stats & Dashboard ───────────────────────────────────────

    def get_stats(self) -> dict:
        """Dashboard-friendly statistics."""
        all_trades = self._load_all_trades()
        resolved = [t for t in all_trades if t.get("outcome") in ("win", "loss")]
        wins = sum(1 for t in resolved if t["outcome"] == "win")
        total_pnl = sum(t.get("pnl", 0) for t in resolved)
        avg_entry = 0.0
        if all_trades:
            avg_entry = sum(t.get("entry_price", 0) for t in all_trades) / len(all_trades)

        return {
            "total_trades": len(all_trades),
            "resolved": len(resolved),
            "pending": len(self._pending),
            "wins": wins,
            "losses": len(resolved) - wins,
            "win_rate": round(wins / len(resolved) * 100, 1) if resolved else 0,
            "total_pnl": round(total_pnl, 2),
            "avg_entry_price": round(avg_entry, 3),
            "session_pnl": round(self._session_pnl, 2),
            "session_trades": self._session_trades,
            "session_wins": self._session_wins,
            "daily_loss": round(abs(min(self._session_pnl, 0)), 2),
        }

    def _load_all_trades(self) -> list[dict]:
        if not PAPER_FILE.exists():
            return []
        trades = []
        with open(PAPER_FILE) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    trades.append(json.loads(line))
                except Exception:
                    continue
        return trades

    def get_recent_trades(self, limit: int = 50) -> list[dict]:
        """Return recent trades for dashboard display."""
        return self._load_all_trades()[-limit:]

    def write_status(self) -> None:
        """Persist status JSON for dashboard consumption."""
        status = self.get_stats()
        status["updated_at"] = time.time()
        status["pending_details"] = [
            {
                "asset": t.asset,
                "direction": t.direction,
                "entry_price": t.entry_price,
                "size_usd": t.size_usd,
                "window_end_ts": t.window_end_ts,
                "remaining_s": max(0, round(t.window_end_ts - time.time())),
            }
            for t in self._pending
        ]
        with open(STATUS_FILE, "w") as f:
            json.dump(status, f, indent=2)
