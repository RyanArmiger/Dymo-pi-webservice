"""CUPS command-line printing wrapper for the DYMO LabelWriter 450.

subprocess.run() is always called with an argument list, never `shell=True`:
label file paths can be derived from user-supplied data (sample/experiment
IDs), and shelling out with those interpolated into a string would be a
command-injection risk.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import config

LP_TIMEOUT_SECONDS = 30
LPSTAT_TIMEOUT_SECONDS = 10

_JOB_ID_RE = re.compile(r"request id is\s+(\S+)")


@dataclass
class PrintResult:
    success: bool
    cups_job_id: str | None
    stdout: str
    stderr: str
    returncode: int


def _extract_job_id(stdout: str) -> str | None:
    match = _JOB_ID_RE.search(stdout)
    if match is None:
        return None
    token = match.group(1)
    if "-" in token:
        return token.rsplit("-", 1)[1]
    return token


def submit_label_page(path: Path, printer_name: str | None = None) -> PrintResult:
    command = [
        "lp",
        "-d",
        printer_name or config.PRINTER_NAME,
        "-o",
        f"PageSize={config.PAGE_SIZE}",
        *config.LP_EXTRA_OPTIONS,
        str(path),
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=LP_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = (exc.stderr or "") + f"\nlp timed out after {LP_TIMEOUT_SECONDS}s"
        return PrintResult(
            success=False,
            cups_job_id=None,
            stdout=stdout,
            stderr=stderr.strip(),
            returncode=-1,
        )

    return PrintResult(
        success=result.returncode == 0,
        cups_job_id=_extract_job_id(result.stdout) if result.returncode == 0 else None,
        stdout=result.stdout,
        stderr=result.stderr,
        returncode=result.returncode,
    )


def printer_state() -> str:
    printer_name = config.PRINTER_NAME
    try:
        result = subprocess.run(
            ["lpstat", "-p", printer_name],
            capture_output=True,
            text=True,
            timeout=LPSTAT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return "unavailable"

    if result.returncode != 0:
        return "unavailable"

    output = result.stdout.lower()
    if "disabled" in output or "paused" in output:
        return "paused"
    if "is idle" in output or "now printing" in output:
        return "ready"
    return "unknown"


def queued_job_count() -> int:
    printer_name = config.PRINTER_NAME
    try:
        result = subprocess.run(
            ["lpstat", "-o", printer_name],
            capture_output=True,
            text=True,
            timeout=LPSTAT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return 0

    if result.returncode != 0:
        return 0

    return len([line for line in result.stdout.splitlines() if line.strip()])


def printer_status_summary() -> dict:
    state = printer_state()
    queued_jobs = queued_job_count()
    return {
        "state": state,
        "queued_jobs": queued_jobs,
        "raw": f"state={state} queued_jobs={queued_jobs}",
    }
