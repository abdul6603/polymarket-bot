"""Order Block Memory — persistent OB/FVG storage across sessions.

Remembers every OB/FVG ever detected, tracks hit rates,
predicts revisit targets based on historical success.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("odin.skills.ob_memory")

DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS zones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    zone_type TEXT NOT NULL,  -- OB or FVG
    direction TEXT NOT NULL,  -- BULLISH or BEARISH
    price_level REAL NOT NULL,
    top REAL NOT NULL,
    bottom REAL NOT NULL,
    strength REAL DEFAULT 0,
    volume_zscore REAL DEFAULT 0,
    detected_at REAL NOT NULL,
    mitigated INTEGER DEFAULT 0,
    mitigated_at REAL DEFAULT 0,
    hit_count INTEGER DEFAULT 0,
    win_count INTEGER DEFAULT 0,
    loss_count INTEGER DEFAULT 0,
    details TEXT DEFAULT '{}',
    UNIQUE(symbol, timeframe, zone_type, direction, price_level)
);
CREATE INDEX IF NOT EXISTS idx_zones_symbol ON zones(symbol);
CREATE INDEX IF NOT EXISTS idx_zones_active ON zones(mitigated, symbol);
"""


@dataclass
class ZoneRecord:
    """A stored OB/FVG zone."""
    id: int
    symbol: str
    timeframe: str
    zone_type: str
    direction: str
    price_level: float
    top: float
    bottom: float
    strength: float
    hit_count: int
    win_count: int
    mitigated: bool
    detected_at: float

    @property
    def hit_rate(self) -> float:
        if self.hit_count == 0:
            return 0.0
        return self.win_count / self.hit_count * 100


