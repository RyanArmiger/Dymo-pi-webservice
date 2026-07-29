"""Stores rendered preview PNGs under random tokens so routes never expose
server file paths directly to the browser (spec section 15)."""
from __future__ import annotations

import secrets
import time
from pathlib import Path

from PIL import Image

import config

_PREVIEW_DIR = config.GENERATED_DIR / "previews"
_MAX_AGE_SECONDS = 60 * 60


def _preview_dir() -> Path:
    _PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    return _PREVIEW_DIR


def save_preview(image: Image.Image) -> str:
    purge_expired()
    token = secrets.token_urlsafe(16)
    path = _preview_dir() / f"{token}.png"
    image.convert("L").save(path, format="PNG")
    return token


def preview_path(token: str) -> Path | None:
    # token comes straight from secrets.token_urlsafe, but validate anyway
    # since it also arrives back via a URL segment from the browser.
    if not token.isalnum() and "-" not in token and "_" not in token:
        return None
    path = _preview_dir() / f"{token}.png"
    return path if path.is_file() else None


def purge_expired(max_age_seconds: int = _MAX_AGE_SECONDS) -> None:
    now = time.time()
    for path in _preview_dir().glob("*.png"):
        if now - path.stat().st_mtime > max_age_seconds:
            path.unlink(missing_ok=True)
