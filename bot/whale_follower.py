"""Whale Follower — Smart Money Copy Trader for Garves.

Monitors top Polymarket crypto traders ($10K+ positions), scores their
wallets by profitability, and generates copy-trade signals when consensus
forms among high-scoring whales.

Architecture:
  WhaleTracker (orchestrator)
    ├── WalletDB        — SQLite persistence
    ├── WhaleScorer     — wallet ranking (EV, Sharpe, profit factor)
    ├── WhaleMonitor    — real-time position polling + signal generation
    ├── CopyExecutor    — FOK order execution via CLOB client
    └── Backtester      — historical validation before live trading
"""
from __future__ import annotations

import json
import logging
import math
import sqlite3
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

import os
import requests

from bot.whale_config import (
    CACHE_TTL_S,
    DATA_API,
    GAMMA_API,
    LEADERBOARD_CATEGORIES,
    LEADERBOARD_PERIODS,
    LEADERBOARD_REFRESH_H,
    LEADERBOARD_TOP_N,
    MAX_COPY_PCT_OF_WHALE,
    MAX_COPY_SIZE_USD,
    MAX_DAILY_EXPOSURE_USD,
    MAX_IMPLIED_PRICE,
    MAX_MANIPULATION_SCORE,
    MAX_SLIPPAGE_PCT,
    MAX_TRACKED_WALLETS,
    MIN_CONSENSUS,
    MIN_MARKET_DURATION_S,
    MIN_TRADES_FOR_BLACKLIST,
    MIN_WALLET_SCORE,
    MIN_WR_THRESHOLD,
    POLL_INTERVAL_S,
    POSITION_CHANGE_MIN_USD,
    RAPID_EXIT_WINDOW_S,
    REQUESTS_PER_MINUTE,
    RESCORE_INTERVAL_H,
    SCORE_WEIGHTS,
    BACKTEST_DAYS,
    BACKTEST_MIN_TRADES,
    BACKTEST_MIN_WR,
)

log = logging.getLogger("garves.whale")

DATA_DIR = Path(__file__).parent.parent / "data"
DB_PATH = DATA_DIR / "whale_wallets.db"
STATUS_FILE = DATA_DIR / "whale_status.json"
COPY_TRADES_FILE = DATA_DIR / "whale_copy_trades.jsonl"

# Crypto-related keywords for filtering positions
CRYPTO_KEYWORDS = ("bitcoin", "btc", "ethereum", "eth", "solana", "sol", "xrp", "ripple", "crypto")


# ═══════════════════════════════════════════════════════════════════
#  WalletDB — SQLite persistence layer
# ═══════════════════════════════════════════════════════════════════

