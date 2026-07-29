from __future__ import annotations

import secrets

from flask import Flask

import config
from app import database


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = config.SECRET_KEY
    app.config["MAX_CONTENT_LENGTH"] = config.MAX_UPLOAD_BYTES
    app.config["PERMANENT_SESSION_LIFETIME"] = config.SESSION_LIFETIME
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = config.SESSION_COOKIE_SECURE

    database.init_db()

    from app.routes import bp

    app.register_blueprint(bp)

    @app.after_request
    def _no_cache_generated(response):
        return response

    return app


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)
