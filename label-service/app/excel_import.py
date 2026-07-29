"""Batch `.xlsx` upload workflow: parsing, per-row validation and template generation."""
from __future__ import annotations

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font
from openpyxl.worksheet.worksheet import Worksheet

import config
from app.validation import LabelRecord, ValidationError, today_uk, validate_record

REQUIRED_COLUMNS = ("sample_id", "experiment_id", "requestor_id")
OPTIONAL_COLUMNS = ("date", "copies")
ALL_COLUMNS = REQUIRED_COLUMNS + OPTIONAL_COLUMNS


def parse_workbook(file_obj) -> tuple[list[LabelRecord], list[str]]:
    # data_only=True reads cached values and never evaluates workbook formulas.
    # read_only=True keeps memory bounded on the 512 MB Raspberry Pi Zero.
    workbook = load_workbook(file_obj, read_only=True, data_only=True)
    sheet = workbook.active
    rows = sheet.iter_rows(values_only=True)
    try:
        header_values = next(rows)
    except StopIteration:
        workbook.close()
        return [], ["Spreadsheet is empty"]

    headers = [str(value).strip().lower() if value is not None else "" for value in header_values]
    column_indexes = {name: index for index, name in enumerate(headers) if name}

    missing = [col for col in REQUIRED_COLUMNS if col not in column_indexes]
    if missing:
        workbook.close()
        return [], [f"Missing required column: {col}" for col in missing]

    data_rows = []
    for row_number, row in enumerate(rows, start=2):
        if not any(value is not None and str(value).strip() for value in row):
            continue
        data_rows.append((row_number, row))
        if len(data_rows) > config.MAX_SPREADSHEET_ROWS:
            workbook.close()
            return [], [
                f"Spreadsheet has more than {config.MAX_SPREADSHEET_ROWS} non-empty data rows"
            ]

    records: list[LabelRecord] = []
    errors: list[str] = []

    for row_number, row in data_rows:

        def raw_cell(col: str):
            index = column_indexes.get(col)
            if index is None or index >= len(row):
                return None
            return row[index]

        def text_cell(col: str) -> str:
            value = raw_cell(col)
            if value is None:
                return ""
            return str(value).strip()

        sample_id = text_cell("sample_id")
        experiment_id = text_cell("experiment_id")
        requestor_id = text_cell("requestor_id")

        if not sample_id and not experiment_id and not requestor_id:
            continue

        date_value = raw_cell("date")
        copies_value = text_cell("copies") if "copies" in column_indexes else None
        copies_value = copies_value if copies_value else None

        try:
            record = validate_record(
                date=date_value,
                sample_id=sample_id,
                experiment_id=experiment_id,
                requestor_id=requestor_id,
                copies=copies_value,
            )
        except ValidationError as exc:
            errors.extend(f"Row {row_number}: {message}" for message in exc.errors)
            continue

        records.append(record)

    workbook.close()
    return records, errors


def total_stickers(records: list[LabelRecord]) -> int:
    return sum(r.copies for r in records)


def check_batch_size(records: list[LabelRecord]) -> None:
    total = total_stickers(records)
    if total > config.MAX_BATCH_LABELS:
        raise ValidationError(
            [f"Batch requests {total} stickers, exceeding the maximum of {config.MAX_BATCH_LABELS}"]
        )


def build_template_workbook(include_example: bool = True) -> Workbook:
    workbook = Workbook()
    sheet: Worksheet = workbook.active
    sheet.title = "Labels"

    headers = ["sample_id", "experiment_id", "requestor_id", "date", "copies"]
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    sheet.freeze_panes = "A2"

    column_widths = {"A": 16, "B": 16, "C": 14, "D": 12, "E": 8}
    for col, width in column_widths.items():
        sheet.column_dimensions[col].width = width

    if include_example:
        sheet.append(["S001", "EXP01", "RA", today_uk(), 1])
        sheet.cell(row=2, column=4).number_format = "DD/MM/YYYY"

    return workbook