class WalletDB:
    """Thread-safe SQLite database for whale wallets, trades, and copy trades."""

    def __init__(self, db_path: Path = DB_PATH):
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_schema(self) -> None:
        with self._lock:
            conn = self._conn()
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS whale_wallets (
                    proxy_wallet TEXT PRIMARY KEY,
                    username TEXT DEFAULT '',
                    first_seen TEXT DEFAULT '',
                    last_active TEXT DEFAULT '',
                    total_trades INTEGER DEFAULT 0,
                    total_pnl REAL DEFAULT 0,
                    total_volume REAL DEFAULT 0,
                    win_count INTEGER DEFAULT 0,
                    loss_count INTEGER DEFAULT 0,
                    ev_per_trade REAL DEFAULT 0,
                    sharpe_ratio REAL DEFAULT 0,
                    profit_factor REAL DEFAULT 0,
                    max_drawdown REAL DEFAULT 0,
                    composite_score REAL DEFAULT 0,
                    is_tracked INTEGER DEFAULT 0,
                    is_blacklisted INTEGER DEFAULT 0,
                    blacklist_reason TEXT DEFAULT '',
                    category TEXT DEFAULT 'CRYPTO',
                    updated_at TEXT DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS whale_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    proxy_wallet TEXT,
                    condition_id TEXT,
                    token_id TEXT DEFAULT '',
                    side TEXT,
                    size REAL,
                    price REAL,
                    usdc_size REAL DEFAULT 0,
                    timestamp TEXT,
                    tx_hash TEXT DEFAULT '',
                    market_title TEXT DEFAULT '',
                    outcome TEXT DEFAULT '',
                    pnl REAL DEFAULT 0,
                    was_copied INTEGER DEFAULT 0,
                    FOREIGN KEY (proxy_wallet) REFERENCES whale_wallets(proxy_wallet)
                );

                CREATE TABLE IF NOT EXISTS copy_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_wallet TEXT,
                    condition_id TEXT,
                    token_id TEXT DEFAULT '',
                    side TEXT,
                    size REAL,
                    entry_price REAL,
                    whale_price REAL,
                    slippage_pct REAL DEFAULT 0,
                    timestamp TEXT,
                    status TEXT DEFAULT 'PENDING',
                    exit_price REAL DEFAULT 0,
                    exit_timestamp TEXT DEFAULT '',
                    pnl REAL DEFAULT 0,
                    signal_type TEXT DEFAULT 'CONSENSUS',
                    market_title TEXT DEFAULT '',
                    dry_run INTEGER DEFAULT 1,
                    FOREIGN KEY (source_wallet) REFERENCES whale_wallets(proxy_wallet)
                );

                CREATE TABLE IF NOT EXISTS position_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    proxy_wallet TEXT,
                    condition_id TEXT,
                    size REAL,
                    avg_price REAL,
                    cur_price REAL,
                    title TEXT DEFAULT '',
                    outcome TEXT DEFAULT '',
                    snapshot_at TEXT,
                    FOREIGN KEY (proxy_wallet) REFERENCES whale_wallets(proxy_wallet)
                );

                CREATE INDEX IF NOT EXISTS idx_wt_wallet
                    ON whale_trades(proxy_wallet, timestamp);
                CREATE INDEX IF NOT EXISTS idx_wt_condition
                    ON whale_trades(condition_id);
                CREATE INDEX IF NOT EXISTS idx_ct_status
                    ON copy_trades(status);
                CREATE INDEX IF NOT EXISTS idx_ps_wallet
                    ON position_snapshots(proxy_wallet, snapshot_at);
            """)
            conn.commit()
            conn.close()

    def upsert_wallet(self, wallet: str, **kwargs) -> None:
        with self._lock:
            conn = self._conn()
            cols = ["proxy_wallet"] + list(kwargs.keys()) + ["updated_at"]
            vals = [wallet] + list(kwargs.values()) + [_now_iso()]
            placeholders = ", ".join("?" for _ in cols)
            updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "proxy_wallet")
            conn.execute(
                f"INSERT INTO whale_wallets ({', '.join(cols)}) VALUES ({placeholders}) "
                f"ON CONFLICT(proxy_wallet) DO UPDATE SET {updates}",
                vals,
            )
            conn.commit()
            conn.close()

    def get_tracked_wallets(self) -> list[dict]:
        with self._lock:
            conn = self._conn()
            rows = conn.execute(
                "SELECT * FROM whale_wallets WHERE is_tracked=1 AND is_blacklisted=0 "
                "ORDER BY composite_score DESC"
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]

    def get_all_wallets(self) -> list[dict]:
        with self._lock:
            conn = self._conn()
            rows = conn.execute(
                "SELECT * FROM whale_wallets ORDER BY composite_score DESC"
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]

    def set_tracked(self, wallet: str, tracked: bool) -> None:
        with self._lock:
            conn = self._conn()
            conn.execute(
                "UPDATE whale_wallets SET is_tracked=?, updated_at=? WHERE proxy_wallet=?",
                (int(tracked), _now_iso(), wallet),
            )
            conn.commit()
            conn.close()

    def blacklist_wallet(self, wallet: str, reason: str) -> None:
        with self._lock:
            conn = self._conn()
            conn.execute(
                "UPDATE whale_wallets SET is_blacklisted=1, is_tracked=0, "
                "blacklist_reason=?, updated_at=? WHERE proxy_wallet=?",
                (reason, _now_iso(), wallet),
            )
            conn.commit()
            conn.close()
        log.warning("[WHALE] Blacklisted wallet %s: %s", wallet[:12], reason)

    def record_whale_trade(self, trade: dict) -> int:
        with self._lock:
            conn = self._conn()
            cur = conn.execute(
                "INSERT INTO whale_trades "
                "(proxy_wallet, condition_id, token_id, side, size, price, usdc_size, "
                " timestamp, tx_hash, market_title, outcome) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    trade["proxy_wallet"], trade.get("condition_id", ""),
                    trade.get("token_id", ""), trade.get("side", ""),
                    trade.get("size", 0), trade.get("price", 0),
                    trade.get("usdc_size", 0), trade.get("timestamp", _now_iso()),
                    trade.get("tx_hash", ""), trade.get("market_title", ""),
                    trade.get("outcome", ""),
                ),
            )
            conn.commit()
            trade_id = cur.lastrowid
            conn.close()
            return trade_id

    def record_copy_trade(self, trade: dict) -> int:
        with self._lock:
            conn = self._conn()
            cur = conn.execute(
                "INSERT INTO copy_trades "
                "(source_wallet, condition_id, token_id, side, size, entry_price, "
                " whale_price, slippage_pct, timestamp, status, signal_type, "
                " market_title, dry_run) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    trade["source_wallet"], trade.get("condition_id", ""),
                    trade.get("token_id", ""), trade.get("side", ""),
                    trade.get("size", 0), trade.get("entry_price", 0),
                    trade.get("whale_price", 0), trade.get("slippage_pct", 0),
                    trade.get("timestamp", _now_iso()), trade.get("status", "FILLED"),
                    trade.get("signal_type", "CONSENSUS"),
                    trade.get("market_title", ""), trade.get("dry_run", 1),
                ),
            )
            conn.commit()
            trade_id = cur.lastrowid
            conn.close()
            return trade_id

    def update_copy_trade(self, trade_id: int, **kwargs) -> None:
        with self._lock:
            conn = self._conn()
            sets = ", ".join(f"{k}=?" for k in kwargs)
            conn.execute(
                f"UPDATE copy_trades SET {sets} WHERE id=?",
                list(kwargs.values()) + [trade_id],
            )
            conn.commit()
            conn.close()

    def get_copy_trades(self, status: str | None = None, limit: int = 50) -> list[dict]:
        with self._lock:
            conn = self._conn()
            if status:
                rows = conn.execute(
                    "SELECT * FROM copy_trades WHERE status=? ORDER BY id DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM copy_trades ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
            conn.close()
            return [dict(r) for r in rows]

    def get_daily_exposure(self) -> float:
        """Total USD exposure from copy trades placed today."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self._lock:
            conn = self._conn()
            row = conn.execute(
                "SELECT COALESCE(SUM(size), 0) as total FROM copy_trades "
                "WHERE timestamp LIKE ? AND status != 'FAILED'",
                (f"{today}%",),
            ).fetchone()
            conn.close()
            return float(row["total"]) if row else 0.0

    def get_wallet_copy_performance(self, wallet: str) -> dict:
        """Win/loss stats for copy trades sourced from a specific whale."""
        with self._lock:
            conn = self._conn()
            rows = conn.execute(
                "SELECT * FROM copy_trades WHERE source_wallet=? "
                "AND status IN ('WON', 'LOST') ORDER BY id DESC",
                (wallet,),
            ).fetchall()
            conn.close()
        trades = [dict(r) for r in rows]
        if not trades:
            return {"total": 0, "wins": 0, "losses": 0, "wr": 0, "pnl": 0}
        wins = sum(1 for t in trades if t["status"] == "WON")
        losses = sum(1 for t in trades if t["status"] == "LOST")
        pnl = sum(t.get("pnl", 0) for t in trades)
        return {
            "total": len(trades),
            "wins": wins,
            "losses": losses,
            "wr": round(wins / len(trades), 3) if trades else 0,
            "pnl": round(pnl, 2),
        }

    def get_backtest_data(self, wallet: str, days: int = 30) -> list[dict]:
        """Get whale trades from the last N days for backtesting."""
        with self._lock:
            conn = self._conn()
            rows = conn.execute(
                "SELECT * FROM whale_trades WHERE proxy_wallet=? "
                "ORDER BY timestamp DESC LIMIT 500",
                (wallet,),
            ).fetchall()
            conn.close()
        return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════
#  WhaleScorer — wallet ranking algorithm
# ═══════════════════════════════════════════════════════════════════

