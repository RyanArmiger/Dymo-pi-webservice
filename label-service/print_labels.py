#!/usr/bin/env python3
"""Phase 1 command-line prototype.

Exercises the same rendering, database and printer modules the Flask app
will use later (spec section 27) so nothing is duplicated between the two
interfaces.

Example:
    python print_labels.py --sample-id S001 --experiment-id EXP01 \\
        --requestor-id RA --date 28/07/2026 --copies 2 --preview
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import config
from app import database
from app.label_renderer import LabelContent, render_pair_page, save_print_file
from app.printer import submit_label_page


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Print DYMO two-up lab labels")
    parser.add_argument("--sample-id")
    parser.add_argument("--experiment-id")
    parser.add_argument("--requestor-id")
    parser.add_argument(
        "--date", default=None, help="DD/MM/YYYY, defaults to today"
    )
    parser.add_argument("--copies", type=int, default=1)
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Render and save the pages without submitting to the printer",
    )
    parser.add_argument(
        "--calibration",
        action="store_true",
        help="Render the alignment/calibration test page instead of a real label",
    )
    parser.add_argument(
        "--out-dir",
        default=str(config.GENERATED_DIR),
        help="Directory to write rendered PNGs into",
    )
    args = parser.parse_args(argv)
    if not args.calibration:
        missing = [
            option
            for option, value in (
                ("--sample-id", args.sample_id),
                ("--experiment-id", args.experiment_id),
                ("--requestor-id", args.requestor_id),
            )
            if not value
        ]
        if missing:
            parser.error(
                "the following arguments are required unless --calibration is used: "
                + ", ".join(missing)
            )
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    database.init_db()

    if args.calibration:
        from app.label_renderer import render_calibration_page

        page = render_calibration_page()
        path = save_print_file(page, out_dir / "calibration.png")
        print(f"Calibration page rendered: {path}")
        if not args.preview:
            result = submit_label_page(path)
            _report_print_result(result)
        return 0

    date_str = args.date or dt.date.today().strftime("%d/%m/%Y")
    copies = args.copies
    if copies < 1:
        print("error: --copies must be at least 1", file=sys.stderr)
        return 1

    conn = database.get_connection()
    try:
        if args.preview:
            # Preview does not allocate permanent IDs (spec section 7/10).
            label_ids = [f"NEXT+{i}" if i else "NEXT" for i in range(copies)]
        else:
            label_ids = database.allocate_label_ids(conn, copies)

        records = [
            LabelContent(
                label_id=label_id,
                date=date_str,
                sample_id=args.sample_id,
                experiment_id=args.experiment_id,
                requestor_id=args.requestor_id,
            )
            for label_id in label_ids
        ]

        if not args.preview:
            batch_id = database.create_batch(
                conn,
                source_filename=None,
                requested_count=copies,
                pair_count=(copies + 1) // 2,
                status="allocated",
            )
            for record in records:
                database.insert_label(
                    conn,
                    label_id=record.label_id,
                    date=record.date,
                    sample_id=record.sample_id,
                    experiment_id=record.experiment_id,
                    requestor_id=record.requestor_id,
                    source="single",
                    batch_id=batch_id,
                )

        pages = []
        for i in range(0, len(records), 2):
            record_a = records[i]
            record_b = records[i + 1] if i + 1 < len(records) else None
            pages.append(render_pair_page(record_a, record_b))

        for i, page in enumerate(pages):
            path = save_print_file(page, out_dir / f"page_{i:03d}.png")
            print(f"Rendered {path} ({'+'.join(r.label_id for r in records[i*2:i*2+2])})")

            if not args.preview:
                result = submit_label_page(path)
                _report_print_result(result)
                if not result.success:
                    database.update_batch_status(conn, batch_id, "failed", result.stderr)
                    return 1

        if not args.preview:
            database.update_batch_status(conn, batch_id, "submitted")
            print(f"Allocated IDs: {label_ids[0]}-{label_ids[-1]}")
    finally:
        conn.close()

    return 0


def _report_print_result(result) -> None:
    if result.success:
        print(f"Submitted to printer (job {result.cups_job_id})")
    else:
        print(f"Print submission FAILED: {result.stderr.strip()}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
