"""Market Resolution Checker — resolve trades by checking actual market outcomes."""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from bot.http_session import get_session

log = logging.getLogger(__name__)

# Telegram notifications via Shelby's bot
_TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
_TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")


def _notify_tg(text: str) -> None:
    """Send a Telegram notification (fire-and-forget, never crashes caller)."""
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

DATA_DIR = Path(__file__).parent.parent / "data"
TRADES_FILE = DATA_DIR / "hawk_trades.jsonl"


def resolve_paper_trades() -> dict:
    """Check all unresolved trades against Gamma API for outcomes.

    Returns summary: {checked, resolved, wins, losses, skipped, total_pnl}.
    """
    if not TRADES_FILE.exists():
        return {"checked": 0, "resolved": 0, "wins": 0, "losses": 0, "skipped": 0, "total_pnl": 0.0, "resolved_trades": []}

    trades = []
    try:
        with open(TRADES_FILE) as f:
            for line in f:
                line = line.strip()
                if line:
                    trades.append(json.loads(line))
    except Exception:
        log.exception("Failed to load trades for resolution")
        return {"checked": 0, "resolved": 0, "wins": 0, "losses": 0, "skipped": 0, "total_pnl": 0.0, "resolved_trades": []}

    unresolved = [t for t in trades if not t.get("resolved")]
    if not unresolved:
        return {"checked": 0, "resolved": 0, "wins": 0, "losses": 0, "skipped": 0, "total_pnl": 0.0, "resolved_trades": []}

    log.info("Checking %d unresolved trades...", len(unresolved))

    # Collect unique condition IDs — support both old market_id and new condition_id
    cid_to_trades: dict[str, list[dict]] = {}
    for t in unresolved:
        cid = t.get("condition_id") or t.get("market_id", "")
        if cid:
            cid_to_trades.setdefault(cid, []).append(t)

    session = get_session()
    stats = {"checked": len(unresolved), "resolved": 0, "wins": 0, "losses": 0, "skipped": 0, "total_pnl": 0.0, "per_trade_pnl": [], "resolved_trades": []}

    for cid, cid_trades in cid_to_trades.items():
        try:
            # Use CLOB API (condition_id works as market ID there)
            resp = session.get(
                f"https://clob.polymarket.com/markets/{cid}",
                timeout=10,
            )
            if resp.status_code != 200:
                stats["skipped"] += len(cid_trades)
                continue

            data = resp.json()

            # Check official resolution first
            is_closed = data.get("closed", False)
            tokens = data.get("tokens", [])

            # Build token price map: token_id -> price
            token_prices = {}
            for tk in tokens:
                tid = tk.get("token_id", "")
                price = float(tk.get("price", 0.5))
                token_prices[tid] = price

            # Check for official winner
            official_winner_tid = ""
            for tk in tokens:
                if tk.get("winner"):
                    official_winner_tid = tk.get("token_id", "")
                    break

            # Effective resolution: if any token price < 0.05, market is decided
            effectively_resolved = any(p < 0.05 for p in token_prices.values())

            if not is_closed and not official_winner_tid and not effectively_resolved:
                stats["skipped"] += len(cid_trades)
                continue

            for t in cid_trades:
                token_id = t.get("token_id", "")
                entry_price = t.get("entry_price", 0.5)
                size_usd = t.get("size_usd", 0)

                our_price = token_prices.get(token_id)
                if our_price is None:
                    stats["skipped"] += 1
                    continue

                # Determine win/loss
                if official_winner_tid:
                    won = token_id == official_winner_tid
                else:
                    won = our_price > 0.95

                if won:
                    payout = size_usd / entry_price
                    pnl = payout - size_usd
                else:
                    pnl = -size_usd

                t["resolved"] = True
                t["outcome"] = "won" if won else "lost"
                t["won"] = won
                t["pnl"] = round(pnl, 2)
                t["resolve_time"] = time.time()

                stats["resolved"] += 1
                stats["total_pnl"] += pnl
                stats["per_trade_pnl"].append(round(pnl, 2))
                stats["resolved_trades"].append(t)
                if won:
                    stats["wins"] += 1
                else:
                    stats["losses"] += 1

                log.info(
                    "Resolved: %s | %s | P&L: $%.2f | token_price=%.4f | risk=%s",
                    t.get("question", "")[:50],
                    "WON" if won else "LOST",
                    pnl,
                    our_price,
                    t.get("risk_score", "?"),
                )

                # V7: Update CLV tracking
                try:
                    from hawk.clv import update_on_resolution
                    update_on_resolution(cid, won)
                except Exception:
                    pass  # CLV failure must never crash resolver

                # Telegram notification
                emoji = "\U0001f7e2" if won else "\U0001f534"
                pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
                _notify_tg(
                    f"{emoji} <b>Hawk {'WON' if won else 'LOST'}</b>\n"
                    f"{t.get('question', '')[:100]}\n"
                    f"P&L: <b>{pnl_str}</b> | {t.get('direction', '').upper()} @ ${entry_price:.2f}"
                )

                # Publish to shared event bus
                try:
                    from shared.events import publish as bus_publish
                    bus_publish(
                        agent="hawk",
                        event_type="trade_resolved",
                        data={
                            "market_question": t.get("question", "")[:200],
                            "outcome": "won" if won else "lost",
                            "pnl_usd": round(pnl, 2),
                            "direction": t.get("direction", ""),
                            "category": t.get("category", ""),
                            "condition_id": cid,
                        },
                        summary=f"Hawk trade {'WON' if won else 'LOST'}: ${pnl:+.2f} on: {t.get('question', '')[:80]}",
                    )
                except Exception:
                    pass  # Bus failure must never crash resolver

        except Exception:
            log.exception("Failed to check market %s", cid[:12])
            stats["skipped"] += len(cid_trades)

    # Rewrite trades file with updated resolution data
    if stats["resolved"] > 0:
        _rewrite_trades(trades)

    return stats



def _rewrite_trades(trades: list[dict]) -> None:
    """Rewrite the full trades JSONL file."""
    try:
        with open(TRADES_FILE, "w") as f:
            for t in trades:
                f.write(json.dumps(t) + "\n")
        log.info("Rewrote trades file with %d resolved updates", sum(1 for t in trades if t.get("resolved")))
    except Exception:
        log.exception("Failed to rewrite trades file")
