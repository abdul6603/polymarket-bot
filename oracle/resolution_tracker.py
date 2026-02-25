"""Oracle Resolution Tracker — live scorecard for pending predictions.

Checks current asset prices against prediction thresholds and shows
which bets are currently winning/losing. Also resolves closed markets.

Usage:
    python -m oracle.resolution_tracker          # Live scorecard
    python -m oracle.resolution_tracker resolve   # Force resolve closed markets
    python -m oracle.resolution_tracker json      # JSON output for dashboard API
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

log = logging.getLogger(__name__)

DATA_DIR = Path.home() / "polymarket-bot" / "data"
DB_PATH = DATA_DIR / "oracle_predictions.db"
CLOB_HOST = "https://clob.polymarket.com"


def _get_current_prices() -> dict[str, float]:
    """Fetch current prices from CoinGecko."""
    try:
        resp = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "bitcoin,ethereum,solana,ripple", "vs_currencies": "usd"},
            timeout=10,
        )
        data = resp.json()
        return {
            "bitcoin": data.get("bitcoin", {}).get("usd", 0),
            "ethereum": data.get("ethereum", {}).get("usd", 0),
            "solana": data.get("solana", {}).get("usd", 0),
            "xrp": data.get("ripple", {}).get("usd", 0),
        }
    except Exception as e:
        log.warning("Failed to fetch prices: %s", e)
        return {}


def _extract_threshold(question: str) -> float | None:
    """Extract dollar threshold from question text."""
    m = re.search(r"above \$([0-9,]+(?:\.\d+)?)", question, re.IGNORECASE)
    if m:
        return float(m.group(1).replace(",", ""))
    m = re.search(r"below \$([0-9,]+(?:\.\d+)?)", question, re.IGNORECASE)
    if m:
        return float(m.group(1).replace(",", ""))
    m = re.search(r"between \$([0-9,]+(?:\.\d+)?)\s*(?:and|-)\s*\$([0-9,]+(?:\.\d+)?)", question, re.IGNORECASE)
    if m:
        return (float(m.group(1).replace(",", "")) + float(m.group(2).replace(",", ""))) / 2
    return None


def _extract_date(question: str) -> str | None:
    """Extract resolution date from question text."""
    m = re.search(r"on (\w+ \d+)", question)
    if m:
        return m.group(1)
    return None


def _resolve_market(condition_id: str) -> dict | None:
    """Check if a market has resolved on CLOB."""
    try:
        resp = requests.get(f"{CLOB_HOST}/markets/{condition_id}", timeout=5)
        if resp.status_code != 200:
            return None
        market = resp.json()
        if not market.get("closed"):
            return None
        tokens = market.get("tokens", [])
        if len(tokens) < 2:
            return None
        yes_winner = float(tokens[0].get("winner", 0)) == 1.0
        return {"closed": True, "yes_won": yes_winner}
    except Exception:
        return None


def get_scorecard(force_resolve: bool = False) -> dict:
    """Build the live scorecard for all predictions."""
    if not DB_PATH.exists():
        return {"error": "No predictions database found"}

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    # Get all predictions
    rows = conn.execute("""
        SELECT id, week_start, condition_id, question, asset, market_type,
               oracle_prob, market_prob, edge, side, size, fill_price,
               outcome, actual_result, pnl, created_at, resolved_at
        FROM predictions
        ORDER BY created_at DESC
    """).fetchall()

    if not rows:
        conn.close()
        return {"predictions": [], "summary": {"total": 0}}

    prices = _get_current_prices()

    predictions = []
    resolved_count = 0
    pending_count = 0
    won_count = 0
    lost_count = 0
    total_pnl = 0.0
    total_wagered = 0.0

    for row in rows:
        pred = dict(row)
        question = pred["question"] or ""
        asset = pred["asset"] or ""
        side = pred["side"] or ""
        size = pred["size"] or 0
        fill_price = pred["fill_price"] or pred.get("market_prob", 0)

        threshold = _extract_threshold(question)
        resolve_date = _extract_date(question)
        current_price = prices.get(asset, 0)

        # Determine current status for pending bets
        live_status = "unknown"
        if pred["outcome"] == "pending" and current_price > 0 and threshold:
            if "above" in question.lower():
                if side == "NO":
                    live_status = "winning" if current_price < threshold else "losing"
                else:
                    live_status = "winning" if current_price > threshold else "losing"
            elif "below" in question.lower():
                if side == "NO":
                    live_status = "winning" if current_price > threshold else "losing"
                else:
                    live_status = "winning" if current_price < threshold else "losing"
            elif "between" in question.lower():
                live_status = "in_range"  # Complex — skip for now

            # Distance from threshold
            if threshold > 0:
                distance_pct = (current_price - threshold) / threshold * 100
            else:
                distance_pct = 0
        else:
            distance_pct = 0

        # Try to resolve if forced or pending
        if pred["outcome"] == "pending" and (force_resolve or True):
            resolution = _resolve_market(pred["condition_id"])
            if resolution and resolution.get("closed"):
                yes_won = resolution["yes_won"]
                actual = 1.0 if yes_won else 0.0

                if side == "YES":
                    won = yes_won
                else:
                    won = not yes_won

                if won:
                    # P&L: bought NO at fill_price, pays $1
                    no_price = 1.0 - fill_price if side == "NO" else fill_price
                    shares = size / no_price if no_price > 0 else 0
                    pnl = shares - size  # payout - cost
                else:
                    pnl = -size

                outcome = "won" if won else "lost"

                # Update DB
                conn.execute("""
                    UPDATE predictions
                    SET outcome = ?, actual_result = ?, pnl = ?, resolved_at = datetime('now')
                    WHERE id = ?
                """, (outcome, actual, round(pnl, 2), pred["id"]))
                conn.commit()

                pred["outcome"] = outcome
                pred["pnl"] = round(pnl, 2)
                pred["actual_result"] = actual
                live_status = "resolved"

        # Calculate potential payout for pending
        potential_payout = 0
        potential_profit = 0
        if pred["outcome"] == "pending" and size > 0:
            no_price = 1.0 - fill_price if side == "NO" else fill_price
            if no_price > 0:
                shares = size / no_price
                potential_payout = round(shares, 2)
                potential_profit = round(shares - size, 2)

        # Tally
        total_wagered += size
        if pred["outcome"] == "won":
            won_count += 1
            total_pnl += pred["pnl"] or 0
            resolved_count += 1
        elif pred["outcome"] == "lost":
            lost_count += 1
            total_pnl += pred["pnl"] or 0
            resolved_count += 1
        else:
            pending_count += 1

        predictions.append({
            "id": pred["id"],
            "week": pred["week_start"],
            "question": question[:80],
            "asset": asset,
            "side": side,
            "size": size,
            "fill_price": round(fill_price, 4) if fill_price else 0,
            "oracle_prob": round(pred["oracle_prob"] or 0, 3),
            "market_prob": round(pred["market_prob"] or 0, 3),
            "edge": round(pred["edge"] or 0, 3),
            "outcome": pred["outcome"],
            "pnl": round(pred["pnl"] or 0, 2),
            "resolve_date": resolve_date,
            "threshold": threshold,
            "current_price": round(current_price, 2) if current_price else 0,
            "distance_pct": round(distance_pct, 1),
            "live_status": live_status,
            "potential_payout": potential_payout,
            "potential_profit": potential_profit,
            "created_at": pred["created_at"],
            "resolved_at": pred["resolved_at"],
        })

    conn.close()

    win_rate = (won_count / resolved_count * 100) if resolved_count > 0 else 0

    return {
        "predictions": predictions,
        "prices": prices,
        "summary": {
            "total": len(predictions),
            "pending": pending_count,
            "resolved": resolved_count,
            "won": won_count,
            "lost": lost_count,
            "win_rate": round(win_rate, 1),
            "total_pnl": round(total_pnl, 2),
            "total_wagered": round(total_wagered, 2),
            "roi_pct": round(total_pnl / total_wagered * 100, 1) if total_wagered > 0 else 0,
        },
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def print_scorecard(data: dict) -> None:
    """Pretty-print the scorecard to terminal."""
    summary = data.get("summary", {})
    predictions = data.get("predictions", [])
    prices = data.get("prices", {})

    print("\n" + "=" * 70)
    print("  ORACLE RESOLUTION TRACKER — Live Scorecard")
    print("=" * 70)

    if prices:
        print(f"\n  Current Prices:")
        for asset, price in prices.items():
            print(f"    {asset.upper():10s} ${price:>10,.2f}")

    print(f"\n  Summary:")
    print(f"    Total bets:    {summary.get('total', 0)}")
    print(f"    Pending:       {summary.get('pending', 0)}")
    print(f"    Resolved:      {summary.get('resolved', 0)}")
    print(f"    Won:           {summary.get('won', 0)}")
    print(f"    Lost:          {summary.get('lost', 0)}")
    print(f"    Win Rate:      {summary.get('win_rate', 0):.1f}%")
    print(f"    Total Wagered: ${summary.get('total_wagered', 0):.2f}")
    print(f"    Total P&L:     ${summary.get('total_pnl', 0):+.2f}")
    print(f"    ROI:           {summary.get('roi_pct', 0):+.1f}%")

    if predictions:
        print(f"\n  {'#':<3} {'Asset':<8} {'Side':<4} {'Size':<6} {'Oracle':<7} {'Mkt':<7} {'Status':<10} {'P&L':<8} Question")
        print("  " + "-" * 90)
        for p in predictions:
            status = p["outcome"]
            if status == "pending":
                ls = p.get("live_status", "?")
                if ls == "winning":
                    status = "WIN*"
                elif ls == "losing":
                    status = "LOSE*"
                else:
                    status = "PEND"

                # Show distance info
                dist = p.get("distance_pct", 0)
                if dist != 0:
                    status += f" ({dist:+.0f}%)"
            elif status == "won":
                status = "WON"
            elif status == "lost":
                status = "LOST"

            pnl_str = f"${p['pnl']:+.2f}" if p["pnl"] != 0 else f"(${p['potential_profit']:.0f})"

            print(f"  {p['id']:<3} {p['asset'][:7]:<8} {p['side']:<4} ${p['size']:<5.0f} "
                  f"{p['oracle_prob']:.0%}{'':>3} {p['market_prob']:.0%}{'':>3} "
                  f"{status:<10} {pnl_str:<8} {p['question'][:40]}")

    print("\n  * = currently winning/losing based on live price (not yet resolved)")
    print("=" * 70 + "\n")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"

    if cmd == "json":
        data = get_scorecard(force_resolve=True)
        print(json.dumps(data, indent=2, default=str))
    elif cmd == "resolve":
        data = get_scorecard(force_resolve=True)
        print_scorecard(data)
    else:
        data = get_scorecard(force_resolve=False)
        print_scorecard(data)


if __name__ == "__main__":
    main()
