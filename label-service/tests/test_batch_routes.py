from io import BytesIO
import re

from openpyxl import Workbook

import config
from app import create_app, database
from app import batch_store
from app.printer import PrintResult


def _authenticated_client(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATABASE_PATH", tmp_path / "batch-routes.db")
    monkeypatch.setattr(config, "BATCH_UPLOAD_DIR", tmp_path / "batch-uploads")
    monkeypatch.setattr(batch_store, "_STORE_DIR", tmp_path / "batch-tmp")
    monkeypatch.setattr(config, "AUTH_REQUIRED", True)
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()
    with client.session_transaction() as session:
        session["admin_authed"] = True
        session["csrf_token"] = "test-csrf"
    return client


def _workbook_bytes(headers, rows):
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    return output


def test_batch_home_has_upload_form_and_template_link(monkeypatch, tmp_path):
    client = _authenticated_client(monkeypatch, tmp_path)

    response = client.get("/batch")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'enctype="multipart/form-data"' in html
    assert "/template.xlsx" in html


def test_invalid_batch_upload_does_not_allocate_ids(monkeypatch, tmp_path):
    client = _authenticated_client(monkeypatch, tmp_path)
    workbook = _workbook_bytes(
        ["sample_id_1", "requestor_id"],
        [["S001", "RA"]],
    )

    response = client.post(
        "/batch/upload",
        data={"csrf_token": "test-csrf", "file": (workbook, "invalid.xlsx")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert "Missing required column: experiment_id" in response.get_data(as_text=True)
    conn = database.get_connection(config.DATABASE_PATH)
    try:
        assert database.get_counter_value(conn) == 0
    finally:
        conn.close()


def test_confirmed_batch_archives_and_redownloads_original_workbook(monkeypatch, tmp_path):
    client = _authenticated_client(monkeypatch, tmp_path)
    workbook = _workbook_bytes(
        ["sample_id_1", "experiment_id", "requestor_id", "date", "copies"],
        [["S001", "EXP01", "RA", "28/07/2026", 1]],
    )
    original_bytes = workbook.getvalue()
    monkeypatch.setattr(
        "app.routes.printer_status_summary",
        lambda: {"state": "ready", "queued_jobs": 0, "raw": "ready"},
    )
    monkeypatch.setattr(
        "app.routes.submit_label_page",
        lambda path: PrintResult(True, "123", "", "", 0),
    )

    upload = client.post(
        "/batch/upload",
        data={"csrf_token": "test-csrf", "file": (workbook, "source.xlsx")},
        content_type="multipart/form-data",
    )
    token_match = re.search(r'name="token" value="([^"]+)"', upload.get_data(as_text=True))
    assert token_match is not None

    printed = client.post(
        "/batch/print",
        data={"csrf_token": "test-csrf", "token": token_match.group(1)},
    )

    assert printed.status_code == 200
    conn = database.get_connection(config.DATABASE_PATH)
    try:
        batch = database.get_batch(conn, 1)
        assert batch["source_filename"] == "source.xlsx"
        assert batch["source_file_size"] == len(original_bytes)
        assert len(batch["source_file_sha256"]) == 64
    finally:
        conn.close()

    downloaded = client.get("/batches/1/source")
    assert downloaded.status_code == 200
    assert downloaded.data == original_bytes
    assert "source.xlsx" in downloaded.headers["Content-Disposition"]
    assert "download workbook" in client.get("/history").get_data(as_text=True)