class WhaleScorer:
    """Multi-factor wallet scoring: EV, Sharpe, profit factor, drawdown."""

    @staticmethod
    def score_wallet(trades: list[dict], pnl: float, volume: float) -> dict:
        """Compute composite score (0-100) from trade history + leaderboard PnL.

        The Data API /trades endpoint doesn't provide per-trade PnL, so we
        use the leaderboard-provided total PnL + volume for scoring, and
        trade count from the API for sample size.
        """
        total = len(trades) if trades else 0

        # Use leaderboard data when per-trade PnL is unavailable
        # Minimum: need either trades or leaderboard volume to score
        if total < 5 and volume < 1000:
            return {"composite_score": 0, "reason": "insufficient_data"}

        # For sample size, prefer API trade count, fall back to volume estimate
        effective_trades = total if total >= 5 else max(int(volume / 500), 5)

        # 1. EV per trade (0-30 pts) — from leaderboard PnL
        ev = pnl / effective_trades if effective_trades > 0 else 0
        # Scale: $10 EV/trade = max score
        ev_score = min(max(ev / 10, 0), SCORE_WEIGHTS["ev_per_trade"])

        # 2. ROI as Sharpe proxy (0-25 pts) — PnL / volume
        roi = pnl / volume if volume > 0 else 0
        # Scale: 20% ROI = max score
        sharpe_score = min(max(roi * 125, 0), SCORE_WEIGHTS["sharpe_ratio"])

        # 3. Profit factor proxy (0-15 pts) — positive PnL = profitable
        if pnl > 0 and volume > 0:
            # Estimate: if ROI > 5%, strong profit factor
            pf_proxy = 1 + (roi * 10)
            pf_score = min(max((pf_proxy - 1) * 5, 0), SCORE_WEIGHTS["profit_factor"])
        else:
            pf_proxy = 0
            pf_score = 0

        # 4. Consistency (0-15 pts) — positive PnL relative to volume
        consistency_score = 0
        if pnl > 0 and volume > 0:
            # Higher volume with profit = more consistent
            consistency_score = min(
                math.log(max(volume, 1)) * roi * 10,
                SCORE_WEIGHTS["consistency"],
            )
            consistency_score = max(consistency_score, 0)

        # 5. Sample size confidence (0-10 pts, logarithmic)
        size_score = min(
            math.log(max(effective_trades, 1)) * 3,
            SCORE_WEIGHTS["sample_size"],
        )

        # 6. Recency (0-5 pts)
        recency_score = SCORE_WEIGHTS["recency"]
        if trades:
            last_ts = trades[0].get("timestamp", "")
            if last_ts and isinstance(last_ts, str):
                try:
                    last_dt = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
                    days_since = (datetime.now(timezone.utc) - last_dt).days
                    recency_score = max(SCORE_WEIGHTS["recency"] - days_since / 7, 0)
                except (ValueError, TypeError):
                    pass

        composite = (ev_score + sharpe_score + pf_score
                     + consistency_score + size_score + recency_score)

        return {
            "composite_score": round(min(composite, 100), 1),
            "ev_per_trade": round(ev, 4),
            "ev_score": round(ev_score, 1),
            "sharpe_ratio": round(roi, 4),
            "sharpe_score": round(sharpe_score, 1),
            "profit_factor": round(pf_proxy, 2),
            "pf_score": round(pf_score, 1),
            "max_drawdown": 0,
            "consistency_score": round(consistency_score, 1),
            "sample_size": effective_trades,
            "size_score": round(size_score, 1),
            "recency_score": round(recency_score, 1),
            "win_count": 0,
            "loss_count": 0,
        }


# ═══════════════════════════════════════════════════════════════════
#  WhaleMonitor — real-time position polling + signal generation
# ═══════════════════════════════════════════════════════════════════

