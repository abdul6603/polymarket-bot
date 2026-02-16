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
from flask import Flask, render_template

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


@app.after_request
def add_no_cache(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


# ── Index route ──

@app.route("/")
def index():
    return render_template("dashboard.html")


# ── Register all route blueprints ──
# Now safe: blueprints import from bot.shared, not from this module.
from bot.routes import register_all_blueprints
register_all_blueprints(app)


# ── Main entry point ──

if __name__ == "__main__":
    import webbrowser
    import time

    # Auto-start Atlas background research loop
    def _auto_start_atlas():
        try:
            atlas = get_atlas()
            if atlas and not atlas.background.is_running():
                atlas.start_background()
                print("[Dashboard] Atlas background research loop auto-started")
        except Exception as e:
            print(f"[Dashboard] Atlas auto-start failed: {e}")

    # Auto-process broadcasts for agents without active loops (Soren, Lisa, Garves)
    def _broadcast_processor():
        """Periodically ack broadcasts for agents that don't have their own loops."""
        import time as _time
        _time.sleep(10)  # Wait for app to start
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
            _time.sleep(30)

    threading.Thread(target=_broadcast_processor, daemon=True, name="broadcast-ack").start()

    threading.Timer(2.0, _auto_start_atlas).start()
    threading.Timer(1.0, lambda: webbrowser.open("http://localhost:8877")).start()
    app.run(host="0.0.0.0", port=8877, debug=False, threaded=True)
