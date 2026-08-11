"""Guided real-machine verification: checklist, answers, report rendering."""

from __future__ import annotations

import datetime as _dt

from lean_computer_use_mcp import cli
from lean_computer_use_mcp.diagnostics import CheckResult
from lean_computer_use_mcp.verify import (
    CHECKLIST,
    default_report_path,
    parse_answer,
    run_verification,
)


def test_checklist_has_ten_complete_items():
    assert len(CHECKLIST) == 10
    for item in CHECKLIST:
        assert item.title
        assert item.how
        assert item.expect
        assert item.fail_hint
    assert CHECKLIST[0].title == "Install & registration smoke"
    assert CHECKLIST[9].title == "End-to-end metrics honesty"


def test_parse_answer_variants():
    assert parse_answer("") == ("pass", "")
    assert parse_answer("   ") == ("pass", "")
    assert parse_answer("fail: sogou drops text") == (
        "fail",
        "sogou drops text",
    )
    assert parse_answer("f") == ("fail", "")
    assert parse_answer("skip: no JianYing here") == (
        "skip",
        "no JianYing here",
    )
    assert parse_answer("s: windows only") == ("skip", "windows only")
    assert parse_answer("pass: works") == ("pass", "works")
    assert parse_answer("everything looked fine") == (
        "pass",
        "everything looked fine",
    )


def test_non_interactive_all_pass(tmp_path):
    out = tmp_path / "verification-2026-08-11.md"
    report = run_verification(
        out,
        answers={item.id: "" for item in CHECKLIST},
        now=lambda: _dt.datetime(2026, 8, 11, 22, 0),
        preflight=lambda: [],
    )
    assert report.ok
    assert report.passed == 10
    assert report.failed == 0
    assert report.skipped == 0
    text = out.read_text(encoding="utf-8")
    assert "10 passed, 0 failed, 0 skipped" in text
    assert "## Preflight" in text
    assert "## 1. Install & registration smoke" in text
    assert "- Status: **pass**" in text
    assert "0.2.0" in text
    assert "0.3.1" in text


def test_fail_and_skip_render_hints(tmp_path):
    out = tmp_path / "report.md"
    answers = {1: "", 2: "fail: window check failed", 3: "skip: no IME"}
    report = run_verification(out, answers=answers, preflight=lambda: [])
    assert not report.ok
    assert report.failed == 1
    assert report.skipped == 1
    assert report.passed == 8
    text = out.read_text(encoding="utf-8")
    assert "- Notes: window check failed" in text
    assert "- Status: **skip**" in text
    assert f"- Next: {CHECKLIST[1].fail_hint}" in text


def test_interactive_prompt_loop(tmp_path):
    replies = ["", "fail: broken", "skip"]
    calls: list[str] = []

    def fake_prompt(_label: str) -> str:
        calls.append(_label)
        return replies.pop(0) if replies else ""

    out = tmp_path / "interactive.md"
    report = run_verification(
        out,
        prompt=fake_prompt,
        now=lambda: _dt.datetime(2026, 8, 11, 22, 0),
        preflight=lambda: [],
    )
    assert len(calls) == 10
    assert report.statuses[1] == "pass"
    assert report.statuses[2] == "fail"
    assert report.statuses[3] == "skip"
    assert report.failed == 1
    assert out.read_text(encoding="utf-8").startswith("# Real-machine")


def test_preflight_rows_rendered(tmp_path):
    out = tmp_path / "preflight.md"
    report = run_verification(
        out,
        answers={item.id: "" for item in CHECKLIST},
        preflight=lambda: [
            CheckResult("upstream_binary", "ok", "found on PATH"),
            CheckResult("upstream_version", "warn", "0.3.0 installed"),
        ],
    )
    text = out.read_text(encoding="utf-8")
    assert "- `upstream_binary`: ok - found on PATH" in text
    assert "- `upstream_version`: warn - 0.3.0 installed" in text
    assert report.ok  # preflight warnings do not fail the run


def test_default_report_path():
    path = default_report_path(now=lambda: _dt.datetime(2026, 8, 11, 22, 0))
    assert path.name == "verification-2026-08-11.md"
    assert path.parts[0] == "benchmarks"
    assert path.parts[1] == "results"


def test_cli_verify_command(tmp_path, monkeypatch, capsys):
    answers = iter([""] * 10)

    def fake_input(_prompt: str = "") -> str:
        return next(answers)

    monkeypatch.setattr("builtins.input", fake_input)
    out = tmp_path / "cli.md"
    code = cli.main(["verify", "--out", str(out)])
    assert code == 0
    assert "10 passed, 0 failed, 0 skipped" in out.read_text(encoding="utf-8")
    captured = capsys.readouterr()
    assert "Verification: 10 passed" in captured.out
    assert "All checklist items passed" in captured.out
