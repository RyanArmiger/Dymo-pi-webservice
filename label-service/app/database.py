"""SQLite persistence: schema, connection handling and atomic ID allocation.

The four-digit label ID is the one value that must never be duplicated, so
every allocation happens inside a `BEGIN IMMEDIATE` transaction that reads and
updates the `counter` table as a single atomic step (see `allocate_label_ids`).
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS counter (
    name TEXT PRIMARY KEY,
    current_value INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS labels (
    id INTEGER PRIMARY KEY,
    label_id TEXT NOT NULL UNIQUE,
    date TEXT NOT NULL,
    sample_id TEXT NOT NULL,
    experiment_id TEXT NOT NULL,
    requestor_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    source TEXT NOT NULL,
    batch_id INTEGER NULL,
    status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS print_batches (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    source_filename TEXT NULL,
    requested_count INTEGER NOT NULL,
    pair_count INTEGER NOT NULL,
    status TEXT NOT NULL,
    error_message TEXT NULL,
    source_file_path TEXT NULL,
    source_file_sha256 TEXT NULL,
    source_file_size INTEGER NULL
);

CREATE TABLE IF NOT EXISTS print_jobs (
    id INTEGER PRIMARY KEY,
    batch_id INTEGER NOT NULL,
    pair_index INTEGER NOT NULL,
    cups_job_id TEXT NULL,
    file_path TEXT NOT NULL,
    submitted_at TEXT NULL,
    status TEXT NOT NULL,
    error_message TEXT NULL
);

CREATE TABLE IF NOT EXISTS reprints (
    id INTEGER PRIMARY KEY,
    label_id TEXT NOT NULL,
    reprinted_at TEXT NOT NULL,
    reason TEXT NULL,
    cups_job_id TEXT NULL
);

CREATE TABLE IF NOT EXISTS admin_actions (
    id INTEGER PRIMARY KEY,
    action TEXT NOT NULL,
    detail TEXT NULL,
    performed_at TEXT NOT NULL
);
"""


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or config.DATABASE_PATH
    conn = sqlite3.connect(str(path), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def init_db(db_path: Path | None = None) -> None:
    conn = get_connection(db_path)
    try:
        conn.executescript(SCHEMA)
        batch_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(print_batches)").fetchall()
        }
        for name, sql_type in (
            ("source_file_path", "TEXT"),
            ("source_file_sha256", "TEXT"),
            ("source_file_size", "INTEGER"),
        ):
            if name not in batch_columns:
                conn.execute(f"ALTER TABLE print_batches ADD COLUMN {name} {sql_type} NULL")
        conn.execute(
            "INSERT OR IGNORE INTO counter (name, current_value, updated_at) VALUES (?, 0, ?)",
            (config.COUNTER_NAME, now_iso()),
        )
    finally:
        conn.close()


@contextmanager
def immediate_transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


class CounterExhaustedError(RuntimeError):
    """Raised when the four-digit counter would exceed LABEL_ID_MAX."""


def allocate_label_ids(conn: sqlite3.Connection, count: int) -> list[str]:
    """Atomically reserve `count` consecutive label IDs.

    Uses BEGIN IMMEDIATE so concurrent callers serialize on the write lock
    instead of racing a read-then-write of the counter (see spec section 6).
    """
    if count <= 0:
        raise ValueError("count must be positive")

    with immediate_transaction(conn):
        row = conn.execute(
            "SELECT current_value FROM counter WHERE name = ?",
            (config.COUNTER_NAME,),
        ).fetchone()
        current = row["current_value"]
        first = current + 1
        last = current + count
        if last > config.LABEL_ID_MAX:
            raise CounterExhaustedError(
                f"Requested IDs {first}-{last} exceed the maximum "
                f"{config.LABEL_ID_MAX}. Counter was not advanced."
            )
        conn.execute(
            "UPDATE counter SET current_value = ?, updated_at = ? WHERE name = ?",
            (last, now_iso(), config.COUNTER_NAME),
        )

    digits = config.LABEL_ID_DIGITS
    return [str(n).zfill(digits) for n in range(first, last + 1)]


def get_counter_value(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT current_value FROM counter WHERE name = ?", (config.COUNTER_NAME,)
    ).fetchone()
    return row["current_value"]


