"""Cross-Agent Brotherhood Bridge — connects Odin to the event bus and Atlas.

Polls shared events for Garves trades, Atlas insights, and Robotox alerts.
Publishes Odin's own signals and trade events.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from shared.events import publish, get_unread

log = logging.getLogger("odin.brotherhood")

ATLAS_SENTIMENT_FILE = Path.home() / "atlas" / "data" / "news_sentiment.json"
SNIPE_ASSIST_FILE = Path.home() / "polymarket-bot" / "data" / "snipe_assist.json"
MOMENTUM_FILE = Path.home() / "polymarket-bot" / "data" / "momentum_mode.json"

# Map Bitunix symbols to Atlas keys
_SYMBOL_MAP = {
    "BTCUSDT": "BTC", "ETHUSDT": "ETH", "XRPUSDT": "XRP",
    "SOLUSDT": "SOL", "DOGEUSDT": "DOGE", "ADAUSDT": "ADA",
}


class BrotherhoodBridge:
    """Connects Odin to the shared event bus and Atlas intelligence."""

    def __init__(self):
        self._brother_state: dict[str, dict] = {
            "garves": {
                "last_direction": None,
                "last_asset": None,
                "recent_wr": None,
                "streak": 0,
            },
        }
        self._atlas_sentiment: dict = {}
        self._anomaly_active = False
        self._anomaly_expires = 0.0
        self._brother_accuracy: dict[str, dict] = {}
        self._robotox_alert = False
        self._robotox_alert_expires = 0.0
        self._events_processed = 0
        self._timing_rec: dict = {}  # Latest snipe timing recommendation
        self._discord_pipeline = None  # Attached by main.py

    def poll_events(self) -> dict:
        """Poll event bus for brother intelligence. Returns summary."""
        try:
            events = get_unread("odin")
        except Exception as e:
            log.debug("[BROTHERHOOD] Event read error: %s", str(e)[:100])
            return {"events_processed": 0}

        processed = 0
        for event in events:
            try:
                self._dispatch_event(event)
                processed += 1
            except Exception as e:
                log.debug("[BROTHERHOOD] Event dispatch error: %s", str(e)[:100])

        # Auto-expire anomaly flag after 30 min
        if self._anomaly_active and time.time() > self._anomaly_expires:
            self._anomaly_active = False

        if self._robotox_alert and time.time() > self._robotox_alert_expires:
            self._robotox_alert = False

        self._events_processed += processed
        return {
            "events_processed": processed,
            "garves": self._brother_state.get("garves", {}),
            "anomaly_active": self._anomaly_active,
        }

    def _dispatch_event(self, event: dict) -> None:
        """Route event to appropriate handler."""
        agent = event.get("agent", "")
        etype = event.get("type", "")
        data = event.get("data", {})

        if agent == "garves" and etype == "trade_placed":
            self._on_garves_trade(data)
        elif agent == "garves" and etype == "trade_resolved":
            self._on_garves_resolved(data)
        elif agent == "garves" and etype == "snipe_timing_recommendation":
            self._on_timing_recommendation(data)
        elif agent == "atlas" and etype == "insight_found":
            self._on_atlas_insight(data)
        elif agent == "robotox" and etype == "alert_fired":
            self._on_robotox_alert(data)
        elif agent == "garves" and etype in ("momentum_activated", "momentum_deactivated"):
            self._on_momentum_event(data)
        elif agent == "discord_scraper" and etype == "discord_signal":
            self._on_discord_signal(event)

    def attach_discord_pipeline(self, pipeline) -> None:
        """Attach the Discord pipeline for signal routing."""
        self._discord_pipeline = pipeline
        log.info("[BROTHERHOOD] Discord pipeline attached")

    def _on_discord_signal(self, event: dict) -> None:
        """Route discord_signal event to the pipeline."""
        if self._discord_pipeline is None:
            log.debug("[BROTHERHOOD] Discord signal received but no pipeline attached")
            return
        try:
            result = self._discord_pipeline.process_signal(event)
            if result:
                log.info("[BROTHERHOOD] Discord signal processed: %s %s → %s",
                         result.ticker, result.direction, result.decision)
        except Exception as e:
            log.warning("[BROTHERHOOD] Discord pipeline error: %s", str(e)[:150])

    def _on_momentum_event(self, data: dict) -> None:
        """Garves momentum mode activated/deactivated."""
        active = data.get("active", False)
        direction = data.get("direction", "")
        if active:
            log.info("[BROTHERHOOD] Garves MOMENTUM ACTIVE: %s %s %+.1f%%",
                     direction.upper(), data.get("trigger_asset", ""),
                     data.get("trigger_pct", 0))
        else:
            log.info("[BROTHERHOOD] Garves MOMENTUM DEACTIVATED")

    def _on_garves_trade(self, data: dict) -> None:
        """Garves placed a trade — update direction/asset."""
        garves = self._brother_state["garves"]
        garves["last_direction"] = data.get("direction")
        garves["last_asset"] = data.get("asset") or data.get("symbol")
        garves["last_conviction"] = data.get("conviction", 0)
        log.info("[BROTHERHOOD] Garves trade: %s %s",
                 garves["last_direction"], garves["last_asset"])

    def _on_garves_resolved(self, data: dict) -> None:
        """Garves trade resolved — update WR/streak."""
        garves = self._brother_state["garves"]
        is_win = data.get("is_win", False)
        asset = data.get("asset") or data.get("symbol", "")

        if is_win:
            garves["streak"] = max(garves["streak"], 0) + 1
        else:
            garves["streak"] = min(garves["streak"], 0) - 1

        # Update per-asset accuracy
        key = asset.upper().replace("USDT", "")
        if key not in self._brother_accuracy:
            self._brother_accuracy[key] = {"wins": 0, "total": 0}
        self._brother_accuracy[key]["total"] += 1
        if is_win:
            self._brother_accuracy[key]["wins"] += 1

        acc = self._brother_accuracy[key]
        garves["recent_wr"] = round(acc["wins"] / max(acc["total"], 1) * 100, 1)

        self.learn_from_brother(data)

    def _on_timing_recommendation(self, data: dict) -> None:
        """Snipe timing recommendation from Garves — cache for trade evaluation."""
        self._timing_rec = data
        log.info("[BROTHERHOOD] Timing rec: %s %s score=%s",
                 data.get("action", ""), data.get("direction", ""),
                 data.get("timing_score", 0))

    def get_timing_recommendation(self) -> dict:
        """Read snipe timing recommendation. Prefers file (fresher), falls back to event cache."""
        try:
            if SNIPE_ASSIST_FILE.exists():
                data = json.loads(SNIPE_ASSIST_FILE.read_text())
                age = time.time() - data.get("timestamp", 0)
                if age < 300:  # Fresh within 5 min
                    odin_ovr = (data.get("agent_overrides") or {}).get("odin", {})
                    return {
                        "action": odin_ovr.get("action", data.get("action", "")),
                        "size_pct": odin_ovr.get("size_pct", data.get("recommended_size_pct", 1.0)),
                        "direction_hint": odin_ovr.get("direction_hint", ""),
                        "confirmation": odin_ovr.get("confirmation", False),
                        "timing_score": data.get("timing_score", 0),
                        "fresh": True,
                        "age_s": round(age, 1),
                    }
        except Exception:
            pass
        # Fallback to event bus cache
        if self._timing_rec and time.time() - self._timing_rec.get("expires_at", 0) < 0:
            return {**self._timing_rec, "fresh": False}
        return {}

    def get_momentum_mode(self) -> dict | None:
        """Read Garves's momentum mode state. Returns dict or None if inactive/expired."""
        try:
            if not MOMENTUM_FILE.exists():
                return None
            data = json.loads(MOMENTUM_FILE.read_text())
            if not data.get("active"):
                return None
            if time.time() >= data.get("expires_at", 0):
                return None
            return data
        except Exception:
            return None

    def _on_atlas_insight(self, data: dict) -> None:
        """Atlas found something — flag anomaly for caution."""
        severity = data.get("severity", "info")
        if severity in ("warning", "critical"):
            self._anomaly_active = True
            self._anomaly_expires = time.time() + 1800  # 30 min
            log.info("[BROTHERHOOD] Atlas anomaly active: %s", data.get("summary", "")[:100])

    def _on_robotox_alert(self, data: dict) -> None:
        """Robotox alert — check if about Odin."""
        target = data.get("agent", "")
        if target.lower() == "odin":
            self._robotox_alert = True
            self._robotox_alert_expires = time.time() + 600  # 10 min
            log.warning("[BROTHERHOOD] Robotox alert about Odin: %s",
                        data.get("message", "")[:150])

    def get_brother_alignment(self, symbol: str, direction: str) -> dict:
        """Check if Garves agrees with our direction for this asset."""
        garves = self._brother_state.get("garves", {})
        g_dir = garves.get("last_direction")
        g_asset = (garves.get("last_asset") or "").upper().replace("USDT", "")
        our_asset = symbol.upper().replace("USDT", "")

        if not g_dir or g_asset != our_asset:
            return {"alignment": 0.5, "reason": "no_data", "garves_direction": g_dir}

        same_dir = g_dir.upper() == direction.upper()
        # Check if Garves's last result on this asset was a win
        acc = self._brother_accuracy.get(our_asset, {})
        recent_wr = acc.get("wins", 0) / max(acc.get("total", 1), 1) * 100

        if same_dir and recent_wr >= 50:
            alignment = 1.0
            reason = "aligned_winning"
        elif same_dir:
            alignment = 0.3
            reason = "aligned_losing"
        else:
            alignment = 0.0
            reason = "opposite"

        return {
            "alignment": alignment,
            "reason": reason,
            "garves_direction": g_dir,
            "garves_wr": round(recent_wr, 1),
        }

    def get_atlas_sentiment(self, symbol: str) -> dict:
        """Read Atlas news sentiment for this symbol."""
        asset = _SYMBOL_MAP.get(symbol, symbol.replace("USDT", ""))

        # Try fresh file read
        try:
            if ATLAS_SENTIMENT_FILE.exists():
                data = json.loads(ATLAS_SENTIMENT_FILE.read_text())
                self._atlas_sentiment = data
        except Exception:
            pass

        # Look up asset sentiment
        for key in [asset, asset.lower(), f"{asset}USD", f"{asset}USDT"]:
            if key in self._atlas_sentiment:
                entry = self._atlas_sentiment[key]
                score = entry.get("score", 0)
                direction = "LONG" if score > 0.1 else "SHORT" if score < -0.1 else "NEUTRAL"
                return {
                    "direction": direction,
                    "score": score,
                    "headlines": entry.get("headlines", [])[:3],
                }

        return {"direction": "NEUTRAL", "score": 0.0, "headlines": []}

    def should_pause_trading(self) -> tuple[bool, str]:
        """True if critical anomaly or Robotox alert active."""
        if self._robotox_alert:
            return True, "Robotox alert active for Odin"
        if self._anomaly_active:
            return True, "Atlas critical anomaly active"
        return False, ""

    def publish_signal(self, signal: dict) -> None:
        """Publish signal generated event."""
        try:
            publish(
                agent="odin",
                event_type="odin_signal_generated",
                data=signal,
                summary=f"Signal: {signal.get('direction', '')} {signal.get('symbol', '')}",
            )
        except Exception as e:
            log.debug("[BROTHERHOOD] Publish signal error: %s", str(e)[:100])

    def publish_trade_open(self, trade: dict) -> None:
        """Publish trade opened event."""
        try:
            publish(
                agent="odin",
                event_type="trade_placed",
                data=trade,
                summary=f"Odin opened {trade.get('direction', '')} {trade.get('symbol', '')}",
            )
        except Exception as e:
            log.debug("[BROTHERHOOD] Publish open error: %s", str(e)[:100])

    def publish_trade_close(self, trade: dict) -> None:
        """Publish trade closed event."""
        try:
            pnl = trade.get("pnl_usd", 0)
            publish(
                agent="odin",
                event_type="trade_resolved",
                data=trade,
                severity="info" if pnl >= 0 else "warning",
                summary=f"Odin closed {trade.get('symbol', '')} PnL=${pnl:+.2f}",
            )
        except Exception as e:
            log.debug("[BROTHERHOOD] Publish close error: %s", str(e)[:100])

    def request_swarm_vote(self, signal: dict) -> dict:
        """Send a trade setup to brother agents for vote. 2/3 must agree.

        Publishes vote request to event bus, then checks for responses.
        In practice: reads Garves direction + Atlas sentiment as 2 voters,
        Odin itself is the 3rd. Requires 2/3 agreement to proceed.
        """
        symbol = signal.get("symbol", "")
        direction = signal.get("direction", "LONG")
        asset = symbol.upper().replace("USDT", "")

        votes = {"odin": direction}  # Odin always votes its own direction

        # Voter 1: Garves (crypto direction)
        garves = self._brother_state.get("garves", {})
        g_dir = garves.get("last_direction")
        g_asset = (garves.get("last_asset") or "").upper().replace("USDT", "")
        if g_dir and g_asset == asset:
            garves_vote = g_dir.upper()
            votes["garves"] = garves_vote
        else:
            # No recent Garves data — abstain (counts as agree)
            votes["garves"] = direction

        # Voter 2: Atlas sentiment
        sentiment = self.get_atlas_sentiment(symbol)
        sent_dir = sentiment.get("direction", "NEUTRAL")
        if sent_dir in ("LONG", "SHORT"):
            votes["atlas"] = sent_dir
        else:
            votes["atlas"] = "NEUTRAL"  # Abstain

        # Count agreement
        agree_count = sum(1 for v in votes.values() if v == direction)
        total_voters = len(votes)
        # Neutral = abstain, counts as neither agree nor disagree
        neutral_count = sum(1 for v in votes.values() if v == "NEUTRAL")
        effective_voters = total_voters - neutral_count
        effective_agree = sum(1 for v in votes.values() if v == direction)

        # 2/3 consensus required (with neutrals removed)
        consensus = effective_agree >= max(2, int(effective_voters * 2 / 3 + 0.5)) if effective_voters > 0 else False

        result = {
            "consensus": consensus,
            "votes": votes,
            "agree_count": agree_count,
            "total_voters": total_voters,
            "direction": direction,
            "recommendation": "PROCEED" if consensus else "HOLD",
        }

        log.info(
            "[SWARM] %s %s: %s (%d/%d agree) votes=%s",
            direction, symbol, result["recommendation"],
            agree_count, total_voters, votes,
        )

        # Publish vote result to event bus
        try:
            publish(
                agent="odin",
                event_type="swarm_vote",
                data=result,
                summary=f"Swarm vote: {result['recommendation']} {direction} {symbol}",
            )
        except Exception:
            pass

        return result

    def learn_from_brother(self, event: dict) -> None:
        """Store Garves knowledge in odin.db for future reference."""
        try:
            from shared.agent_memory import AgentMemory
            mem = AgentMemory("odin")

            asset = (event.get("asset") or event.get("symbol", "")).upper().replace("USDT", "")
            direction = event.get("direction", "").lower()
            is_win = event.get("is_win", False)

            if is_win and asset and direction:
                mem.set_knowledge(
                    category="brother_intel",
                    key=f"garves_{asset}_{direction}",
                    value=f"Garves won {direction} {asset} — consider alignment",
                    source="garves_trade_resolved",
                    ttl_hours=4,
                )
            elif asset:
                mem.set_knowledge(
                    category="brother_intel",
                    key=f"garves_{asset}_uncertain",
                    value=f"Garves lost {direction} {asset} — direction uncertain",
                    source="garves_trade_resolved",
                    ttl_hours=2,
                )
        except Exception as e:
            log.debug("[BROTHERHOOD] Learn error: %s", str(e)[:100])

    def get_status(self) -> dict:
        """Dashboard-friendly status snapshot."""
        garves = self._brother_state.get("garves", {})
        status = {
            "garves_direction": garves.get("last_direction"),
            "garves_asset": garves.get("last_asset"),
            "garves_wr": garves.get("recent_wr"),
            "garves_streak": garves.get("streak", 0),
            "anomaly_active": self._anomaly_active,
            "robotox_alert": self._robotox_alert,
            "events_processed_total": self._events_processed,
            "brother_accuracy": self._brother_accuracy,
            "timing_rec": self._timing_rec,
        }
        if self._discord_pipeline:
            status["discord_pipeline"] = self._discord_pipeline.get_status()
        return status
