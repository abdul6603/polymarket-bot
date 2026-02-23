"""CLV (Closing Line Value) Tracker for Hawk.

Tracks the difference between our entry price and the market's closing price.
Positive CLV = we bought at a better price than the market closed at = good entries.
Negative CLV = we bought at worse prices = adverse selection / bad timing.

CLV is the single best predictor of long-term profitability in betting.
Even unprofitable bettors with positive CLV are likely to become profitable
with better bankroll management.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from bot.http_session import get_session

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
CLV_FILE = DATA_DIR / "hawk_clv.jsonl"


@dataclass
class CLVRecord:
    condition_id: str
    token_id: str
    direction: str         # "YES" or "NO"
    entry_price: float     # what we paid
    entry_time: float      # unix timestamp
    closing_price: float   # market price at resolution (0.0 or 1.0 for resolved)
    market_price_at_entry: float  # market mid-price when we entered
    clv: float             # closing_price - entry_price (for YES bets)
    clv_pct: float         # clv as percentage of entry_price
    question: str


def record_entry(
    condition_id: str,
    token_id: str,
    direction: str,
    entry_price: float,
    question: str = "",
) -> None:
    """Record a trade entry for CLV tracking.

    Called when Hawk places a trade. Records the entry price + current market price.
    """
    # Fetch current market mid-price for comparison
    market_price = _get_current_price(condition_id, token_id)

    record = {
        "condition_id": condition_id,
        "token_id": token_id,
        "direction": direction,
        "entry_price": entry_price,
        "market_price_at_entry": market_price,
        "entry_time": time.time(),
        "question": question[:200],
        "closing_price": None,
        "clv": None,
        "resolved": False,
    }

    try:
        with open(CLV_FILE, "a") as f:
            f.write(json.dumps(record) + "\n")
        log.info("[CLV] Entry recorded: %s @ %.4f (market=%.4f) | %s",
                 direction, entry_price, market_price, question[:60])
    except Exception:
        log.debug("[CLV] Failed to record entry")


def update_on_resolution(condition_id: str, won: bool) -> CLVRecord | None:
    """Update CLV record when a trade resolves.

    The closing price for a resolved market is 1.0 (YES won) or 0.0 (YES lost).
    For our trade: if we bought YES and YES won, closing_price = 1.0.
    """
    if not CLV_FILE.exists():
        return None

    records = []
    updated = None
    try:
        with open(CLV_FILE) as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    except Exception:
        return None

    for r in records:
        if r.get("condition_id") == condition_id and not r.get("resolved"):
            # For binary markets: resolved YES = 1.0, resolved NO = 0.0
            if r["direction"] == "YES":
                closing = 1.0 if won else 0.0
            else:
                closing = 0.0 if won else 1.0

            r["closing_price"] = closing
            r["resolved"] = True
            r["resolve_time"] = time.time()

            # CLV = closing_price - entry_price (for the token we bought)
            # Positive CLV = we got a good price
            entry = r["entry_price"]
            r["clv"] = round(closing - entry, 4)
            r["clv_pct"] = round((closing - entry) / max(entry, 0.01) * 100, 2)

            updated = CLVRecord(
                condition_id=condition_id,
                token_id=r.get("token_id", ""),
                direction=r["direction"],
                entry_price=entry,
                entry_time=r.get("entry_time", 0),
                closing_price=closing,
                market_price_at_entry=r.get("market_price_at_entry", 0),
                clv=r["clv"],
                clv_pct=r["clv_pct"],
                question=r.get("question", ""),
            )

            log.info("[CLV] Resolved: CLV=%+.4f (%+.1f%%) | entry=%.4f close=%.1f | %s",
                     r["clv"], r["clv_pct"], entry, closing, r.get("question", "")[:60])
            break

    # Rewrite file
    if updated:
        try:
            with open(CLV_FILE, "w") as f:
                for r in records:
                    f.write(json.dumps(r) + "\n")
        except Exception:
            log.debug("[CLV] Failed to update CLV file")

    return updated


def get_clv_stats() -> dict:
    """Get aggregate CLV statistics for dashboard display."""
    if not CLV_FILE.exists():
        return {"total_trades": 0, "resolved": 0, "avg_clv": 0.0, "avg_clv_pct": 0.0,
                "positive_clv_rate": 0.0, "trades": []}

    records = []
    try:
        with open(CLV_FILE) as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    except Exception:
        return {"total_trades": 0, "resolved": 0, "avg_clv": 0.0, "avg_clv_pct": 0.0,
                "positive_clv_rate": 0.0, "trades": []}

    resolved = [r for r in records if r.get("resolved") and r.get("clv") is not None]

    if not resolved:
        return {"total_trades": len(records), "resolved": 0, "avg_clv": 0.0,
                "avg_clv_pct": 0.0, "positive_clv_rate": 0.0, "trades": records}

    clvs = [r["clv"] for r in resolved]
    clv_pcts = [r["clv_pct"] for r in resolved]
    positive = sum(1 for c in clvs if c > 0)

    return {
        "total_trades": len(records),
        "resolved": len(resolved),
        "avg_clv": round(sum(clvs) / len(clvs), 4),
        "avg_clv_pct": round(sum(clv_pcts) / len(clv_pcts), 2),
        "positive_clv_rate": round(positive / len(resolved) * 100, 1),
        "trades": records,
    }


def _get_current_price(condition_id: str, token_id: str) -> float:
    """Fetch current market price from CLOB for a specific token."""
    try:
        session = get_session()
        resp = session.get(
            f"https://clob.polymarket.com/markets/{condition_id}",
            timeout=5,
        )
        if resp.status_code == 200:
            data = resp.json()
            for tk in data.get("tokens", []):
                if tk.get("token_id") == token_id:
                    return float(tk.get("price", 0.5))
    except Exception:
        pass
    return 0.5
