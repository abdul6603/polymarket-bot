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
from typing import Any

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
        # ensure consistent encoding on writes
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(CLV_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
        log.info("[CLV] Entry recorded: %s @ %.4f (market=%.4f) | %s",
                 direction, entry_price, market_price, question[:60])
    except Exception:
        # Keep exception visible for ops — debug level to avoid noisy INFO
        log.exception("[CLV] Failed to record entry for %s %s", condition_id[:12], question[:60])


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
        log.exception("[CLV] Failed reading CLV file for resolution update")
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
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            tmp = CLV_FILE.with_suffix(".jsonl.tmp")
            # write via a tmp file then replace atomically
            with open(tmp, "w", encoding="utf-8") as f:
                for r in records:
                    f.write(json.dumps(r) + "\n")
            tmp.replace(CLV_FILE)
        except Exception:
            log.exception("[CLV] Failed to update CLV file for %s", condition_id[:12])

    return updated


def get_clv_stats() -> dict:
    """Get aggregate CLV statistics for dashboard display."""
    # Ensure data dir exists
    try:
        if not CLV_FILE.exists():
            return {
                "total_trades": 0,
                "resolved": 0,
                "avg_clv": 0.0,
                "avg_clv_pct": 0.0,
                "positive_clv_rate": 0.0,
                "trades": [],
            }

        records: list[dict[str, Any]] = []
        with open(CLV_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except Exception:
                    log.debug("[CLV] Skipping malformed line in CLV file")

        resolved = [r for r in records if r.get("resolved") and r.get("clv") is not None]

        if not resolved:
            return {
                "total_trades": len(records),
                "resolved": 0,
                "avg_clv": 0.0,
                "avg_clv_pct": 0.0,
                "positive_clv_rate": 0.0,
                "trades": records[-50:],  # return recent up to 50
            }

        clvs = [float(r["clv"]) for r in resolved]
        clv_pcts = [float(r.get("clv_pct", 0.0)) for r in resolved]
        positive = sum(1 for c in clvs if c > 0)

        return {
            "total_trades": len(records),
            "resolved": len(resolved),
            "avg_clv": round(sum(clvs) / len(clvs), 4),
            "avg_clv_pct": round(sum(clv_pcts) / len(clv_pcts), 2),
            "positive_clv_rate": round(positive / len(resolved) * 100, 1),
            "trades": records[-50:],  # return recent up to 50
        }
    except Exception:
        log.exception("[CLV] Failed to compute CLV stats")
        return {
            "total_trades": 0,
            "resolved": 0,
            "avg_clv": 0.0,
            "avg_clv_pct": 0.0,
            "positive_clv_rate": 0.0,
            "trades": [],
        }


def get_realtime_clv(condition_id: str, token_id: str) -> dict | None:
    """Fetch current price and compute realtime CLV vs entry price.

    Returns dict with realtime_clv, realtime_clv_pct, current_price, entry_price, age_hours
    or None if no unresolved entry found.
    """
    if not CLV_FILE.exists():
        return None

    entry_rec = None
    try:
        with open(CLV_FILE) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                if (r.get("condition_id") == condition_id and
                        r.get("token_id", "") == token_id and
                        not r.get("resolved")):
                    entry_rec = r
                    break
    except Exception:
        return None

    if not entry_rec:
        return None

    current_price = _get_current_price(condition_id, token_id)
    entry_price = entry_rec.get("entry_price", 0.5)
    entry_time = entry_rec.get("entry_time", time.time())
    age_hours = (time.time() - entry_time) / 3600

    rt_clv = current_price - entry_price
    rt_clv_pct = (rt_clv / max(entry_price, 0.01)) * 100

    return {
        "condition_id": condition_id,
        "token_id": token_id,
        "entry_price": entry_price,
        "current_price": current_price,
        "realtime_clv": round(rt_clv, 4),
        "realtime_clv_pct": round(rt_clv_pct, 2),
        "age_hours": round(age_hours, 2),
        "question": entry_rec.get("question", ""),
    }


CLV_EXIT_THRESHOLD = -0.10  # Exit if 10+ cents underwater
CLV_EXIT_MIN_AGE_HOURS = 0.5  # Must be open >30min before CLV exit


def should_exit_on_clv(condition_id: str, token_id: str) -> tuple[bool, str]:
    """Check if a position should be exited based on realtime CLV.

    Returns (should_exit, reason_string).
    Only triggers if CLV < -0.10 AND position age > 30 minutes.
    """
    rt = get_realtime_clv(condition_id, token_id)
    if not rt:
        return False, ""

    if rt["realtime_clv"] < CLV_EXIT_THRESHOLD and rt["age_hours"] > CLV_EXIT_MIN_AGE_HOURS:
        reason = (f"CLV exit: {rt['realtime_clv_pct']:+.1f}% "
                  f"(${rt['entry_price']:.2f}→${rt['current_price']:.2f}, "
                  f"age={rt['age_hours']:.1f}h)")
        return True, reason

    return False, ""


def get_clv_by_dimension() -> dict:
    """Get CLV stats broken down by category and edge_source.

    Returns {
        "by_category": {"sports": {"avg_clv": ..., "count": ...}, ...},
        "by_edge_source": {"sportsbook_divergence": {...}, ...},
    }
    """
    if not CLV_FILE.exists():
        return {"by_category": {}, "by_edge_source": {}}

    # Load trade file for category/edge_source mapping
    trades_file = DATA_DIR / "hawk_trades.jsonl"
    trade_map: dict[str, dict] = {}
    if trades_file.exists():
        try:
            with open(trades_file) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        t = json.loads(line)
                        cid = t.get("condition_id", "")
                        if cid:
                            trade_map[cid] = t
        except Exception:
            pass

    records = []
    try:
        with open(CLV_FILE) as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    except Exception:
        return {"by_category": {}, "by_edge_source": {}}

    resolved = [r for r in records if r.get("resolved") and r.get("clv") is not None]

    by_cat: dict[str, list[float]] = {}
    by_src: dict[str, list[float]] = {}

    for r in resolved:
        cid = r.get("condition_id", "")
        trade = trade_map.get(cid, {})
        cat = trade.get("category", "unknown")
        src = trade.get("edge_source", "unknown")
        clv = r["clv"]

        by_cat.setdefault(cat, []).append(clv)
        by_src.setdefault(src, []).append(clv)

    def _summarize(groups: dict[str, list[float]]) -> dict:
        result = {}
        for key, values in groups.items():
            result[key] = {
                "avg_clv": round(sum(values) / len(values), 4) if values else 0,
                "count": len(values),
                "positive_rate": round(sum(1 for v in values if v > 0) / len(values) * 100, 1) if values else 0,
            }
        return result

    return {
        "by_category": _summarize(by_cat),
        "by_edge_source": _summarize(by_src),
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
