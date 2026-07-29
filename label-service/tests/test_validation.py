from __future__ import annotations

from datetime import date, datetime

import pytest

import config
from app.validation import (
    ValidationError,
    format_label_id,
    parse_uk_date,
    today_uk,
    validate_record,
)


def test_parse_uk_date_native_date():
    assert parse_uk_date(date(2026, 7, 28)) == "28/07/2026"


def test_parse_uk_date_native_datetime():
    assert parse_uk_date(datetime(2026, 7, 28, 13, 5)) == "28/07/2026"


def test_parse_uk_date_valid_uk_string():
    assert parse_uk_date("28/07/2026") == "28/07/2026"


def test_parse_uk_date_valid_iso_string_converted():
    assert parse_uk_date("2026-07-28") == "28/07/2026"


def test_parse_uk_date_rejects_garbage():
    with pytest.raises(ValidationError):
        parse_uk_date("not-a-date")


def test_parse_uk_date_rejects_impossible_calendar_date_uk_format():
    with pytest.raises(ValidationError):
        parse_uk_date("31/02/2026")


def test_parse_uk_date_rejects_impossible_calendar_date_iso_format():
    with pytest.raises(ValidationError):
        parse_uk_date("2026-02-31")


def test_today_uk_format():
    result = today_uk()
    assert len(result) == 10
    datetime.strptime(result, "%d/%m/%Y")


def test_validate_record_defaults_date_and_copies():
    record = validate_record(
        date=None, sample_id="S1", experiment_id="E1", requestor_id="RA", copies=None
    )
    assert record.date == today_uk()
    assert record.copies == 1


def test_validate_record_empty_string_date_and_copies_default():
    record = validate_record(
        date="   ", sample_id="S1", experiment_id="E1", requestor_id="RA", copies="  "
    )
    assert record.date == today_uk()
    assert record.copies == 1


def test_validate_record_strips_whitespace():
    record = validate_record(
        date="28/07/2026",
        sample_id="  S1  ",
        experiment_id="  E1 ",
        requestor_id=" RA ",
        copies=2,
    )
    assert record.sample_id == "S1"
    assert record.experiment_id == "E1"
    assert record.requestor_id == "RA"


def test_validate_record_rejects_empty_required_fields():
    with pytest.raises(ValidationError) as exc_info:
        validate_record(date=None, sample_id="  ", experiment_id="", requestor_id="  ", copies=1)
    errors = exc_info.value.errors
    assert any("sample_id is required" in e for e in errors)
    assert any("experiment_id is required" in e for e in errors)
    assert any("requestor_id is required" in e for e in errors)


def test_validate_record_rejects_overlength_sample_id():
    too_long = "X" * (config.MAX_SAMPLE_ID_LENGTH + 1)
    with pytest.raises(ValidationError) as exc_info:
        validate_record(date=None, sample_id=too_long, experiment_id="E1", requestor_id="RA", copies=1)
    assert any("sample_id exceeds maximum length" in e for e in exc_info.value.errors)


def test_validate_record_rejects_overlength_experiment_id():
    too_long = "X" * (config.MAX_EXPERIMENT_ID_LENGTH + 1)
    with pytest.raises(ValidationError) as exc_info:
        validate_record(date=None, sample_id="S1", experiment_id=too_long, requestor_id="RA", copies=1)
    assert any("experiment_id exceeds maximum length" in e for e in exc_info.value.errors)


def test_validate_record_rejects_overlength_requestor_id():
    too_long = "X" * (config.MAX_REQUESTOR_ID_LENGTH + 1)
    with pytest.raises(ValidationError) as exc_info:
        validate_record(date=None, sample_id="S1", experiment_id="E1", requestor_id=too_long, copies=1)
    assert any("requestor_id exceeds maximum length" in e for e in exc_info.value.errors)


def test_validate_record_accepts_max_length_fields():
    sample_id = "S" * config.MAX_SAMPLE_ID_LENGTH
    experiment_id = "E" * config.MAX_EXPERIMENT_ID_LENGTH
    requestor_id = "R" * config.MAX_REQUESTOR_ID_LENGTH
    record = validate_record(
        date=None, sample_id=sample_id, experiment_id=experiment_id, requestor_id=requestor_id, copies=1
    )
    assert record.sample_id == sample_id
    assert record.experiment_id == experiment_id
    assert record.requestor_id == requestor_id


@pytest.mark.parametrize("copies", ["abc", 3.5, "3.5"])
def test_validate_record_rejects_non_integer_copies(copies):
    with pytest.raises(ValidationError) as exc_info:
        validate_record(date=None, sample_id="S1", experiment_id="E1", requestor_id="RA", copies=copies)
    assert any("whole number" in e for e in exc_info.value.errors)


def test_validate_record_accepts_float_whole_number_copies():
    record = validate_record(date=None, sample_id="S1", experiment_id="E1", requestor_id="RA", copies=3.0)
    assert record.copies == 3


def test_validate_record_can_require_two_copies_for_single_sample_workflow():
    with pytest.raises(ValidationError) as exc_info:
        validate_record(
            date=None,
            sample_id="S1",
            experiment_id="E1",
            requestor_id="RA",
            copies=1,
            min_copies=2,
        )
    assert any("at least 2" in e for e in exc_info.value.errors)


@pytest.mark.parametrize("copies", [0, -1, -5])
def test_validate_record_rejects_zero_and_negative_copies(copies):
    with pytest.raises(ValidationError) as exc_info:
        validate_record(date=None, sample_id="S1", experiment_id="E1", requestor_id="RA", copies=copies)
    assert any("at least 1" in e for e in exc_info.value.errors)


def test_validate_record_rejects_over_limit_copies():
    with pytest.raises(ValidationError) as exc_info:
        validate_record(
            date=None,
            sample_id="S1",
            experiment_id="E1",
            requestor_id="RA",
            copies=config.MAX_COPIES_PER_ROW + 1,
        )
    assert any("exceeds maximum" in e for e in exc_info.value.errors)


def test_validate_record_accepts_max_copies():
    record = validate_record(
        date=None, sample_id="S1", experiment_id="E1", requestor_id="RA", copies=config.MAX_COPIES_PER_ROW
    )
    assert record.copies == config.MAX_COPIES_PER_ROW


def test_validate_record_reports_all_problems_together():
    with pytest.raises(ValidationError) as exc_info:
        validate_record(
            date="31/02/2026",
            sample_id="",
            experiment_id="",
            requestor_id="",
            copies=-1,
        )
    errors = exc_info.value.errors
    assert len(errors) >= 5
    assert any("Invalid date" in e for e in errors)
    assert any("sample_id is required" in e for e in errors)
    assert any("experiment_id is required" in e for e in errors)
    assert any("requestor_id is required" in e for e in errors)
    assert any("at least 1" in e for e in errors)


def test_format_label_id_zero_pads():
    assert format_label_id(1) == "1".zfill(config.LABEL_ID_DIGITS)
    assert format_label_id(9999) == "9999"
    assert len(format_label_id(1)) == config.LABEL_ID_DIGITS