class WhaleMonitor:
    """Polls tracked wallets for position changes, generates copy signals."""

    def __init__(self, db: WalletDB):
        self._db = db
        self._position_cache: dict[str, dict[str, dict]] = {}
        self._request_timestamps: list[float] = []
        self._response_cache: dict[str, tuple[float, list]] = {}
        self._active_signals: list[dict] = []
        self._lock = Lock()
        # Manipulation tracking: wallet -> {condition_id -> entry_timestamp}
        self._entry_timestamps: dict[str, dict[str, float]] = {}
        self._manipulation_scores: dict[str, int] = {}
        # Market end time cache: condition_id -> end_timestamp
        self._market_end_cache: dict[str, float] = {}
        # Wallet co-entry tracking for clustering: frozenset(w1,w2) -> overlap_count
        self._co_entry_counts: dict[frozenset, int] = {}
        self._entry_counts: dict[str, int] = {}

    def poll_wallets(self) -> list[dict]:
        """Poll all tracked wallets in parallel and return new copy signals."""
        wallets = self._db.get_tracked_wallets()
        if not wallets:
            return []

        signals = []

        def _poll_one(w: dict) -> list[dict]:
            wallet = w["proxy_wallet"]
            try:
                positions = self._fetch_positions(wallet)
                if positions is None:
                    return []
                new_signals = self._detect_changes(wallet, positions, w)
                # Update cache
                self._position_cache[wallet] = {
                    p.get("conditionId", ""): p for p in positions
                }
                return new_signals
            except Exception as e:
                log.debug("[WHALE] Poll error for %s: %s", wallet[:12], str(e)[:100])
                return []

        # Parallel polling — 8 threads, respects rate limiter in _fetch_positions
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = pool.map(_poll_one, wallets)
            for sigs in results:
                signals.extend(sigs)

        # Consensus check: group signals by condition_id + outcome
        if signals:
            signals = self._apply_consensus(signals)

        with self._lock:
            self._active_signals = signals

        return signals

    def _fetch_positions(self, wallet: str) -> list[dict] | None:
        """Fetch positions with rate limiting and caching."""
        now = time.time()

        # Cache check
        cached = self._response_cache.get(wallet)
        if cached and now - cached[0] < CACHE_TTL_S:
            return cached[1]

        # Rate limit check
        self._request_timestamps = [t for t in self._request_timestamps if now - t < 60]
        if len(self._request_timestamps) >= REQUESTS_PER_MINUTE:
            log.debug("[WHALE] Rate limited, skipping %s", wallet[:12])
            return None

        try:
            resp = requests.get(
                f"{DATA_API}/positions",
                params={"user": wallet, "sizeThreshold": "0", "limit": "200"},
                timeout=8,
                headers={"User-Agent": "GarvesWhaleTracker/1.0"},
            )
            self._request_timestamps.append(now)

            if resp.status_code != 200:
                return None

            data = resp.json()
            if not isinstance(data, list):
                return None

            # Filter to crypto markets only
            crypto_positions = [
                p for p in data
                if _is_crypto_market(p.get("title", ""))
                and float(p.get("size", 0)) > 0
            ]

            self._response_cache[wallet] = (now, crypto_positions)
            return crypto_positions

        except Exception as e:
            log.debug("[WHALE] Fetch error %s: %s", wallet[:12], str(e)[:80])
            return None

    def _detect_changes(
        self, wallet: str, current: list[dict], wallet_info: dict,
    ) -> list[dict]:
        """Compare current positions with cache, detect entries, increases, and exits."""
        previous = self._position_cache.get(wallet, {})
        signals = []
        now = time.time()
        current_cids = set()

        for pos in current:
            cid = pos.get("conditionId", "")
            if not cid:
                continue
            current_cids.add(cid)

            cur_size = float(pos.get("size", 0))
            cur_value = cur_size * float(pos.get("curPrice", 0))
            old = previous.get(cid)

            if old is None:
                # NEW position — whale just entered
                if cur_value >= POSITION_CHANGE_MIN_USD:
                    signals.append(self._build_signal(
                        wallet, wallet_info, pos, "NEW_ENTRY", cur_size, 0,
                    ))
                    # Track entry time for manipulation detection
                    self._entry_timestamps.setdefault(wallet, {})[cid] = now
                    # Track for clustering
                    self._entry_counts[wallet] = self._entry_counts.get(wallet, 0) + 1
            else:
                old_size = float(old.get("size", 0))
                change = cur_size - old_size
                change_value = abs(change) * float(pos.get("curPrice", 0))

                if change > 0 and change_value >= POSITION_CHANGE_MIN_USD:
                    # Size INCREASE
                    signals.append(self._build_signal(
                        wallet, wallet_info, pos, "INCREASE", cur_size, old_size,
                    ))
                elif change < 0 and change_value >= POSITION_CHANGE_MIN_USD:
                    # Size DECREASE — whale is exiting partially
                    signals.append(self._build_signal(
                        wallet, wallet_info, pos, "EXIT_PARTIAL", cur_size, old_size,
                    ))
                    # Manipulation check: entry→exit within 5 min
                    self._check_rapid_exit(wallet, cid, now)

        # Detect full exits: positions in cache but not in current
        if previous:
            for cid, old_pos in previous.items():
                if cid not in current_cids:
                    old_size = float(old_pos.get("size", 0))
                    old_value = old_size * float(old_pos.get("curPrice", 0))
                    if old_value >= POSITION_CHANGE_MIN_USD:
                        signals.append(self._build_signal(
                            wallet, wallet_info, old_pos, "EXIT_FULL", 0, old_size,
                        ))
                        self._check_rapid_exit(wallet, cid, now)

        return signals

    def _check_rapid_exit(self, wallet: str, cid: str, now: float) -> None:
        """Flag manipulation if whale exits within RAPID_EXIT_WINDOW_S of entry."""
        entry_time = self._entry_timestamps.get(wallet, {}).get(cid)
        if entry_time and (now - entry_time) < RAPID_EXIT_WINDOW_S:
            score = self._manipulation_scores.get(wallet, 0) + 1
            self._manipulation_scores[wallet] = score
            log.warning(
                "[WHALE] Rapid reversal detected: %s exited %s in %.0fs (score=%d)",
                wallet[:12], cid[:12], now - entry_time, score,
            )
            if score >= MAX_MANIPULATION_SCORE:
                self._db.blacklist_wallet(
                    wallet, f"Manipulation: {score} rapid reversals detected",
                )
        # Clean up entry timestamp
        if wallet in self._entry_timestamps:
            self._entry_timestamps[wallet].pop(cid, None)

    @staticmethod
    def _build_signal(
        wallet: str, wallet_info: dict, pos: dict,
        signal_type: str, cur_size: float, old_size: float,
    ) -> dict:
        """Build a copy trade signal from a position change."""
        price = float(pos.get("curPrice", 0))
        avg_price = float(pos.get("avgPrice", 0))
        outcome = pos.get("outcome", "Yes")

        return {
            "wallet": wallet,
            "wallet_score": wallet_info.get("composite_score", 0),
            "username": wallet_info.get("username", ""),
            "condition_id": pos.get("conditionId", ""),
            "title": pos.get("title", ""),
            "outcome": outcome,
            "side": "BUY",
            "whale_size": round(cur_size, 2),
            "whale_increase": round(cur_size - old_size, 2),
            "whale_price": round(avg_price, 4),
            "current_price": round(price, 4),
            "signal_type": signal_type,
            "timestamp": _now_iso(),
        }

    def _apply_consensus(self, signals: list[dict]) -> list[dict]:
        """Filter signals: require MIN_CONSENSUS independent whales on same direction.

        Wallet clustering: if two wallets consistently co-enter (>80% overlap),
        they count as one entity for consensus purposes.
        """
        # Separate entry signals from exit signals
        entry_signals = [s for s in signals if s["signal_type"] not in ("EXIT_PARTIAL", "EXIT_FULL")]
        exit_signals = [s for s in signals if s["signal_type"] in ("EXIT_PARTIAL", "EXIT_FULL")]

        # Track co-entries for clustering detection
        groups: dict[tuple, list[dict]] = defaultdict(list)
        for s in entry_signals:
            key = (s["condition_id"], s["outcome"])
            groups[key].append(s)

        for key, group in groups.items():
            wallets = sorted(s["wallet"] for s in group)
            for i in range(len(wallets)):
                for j in range(i + 1, len(wallets)):
                    pair = frozenset((wallets[i], wallets[j]))
                    self._co_entry_counts[pair] = self._co_entry_counts.get(pair, 0) + 1

        # Build consensus with clustering awareness
        consensus_signals = []
        for key, group in groups.items():
            wallets = {s["wallet"] for s in group}

            # Remove clustered wallets: if pair has >80% co-entry rate, count as 1
            independent = set(wallets)
            for pair, co_count in self._co_entry_counts.items():
                if not pair.issubset(wallets):
                    continue
                w1, w2 = list(pair)
                min_entries = min(
                    self._entry_counts.get(w1, 1),
                    self._entry_counts.get(w2, 1),
                )
                if min_entries > 3 and co_count / min_entries > 0.8:
                    # Clustered — remove the lower-scored wallet
                    scores = {s["wallet"]: s["wallet_score"] for s in group}
                    weaker = w2 if scores.get(w1, 0) >= scores.get(w2, 0) else w1
                    independent.discard(weaker)
                    log.info(
                        "[WHALE] Cluster detected: %s + %s (%.0f%% overlap) — counting as 1",
                        w1[:12], w2[:12], co_count / min_entries * 100,
                    )

            if len(independent) >= MIN_CONSENSUS:
                best = max(group, key=lambda s: s["wallet_score"])
                best["consensus_count"] = len(independent)
                best["signal_type"] = "CONSENSUS"
                best["consensus_wallets"] = list(independent)
                consensus_signals.append(best)
                log.info(
                    "[WHALE] CONSENSUS signal: %s | %s | %d independent whales | price=$%.3f",
                    best["title"][:50], best["outcome"],
                    len(independent), best["current_price"],
                )
            else:
                for s in group:
                    s["consensus_count"] = len(independent)
                    log.info(
                        "[WHALE] Signal (no consensus): %s | %s from %s (score=%.0f) [%d independent]",
                        s["title"][:40], s["outcome"],
                        s["wallet"][:12], s["wallet_score"], len(independent),
                    )

        # Process exit signals — these bypass consensus (if our whale exits, we should too)
        for s in exit_signals:
            s["consensus_count"] = 1
            consensus_signals.append(s)
            log.info(
                "[WHALE] EXIT signal: %s | %s from %s (score=%.0f)",
                s["title"][:40], s["signal_type"],
                s["wallet"][:12], s["wallet_score"],
            )

        return consensus_signals

    def get_active_signals(self) -> list[dict]:
        with self._lock:
            return list(self._active_signals)


