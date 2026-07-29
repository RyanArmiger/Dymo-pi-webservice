"""Minimal session-based CSRF protection (no extra dependency beyond Flask)."""
from __future__ import annotations

import hmac
import secrets

from flask import abort, request, session

_SESSION_KEY = "csrf_token"


def get_csrf_token() -> str:
    token = session.get(_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[_SESSION_KEY] = token
    return token


def validate_csrf(submitted_token: str | None) -> bool:
    expected = session.get(_SESSION_KEY)
    if not expected or not submitted_token:
        return False
    return hmac.compare_digest(expected, submitted_token)


def require_csrf() -> None:
    if request.method == "POST":
        token = request.form.get("csrf_token")
        if not validate_csrf(token):
            abort(400, description="Invalid or missing CSRF token")
