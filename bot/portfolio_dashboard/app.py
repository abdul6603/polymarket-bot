"""
Agent Swarm — Portfolio Dashboard

Standalone Flask app serving a rebranded, client-ready version of the
Command Center. Uses the same backend API routes (bot.routes) but with:
  - 12 curated tabs (trading agents, intelligence, infrastructure)
  - Agency branding (no internal references)
  - Demo data toggle for masking real financials
  - Wallet address masking via JS MutationObserver

Runs on port 8899 by default (main dashboard is on 7777).
"""
from __future__ import annotations

import importlib
import logging
import os
import pkgutil
import time
import traceback
from pathlib import Path
from typing import List

from flask import Flask, Blueprint, jsonify, render_template

log = logging.getLogger("portfolio_dashboard")

# Routes to include (maps to bot.routes.<name>)
# These provide the /api/* endpoints that the 12 tabs need
INCLUDED_ROUTES = [
    "overview", "traders", "pnl", "garves", "hawk", "odin",
    "oracle", "quant", "discord_intel", "atlas", "infra", "system",
    # Support routes needed by multiple tabs
    "brain", "llm", "chat", "_utils",
]

# Routes to EXCLUDE (internal-only or broken)
EXCLUDED_ROUTES = [
    "lisa", "mercury", "soren", "shelby", "thor",
    "robotox", "sentinel", "viper", "arbiter", "whale",
]


def _register_selected_blueprints(app: Flask) -> List[str]:
    """Import and register only the selected route blueprints."""
    failed: List[str] = []
    package_name = "bot.routes"

    try:
        importlib.import_module(package_name)
    except Exception:
        log.error("Cannot import %s — is the bot package on PYTHONPATH?", package_name)
        return [package_name]

    for route_name in INCLUDED_ROUTES:
        mod_name = f"{package_name}.{route_name}"
        try:
            mod = importlib.import_module(mod_name)
            # Look for a Blueprint named 'bp' or '*_bp'
            bp = getattr(mod, "bp", None)
            if isinstance(bp, Blueprint):
                app.register_blueprint(bp)
                log.info("Registered: %s", mod_name)
                continue

            for attr_name in dir(mod):
                if attr_name.endswith("_bp"):
                    obj = getattr(mod, attr_name, None)
                    if isinstance(obj, Blueprint):
                        app.register_blueprint(obj)
                        log.info("Registered: %s (%s)", mod_name, attr_name)
                        break
            else:
                reg = getattr(mod, "register_routes", None)
                if callable(reg):
                    reg(app)
                    log.info("Registered via register_routes(): %s", mod_name)
                else:
                    log.debug("No blueprint in %s — skipped", mod_name)
        except Exception:
            log.exception("Failed to load %s", mod_name)
            failed.append(mod_name)

    return failed


def create_app() -> Flask:
    """Create the portfolio dashboard Flask app."""
    _dir = Path(__file__).resolve().parent

    app = Flask(
        __name__,
        static_folder=str(_dir / "static"),
        static_url_path="/static",
        template_folder=str(_dir / "templates"),
    )
    app.config["SECRET_KEY"] = os.environ.get("PORTFOLIO_SECRET", "portfolio_secret")

    # Health endpoint
    @app.route("/health")
    def health():
        return jsonify({"status": "ok", "app": "portfolio_dashboard"})

    # Register selected API routes
    failed = _register_selected_blueprints(app)
    if failed:
        log.warning("Portfolio dashboard: %d route(s) failed to load", len(failed))
        for m in failed:
            log.warning("  - %s", m)

    # Main dashboard route
    @app.route("/")
    def index():
        try:
            return render_template("dashboard.html", cache_bust=str(int(time.time())))
        except Exception:
            log.exception("Failed to render portfolio dashboard")
            return "<h1>Portfolio Dashboard</h1><p>Template error. Check logs.</p>", 500

    return app


app = create_app()

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    port = int(os.environ.get("PORTFOLIO_PORT", 8899))
    log.info("Starting Agent Swarm Portfolio Dashboard on port %d", port)
    app.run(host="0.0.0.0", port=port, debug=False)
