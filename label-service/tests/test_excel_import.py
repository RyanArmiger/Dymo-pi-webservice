from __future__ import annotations

import datetime as dt
from io import BytesIO

import pytest
from openpyxl import Workbook

import config
from app.excel_import import (
    build_template_workbook,
    check_batch_size,
    parse_workbook,
    total_stickers,
)
from app.validation import LabelRecord, ValidationError


def _workbook_to_bytesio(workbook: Workbook) -> BytesIO:
    buf = BytesIO()
    workbook.save(buf)
    buf.seek(0)
    return buf


def _build_workbook(headers: list[str], rows: list[list]) -> BytesIO:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    return _workbook_to_bytesio(workbook)


def test_parse_workbook_happy_path_all_columns():
    buf = _build_workbook(
        [
            "sample_id_1",
            "sample_id_2",
            "sample_id_3",
            "experiment_id",
            "requestor_id",
            "date",
            "copies",
        ],
        [["S001", "S002", "S003", "E001", "RA", "28/07/2026", 2]],
    )
    records, errors = parse_workbook(buf)
    assert errors == []
    assert len(records) == 1
    record = records[0]
    assert record.sample_id == "S001\nS002\nS003"
    assert record.experiment_id == "E001"
    assert record.requestor_id == "RA"
    assert record.date == "28/07/2026"
    assert record.copies == 2


def test_parse_workbook_case_insensitive_whitespace_headers():
    buf = _build_workbook(
        [" Sample_ID_1 ", "EXPERIMENT_ID", " requestor_id", "Date", "Copies"],
        [["S001", "E001", "RA", "28/07/2026", 1]],
    )
    records, errors = parse_workbook(buf)
    assert errors == []
    assert len(records) == 1
    assert records[0].sample_id == "S001"


def test_parse_workbook_missing_required_column_single_clear_error():
    buf = _build_workbook(
        ["sample_id_1", "requestor_id"],
        [["S001", "RA"]],
    )
    records, errors = parse_workbook(buf)
    assert records == []
    assert len(errors) == 1
    assert "experiment_id" in errors[0]


def test_parse_workbook_empty_rows_skipped_without_errors():
    buf = _build_workbook(
        ["sample_id_1", "experiment_id", "requestor_id", "date", "copies"],
        [
            ["S001", "E001", "RA", "28/07/2026", 1],
            [None, None, None, None, None],
            ["S002", "E002", "RB", "28/07/2026", 1],
        ],
    )
    records, errors = parse_workbook(buf)
    assert errors == []
    assert len(records) == 2


def test_parse_workbook_missing_optional_date_and_copies_default():
    buf = _build_workbook(
        ["sample_id_1", "experiment_id", "requestor_id"],
        [["S001", "E001", "RA"]],
    )
    records, errors = parse_workbook(buf)
    assert errors == []
    assert len(records) == 1
    assert records[0].copies == 1
    from app.validation import today_uk

    assert records[0].date == today_uk()


def test_parse_workbook_excel_date_typed_cell_parsed():
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["sample_id_1", "experiment_id", "requestor_id", "date", "copies"])
    sheet.append(["S001", "E001", "RA", dt.datetime(2026, 7, 28), 1])
    buf = _workbook_to_bytesio(workbook)

    records, errors = parse_workbook(buf)
    assert errors == []
    assert len(records) == 1
    assert records[0].date == "28/07/2026"


def test_parse_workbook_invalid_rows_collected_with_row_numbers_valid_rows_still_succeed():
    buf = _build_workbook(
        ["sample_id_1", "experiment_id", "requestor_id", "date", "copies"],
        [
            ["", "E001", "RA", "28/07/2026", 1],
            ["S002", "E002", "RB", "28/07/2026", 1],
            ["S003", "E003", "RC", "not-a-date", 1],
        ],
    )
    records, errors = parse_workbook(buf)
    assert len(records) == 1
    assert records[0].sample_id == "S002"
    assert len(errors) == 2
    assert any(e.startswith("Row 2:") for e in errors)
    assert any(e.startswith("Row 4:") for e in errors)


def test_check_batch_size_raises_over_max():
    records = [
        LabelRecord(date="28/07/2026", sample_id="S", experiment_id="E", requestor_id="R", copies=config.MAX_BATCH_LABELS)
        for _ in range(2)
    ]
    with pytest.raises(ValidationError) as exc_info:
        check_batch_size(records)
    assert "exceeding the maximum" in exc_info.value.errors[0]


def test_check_batch_size_accepts_exactly_max():
    records = [
        LabelRecord(date="28/07/2026", sample_id="S", experiment_id="E", requestor_id="R", copies=config.MAX_BATCH_LABELS)
    ]
    check_batch_size(records)  # should not raise


def test_total_stickers_sums_copies():
    records = [
        LabelRecord(date="28/07/2026", sample_id="S1", experiment_id="E1", requestor_id="R1", copies=3),
        LabelRecord(date="28/07/2026", sample_id="S2", experiment_id="E2", requestor_id="R2", copies=5),
    ]
    assert total_stickers(records) == 8


def test_build_template_workbook_roundtrip_via_parse_workbook():
    workbook = build_template_workbook(include_example=True)
    buf = _workbook_to_bytesio(workbook)
    records, errors = parse_workbook(buf)
    assert errors == []
    assert len(records) == 1
    assert records[0].sample_id == "S001\nS002\nS003"


def test_build_template_workbook_headers():
    workbook = build_template_workbook(include_example=False)
    sheet = workbook.active
    headers = [cell.value for cell in sheet[1]]
    assert headers == [
        "sample_id_1",
        "sample_id_2",
        "sample_id_3",
        "experiment_id",
        "requestor_id",
        "date",
        "copies",
    ]


def test_parse_workbook_allows_blank_optional_sample_id_columns():
    buf = _build_workbook(
        [
            "sample_id_1",
            "sample_id_2",
            "sample_id_3",
            "experiment_id",
            "requestor_id",
        ],
        [["S001", "", "S003", "E001", "RA"]],
    )

    records, errors = parse_workbook(buf)

    assert errors == []
    assert records[0].sample_id == "S001\nS003"


def test_parse_workbook_requires_first_sample_id_column_per_row():
    buf = _build_workbook(
        [
            "sample_id_1",
            "sample_id_2",
            "sample_id_3",
            "experiment_id",
            "requestor_id",
        ],
        [["", "S002", "", "E001", "RA"]],
    )

    records, errors = parse_workbook(buf)

    assert records == []
    assert errors == ["Row 2: sample_id_1 is required"]