# ═══════════════════════════════════════════════════════════════════
#  CopyExecutor — FOK order execution
# ═══════════════════════════════════════════════════════════════════

class CopyExecutor:
    """Executes copy trades via CLOB client with safety limits."""

    def __init__(self, db: WalletDB, clob_client, dry_run: bool = True):
        self._db = db
        self._client = clob_client
        self._dry_run = dry_run

    def execute_signal(self, signal: dict) -> dict | None:
        """Execute a copy trade from a whale signal. Returns trade record or None."""
        signal_type = signal.get("signal_type", "")

        # Handle exit signals separately
        if signal_type in ("EXIT_PARTIAL", "EXIT_FULL"):
            return self._handle_exit_signal(signal)

        whale_price = signal.get("whale_price", 0)
        current_price = signal.get("current_price", 0)

        # 0. Market duration check — skip short-lived markets
        market_remaining = self._get_market_remaining_s(signal.get("condition_id", ""))
        if market_remaining is not None and market_remaining < MIN_MARKET_DURATION_S:
            log.info(
                "[WHALE COPY] SKIP: market ends in %.0f min (< 60 min) | %s",
                market_remaining / 60, signal.get("title", "")[:40],
            )
            return None

        # 1. Max implied price check
        if current_price > MAX_IMPLIED_PRICE:
            log.info(
                "[WHALE COPY] SKIP: price $%.3f > cap $%.3f | %s",
                current_price, MAX_IMPLIED_PRICE, signal["title"][:40],
            )
            return None

        # 2. Slippage check
        if whale_price > 0:
            slippage = abs(current_price - whale_price) / whale_price * 100
            if slippage > MAX_SLIPPAGE_PCT:
                log.info(
                    "[WHALE COPY] SKIP: slippage %.1f%% > cap %.1f%% | whale=$%.3f now=$%.3f",
                    slippage, MAX_SLIPPAGE_PCT, whale_price, current_price,
                )
                return None
        else:
            slippage = 0

        # 3. Daily exposure cap
        daily_exposure = self._db.get_daily_exposure()
        if daily_exposure >= MAX_DAILY_EXPOSURE_USD:
            log.info(
                "[WHALE COPY] SKIP: daily exposure $%.2f >= cap $%.2f",
                daily_exposure, MAX_DAILY_EXPOSURE_USD,
            )
            return None

        # 4. Copy size: min(15% of whale, 3% bankroll, remaining daily budget)
        whale_size = signal.get("whale_increase", 0) or signal.get("whale_size", 0)
        bankroll = float(os.getenv("BANKROLL_USD", "1000"))
        bankroll_cap = round(bankroll * 0.03, 2)
        size = min(
            whale_size * MAX_COPY_PCT_OF_WHALE,
            MAX_COPY_SIZE_USD,
            bankroll_cap,
            MAX_DAILY_EXPOSURE_USD - daily_exposure,
        )
        if size < 1.0:
            log.info("[WHALE COPY] SKIP: computed size $%.2f < $1.00", size)
            return None
        size = round(size, 2)

        # 5. Auto-blacklist check for this whale
        perf = self._db.get_wallet_copy_performance(signal["wallet"])
        if perf["total"] >= MIN_TRADES_FOR_BLACKLIST and perf["wr"] < MIN_WR_THRESHOLD:
            self._db.blacklist_wallet(
                signal["wallet"],
                f"Copy WR {perf['wr']*100:.0f}% < {MIN_WR_THRESHOLD*100:.0f}% "
                f"over {perf['total']} trades",
            )
            return None

        # Build trade record
        trade = {
            "source_wallet": signal["wallet"],
            "condition_id": signal["condition_id"],
            "token_id": signal.get("token_id", ""),
            "side": signal["side"],
            "size": size,
            "entry_price": current_price,
            "whale_price": whale_price,
            "slippage_pct": round(slippage, 2),
            "timestamp": _now_iso(),
            "signal_type": signal.get("signal_type", "CONSENSUS"),
            "market_title": signal.get("title", ""),
            "dry_run": 1 if self._dry_run else 0,
        }

        if self._dry_run:
            trade["status"] = "FILLED"
            log.info(
                "[WHALE COPY][DRY] %s $%.2f @ $%.3f | whale=%s | %s",
                signal["side"], size, current_price,
                signal["wallet"][:12], signal["title"][:40],
            )
        else:
            order_id = self._place_fok_order(signal, size, current_price)
            trade["status"] = "FILLED" if order_id else "FAILED"

        trade_id = self._db.record_copy_trade(trade)
        trade["id"] = trade_id

        # Log to JSONL for dashboard
        self._log_copy_trade(trade)
        return trade

    def _handle_exit_signal(self, signal: dict) -> dict | None:
        """Close our copy trade when the whale exits."""
        cid = signal.get("condition_id", "")
        if not cid:
            return None

        # Find our open copy trade for this market
        open_trades = self._db.get_copy_trades(status="FILLED", limit=100)
        matching = [t for t in open_trades if t.get("condition_id") == cid]
        if not matching:
            log.debug("[WHALE EXIT] No open copy trade for %s", cid[:12])
            return None

        for trade in matching:
            exit_price = signal.get("current_price", 0)
            entry_price = trade.get("entry_price", 0)
            pnl = (exit_price - entry_price) * trade.get("size", 0) if entry_price > 0 else 0

            if self._dry_run:
                log.info(
                    "[WHALE EXIT][DRY] Closing copy trade #%d | entry=$%.3f exit=$%.3f | PnL=$%.2f | %s",
                    trade["id"], entry_price, exit_price, pnl, signal.get("title", "")[:40],
                )
            else:
                # Place sell order
                self._place_exit_order(trade, exit_price)

            # Update trade status
            status = "WON" if pnl > 0 else "LOST"
            self._db.update_copy_trade(
                trade["id"],
                status=status,
                exit_price=round(exit_price, 4),
                exit_timestamp=_now_iso(),
                pnl=round(pnl, 2),
            )
            log.info(
                "[WHALE EXIT] Trade #%d → %s ($%.2f) | whale=%s",
                trade["id"], status, pnl, signal.get("wallet", "")[:12],
            )

        return {"action": "exit", "trades_closed": len(matching)}

    def _place_exit_order(self, trade: dict, price: float) -> str | None:
        """Place a sell order to close a copy trade position."""
        if not self._client:
            return None
        try:
            from py_clob_client.clob_types import OrderArgs, OrderType
            from py_clob_client.order_builder.constants import SELL

            token_id = trade.get("token_id", "")
            if not token_id:
                return None
            size = trade.get("size", 0)
            shares = int(size / price) if price > 0 else 0
            if shares < 1:
                return None

            order_args = OrderArgs(price=price, size=shares, side=SELL, token_id=token_id)
            signed = self._client.create_order(order_args)
            resp = self._client.post_order(signed, OrderType.FOK)
            return resp.get("orderID") or resp.get("id", "")
        except Exception as e:
            log.error("[WHALE EXIT] Sell order error: %s", str(e)[:200])
            return None

    def _get_market_remaining_s(self, condition_id: str) -> float | None:
        """Query Gamma API for market end time, return seconds remaining."""
        if not condition_id:
            return None

        # Check cache on the monitor (shared via WhaleTracker)
        try:
            resp = requests.get(
                f"{GAMMA_API}/markets",
                params={"condition_id": condition_id, "limit": "1"},
                timeout=5,
                headers={"User-Agent": "GarvesWhaleTracker/1.0"},
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            if not data:
                return None

            market = data[0] if isinstance(data, list) else data
            end_str = market.get("end_date_iso") or market.get("endDate", "")
            if not end_str:
                return None

            from datetime import datetime, timezone
            end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
            remaining = (end_dt - datetime.now(timezone.utc)).total_seconds()
            return remaining
        except Exception as e:
            log.debug("[WHALE] Market duration check error: %s", str(e)[:80])
            return None

    def _place_fok_order(self, signal: dict, size: float, price: float) -> str | None:
        """Place FOK order on CLOB. Returns order_id or None."""
        if not self._client:
            log.error("[WHALE COPY] No CLOB client for live order")
            return None

        try:
            from py_clob_client.clob_types import OrderArgs, OrderType
            from py_clob_client.order_builder.constants import BUY

            token_id = signal.get("token_id", "")
            if not token_id:
                log.warning("[WHALE COPY] No token_id for %s", signal["condition_id"][:12])
                return None

            shares = int(size / price) if price > 0 else 0
            if shares < 1:
                return None

            order_args = OrderArgs(
                price=price, size=shares, side=BUY, token_id=token_id,
            )
            signed = self._client.create_order(order_args)
            resp = self._client.post_order(signed, OrderType.FOK)
            order_id = resp.get("orderID") or resp.get("id", "")
            status = (resp.get("status") or "").lower()

            if status in ("matched", "filled"):
                log.info("[WHALE COPY][LIVE] FILLED: $%.2f @ $%.3f | %s",
                         size, price, order_id)
                return order_id

            log.info("[WHALE COPY] FOK not filled: status=%s", status)
            return None

        except Exception as e:
            log.error("[WHALE COPY] Order error: %s", str(e)[:200])
            return None

    @staticmethod
    def _log_copy_trade(trade: dict) -> None:
        try:
            COPY_TRADES_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(COPY_TRADES_FILE, "a") as f:
                f.write(json.dumps(trade, default=str) + "\n")
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════
#  Backtester — historical validation
# ═══════════════════════════════════════════════════════════════════

class Backtester:
    """Run a 30-day historical backtest on the whale-following strategy."""

    def __init__(self, db: WalletDB):
        self._db = db

    def run_backtest(self) -> dict:
        """Simulate copy trades using historical whale trade data."""
        wallets = self._db.get_tracked_wallets()
        if not wallets:
            return {"error": "No tracked wallets", "passed": False}

        all_simulated = []
        per_wallet = {}

        for w in wallets:
            wallet = w["proxy_wallet"]
            trades = self._db.get_backtest_data(wallet, BACKTEST_DAYS)
            if len(trades) < 3:
                continue

            # Whale's leaderboard ROI — used as PnL proxy when per-trade PnL unavailable
            whale_pnl = float(w.get("total_pnl", 0))
            whale_vol = float(w.get("total_volume", 0))
            whale_roi = whale_pnl / whale_vol if whale_vol > 0 else 0

            sim_trades = []
            daily_exposure: dict[str, float] = defaultdict(float)

            for t in trades:
                price = t.get("price", 0)
                size = t.get("usdc_size", 0) or (t.get("size", 0) * price)
                if price <= 0 or price > MAX_IMPLIED_PRICE:
                    continue

                copy_size = min(size * MAX_COPY_PCT_OF_WHALE, MAX_COPY_SIZE_USD)
                day = (t.get("timestamp") or "")[:10]
                if daily_exposure[day] + copy_size > MAX_DAILY_EXPOSURE_USD:
                    continue
                daily_exposure[day] += copy_size

                pnl = t.get("pnl", 0)
                if pnl != 0:
                    whale_cost = size
                    pnl_scaled = pnl * (copy_size / whale_cost) if whale_cost > 0 else 0
                else:
                    # Estimate PnL from whale's aggregate ROI
                    pnl_scaled = copy_size * whale_roi if whale_roi != 0 else 0

                sim_trades.append({
                    "wallet": wallet,
                    "size": copy_size,
                    "price": price,
                    "pnl": round(pnl_scaled, 4),
                    "won": pnl_scaled > 0,
                })

            if sim_trades:
                wins = sum(1 for t in sim_trades if t["won"])
                total_pnl = sum(t["pnl"] for t in sim_trades)
                per_wallet[wallet[:12]] = {
                    "trades": len(sim_trades),
                    "wins": wins,
                    "wr": round(wins / len(sim_trades), 3),
                    "pnl": round(total_pnl, 2),
                }
                all_simulated.extend(sim_trades)

        if not all_simulated:
            return {"passed": False, "reason": "no_trades", "total_trades": 0,
                    "per_wallet": per_wallet}

        total_wins = sum(1 for t in all_simulated if t["won"])
        total_pnl = sum(t["pnl"] for t in all_simulated)
        wr = total_wins / len(all_simulated)
        passed = len(all_simulated) >= BACKTEST_MIN_TRADES and wr >= BACKTEST_MIN_WR

        result = {
            "passed": passed,
            "total_trades": len(all_simulated),
            "wins": total_wins,
            "losses": len(all_simulated) - total_wins,
            "win_rate": round(wr * 100, 1),
            "total_pnl": round(total_pnl, 2),
            "avg_pnl_per_trade": round(total_pnl / len(all_simulated), 2),
            "per_wallet": per_wallet,
            "config": {
                "lookback_days": BACKTEST_DAYS,
                "min_wr": BACKTEST_MIN_WR,
                "min_trades": BACKTEST_MIN_TRADES,
                "max_copy_size": MAX_COPY_SIZE_USD,
                "max_daily_exposure": MAX_DAILY_EXPOSURE_USD,
            },
            "ran_at": _now_iso(),
        }

        log.info(
            "[WHALE BACKTEST] %s | %d trades | WR=%.1f%% | P&L=$%.2f",
            "PASSED" if passed else "FAILED",
            len(all_simulated), wr * 100, total_pnl,
        )
        return result


# ═══════════════════════════════════════════════════════════════════
#  WhaleTracker — main orchestrator
# ═══════════════════════════════════════════════════════════════════

class WhaleTracker:
    """Orchestrates whale discovery, scoring, monitoring, and copy execution.

    Lifecycle:
      1. seed_wallets()     — pull top traders from leaderboard
      2. score_wallets()    — analyze and rank
      3. select_targets()   — pick top N for tracking
      4. tick()             — poll + detect + execute (called every POLL_INTERVAL_S)
    """

    def __init__(self, clob_client=None, dry_run: bool = True):
        self.db = WalletDB()
        self.scorer = WhaleScorer()
        self.monitor = WhaleMonitor(self.db)
        self.executor = CopyExecutor(self.db, clob_client, dry_run=dry_run)
        self.backtester = Backtester(self.db)
        self.enabled = False
        self.dry_run = dry_run

        self._last_seed = 0.0
        self._last_rescore = 0.0
        self._backtest_result: dict = {}
        self._tick_count = 0
        self._stats = {
            "signals_generated": 0,
            "trades_executed": 0,
            "trades_skipped": 0,
            "daily_exposure": 0.0,
        }

    def initialize(self) -> None:
        """Bootstrap: seed, score, select, backtest."""
        log.info("[WHALE] Initializing whale tracker...")
        self.seed_wallets()
        self.score_wallets()
        self.select_targets()
        self._backtest_result = self.backtester.run_backtest()
        self.enabled = True
        log.info("[WHALE] Initialized: %d wallets tracked, backtest %s",
                 len(self.db.get_tracked_wallets()),
                 "PASSED" if self._backtest_result.get("passed") else "PENDING")

    def seed_wallets(self) -> int:
        """Pull top traders from Polymarket leaderboard."""
        now = time.time()
        if now - self._last_seed < LEADERBOARD_REFRESH_H * 3600:
            return 0

        log.info("[WHALE] Seeding wallets from leaderboard...")
        wallets_found = set()

        for cat in LEADERBOARD_CATEGORIES:
            for period in LEADERBOARD_PERIODS:
                try:
                    resp = requests.get(
                        f"{DATA_API}/v1/leaderboard",
                        params={
                            "category": cat,
                            "timePeriod": period,
                            "orderBy": "PNL",
                            "limit": str(LEADERBOARD_TOP_N),
                        },
                        timeout=10,
                        headers={"User-Agent": "GarvesWhaleTracker/1.0"},
                    )
                    if resp.status_code != 200:
                        continue
                    data = resp.json()
                    if not isinstance(data, list):
                        continue

                    for trader in data:
                        wallet = trader.get("proxyWallet", "")
                        if not wallet:
                            continue
                        wallets_found.add(wallet)
                        self.db.upsert_wallet(
                            wallet,
                            username=trader.get("userName", ""),
                            total_pnl=float(trader.get("pnl", 0)),
                            total_volume=float(trader.get("vol", 0)),
                            first_seen=_now_iso(),
                            category=cat,
                        )
                    time.sleep(1)  # Rate limit

                except Exception as e:
                    log.warning("[WHALE] Leaderboard fetch error (%s/%s): %s",
                                cat, period, str(e)[:100])

        self._last_seed = now
        log.info("[WHALE] Seeded %d unique wallets", len(wallets_found))
        return len(wallets_found)

    def score_wallets(self) -> None:
        """Score all wallets using leaderboard PnL + trade history."""
        wallets = self.db.get_all_wallets()
        scored = 0

        for w in wallets:
            wallet = w["proxy_wallet"]
            if w.get("is_blacklisted"):
                continue

            pnl = float(w.get("total_pnl", 0))
            volume = float(w.get("total_volume", 0))

            # Skip wallets with no leaderboard data
            if pnl <= 0 and volume < 1000:
                continue

            trades = []
            try:
                resp = requests.get(
                    f"{DATA_API}/trades",
                    params={"user": wallet, "limit": "200", "takerOnly": "true"},
                    timeout=10,
                    headers={"User-Agent": "GarvesWhaleTracker/1.0"},
                )
                time.sleep(0.5)  # Rate limit

                if resp.status_code == 200:
                    raw_trades = resp.json()
                    if isinstance(raw_trades, list):
                        for t in raw_trades:
                            price = float(t.get("price", 0))
                            size = float(t.get("size", 0))
                            trades.append({
                                "price": price,
                                "size": size,
                                "usdc_size": round(size * price, 2),
                                "pnl": 0,
                                "timestamp": t.get("timestamp", ""),
                                "side": t.get("side", ""),
                            })

                            # Store for backtesting
                            self.db.record_whale_trade({
                                "proxy_wallet": wallet,
                                "condition_id": t.get("conditionId", t.get("asset", "")),
                                "token_id": t.get("asset", ""),
                                "side": t.get("side", ""),
                                "size": size,
                                "price": price,
                                "usdc_size": round(size * price, 2),
                                "timestamp": t.get("timestamp", _now_iso()),
                                "market_title": t.get("title", ""),
                                "outcome": t.get("outcome", ""),
                            })
            except Exception as e:
                log.debug("[WHALE] Trade fetch error for %s: %s", wallet[:12], str(e)[:80])

            # Score using leaderboard PnL + whatever trade data we got
            scores = self.scorer.score_wallet(trades, pnl, volume)

            self.db.upsert_wallet(
                wallet,
                composite_score=scores["composite_score"],
                ev_per_trade=scores.get("ev_per_trade", 0),
                sharpe_ratio=scores.get("sharpe_ratio", 0),
                profit_factor=scores.get("profit_factor", 0),
                max_drawdown=scores.get("max_drawdown", 0),
                total_trades=scores.get("sample_size", 0),
                win_count=scores.get("win_count", 0),
                loss_count=scores.get("loss_count", 0),
                last_active=_now_iso(),
            )
            scored += 1

        self._last_rescore = time.time()
        log.info("[WHALE] Scored %d wallets", scored)

    def select_targets(self) -> None:
        """Pick top N wallets with score >= threshold for active tracking."""
        wallets = self.db.get_all_wallets()

        for w in wallets:
            self.db.set_tracked(w["proxy_wallet"], False)

        eligible = [
            w for w in wallets
            if w.get("composite_score", 0) >= MIN_WALLET_SCORE
            and not w.get("is_blacklisted")
        ]
        eligible.sort(key=lambda w: w["composite_score"], reverse=True)

        for w in eligible[:MAX_TRACKED_WALLETS]:
            self.db.set_tracked(w["proxy_wallet"], True)
            log.info(
                "[WHALE] Tracking: %s (score=%.0f, PnL=$%.0f)",
                w.get("username") or w["proxy_wallet"][:12],
                w["composite_score"], w.get("total_pnl", 0),
            )

    def tick(self) -> None:
        """One monitoring cycle: poll wallets, detect signals, execute copies."""
        if not self.enabled:
            return

        self._tick_count += 1

        # Periodic reseed (every 24h)
        if time.time() - self._last_seed > LEADERBOARD_REFRESH_H * 3600:
            try:
                self.seed_wallets()
                self.score_wallets()
                self.select_targets()
            except Exception as e:
                log.warning("[WHALE] Reseed error: %s", str(e)[:100])

        # Poll and get signals
        signals = self.monitor.poll_wallets()
        self._stats["signals_generated"] += len(signals)

        # Execute consensus signals
        for signal in signals:
            try:
                result = self.executor.execute_signal(signal)
                if result:
                    self._stats["trades_executed"] += 1
                else:
                    self._stats["trades_skipped"] += 1
            except Exception as e:
                log.warning("[WHALE] Execute error: %s", str(e)[:100])
                self._stats["trades_skipped"] += 1

        self._stats["daily_exposure"] = self.db.get_daily_exposure()

        # Performance check every 50 ticks (~200s)
        if self._tick_count % 50 == 0:
            self.check_performance()

        # Write status for dashboard every 5 ticks (~20s)
        if self._tick_count % 5 == 0:
            self._write_status()

    def check_performance(self) -> None:
        """Auto-blacklist whales whose copied trades fall below 55% WR."""
        tracked = self.db.get_tracked_wallets()
        for w in tracked:
            wallet = w["proxy_wallet"]
            perf = self.db.get_wallet_copy_performance(wallet)
            if (perf["total"] >= MIN_TRADES_FOR_BLACKLIST
                    and perf["wr"] < MIN_WR_THRESHOLD):
                self.db.blacklist_wallet(
                    wallet,
                    f"Copy WR {perf['wr']*100:.0f}% < {MIN_WR_THRESHOLD*100:.0f}% "
                    f"over {perf['total']} trades (P&L ${perf['pnl']:.2f})",
                )

    def get_status(self) -> dict:
        """Dashboard-friendly status summary."""
        tracked = self.db.get_tracked_wallets()
        all_wallets = self.db.get_all_wallets()
        recent_copies = self.db.get_copy_trades(limit=10)
        active_signals = self.monitor.get_active_signals()

        all_copies = self.db.get_copy_trades(limit=200)
        resolved = [t for t in all_copies if t["status"] in ("WON", "LOST")]
        wins = sum(1 for t in resolved if t["status"] == "WON")
        total_pnl = sum(t.get("pnl", 0) for t in resolved)

        # Gather manipulation + clustering stats from monitor
        manipulation_scores = getattr(self.monitor, "_manipulation_scores", {})
        co_entries = getattr(self.monitor, "_co_entry_counts", {})
        clusters = []
        entry_counts = getattr(self.monitor, "_entry_counts", {})
        for pair, cnt in co_entries.items():
            w1, w2 = list(pair)
            min_e = min(entry_counts.get(w1, 1), entry_counts.get(w2, 1))
            if min_e > 3 and cnt / min_e > 0.8:
                clusters.append({"wallets": [w1[:12], w2[:12]], "overlap_pct": round(cnt / min_e * 100, 1)})

        return {
            "enabled": self.enabled,
            "dry_run": self.dry_run,
            "tracked_wallets": len(tracked),
            "total_wallets_scanned": len(all_wallets),
            "blacklisted": sum(1 for w in all_wallets if w.get("is_blacklisted")),
            "active_signals": len(active_signals),
            "tick_count": self._tick_count,
            "stats": self._stats,
            "performance": {
                "total_copies": len(all_copies),
                "resolved": len(resolved),
                "wins": wins,
                "losses": len(resolved) - wins,
                "win_rate": round(wins / len(resolved) * 100, 1) if resolved else 0,
                "total_pnl": round(total_pnl, 2),
            },
            "recent_copies": recent_copies[:5],
            "top_wallets": [
                {
                    "wallet": w["proxy_wallet"][:12],
                    "username": w.get("username", ""),
                    "score": w.get("composite_score", 0),
                    "pnl": w.get("total_pnl", 0),
                    "trades": w.get("total_trades", 0),
                }
                for w in tracked[:10]
            ],
            "backtest": self._backtest_result,
            "signals": active_signals[:5],
            "daily_exposure": self._stats.get("daily_exposure", 0),
            "daily_cap": MAX_DAILY_EXPOSURE_USD,
            "manipulation_flags": {
                w[:12]: s for w, s in manipulation_scores.items() if s > 0
            },
            "detected_clusters": clusters,
            "updated_at": _now_iso(),
        }

    def _write_status(self) -> None:
        try:
            status = self.get_status()
            STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
            STATUS_FILE.write_text(json.dumps(status, indent=2, default=str))
        except Exception as e:
            log.debug("[WHALE] Status write error: %s", str(e)[:80])


# ═══════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_crypto_market(title: str) -> bool:
    t = title.lower()
    return any(kw in t for kw in CRYPTO_KEYWORDS)


def _max_drawdown(trades: list[dict]) -> float:
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in trades:
        cumulative += t.get("pnl", 0)
        peak = max(peak, cumulative)
        dd = peak - cumulative
        max_dd = max(max_dd, dd)
    return max_dd
