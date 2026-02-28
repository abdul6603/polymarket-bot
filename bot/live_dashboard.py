"""
Resilient Flask + (optional) SocketIO dashboard entrypoint.

This module is defensive: route/blueprint imports are attempted lazily during
app startup and any failing imports are caught and logged. The dashboard will
still start in a degraded mode exposing a /health endpoint and a simple status
page listing any failed route modules so operators (Robotox) can triage.

Design goals:
- Never raise an uncaught exception at import time due to a broken blueprint.
- Log full tracebacks for each failing import.
- Register a lightweight fallback blueprint that points to logs.
"""
from __future__ import annotations

import importlib
import logging
import os
import pkgutil
import traceback
from pathlib import Path
from typing import List

from flask import Flask, Blueprint, jsonify, current_app, render_template_string

log = logging.getLogger("bot.live_dashboard")


def _discover_route_modules(package_name: str = "bot.routes") -> List[str]:
    """Return a list of submodule names under bot.routes (dotted import names)."""
    try:
        pkg = importlib.import_module(package_name)
    except Exception:
        log.debug("No package %s available to discover routes", package_name)
        return []

    pkg_path = getattr(pkg, "__path__", None)
    if not pkg_path:
        return []

    found = []
    for finder, name, ispkg in pkgutil.iter_modules(pkg.__path__):
        # full import path
        found.append(f"{package_name}.{name}")
    return found


def _register_blueprints(app: Flask, package_name: str = "bot.routes") -> List[str]:
    """Attempt to import route modules and register any Blueprint named 'bp'.

    Returns a list of module names that failed to import.
    """
    failed: List[str] = []
    modules = _discover_route_modules(package_name)

    for mod_name in modules:
        try:
            log.debug("Importing dashboard route module %s", mod_name)
            mod = importlib.import_module(mod_name)
            # If module exposes `bp` (Blueprint) register it.
            bp = getattr(mod, "bp", None)
            if isinstance(bp, Blueprint):
                app.register_blueprint(bp)
                log.info("Registered blueprint from %s", mod_name)
            else:
                # If module defines a register_routes(app) function, call it.
                reg = getattr(mod, "register_routes", None)
                if callable(reg):
                    try:
                        reg(app)
                        log.info("Registered routes via register_routes() in %s", mod_name)
                    except Exception:
                        log.exception("register_routes() failed in %s", mod_name)
                        failed.append(mod_name)
                else:
                    log.debug("No blueprint or register_routes() in %s — skipping", mod_name)
        except Exception:
            log.exception("Failed to import/register routes from %s", mod_name)
            failed.append(mod_name)
    return failed


def create_app(config: dict | None = None) -> Flask:
    """Create a Flask app with defensive blueprint imports.

    config: optional dict to pass into app.config.update()
    """
    app = Flask(__name__, static_folder=None)
    # Minimal config defaults
    app.config.setdefault("DEBUG", False)
    app.config.setdefault("SECRET_KEY", os.environ.get("DASHBOARD_SECRET", "dashboard_secret"))

    if config:
        app.config.update(config)

    # Health endpoint early so monitoring can hit it immediately
    @app.route("/health")
    def _health():
        return jsonify({"status": "ok", "app": "live_dashboard"})

    # Status page showing degraded modules (filled later)
    @app.route("/status")
    def _status():
        failed = getattr(current_app, "_failed_route_imports", [])
        return render_template_string(
            "<h2>Dashboard status</h2>"
            "<p>Failed route modules: {{failed|length}}</p>"
            "<ul>{% for m in failed %}<li>{{m}}</li>{% endfor %}</ul>"
            "<p>Check logs for tracebacks.</p>",
            failed=failed,
        )

    # Try to init (optional) SocketIO without making it fatal
    try:
        from flask_socketio import SocketIO
        socketio = SocketIO(app, logger=False, engineio_logger=False)
        # store object so callers can use it if available
        app.extensions["socketio"] = socketio
        log.info("SocketIO initialized")
    except Exception:
        log.debug("SocketIO not available or failed to initialize; continuing without websockets")
        # don't re-raise — dashboard can work without socketio

    # Attempt to import and register route blueprints
    failed_imports = []
    try:
        failed_imports = _register_blueprints(app, "bot.routes")
    except Exception:
        log.exception("Unexpected failure during blueprint registration")
        # ensure we capture this unexpected failure in failed_imports list
        failed_imports.append("bot.routes.__all__")  # sentinel

    # Save failed imports list on app for /status endpoint
    app._failed_route_imports = failed_imports  # type: ignore[attr-defined]

    # If any imports failed, register a fallback blueprint that gives a friendly page.
    if failed_imports:
        fallback_bp = Blueprint("dashboard_degraded", __name__, url_prefix="/dashboard")

        @fallback_bp.route("/")
        def degraded_index():
            failed = current_app._failed_route_imports  # type: ignore[attr-defined]
            msg = (
                "<h1>Dashboard (degraded)</h1>"
                f"<p>{len(failed)} route module(s) failed to load.</p>"
                "<p>See server logs for full tracebacks. This page intentionally hides internal details.</p>"
            )
            return render_template_string(msg)

        app.register_blueprint(fallback_bp)
        log.warning("Dashboard started in degraded mode: %d failing modules", len(failed_imports))
        for m in failed_imports:
            log.warning(" - Failed route module: %s", m)

    # Basic root route
    @app.route("/")
    def index():
        if app._failed_route_imports:
            return render_template_string(
                "<h1>Dashboard</h1><p>Degraded. Visit <a href='/status'>/status</a>.</p>"
            )
        return render_template_string("<h1>Dashboard</h1><p>All systems nominal.</p>")

    return app


if __name__ == "__main__":
    # Allow running the dashboard directly for local development.
    logging.basicConfig(level=logging.INFO)
    app = create_app({"DEBUG": True})
    # Use socketio.run if available, else fallback to Flask's built-in server.
    sio = app.extensions.get("socketio")
    if sio:
        try:
            sio.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 7777)))
        except Exception:
            log.exception("SocketIO run failed — falling back to Flask.run")
            app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 7777)))
    else:
        app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 7777)))