def set_counter_value(conn: sqlite3.Connection, value: int, reason: str) -> None:
    """Administrator-only override. Always logged to admin_actions."""
    with immediate_transaction(conn):
        conn.execute(
            "UPDATE counter SET current_value = ?, updated_at = ? WHERE name = ?",
            (value, now_iso(), config.COUNTER_NAME),
        )
        conn.execute(
            "INSERT INTO admin_actions (action, detail, performed_at) VALUES (?, ?, ?)",
            ("counter_set", f"new_value={value} reason={reason}", now_iso()),
        )


def insert_label(
    conn: sqlite3.Connection,
    *,
    label_id: str,
    date: str,
    sample_id: str,
    experiment_id: str,
    requestor_id: str,
    source: str,
    batch_id: int | None,
    status: str = "allocated",
) -> int:
    cur = conn.execute(
        """
        INSERT INTO labels
            (label_id, date, sample_id, experiment_id, requestor_id,
             created_at, source, batch_id, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            label_id,
            date,
            sample_id,
            experiment_id,
            requestor_id,
            now_iso(),
            source,
            batch_id,
            status,
        ),
    )
    return cur.lastrowid


def update_label_status(conn: sqlite3.Connection, label_id: str, status: str) -> None:
    conn.execute("UPDATE labels SET status = ? WHERE label_id = ?", (status, label_id))


def get_label(conn: sqlite3.Connection, label_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT labels.*, print_batches.source_filename,
               print_batches.source_file_path,
               print_batches.source_file_sha256,
               print_batches.source_file_size
        FROM labels
        LEFT JOIN print_batches ON print_batches.id = labels.batch_id
        WHERE labels.label_id = ?
        """,
        (label_id,),
    ).fetchone()


def create_batch(
    conn: sqlite3.Connection,
    *,
    source_filename: str | None,
    requested_count: int,
    pair_count: int,
    status: str = "pending",
) -> int:
    cur = conn.execute(
        """
        INSERT INTO print_batches
            (created_at, source_filename, requested_count, pair_count, status, error_message)
        VALUES (?, ?, ?, ?, ?, NULL)
        """,
        (now_iso(), source_filename, requested_count, pair_count, status),
    )
    return cur.lastrowid


def update_batch_status(
    conn: sqlite3.Connection, batch_id: int, status: str, error_message: str | None = None
) -> None:
    conn.execute(
        "UPDATE print_batches SET status = ?, error_message = ? WHERE id = ?",
        (status, error_message, batch_id),
    )


def set_batch_source_file(
    conn: sqlite3.Connection,
    batch_id: int,
    *,
    file_path: str,
    sha256: str,
    size: int,
) -> None:
    conn.execute(
        """
        UPDATE print_batches
        SET source_file_path = ?, source_file_sha256 = ?, source_file_size = ?
        WHERE id = ?
        """,
        (file_path, sha256, size, batch_id),
    )


def get_batch(conn: sqlite3.Connection, batch_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM print_batches WHERE id = ?", (batch_id,)).fetchone()


def create_print_job(
    conn: sqlite3.Connection,
    *,
    batch_id: int,
    pair_index: int,
    file_path: str,
    status: str = "pending",
) -> int:
    cur = conn.execute(
        """
        INSERT INTO print_jobs (batch_id, pair_index, cups_job_id, file_path, submitted_at, status, error_message)
        VALUES (?, ?, NULL, ?, NULL, ?, NULL)
        """,
        (batch_id, pair_index, file_path, status),
    )
    return cur.lastrowid


def update_print_job(
    conn: sqlite3.Connection,
    job_id: int,
    *,
    status: str,
    cups_job_id: str | None = None,
    error_message: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE print_jobs
        SET status = ?, cups_job_id = ?, submitted_at = ?, error_message = ?
        WHERE id = ?
        """,
        (status, cups_job_id, now_iso(), error_message, job_id),
    )


def insert_reprint(
    conn: sqlite3.Connection,
    *,
    label_id: str,
    reason: str | None,
    cups_job_id: str | None,
) -> int:
    cur = conn.execute(
        "INSERT INTO reprints (label_id, reprinted_at, reason, cups_job_id) VALUES (?, ?, ?, ?)",
        (label_id, now_iso(), reason, cups_job_id),
    )
    return cur.lastrowid


def list_labels(conn: sqlite3.Connection, limit: int = 200) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT labels.*, print_batches.source_filename,
               print_batches.source_file_path
        FROM labels
        LEFT JOIN print_batches ON print_batches.id = labels.batch_id
        ORDER BY labels.id DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()


def count_reprints(conn: sqlite3.Connection, label_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM reprints WHERE label_id = ?", (label_id,)
    ).fetchone()
    return row["n"]
