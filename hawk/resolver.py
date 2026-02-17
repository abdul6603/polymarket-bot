"""Market Resolution Checker — resolve paper trades by checking actual market outcomes."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from bot.http_session import get_session

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
TRADES_FILE = DATA_DIR / "hawk_trades.jsonl"


def resolve_paper_trades() -> dict:
    """Check all unresolved paper trades against Gamma API for outcomes.

    Returns summary: {checked, resolved, wins, losses, skipped, total_pnl}.
    """
    if not TRADES_FILE.exists():
        return {"checked": 0, "resolved": 0, "wins": 0, "losses": 0, "skipped": 0, "total_pnl": 0.0}

    trades = []
    try:
        with open(TRADES_FILE) as f:
            for line in f:
                line = line.strip()
                if line:
                    trades.append(json.loads(line))
    except Exception:
        log.exception("Failed to load trades for resolution")
        return {"checked": 0, "resolved": 0, "wins": 0, "losses": 0, "skipped": 0, "total_pnl": 0.0}

    unresolved = [t for t in trades if not t.get("resolved")]
    if not unresolved:
        return {"checked": 0, "resolved": 0, "wins": 0, "losses": 0, "skipped": 0, "total_pnl": 0.0}

    log.info("Checking %d unresolved paper trades...", len(unresolved))

    # Collect unique condition IDs — support both old market_id and new condition_id
    cid_to_trades: dict[str, list[dict]] = {}
    for t in unresolved:
        cid = t.get("condition_id") or t.get("market_id", "")
        if cid:
            cid_to_trades.setdefault(cid, []).append(t)

    session = get_session()
    stats = {"checked": len(unresolved), "resolved": 0, "wins": 0, "losses": 0, "skipped": 0, "total_pnl": 0.0}

    for cid, cid_trades in cid_to_trades.items():
        try:
            resp = session.get(
                f"https://gamma-api.polymarket.com/markets/{cid}",
                timeout=10,
            )
            if resp.status_code != 200:
                resp = session.get(
                    f"https://clob.polymarket.com/markets/{cid}",
                    timeout=10,
                )
                if resp.status_code != 200:
                    stats["skipped"] += len(cid_trades)
                    continue

            data = resp.json()

            resolved_flag = data.get("resolved", False)
            if not resolved_flag:
                stats["skipped"] += len(cid_trades)
                continue

            winning_outcome = _get_winning_outcome(data)
            if not winning_outcome:
                stats["skipped"] += len(cid_trades)
                continue

            for t in cid_trades:
                direction = t.get("direction", "yes")
                entry_price = t.get("entry_price", 0.5)
                size_usd = t.get("size_usd", 0)

                won = direction == winning_outcome
                if won:
                    payout = size_usd / entry_price
                    pnl = payout - size_usd
                else:
                    pnl = -size_usd

                t["resolved"] = True
                t["outcome"] = winning_outcome
                t["won"] = won
                t["pnl"] = round(pnl, 2)
                t["resolve_time"] = time.time()

                stats["resolved"] += 1
                stats["total_pnl"] += pnl
                if won:
                    stats["wins"] += 1
                else:
                    stats["losses"] += 1

                log.info(
                    "Resolved: %s | %s %s | %s | P&L: $%.2f | risk=%s",
                    t.get("question", "")[:50],
                    direction.upper(),
                    "WON" if won else "LOST",
                    winning_outcome.upper(),
                    pnl,
                    t.get("risk_score", "?"),
                )

        except Exception:
            log.exception("Failed to check market %s", cid[:12])
            stats["skipped"] += len(cid_trades)

    # Rewrite trades file with updated resolution data
    if stats["resolved"] > 0:
        _rewrite_trades(trades)

        # V2: Trigger post-trade reviewer
        try:
            from hawk.reviewer import review_resolved_trades
            review_resolved_trades()
            log.info("Post-trade review triggered after %d resolutions", stats["resolved"])
        except Exception:
            log.exception("Post-trade review failed after resolution")

    return stats


def _get_winning_outcome(data: dict) -> str:
    """Determine winning outcome from market data."""
    outcomes = data.get("outcomes", [])
    prices = data.get("outcomePrices", [])

    if isinstance(outcomes, str):
        try:
            outcomes = json.loads(outcomes)
        except (json.JSONDecodeError, TypeError):
            outcomes = []
    if isinstance(prices, str):
        try:
            prices = json.loads(prices)
        except (json.JSONDecodeError, TypeError):
            prices = []

    if outcomes and prices and len(outcomes) == len(prices):
        for i, p in enumerate(prices):
            try:
                if float(p) >= 0.95:
                    return outcomes[i].lower()
            except (ValueError, TypeError):
                continue

    tokens = data.get("tokens", [])
    for t in tokens:
        winner = t.get("winner")
        if winner:
            return (t.get("outcome") or "yes").lower()

    return ""


def _rewrite_trades(trades: list[dict]) -> None:
    """Rewrite the full trades JSONL file."""
    try:
        with open(TRADES_FILE, "w") as f:
            for t in trades:
                f.write(json.dumps(t) + "\n")
        log.info("Rewrote trades file with %d resolved updates", sum(1 for t in trades if t.get("resolved")))
    except Exception:
        log.exception("Failed to rewrite trades file")
