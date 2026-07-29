from __future__ import annotations

import io
import time
from functools import wraps
from pathlib import Path
from threading import Lock

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from werkzeug.security import check_password_hash
from werkzeug.utils import secure_filename

import config
from app import batch_store, database, preview_store
from app.csrf import get_csrf_token, require_csrf
from app.excel_import import build_template_workbook, check_batch_size, parse_workbook, total_stickers
from app.label_renderer import LabelContent, render_calibration_page, render_pair_page, save_print_file
from app.printer import printer_status_summary, submit_label_page
from app.validation import LabelRecord, ValidationError, format_label_id, today_uk, validate_record

bp = Blueprint("labels", __name__)
_failed_logins: dict[str, list[float]] = {}
_failed_logins_lock = Lock()


@bp.app_context_processor
def inject_csrf() -> dict:
    return {"csrf_token": get_csrf_token()}


@bp.before_request
def require_app_login():
    """Protect every application route except the login page itself."""
    if not config.AUTH_REQUIRED:
        return None
    if request.endpoint in {"labels.admin_home", "labels.admin_login"}:
        return None
    if session.get("admin_authed"):
        return None
    if request.method == "GET":
        session["return_to"] = request.path
    return redirect(url_for("labels.admin_home"))


def _pair_up(records: list[LabelContent]) -> list[tuple[LabelContent, LabelContent | None]]:
    pairs = []
    for i in range(0, len(records), 2):
        a = records[i]
        b = records[i + 1] if i + 1 < len(records) else None
        pairs.append((a, b))
    return pairs


def _printer_ready_or_error() -> str | None:
    """Returns an error message if the queue isn't usable, else None.

    IDs must not be allocated unless the printer is configured and enabled
    (spec section 13), so callers check this before allocating.
    """
    status = printer_status_summary()
    if status["state"] == "unavailable":
        return "Printer is unavailable. Check the CUPS queue before printing."
    return None


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin_authed"):
            return redirect(url_for("labels.admin_home"))
        return view(*args, **kwargs)

    return wrapped


# ---------------------------------------------------------------------------
# Single label
# ---------------------------------------------------------------------------


@bp.route("/", methods=["GET"])
def index():
    return render_template(
        "index.html",
        today=today_uk(),
        max_copies=config.MAX_COPIES_PER_ROW,
        default_experiment_id=session.get("last_experiment_id", ""),
        default_requestor_id=session.get("last_requestor_id", ""),
    )


def _records_from_form(form) -> list[LabelRecord]:
    """Read one required label and one optional independent second label."""
    records = []
    errors = []
    first_date = form.get("date_1") or None

    for position in (1, 2):
        sample_id = form.get(f"sample_id_{position}", "")
        experiment_id = form.get(f"experiment_id_{position}", "")
        requestor_id = form.get(f"requestor_id_{position}", "")

        # Label 2's experiment/requestor fields follow Label 1 by default, so
        # only an entered Sample ID opts into allocating the second label.
        if position == 2 and not sample_id.strip():
            continue

        try:
            records.append(
                validate_record(
                    date=(form.get(f"date_{position}") or first_date),
                    sample_id=sample_id,
                    experiment_id=experiment_id,
                    requestor_id=requestor_id,
                    copies=1,
                )
            )
        except ValidationError as exc:
            errors.extend(f"Label {position}: {error}" for error in exc.errors)

    if errors:
        raise ValidationError(errors)
    return records


