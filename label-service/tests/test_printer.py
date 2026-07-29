from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import config
from app import printer


@dataclass
class FakeCompletedProcess:
    returncode: int
    stdout: str = ""
    stderr: str = ""


def test_submit_label_page_builds_command_as_list(monkeypatch):
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return FakeCompletedProcess(returncode=0, stdout="request id is DYMO_LabelWriter_450-123 (1 file(s))\n")

    monkeypatch.setattr("app.printer.subprocess.run", fake_run)

    result = printer.submit_label_page(Path("/tmp/fake_label.png"))

    command = captured["command"]
    assert isinstance(command, list)
    expected_prefix = ["lp", "-d", config.PRINTER_NAME, "-o", f"PageSize={config.PAGE_SIZE}"]
    assert command[: len(expected_prefix)] == expected_prefix
    remainder = command[len(expected_prefix):]
    assert remainder[: len(config.LP_EXTRA_OPTIONS)] == config.LP_EXTRA_OPTIONS
    assert remainder[-1] == "/tmp/fake_label.png"
    assert result.success is True
    assert result.cups_job_id == "123"


def test_submit_label_page_nonzero_returncode_is_failure(monkeypatch):
    def fake_run(command, **kwargs):
        return FakeCompletedProcess(returncode=1, stdout="", stderr="lp: unable to print")

    monkeypatch.setattr("app.printer.subprocess.run", fake_run)

    result = printer.submit_label_page(Path("/tmp/fake_label.png"))
    assert result.success is False
    assert result.cups_job_id is None
    assert result.returncode == 1


def test_extract_job_id_no_match_returns_none():
    assert printer._extract_job_id("no job id here") is None


def test_extract_job_id_without_dash_returns_full_token():
    assert printer._extract_job_id("request id is 123 (1 file(s))\n") == "123"


def test_printer_state_idle_maps_to_ready(monkeypatch):
    def fake_run(command, **kwargs):
        return FakeCompletedProcess(returncode=0, stdout=f"printer {config.PRINTER_NAME} is idle.  enabled since ...\n")

    monkeypatch.setattr("app.printer.subprocess.run", fake_run)
    assert printer.printer_state() == "ready"


def test_printer_state_disabled_maps_to_paused(monkeypatch):
    def fake_run(command, **kwargs):
        return FakeCompletedProcess(returncode=0, stdout=f"printer {config.PRINTER_NAME} disabled since ...\n")

    monkeypatch.setattr("app.printer.subprocess.run", fake_run)
    assert printer.printer_state() == "paused"


def test_printer_status_summary(monkeypatch):
    def fake_run(command, **kwargs):
        if command[0] == "lpstat" and "-p" in command:
            return FakeCompletedProcess(returncode=0, stdout=f"printer {config.PRINTER_NAME} is idle.\n")
        if command[0] == "lpstat" and "-o" in command:
            return FakeCompletedProcess(returncode=0, stdout="job1\njob2\n")
        raise AssertionError(f"unexpected command {command}")

    monkeypatch.setattr("app.printer.subprocess.run", fake_run)

    summary = printer.printer_status_summary()
    assert summary["state"] == "ready"
    assert summary["queued_jobs"] == 2
    assert summary["raw"] == "state=ready queued_jobs=2"
