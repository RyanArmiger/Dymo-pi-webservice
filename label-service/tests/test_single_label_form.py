from werkzeug.datastructures import MultiDict
import re

import pytest

import config
from app import create_app
from app import database, preview_store
from app.routes import _records_from_form
from app.printer import PrintResult
from app.validation import ValidationError


def _form(**overrides):
    values = {
        "date_1": "28/07/2026",
        "sample_id_1": "S001",
        "experiment_id_1": "EXP01",
        "requestor_id_1": "RA",
        "date_2": "",
        "sample_id_2": "",
        "experiment_id_2": "",
        "requestor_id_2": "",
    }
    values.update(overrides)
    return MultiDict(values)


def test_blank_second_label_creates_only_one_record():
    records = _records_from_form(_form())

    assert len(records) == 1
    assert records[0].sample_id == "S001"


def test_complete_second_label_creates_independent_record():
    records = _records_from_form(
        _form(
            sample_id_2="S002",
            experiment_id_2="EXP02",
            requestor_id_2="RB",
        )
    )

    assert len(records) == 2
    assert records[1].sample_id == "S002"
    assert records[1].date == "28/07/2026"


def test_three_sample_ids_are_normalised_and_kept_on_one_label():
    records = _records_from_form(_form(sample_id_1="S001\r\nS002\n\nS003"))

    assert len(records) == 1
    assert records[0].sample_id == "S001\nS002\nS003"


def test_more_than_three_sample_ids_is_rejected():
    with pytest.raises(ValidationError) as exc_info:
        _records_from_form(_form(sample_id_1="S001\nS002\nS003\nS004"))

    assert "more than 3 entries" in str(exc_info.value)


def test_second_label_defaults_without_sample_do_not_create_record():
    records = _records_from_form(
        _form(
            date_2="29/07/2026",
            experiment_id_2="EXP01",
            requestor_id_2="RA",
        )
    )

    assert len(records) == 1


def test_successful_print_retains_experiment_and_requestor_for_next_form(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(config, "DATABASE_PATH", tmp_path / "single-form.db")
    monkeypatch.setattr(config, "GENERATED_DIR", tmp_path / "generated")
    monkeypatch.setattr(
        "app.routes.printer_status_summary",
        lambda: {"state": "ready", "queued_jobs": 0, "raw": "ready"},
    )
    monkeypatch.setattr(
        "app.routes.submit_label_page",
        lambda path: PrintResult(True, "123", "", "", 0),
    )
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()
    with client.session_transaction() as session:
        session["admin_authed"] = True
        session["csrf_token"] = "test-csrf"

    response = client.post(
        "/print",
        data={
            "csrf_token": "test-csrf",
            "date_1": "28/07/2026",
            "sample_id_1": "S001",
            "experiment_id_1": "EXP01",
            "requestor_id_1": "RA",
            "sample_id_2": "",
            "experiment_id_2": "EXP01",
            "requestor_id_2": "RA",
        },
    )
    assert response.status_code == 200

    next_form = client.get("/").get_data(as_text=True)

    def field_value(field_id):
        match = re.search(
            rf'<input[^>]*id="{field_id}"[^>]*value="([^"]*)"', next_form, re.S
        )
        assert match is not None
        return match.group(1)

    sample_match = re.search(
        r'<textarea[^>]*id="sample_id_1"[^>]*>(.*?)</textarea>', next_form, re.S
    )
    assert sample_match is not None
    assert sample_match.group(1) == ""
    assert field_value("experiment_id_1") == "EXP01"
    assert field_value("requestor_id_1") == "RA"
    assert field_value("experiment_id_2") == "EXP01"
    assert field_value("requestor_id_2") == "RA"


def test_preview_shows_provisional_ids_without_allocating_them(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATABASE_PATH", tmp_path / "preview-ids.db")
    monkeypatch.setattr(preview_store, "_PREVIEW_DIR", tmp_path / "previews")
    monkeypatch.setattr(
        "app.routes.printer_status_summary",
        lambda: {"state": "ready", "queued_jobs": 0, "raw": "ready"},
    )
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()
    with client.session_transaction() as session:
        session["admin_authed"] = True
        session["csrf_token"] = "test-csrf"

    response = client.post(
        "/preview",
        data={
            "csrf_token": "test-csrf",
            "date_1": "28/07/2026",
            "sample_id_1": "S001",
            "experiment_id_1": "EXP01",
            "requestor_id_1": "RA",
            "sample_id_2": "S002",
            "experiment_id_2": "EXP01",
            "requestor_id_2": "RA",
        },
    )

    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "0001&ndash;0002" in html
    conn = database.get_connection(config.DATABASE_PATH)
    try:
        assert database.get_counter_value(conn) == 0
    finally:
        conn.close()