@bp.route("/preview", methods=["POST"])
def preview():
    require_csrf()
    try:
        records = _records_from_form(request.form)
    except ValidationError as exc:
        return render_template(
            "index.html", today=today_uk(), max_copies=config.MAX_COPIES_PER_ROW,
            errors=exc.errors, form=request.form,
        )

    conn = database.get_connection()
    try:
        provisional_first_number = database.get_counter_value(conn) + 1
    finally:
        conn.close()
    provisional_ids = [
        format_label_id(provisional_first_number + offset)
        for offset in range(len(records))
    ]
    contents = [
        LabelContent(
            label_id=label_id,
            date=record.date,
            sample_id=record.sample_id,
            experiment_id=record.experiment_id,
            requestor_id=record.requestor_id,
        )
        for label_id, record in zip(provisional_ids, records)
    ]
    pages = [render_pair_page(a, b) for a, b in _pair_up(contents)]
    tokens = [preview_store.save_preview(p) for p in pages]

    return render_template(
        "preview.html",
        records=records,
        sticker_count=len(records),
        provisional_id_first=provisional_ids[0],
        provisional_id_last=provisional_ids[-1],
        preview_tokens=tokens,
        printer_error=_printer_ready_or_error(),
    )


@bp.route("/print", methods=["POST"])
def do_print():
    require_csrf()
    try:
        records = _records_from_form(request.form)
    except ValidationError as exc:
        return render_template(
            "index.html", today=today_uk(), max_copies=config.MAX_COPIES_PER_ROW,
            errors=exc.errors, form=request.form,
        )

    printer_error = _printer_ready_or_error()
    if printer_error:
        return render_template(
            "index.html", today=today_uk(), max_copies=config.MAX_COPIES_PER_ROW,
            errors=[printer_error], form=request.form,
        )

    conn = database.get_connection()
    try:
        label_ids = database.allocate_label_ids(conn, len(records))
        batch_id = database.create_batch(
            conn,
            source_filename=None,
            requested_count=len(records),
            pair_count=1,
            status="allocated",
        )
        contents = []
        for label_id, record in zip(label_ids, records):
            database.insert_label(
                conn,
                label_id=label_id,
                date=record.date,
                sample_id=record.sample_id,
                experiment_id=record.experiment_id,
                requestor_id=record.requestor_id,
                source="single",
                batch_id=batch_id,
            )
            contents.append(
                LabelContent(
                    label_id=label_id,
                    date=record.date,
                    sample_id=record.sample_id,
                    experiment_id=record.experiment_id,
                    requestor_id=record.requestor_id,
                )
            )

        submitted_jobs, error = _render_and_submit_pairs(conn, batch_id, contents)
        if error:
            database.update_batch_status(conn, batch_id, "failed", error)
            return render_template(
                "index.html", today=today_uk(), max_copies=config.MAX_COPIES_PER_ROW,
                errors=[f"Print job failed after allocating IDs "
                        f"{label_ids[0]}-{label_ids[-1]}: {error}"],
            )

        database.update_batch_status(conn, batch_id, "submitted")
        session["last_experiment_id"] = records[0].experiment_id
        session["last_requestor_id"] = records[0].requestor_id
        return render_template(
            "preview.html",
            confirmation=True,
            sticker_count=len(records),
            label_id_first=label_ids[0],
            label_id_last=label_ids[-1],
            submitted_jobs=submitted_jobs,
            submitted_at=database.now_iso(),
        )
    finally:
        conn.close()


def _render_and_submit_pairs(conn, batch_id: int, contents: list[LabelContent]):
    """Renders + submits every pair for a batch. Stops at the first failure
    by default (spec section 19). Returns (list_of_cups_job_ids, error_or_None).
    """
    submitted_jobs = []
    for index, (a, b) in enumerate(_pair_up(contents)):
        page = render_pair_page(a, b)
        path = save_print_file(page, config.GENERATED_DIR / f"batch_{batch_id}_pair_{index}.png")
        job_id = database.create_print_job(conn, batch_id=batch_id, pair_index=index, file_path=str(path))

        result = submit_label_page(path)
        if result.success:
            database.update_print_job(conn, job_id, status="submitted", cups_job_id=result.cups_job_id)
            for content in (a, b):
                if content is not None:
                    database.update_label_status(conn, content.label_id, "submitted")
            submitted_jobs.append(result.cups_job_id)
        else:
            database.update_print_job(conn, job_id, status="failed", error_message=result.stderr)
            for content in (a, b):
                if content is not None:
                    database.update_label_status(conn, content.label_id, "failed")
            if config.STOP_ON_FIRST_BATCH_FAILURE:
                return submitted_jobs, result.stderr
    return submitted_jobs, None


