from __future__ import annotations

import threading

import pytest

import config
from app import database


def test_allocate_label_ids_sequential_starts_at_one(conn):
    ids = database.allocate_label_ids(conn, 1)
    assert ids == ["0001"]


def test_allocate_label_ids_reserves_n_consecutive_and_advances_counter(conn):
    first_batch = database.allocate_label_ids(conn, 3)
    assert first_batch == ["0001", "0002", "0003"]
    assert database.get_counter_value(conn) == 3

    second_batch = database.allocate_label_ids(conn, 2)
    assert second_batch == ["0004", "0005"]
    assert database.get_counter_value(conn) == 5


def test_allocate_label_ids_concurrent_no_duplicates(db_path):
    thread_count = 20
    ids_per_thread = 15
    results: list[list[str]] = [None] * thread_count
    errors: list[Exception] = []

    def worker(index: int) -> None:
        try:
            thread_conn = database.get_connection(db_path)
            try:
                results[index] = database.allocate_label_ids(thread_conn, ids_per_thread)
            finally:
                thread_conn.close()
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(thread_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"worker threads raised: {errors}"

    all_ids: list[str] = []
    for batch in results:
        assert batch is not None
        assert len(batch) == ids_per_thread
        all_ids.extend(batch)

    assert len(all_ids) == thread_count * ids_per_thread
    assert len(set(all_ids)) == len(all_ids), "duplicate label IDs were allocated"

    final_conn = database.get_connection(db_path)
    try:
        assert database.get_counter_value(final_conn) == thread_count * ids_per_thread
    finally:
        final_conn.close()


def test_allocate_label_ids_exhaustion_raises_and_does_not_advance_counter(conn):
    database.set_counter_value(conn, config.LABEL_ID_MAX - 1, reason="test setup")
    assert database.get_counter_value(conn) == config.LABEL_ID_MAX - 1

    with pytest.raises(database.CounterExhaustedError):
        database.allocate_label_ids(conn, 5)

    assert database.get_counter_value(conn) == config.LABEL_ID_MAX - 1


def test_allocate_label_ids_exact_fit_at_max_succeeds(conn):
    database.set_counter_value(conn, config.LABEL_ID_MAX - 2, reason="test setup")
    ids = database.allocate_label_ids(conn, 2)
    assert ids[-1] == str(config.LABEL_ID_MAX).zfill(config.LABEL_ID_DIGITS)
    assert database.get_counter_value(conn) == config.LABEL_ID_MAX


def test_allocate_label_ids_rejects_non_positive_count(conn):
    with pytest.raises(ValueError):
        database.allocate_label_ids(conn, 0)
    with pytest.raises(ValueError):
        database.allocate_label_ids(conn, -3)
