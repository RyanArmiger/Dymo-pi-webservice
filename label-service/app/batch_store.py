"""Short-lived server-side storage for a parsed (validated) Excel batch.

Parsed rows are held here, keyed by a random token, between the
upload/preview step and the confirm-print step so the browser round-trip
only needs to carry the token rather than the whole batch. Never used to
hold allocated label IDs -- those are only ever created inside
database.allocate_label_ids at print-confirmation time.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import secrets
import time
from pathlib import Path

import config
from app.validation import LabelRecord

_STORE_DIR = config.GENERATED_DIR / "batch_tmp"
_MAX_AGE_SECONDS = 60 * 60


def _store_dir() -> Path:
    _STORE_DIR.mkdir(parents=True, exist_ok=True)
    return _STORE_DIR


def save_batch(
    records: list[LabelRecord],
    source_filename: str | None,
    source_bytes: bytes | None = None,
) -> str:
    purge_expired()
    token = secrets.token_urlsafe(16)
    payload = {
        "source_filename": source_filename,
        "created_at": time.time(),
        "records": [dataclasses.asdict(r) for r in records],
    }
    path = _store_dir() / f"{token}.json"
    path.write_text(json.dumps(payload))
    if source_bytes is not None:
        (_store_dir() / f"{token}.xlsx").write_bytes(source_bytes)
    return token


def load_batch(token: str) -> tuple[list[LabelRecord], str | None] | None:
    path = _store_dir() / f"{token}.json"
    if not path.is_file():
        return None
    payload = json.loads(path.read_text())
    records = [LabelRecord(**r) for r in payload["records"]]
    return records, payload.get("source_filename")


def delete_batch(token: str) -> None:
    for suffix in (".json", ".xlsx"):
        (_store_dir() / f"{token}{suffix}").unlink(missing_ok=True)


def archive_source(token: str, batch_id: int, source_filename: str) -> tuple[str, str, int]:
    """Move a confirmed workbook into the permanent batch archive."""
    source = _store_dir() / f"{token}.xlsx"
    if not source.is_file():
        raise FileNotFoundError("Temporary source workbook is missing")

    config.BATCH_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    archive_name = f"batch_{batch_id:06d}_{source_filename}"
    destination = config.BATCH_UPLOAD_DIR / archive_name
    source.replace(destination)
    data = destination.read_bytes()
    return archive_name, hashlib.sha256(data).hexdigest(), len(data)


def purge_expired(max_age_seconds: int = _MAX_AGE_SECONDS) -> None:
    now = time.time()
    for path in _store_dir().glob("*.json"):
        if now - path.stat().st_mtime > max_age_seconds:
            delete_batch(path.stem)
