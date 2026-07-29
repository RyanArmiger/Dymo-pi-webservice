"""Server-side validation for label field values.

Nothing here truncates a value to make it fit: a value either satisfies the
configured limits or the whole request is rejected with a clear message.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

import config

_UK_DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ValidationError(Exception):
    """Raised with one or more human-readable validation problems."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__(str(self))

    def __str__(self) -> str:
        return "; ".join(self.errors)


def parse_uk_date(value: str | date | datetime) -> str:
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y")
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")

    if not isinstance(value, str):
        raise ValidationError([f"Invalid date: {value!r}"])

    text = value.strip()
    if _UK_DATE_RE.match(text):
        try:
            datetime.strptime(text, "%d/%m/%Y")
        except ValueError:
            raise ValidationError([f"Invalid date: {text!r} is not a real calendar date"])
        return text

    if _ISO_DATE_RE.match(text):
        try:
            parsed = datetime.strptime(text, "%Y-%m-%d")
        except ValueError:
            raise ValidationError([f"Invalid date: {text!r} is not a real calendar date"])
        return parsed.strftime("%d/%m/%Y")

    raise ValidationError([f"Invalid date: {text!r} (expected DD/MM/YYYY or YYYY-MM-DD)"])


def today_uk() -> str:
    return datetime.now().strftime("%d/%m/%Y")


@dataclass
class LabelRecord:
    date: str
    sample_id: str
    experiment_id: str
    requestor_id: str
    copies: int


def validate_record(
    *,
    date: str | date | datetime | None,
    sample_id: str,
    experiment_id: str,
    requestor_id: str,
    copies: int | str | None,
    min_copies: int = 1,
) -> LabelRecord:
    errors: list[str] = []

    parsed_date: str | None = None
    if date is None or (isinstance(date, str) and date.strip() == ""):
        parsed_date = today_uk()
    else:
        try:
            parsed_date = parse_uk_date(date)
        except ValidationError as exc:
            errors.extend(exc.errors)

    # Preserve multiple sample IDs in the existing field, one per line.
    # Blank lines are ignored and line endings are normalised before storage.
    sample_ids = [line.strip() for line in sample_id.splitlines() if line.strip()]
    sample_id = "\n".join(sample_ids)
    experiment_id = experiment_id.strip()
    requestor_id = requestor_id.strip()

    if not sample_id:
        errors.append("sample_id is required")
    else:
        if len(sample_ids) > config.MAX_SAMPLE_IDS_PER_LABEL:
            errors.append(
                f"sample_id contains more than {config.MAX_SAMPLE_IDS_PER_LABEL} entries"
            )
        for index, value in enumerate(sample_ids, start=1):
            if len(value) > config.MAX_SAMPLE_ID_LENGTH:
                field_name = "sample_id" if index == 1 else f"sample_id {index}"
                errors.append(
                    f"{field_name} exceeds maximum length of "
                    f"{config.MAX_SAMPLE_ID_LENGTH} characters"
                )

    if not experiment_id:
        errors.append("experiment_id is required")
    elif len(experiment_id) > config.MAX_EXPERIMENT_ID_LENGTH:
        errors.append(
            f"experiment_id exceeds maximum length of {config.MAX_EXPERIMENT_ID_LENGTH} characters"
        )

    if not requestor_id:
        errors.append("requestor_id is required")
    elif len(requestor_id) > config.MAX_REQUESTOR_ID_LENGTH:
        errors.append(
            f"requestor_id exceeds maximum length of {config.MAX_REQUESTOR_ID_LENGTH} characters"
        )

    parsed_copies: int | None = None
    if copies is None or (isinstance(copies, str) and copies.strip() == ""):
        parsed_copies = min_copies
    else:
        try:
            # Spreadsheet cells may come back as floats (e.g. 3.0) even for
            # whole-number columns, so accept those without accepting 3.5.
            as_float = float(str(copies).strip())
        except ValueError:
            errors.append(f"copies must be a whole number, got {copies!r}")
            as_float = None
        if as_float is not None:
            if as_float != int(as_float):
                errors.append(f"copies must be a whole number, got {copies!r}")
            else:
                parsed_copies = int(as_float)
                if parsed_copies < min_copies:
                    errors.append(f"copies must be at least {min_copies}")
                elif parsed_copies > config.MAX_COPIES_PER_ROW:
                    errors.append(
                        f"copies exceeds maximum of {config.MAX_COPIES_PER_ROW} per row"
                    )

    if errors:
        raise ValidationError(errors)

    return LabelRecord(
        date=parsed_date,
        sample_id=sample_id,
        experiment_id=experiment_id,
        requestor_id=requestor_id,
        copies=parsed_copies,
    )


def format_label_id(n: int) -> str:
    return str(n).zfill(config.LABEL_ID_DIGITS)
