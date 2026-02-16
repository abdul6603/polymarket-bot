"""
Route blueprints for the Command Center dashboard.
Each file contains a Flask Blueprint with related API routes.
"""
from __future__ import annotations

from flask import Flask


def register_all_blueprints(app: Flask) -> None:
    """Import and register every route blueprint on the Flask app."""
    from bot.routes.garves import garves_bp
    from bot.routes.soren import soren_bp
    from bot.routes.atlas import atlas_bp
    from bot.routes.shelby import shelby_bp
    from bot.routes.mercury import mercury_bp
    from bot.routes.sentinel import sentinel_bp
    from bot.routes.chat import chat_bp
    from bot.routes.overview import overview_bp
    from bot.routes.infra import infra_bp
    from bot.routes.thor import thor_bp
    from bot.routes.brain import brain_bp

    app.register_blueprint(garves_bp)
    app.register_blueprint(soren_bp)
    app.register_blueprint(atlas_bp)
    app.register_blueprint(shelby_bp)
    app.register_blueprint(mercury_bp)
    app.register_blueprint(sentinel_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(overview_bp)
    app.register_blueprint(infra_bp)
    app.register_blueprint(thor_bp)
    app.register_blueprint(brain_bp)