class OBMemory:
    """Persistent Order Block and FVG memory with SQLite backend."""

    def __init__(self, db_path: Path | None = None):
        self._db_path = db_path or Path.home() / "odin" / "data" / "ob_memory.db"
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.executescript(DB_SCHEMA)
        self._conn.commit()

    def store_patterns(self, symbol: str, timeframe: str, smc_data: dict) -> int:
        """Store detected OBs and FVGs from SMC analysis. Returns count stored."""
        stored = 0
        now = time.time()

        for ob in smc_data.get("active_obs", []):
            if self._upsert_zone(symbol, timeframe, "OB", ob, now):
                stored += 1

        for fvg in smc_data.get("active_fvgs", []):
            if self._upsert_zone(symbol, timeframe, "FVG", fvg, now):
                stored += 1

        if stored > 0:
            log.info("[OB_MEM] Stored %d zones for %s %s", stored, symbol, timeframe)
        return stored

    def _upsert_zone(self, symbol: str, tf: str, ztype: str, pattern: dict, now: float) -> bool:
        """Insert or update a zone. Returns True if new."""
        direction = pattern.get("direction", "NEUTRAL")
        if hasattr(direction, "name"):
            direction = direction.name
        elif hasattr(direction, "value"):
            direction = str(direction.value)

        price_level = pattern.get("price_level", 0)
        top = pattern.get("top", price_level)
        bottom = pattern.get("bottom", price_level)
        strength = pattern.get("strength", 0)
        vol_z = pattern.get("volume_zscore", 0)
        mitigated = 1 if pattern.get("mitigated", False) else 0
        details = json.dumps(pattern.get("details", {}))

        try:
            self._conn.execute(
                """INSERT INTO zones
                   (symbol, timeframe, zone_type, direction, price_level, top, bottom,
                    strength, volume_zscore, detected_at, mitigated, details)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(symbol, timeframe, zone_type, direction, price_level)
                   DO UPDATE SET strength=MAX(strength, excluded.strength),
                                 mitigated=excluded.mitigated,
                                 details=excluded.details""",
                (symbol, tf, ztype, direction, price_level, top, bottom,
                 strength, vol_z, now, mitigated, details),
            )
            self._conn.commit()
            return True
        except Exception as e:
            log.debug("[OB_MEM] Upsert error: %s", str(e)[:100])
            return False

    def get_active_zones(self, symbol: str, price_range: tuple[float, float] | None = None) -> list[ZoneRecord]:
        """Get all unmitigated zones for a symbol, optionally within a price range."""
        query = "SELECT * FROM zones WHERE symbol=? AND mitigated=0"
        params: list = [symbol]

        if price_range:
            query += " AND bottom <= ? AND top >= ?"
            params.extend([price_range[1], price_range[0]])

        query += " ORDER BY strength DESC"

        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_record(r) for r in rows]

    def get_historical_zones(self, symbol: str, limit: int = 50) -> list[ZoneRecord]:
        """Get all zones (including mitigated) for pattern analysis."""
        rows = self._conn.execute(
            "SELECT * FROM zones WHERE symbol=? ORDER BY detected_at DESC LIMIT ?",
            (symbol, limit),
        ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def record_hit(self, zone_id: int, is_win: bool) -> None:
        """Record when price revisits a zone and whether the trade won."""
        col = "win_count" if is_win else "loss_count"
        self._conn.execute(
            f"UPDATE zones SET hit_count=hit_count+1, {col}={col}+1 WHERE id=?",
            (zone_id,),
        )
        self._conn.commit()

    def mark_mitigated(self, zone_id: int) -> None:
        """Mark a zone as mitigated (price fully filled it)."""
        self._conn.execute(
            "UPDATE zones SET mitigated=1, mitigated_at=? WHERE id=?",
            (time.time(), zone_id),
        )
        self._conn.commit()

    def predict_revisits(self, symbol: str, current_price: float, radius_pct: float = 3.0) -> list[dict]:
        """Predict which zones price is likely to revisit.

        Returns zones sorted by probability (strength * proximity * history).
        """
        low = current_price * (1 - radius_pct / 100)
        high = current_price * (1 + radius_pct / 100)
        zones = self.get_active_zones(symbol, (low, high))

        predictions = []
        for z in zones:
            distance_pct = abs(z.price_level - current_price) / current_price * 100
            proximity_score = max(0, 1 - distance_pct / radius_pct)
            history_score = z.hit_rate / 100 if z.hit_count >= 3 else 0.5
            probability = (z.strength / 100 * 0.4 + proximity_score * 0.35 + history_score * 0.25) * 100

            predictions.append({
                "zone_id": z.id,
                "type": z.zone_type,
                "direction": z.direction,
                "price_level": z.price_level,
                "top": z.top,
                "bottom": z.bottom,
                "strength": z.strength,
                "distance_pct": round(distance_pct, 2),
                "hit_rate": round(z.hit_rate, 1),
                "probability": round(probability, 1),
            })

        predictions.sort(key=lambda p: p["probability"], reverse=True)
        return predictions[:10]

    def get_stats(self) -> dict:
        """Overall memory stats."""
        row = self._conn.execute(
            "SELECT COUNT(*), SUM(CASE WHEN mitigated=0 THEN 1 ELSE 0 END), "
            "SUM(hit_count), SUM(win_count) FROM zones"
        ).fetchone()
        total, active, hits, wins = row[0] or 0, row[1] or 0, row[2] or 0, row[3] or 0
        return {
            "total_zones": total,
            "active_zones": active,
            "total_hits": hits,
            "overall_hit_rate": round(wins / max(hits, 1) * 100, 1),
            "db_size_kb": round(self._db_path.stat().st_size / 1024, 1)
            if self._db_path.exists() else 0,
        }

    def _row_to_record(self, row: tuple) -> ZoneRecord:
        return ZoneRecord(
            id=row[0], symbol=row[1], timeframe=row[2], zone_type=row[3],
            direction=row[4], price_level=row[5], top=row[6], bottom=row[7],
            strength=row[8], hit_count=row[10] if len(row) > 10 else 0,
            win_count=row[11] if len(row) > 11 else 0,
            mitigated=bool(row[9] if len(row) > 9 else 0),
            detected_at=row[13] if len(row) > 13 else 0,
        )

    def close(self) -> None:
        self._conn.close()