# ---------------------------------------------------------------------------
# Batch (Excel) upload
# ---------------------------------------------------------------------------


@bp.route("/batch", methods=["GET"])
def batch_home():
    return render_template("batch_preview.html")


@bp.route("/batch/upload", methods=["POST"])
def batch_upload():
    require_csrf()
    file = request.files.get("file")
    if not file or file.filename == "":
        return render_template("batch_preview.html", errors=["No file uploaded"])

    filename = secure_filename(file.filename)
    if not filename.lower().endswith(".xlsx"):
        return render_template("batch_preview.html", errors=["Only .xlsx files are accepted"])

    records, errors = parse_workbook(file.stream)
    if errors:
        return render_template("batch_preview.html", errors=errors, source_filename=filename)

    try:
        check_batch_size(records)
    except ValidationError as exc:
        return render_template("batch_preview.html", errors=exc.errors, source_filename=filename)

    if not records:
        return render_template("batch_preview.html", errors=["No valid rows found in the spreadsheet"])

    file.stream.seek(0)
    token = batch_store.save_batch(records, filename, file.stream.read())
    return _render_batch_preview(token, filename)


@bp.route("/batch/preview", methods=["POST"])
def batch_preview():
    require_csrf()
    token = request.form.get("token", "")
    loaded = batch_store.load_batch(token)
    if loaded is None:
        return render_template("batch_preview.html", errors=["This batch preview has expired. Please re-upload."])
    records, filename = loaded
    return _render_batch_preview(token, filename)


def _render_batch_preview(token: str, filename: str | None):
    loaded = batch_store.load_batch(token)
    records, _ = loaded
    total = total_stickers(records)
    conn = database.get_connection()
    try:
        next_id = database.get_counter_value(conn) + 1
    finally:
        conn.close()
    provisional_last = next_id + total - 1
    return render_template(
        "batch_preview.html",
        token=token,
        source_filename=filename,
        records=records,
        total_stickers=total,
        physical_pages=(total + 1) // 2,
        has_blank_final_position=total % 2 == 1,
        provisional_first=format_label_id(next_id),
        provisional_last=format_label_id(provisional_last),
        printer_error=_printer_ready_or_error(),
    )


@bp.route("/batch/print", methods=["POST"])
def batch_print():
    require_csrf()
    token = request.form.get("token", "")
    loaded = batch_store.load_batch(token)
    if loaded is None:
        return render_template("batch_preview.html", errors=["This batch preview has expired. Please re-upload."])
    records, filename = loaded

    printer_error = _printer_ready_or_error()
    if printer_error:
        return render_template("batch_preview.html", errors=[printer_error], token=token, source_filename=filename)

    total = total_stickers(records)
    conn = database.get_connection()
    try:
        batch_id = database.create_batch(
            conn,
            source_filename=filename,
            requested_count=total,
            pair_count=(total + 1) // 2,
            status="preparing",
        )
        try:
            archive_name, archive_sha256, archive_size = batch_store.archive_source(
                token, batch_id, filename
            )
        except OSError as exc:
            database.update_batch_status(conn, batch_id, "failed", str(exc))
            return render_template(
                "batch_preview.html",
                errors=[f"Could not archive the source workbook; no IDs were allocated: {exc}"],
            )

        database.set_batch_source_file(
            conn,
            batch_id,
            file_path=archive_name,
            sha256=archive_sha256,
            size=archive_size,
        )
        label_ids = database.allocate_label_ids(conn, total)
        database.update_batch_status(conn, batch_id, "allocated")

        contents = []
        id_iter = iter(label_ids)
        for record in records:
            for _ in range(record.copies):
                label_id = next(id_iter)
                database.insert_label(
                    conn,
                    label_id=label_id,
                    date=record.date,
                    sample_id=record.sample_id,
                    experiment_id=record.experiment_id,
                    requestor_id=record.requestor_id,
                    source="excel",
                    batch_id=batch_id,
                )
                contents.append(
                    LabelContent(
                        label_id=label_id,
                        date=record.date,
                        sample_id=record.sample_id,
                        experiment_id=record.experiment_id,
                        requestor_id=record.requestor_id,
                    )
                )

        submitted_jobs, error = _render_and_submit_pairs(conn, batch_id, contents)
        batch_store.delete_batch(token)
        if error:
            database.update_batch_status(conn, batch_id, "failed", error)
            return render_template(
                "batch_preview.html",
                errors=[f"Print job failed after allocating IDs "
                        f"{label_ids[0]}-{label_ids[-1]}: {error}"],
            )

        database.update_batch_status(conn, batch_id, "submitted")
        return render_template(
            "preview.html",
            confirmation=True,
            sticker_count=total,
            label_id_first=label_ids[0],
            label_id_last=label_ids[-1],
            submitted_jobs=submitted_jobs,
            submitted_at=database.now_iso(),
        )
    finally:
        conn.close()


