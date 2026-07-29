from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import config
from app import database


@pytest.fixture()
def db_path(tmp_path) -> Path:
    path = tmp_path / "labels.db"
    database.init_db(db_path=path)
    return path


@pytest.fixture()
def conn(db_path):
    connection = database.get_connection(db_path)
    yield connection
    connection.close()
