"""Doctor diagnostics: binary, version, DPI, target-window checks."""

from __future__ import annotations

import shutil
import sys

from lean_computer_use_mcp import cli
from lean_computer_use_mcp.diagnostics import (
    DoctorReport,
    check_binary,
    check_dpi_awareness,
    check_upstream_version,
    check_window,
    check_window_dpi,
    run_doctor,
)
from lean_computer_use_mcp.errors import AppNotFoundError
from lean_computer_use_mcp.upstream.win_input import (
    WindowCandidate,
    WindowInfo,
    WindowStatus,
)


def make_status(*titles: str, occluded: bool = False, covered_by=()) -> WindowStatus:
    candidates = []
    for index, title in enumerate(titles):
        info = WindowInfo(hwnd=index + 1, left=0, top=0, width=800, height=600)
        candidates.append(
            WindowCandidate(info=info, title=title, occluded=occluded, covered_by=tuple(covered_by))
        )
    return WindowStatus(
        app="X",
        candidates=tuple(candidates),
        main=candidates[0],
        ambiguous=len(candidates) > 1,
    )


def test_check_binary_found(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "C:/bin/open-computer-use.cmd")
    result = check_binary("open-computer-use")
    assert result.status == "ok"
    assert "open-computer-use" in result.detail


def test_check_binary_missing(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    result = check_binary("open-computer-use")
    assert result.status == "fail"
    assert "not found" in result.detail


def test_check_upstream_version_ok(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "C:/bin/open-computer-use.cmd")
    result = check_upstream_version(
        "open-computer-use", runner=lambda binary: "0.3.1\n"
    )
    assert result.status == "ok"
    assert result.data["version"] == "0.3.1"
    assert "0.3.1" in result.detail


def test_check_upstream_version_skipped_when_binary_missing(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    result = check_upstream_version("open-computer-use")
    assert result.status == "skip"


def test_check_upstream_version_warns_on_failure(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "C:/bin/open-computer-use.cmd")

    def boom(binary):
        raise TimeoutError("hung")

    result = check_upstream_version("open-computer-use", runner=boom)
    assert result.status == "warn"


def test_check_dpi_awareness_values(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert check_dpi_awareness(getter=lambda: 2).status == "ok"
    assert check_dpi_awareness(getter=lambda: 1).status == "warn"
    assert check_dpi_awareness(getter=lambda: 0).status == "fail"


def test_check_dpi_awareness_skips_off_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert check_dpi_awareness().status == "skip"


def test_check_window_single_visible_ok(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    result = check_window("ChatGPT", status_getter=lambda app: make_status("ChatGPT"))
    assert result.status == "ok"
    assert result.data["window_count"] == 1
    assert result.data["main_title"] == "ChatGPT"
    assert result.data["occluded"] is False
    assert result.data["ambiguous"] is False


def test_check_window_ambiguous_warns(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    status = make_status("ChatGPT", "ChatGPT - Settings")
    result = check_window("ChatGPT", status_getter=lambda app: status)
    assert result.status == "warn"
    assert "2 windows match" in result.detail
    assert result.data["candidates"] == ["ChatGPT", "ChatGPT - Settings"]


def test_check_window_occluded_warns(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    status = make_status("ChatGPT", occluded=True, covered_by=("Notepad",))
    result = check_window("ChatGPT", status_getter=lambda app: status)
    assert result.status == "warn"
    assert "Notepad" in result.detail


def test_check_window_app_not_found_fails(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")

    def missing(app):
        raise AppNotFoundError("no window", reason="window_not_found")

    result = check_window("Nope", status_getter=missing)
    assert result.status == "fail"


def test_check_window_skips_off_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert check_window("X").status == "skip"


def test_check_window_dpi(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    result = check_window_dpi(
        "ChatGPT",
        status_getter=lambda app: make_status("ChatGPT"),
        dpi_getter=lambda hwnd: 144,
    )
    assert result.status == "ok"
    assert result.data["dpi"] == 144
    assert "150%" in result.detail


def test_check_window_dpi_unavailable_warns(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    result = check_window_dpi(
        "ChatGPT",
        status_getter=lambda app: make_status("ChatGPT"),
        dpi_getter=lambda hwnd: 0,
    )
    assert result.status == "warn"


def test_run_doctor_without_app_skips_window_checks(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "C:/bin/open-computer-use.cmd")
    monkeypatch.setattr(sys, "platform", "win32")
    report = run_doctor("open-computer-use")
    names = [check.name for check in report.checks]
    assert names == [
        "upstream_binary",
        "upstream_version",
        "window",
        "window_dpi",
        "dpi_awareness",
    ]
    assert [check.status for check in report.checks if check.name == "window"] == ["skip"]


def test_doctor_report_ok_only_without_hard_failures():
    report = DoctorReport(
        [cli_dummy("ok"), cli_dummy("warn"), cli_dummy("skip")]
    )
    assert report.ok is True
    report = DoctorReport([cli_dummy("ok"), cli_dummy("fail")])
    assert report.ok is False


def cli_dummy(status: str):
    from lean_computer_use_mcp.diagnostics import CheckResult

    return CheckResult("x", status)


def test_doctor_command_prints_report(monkeypatch, capsys):
    report = DoctorReport(
        [
            cli_dummy("ok"),
            cli_dummy("warn"),
        ]
    )
    monkeypatch.setattr(cli, "run_doctor", lambda binary, app=None: report)
    code = cli.main(["doctor", "--app", "ChatGPT"])
    assert code == 0
    out = capsys.readouterr().out
    assert "x: ok" in out
    assert "x: warn" in out


def test_doctor_command_fails_on_hard_failure(monkeypatch, capsys):
    report = DoctorReport([cli_dummy("fail")])
    monkeypatch.setattr(cli, "run_doctor", lambda binary, app=None: report)
    code = cli.main(["doctor"])
    assert code == 1