@bp.route("/template.xlsx", methods=["GET"])
def template_download():
    workbook = build_template_workbook(include_example=True)
    buffer = io.BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    return send_file(
        buffer,
        as_attachment=True,
        download_name="label_batch_template.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@bp.route("/batches/<int:batch_id>/source", methods=["GET"])
def batch_source_download(batch_id: int):
    conn = database.get_connection()
    try:
        batch = database.get_batch(conn, batch_id)
    finally:
        conn.close()

    if batch is None or not batch["source_file_path"]:
        abort(404)
    archive_name = batch["source_file_path"]
    if Path(archive_name).name != archive_name:
        abort(404)
    path = config.BATCH_UPLOAD_DIR / archive_name
    if not path.is_file():
        abort(404)
    return send_file(
        path,
        as_attachment=True,
        download_name=batch["source_filename"] or "batch_source.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ---------------------------------------------------------------------------
# Preview images
# ---------------------------------------------------------------------------


@bp.route("/preview-image/<token>.png", methods=["GET"])
def preview_image(token: str):
    path = preview_store.preview_path(token)
    if path is None:
        abort(404)
    return send_file(path, mimetype="image/png")


# ---------------------------------------------------------------------------
# History / reprint
# ---------------------------------------------------------------------------


@bp.route("/history", methods=["GET"])
def history():
    conn = database.get_connection()
    try:
        rows = database.list_labels(conn)
    finally:
        conn.close()
    return render_template("history.html", labels=rows)


@bp.route("/labels/<label_id>", methods=["GET"])
def label_detail(label_id: str):
    conn = database.get_connection()
    try:
        row = database.get_label(conn, label_id)
        if row is None:
            abort(404)
        reprint_count = database.count_reprints(conn, label_id)
    finally:
        conn.close()
    return render_template("history.html", labels=[row], reprint_count=reprint_count, single=True)


@bp.route("/labels/<label_id>/reprint", methods=["POST"])
def reprint_label(label_id: str):
    require_csrf()
    conn = database.get_connection()
    try:
        row = database.get_label(conn, label_id)
        if row is None:
            abort(404)

        printer_error = _printer_ready_or_error()
        if printer_error:
            return render_template("history.html", labels=[row], single=True, errors=[printer_error])

        content = LabelContent(
            label_id=row["label_id"],
            date=row["date"],
            sample_id=row["sample_id"],
            experiment_id=row["experiment_id"],
            requestor_id=row["requestor_id"],
        )
        page = render_pair_page(content, None)
        path = save_print_file(page, config.GENERATED_DIR / f"reprint_{label_id}.png")
        result = submit_label_page(path)
        database.insert_reprint(
            conn,
            label_id=label_id,
            reason=request.form.get("reason") or None,
            cups_job_id=result.cups_job_id,
        )
        reprint_count = database.count_reprints(conn, label_id)
        errors = None if result.success else [f"Reprint submission failed: {result.stderr}"]
        return render_template(
            "history.html", labels=[row], reprint_count=reprint_count, single=True, errors=errors,
            reprint_job_id=result.cups_job_id,
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


@bp.route("/status", methods=["GET"])
def status():
    return render_template("history.html", labels=[], printer_status=printer_status_summary(), status_only=True)


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------


@bp.route("/admin", methods=["GET"])
def admin_home():
    if not session.get("admin_authed"):
        return render_template(
            "admin.html",
            authed=False,
            auth_configured=bool(config.ADMIN_PASSWORD_HASH),
        )

    conn = database.get_connection()
    try:
        counter_value = database.get_counter_value(conn)
    finally:
        conn.close()
    return render_template(
        "admin.html",
        authed=True,
        counter_value=counter_value,
        counter_max=config.LABEL_ID_MAX,
        printer_status=printer_status_summary(),
    )


@bp.route("/admin/login", methods=["POST"])
def admin_login():
    require_csrf()
    client_key = request.remote_addr or "unknown"
    now = time.monotonic()
    cutoff = now - config.LOGIN_LOCKOUT_SECONDS

    with _failed_logins_lock:
        failures = [t for t in _failed_logins.get(client_key, []) if t >= cutoff]
        _failed_logins[client_key] = failures
        locked = len(failures) >= config.LOGIN_MAX_FAILURES

    if locked:
        flash("Too many incorrect attempts. Try again in a few minutes.")
        return redirect(url_for("labels.admin_home"))

    password = request.form.get("password", "")
    if config.ADMIN_PASSWORD_HASH and check_password_hash(config.ADMIN_PASSWORD_HASH, password):
        session["admin_authed"] = True
        session.permanent = True
        with _failed_logins_lock:
            _failed_logins.pop(client_key, None)
        destination = session.pop("return_to", None)
        if destination and destination.startswith("/") and not destination.startswith("//"):
            return redirect(destination)
    else:
        with _failed_logins_lock:
            _failed_logins.setdefault(client_key, []).append(now)
        flash("Incorrect password")
    return redirect(url_for("labels.admin_home"))


@bp.route("/admin/logout", methods=["POST"])
def admin_logout():
    require_csrf()
    session.clear()
    return redirect(url_for("labels.admin_home"))


@bp.route("/admin/counter", methods=["POST"])
@admin_required
def admin_set_counter():
    require_csrf()
    if request.form.get("confirm") != "yes":
        flash("You must explicitly confirm a counter change")
        return redirect(url_for("labels.admin_home"))

    try:
        new_value = int(request.form.get("new_value", ""))
    except ValueError:
        flash("New counter value must be an integer")
        return redirect(url_for("labels.admin_home"))

    if not (0 <= new_value <= config.LABEL_ID_MAX):
        flash(f"New counter value must be between 0 and {config.LABEL_ID_MAX}")
        return redirect(url_for("labels.admin_home"))

    reason = request.form.get("reason", "").strip()
    if not reason:
        flash("A reason is required to change the counter")
        return redirect(url_for("labels.admin_home"))

    conn = database.get_connection()
    try:
        database.set_counter_value(conn, new_value, reason)
    finally:
        conn.close()
    flash(f"Counter updated to {new_value}")
    return redirect(url_for("labels.admin_home"))


@bp.route("/admin/calibration-print", methods=["POST"])
@admin_required
def admin_calibration_print():
    require_csrf()
    page = render_calibration_page()
    path = save_print_file(page, config.GENERATED_DIR / "calibration.png")
    result = submit_label_page(path)
    if result.success:
        flash(f"Calibration page submitted (job {result.cups_job_id})")
    else:
        flash(f"Calibration print failed: {result.stderr}")
    return redirect(url_for("labels.admin_home"))
