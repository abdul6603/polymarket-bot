"""
COMMAND CENTER -- Unified Dashboard for Shelby, Garves, Soren & Atlas
Run: python -m bot.live_dashboard
Opens on http://localhost:8877

The Flask app lives here. All shared state, helpers, and path constants
are in bot.shared (to avoid circular imports with route blueprints).
Route handlers live in bot/routes/ as Flask Blueprints.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, render_template, request

try:
    from flask_socketio import SocketIO, emit
    HAS_SOCKETIO = True
except ImportError:
    HAS_SOCKETIO = False

# Load .env so OPENAI_API_KEY is available
load_dotenv(Path(__file__).parent.parent / ".env")

# ── Re-export everything from bot.shared for backward compatibility ──
# Any code that still does `from bot.live_dashboard import X` will keep working.
from bot.shared import (  # noqa: F401
    DATA_DIR,
    TRADES_FILE,
    LOG_FILE,
    SOREN_QUEUE_FILE,
    SOREN_TRENDS_FILE,
    INDICATOR_ACCURACY_FILE,
    SHELBY_TASKS_FILE,
    SHELBY_PROFILE_FILE,
    SHELBY_CONVERSATION_FILE,
    SOREN_ROOT,
    SOREN_OUTPUT_DIR,
    ATLAS_ROOT,
    MERCURY_ROOT,
    SHELBY_ROOT_DIR,
    COMPETITOR_INTEL_FILE,
    SHELBY_SCHEDULER_LOG,
    MERCURY_POSTING_LOG,
    MERCURY_ANALYTICS_FILE,
    SHELBY_ASSESSMENTS_FILE,
    SHELBY_AGENT_REGISTRY_FILE,
    SHELBY_TELEGRAM_CONFIG,
    ET,
    _DEFAULT_ASSESSMENTS,
    _generation_status,
    _chat_history,
    _system_cache,
    _weather_cache,
    _updates_cache,
    _AGENT_PROMPTS,
    get_atlas,
    _load_trades,
    _load_recent_logs,
)

# ── Flask app ──

app = Flask(
    __name__,
    static_folder=str(Path(__file__).parent / "static"),
    template_folder=str(Path(__file__).parent / "templates"),
)
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0


@app.after_request
def _no_cache(response):
    """Prevent browser from caching HTML, CSS, and JS."""
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

# ── SocketIO for real-time push (optional — falls back to polling if unavailable) ──
socketio = None
if HAS_SOCKETIO:
    socketio = SocketIO(app, async_mode="threading", cors_allowed_origins=["http://localhost:8877", "http://127.0.0.1:8877"])


def broadcast_event(event_type, data=None):
    """Emit a Socket.IO event to all connected clients (if socketio is available)."""
    if socketio:
        try:
            socketio.emit(event_type, data or {})
        except Exception:
            pass


@app.after_request
def add_cache_headers(response):
    # Only disable caching for API responses, allow browser caching for static assets
    if request.path.startswith("/api/") or request.path == "/" or request.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


# ── Index route ──

@app.route("/")
def index():
    import time as _t
    return render_template("dashboard.html", cache_bust=int(_t.time()))


# ── Register all route blueprints ──
# Now safe: blueprints import from bot.shared, not from this module.
from bot.routes import register_all_blueprints
register_all_blueprints(app)


# ── SocketIO events (if available) ──
if socketio:
    @socketio.on("connect")
    def handle_connect():
        emit("status", {"connected": True, "server": "Command Center"})

    @socketio.on("request_heartbeats")
    def handle_request_heartbeats():
        try:
            sys.path.insert(0, str(Path.home() / ".agent-hub"))
            from hub import AgentHub
            emit("heartbeats", AgentHub.get_heartbeats())
        except Exception:
            emit("heartbeats", {})

    @socketio.on("request_health")
    def handle_request_health():
        try:
            sys.path.insert(0, str(Path.home() / ".agent-hub"))
            from hub import AgentHub
            emit("system_health", AgentHub.system_health())
        except Exception:
            emit("system_health", {"overall": "unknown"})


# ── Main entry point ──

if __name__ == "__main__":
    import socket
    import webbrowser
    import time

    # ── Guard: exit immediately if port 8877 is already in use ──
    _sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    _sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        _sock.bind(("127.0.0.1", 8877))
        _sock.close()
    except OSError:
        print("[Dashboard] Port 8877 already in use — another instance is running. Exiting.")
        sys.exit(0)

    # Atlas runs on Pro as its own LaunchAgent (com.atlas.agent).
    # Dashboard reads Pro's status via SSH — no local loop needed.
    def _auto_start_atlas():
        print("[Dashboard] Atlas background loop disabled — runs on Pro (status fetched via SSH)")

    # Auto-process broadcasts + send heartbeats for agents without active loops
    def _broadcast_processor():
        """Periodically ack broadcasts and send heartbeats for dashboard + passive agents."""
        import time as _time
        _time.sleep(10)  # Wait for app to start

        # Initialize hub for dashboard heartbeats
        try:
            sys.path.insert(0, str(Path.home() / ".agent-hub"))
            from hub import AgentHub
            dashboard_hub = AgentHub("dashboard")
            dashboard_hub.register(port=8877, capabilities=["web_ui", "api", "chat"])
        except Exception:
            dashboard_hub = None

        while True:
            try:
                sys.path.insert(0, str(SHELBY_ROOT_DIR))
                from core.broadcast import get_unread_broadcasts, acknowledge_broadcast

                for agent, data_dir in [
                    ("garves", DATA_DIR),
                    ("soren", SOREN_ROOT / "data"),
                    ("lisa", MERCURY_ROOT / "data"),
                ]:
                    unread = get_unread_broadcasts(data_dir)
                    for bc in unread:
                        acknowledge_broadcast(agent, bc.get("id", ""), data_dir)
            except Exception:
                pass

            # Send dashboard heartbeat
            if dashboard_hub:
                try:
                    dashboard_hub.heartbeat(status="online", metrics={
                        "port": 8877,
                        "uptime": "active",
                    })
                except Exception:
                    pass

            # Thor auto-wake check (every 30s loop iteration)
            try:
                from bot.routes.thor import thor_auto_wake_check
                thor_auto_wake_check()
            except Exception:
                pass

            _time.sleep(30)

    threading.Thread(target=_broadcast_processor, daemon=True, name="broadcast-ack").start()

    # Auto-start Lisa auto-poster daemon
    def _auto_start_lisa_poster():
        try:
            from mercury.core.auto_poster import AutoPoster
            poster = AutoPoster()
            if not poster.is_running():
                poster.start()
                print("[Dashboard] Lisa auto-poster daemon started")
        except Exception as e:
            print(f"[Dashboard] Lisa auto-poster start failed: {e}")

    # Auto-start Lisa reply hunter
    def _auto_start_reply_hunter():
        try:
            from mercury.core.reply_hunter import ReplyHunter
            hunter = ReplyHunter()
            if not hunter._thread or not hunter._thread.is_alive():
                hunter.start(interval=1800)
                print("[Dashboard] Lisa reply hunter started (30min cycle)")
        except Exception as e:
            print(f"[Dashboard] Lisa reply hunter start failed: {e}")

    threading.Timer(2.0, _auto_start_atlas).start()
    threading.Timer(5.0, _auto_start_lisa_poster).start()
    threading.Timer(8.0, _auto_start_reply_hunter).start()
    # threading.Timer(1.0, lambda: webbrowser.open("http://localhost:8877")).start()

    if socketio:
        print("[Dashboard] Running with Flask-SocketIO (WebSocket support)")
        socketio.run(app, host="0.0.0.0", port=8877, debug=False, allow_unsafe_werkzeug=True)
    else:
        print("[Dashboard] Running without SocketIO (polling only)")
        app.run(host="0.0.0.0", port=8877, debug=False, threaded=True)
