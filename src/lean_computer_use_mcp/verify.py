"""Guided real-machine verification (docs/VERIFICATION.md).

The ten-item checklist in docs/VERIFICATION.md is the only part of the
project that cannot be verified in CI or on a fake upstream. This module
renders each item, collects pass/fail/skip answers with notes, and writes a
dated markdown report under ``benchmarks/results/`` (gitignored) so the next
release can compare regressions.

Nothing here touches screenshots, accessibility trees, or API keys; the
report only records statuses, notes, and facade/upstream versions.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from lean_computer_use_mcp import __version__
from lean_computer_use_mcp.diagnostics import (
    UPSTREAM_PINNED_VERSION,
    CheckResult,
    check_binary,
    check_upstream_version,
)


@dataclass(frozen=True)
class VerificationItem:
    """One checklist entry; text mirrors docs/VERIFICATION.md."""

    id: int
    title: str
    how: str
    expect: str
    fail_hint: str


CHECKLIST: tuple[VerificationItem, ...] = (
    VerificationItem(
        1,
        "Install & registration smoke",
        "uvx --from . lean-computer-use --version; register the MCP server in "
        "~/.zcode/cli/config.json (mcp.servers, uv.exe run --project <repo> "
        "lean-computer-use serve, timeoutMs 60000) and ask the agent to call "
        "cu_find_app and cu_observe",
        "--version prints '0.2.0 (pinned upstream 0.3.1)'; cu_find_app and "
        "cu_observe return real apps with visible windows",
        "server dies on startup -> run 'lean-computer-use serve' in a terminal "
        "and read the traceback; --version/doctor must not require a desktop",
    ),
    VerificationItem(
        2,
        "doctor",
        "uv run lean-computer-use doctor --app explorer",
        "upstream_binary ok, upstream_version ok 0.3.1, window ok (or warn with "
        "ambiguity/occlusion hints), window_dpi ok (96/144 ...), dpi_awareness ok",
        "version mismatch -> upgrade or re-pin; window fail -> the app really "
        "has no visible window",
    ),
    VerificationItem(
        3,
        "IME Chinese input recording",
        "lean-computer-use record --app <editor>, type a Chinese sentence with "
        "the IME (pinyin -> candidates -> space), stop with Ctrl+Shift+R",
        "the typing step has action: 'type_text' and value = the composed "
        "Chinese text (and ime_text/ime_keys)",
        "value null -> composition sampling failed; the step still carries "
        "ime_keys and replay falls back to raw keys - report the IME to "
        "maintainers",
    ),
    VerificationItem(
        4,
        "Drag recording",
        "Record a drag in a timeline/upload surface: press, move >= 3 px, release",
        "recording JSON contains one action: 'drag' step with x/y -> to_x/to_y; "
        "a press without movement stays 'click'; replay --run executes it",
        "no drag step -> check the recorded events for press/move/release; "
        "sub-threshold jitter is intentionally a click",
    ),
    VerificationItem(
        5,
        "Real-input fallback (T2)",
        "Replay clicks with click_method='real' on a self-drawn app (JianYing) "
        "while the upstream real path is healthy, then break the upstream "
        "real-click path (e.g. rename the binary) and repeat",
        "healthy: real_input.path == 'upstream'; broken: real_input.path == "
        "'fallback' with real_input.upstream_error; after a fallback click "
        "verify visually that the action landed",
        "the upstream ok flag alone is not trustworthy for custom-rendered "
        "apps; check the screenshot diff / element table",
    ),
    VerificationItem(
        6,
        "Window tools: ambiguity & occlusion",
        "Open two windows of the same app; cu_window(app, 'list') and "
        "cu_window(app, 'activate') without a title; cover the target window "
        "with another window",
        "list returns >= 2 candidates with ambiguous: true; activate returns "
        "AMBIGUOUS_TARGET with the candidates (never a random pick); occluded: "
        "true on the main candidate with covered_by - a status, not an error",
        "single match only -> the second window was not visible; occlusion "
        "missing -> check the cover window geometry",
    ),
    VerificationItem(
        7,
        "Screenshot fingerprint gating (T4)",
        "On JianYing observe twice without touching the app; resize/restructure "
        "the window; move the window at the same size",
        "second cu_act with the first state_id is NOT stale; after resize -> "
        "STALE_STATE (signal tree or image); after move -> STALE_STATE "
        "(signal image: the fingerprint folds in the window screen rect)",
        "no stale on move -> window rect not reaching the fingerprint; no stale "
        "on resize -> trivial tree + identical pixels (report)",
    ),
    VerificationItem(
        8,
        "Replay auto-recovery (T5)",
        "Record a short workflow, change the app state (open a modal), "
        "replay --run",
        "STALE_STATE -> re-observe -> retry, printed as '(auto-recovered Nx)'; "
        "a persistent stale condition fails after 3 retries (hard cap)",
        "no recovery -> replay path not wired to the state gate; endless "
        "retries -> hard cap regressed",
    ),
    VerificationItem(
        9,
        "Cross-app chain (WORKFLOWS.md pattern)",
        "Record two skills (e.g. JianYing export -> browser upload), replay "
        "them back to back, hand the exported file path through the agent "
        "context",
        "each skill focuses its own app and keeps its own confirmation prompts; "
        "the second skill receives the path from the first replay's state",
        "focus loss -> cu_window activation not awaited; path not received -> "
        "check the cross-app handoff in WORKFLOWS.md",
    ),
    VerificationItem(
        10,
        "End-to-end metrics honesty",
        "Run record -> compile -> replay with --metrics-path metrics.jsonl",
        "replay rows carry image_bytes: 0 (screenshots stay local), "
        "text_chars > 0, nodes > 0; no STALE_STATE unless the app really "
        "changed between observe and act",
        "image_bytes > 0 -> a screenshot leaked into model-visible output; "
        "phantom STALE_STATE -> freshness gate too aggressive (report)",
    ),
)


def parse_answer(raw: str) -> tuple[str, str]:
    """Parse one answer into (status, note).

    '' -> pass; 'fail: <note>' -> fail; 'skip: <note>' -> skip. Any other
    text counts as pass with the text as the note.
    """
    text = raw.strip()
    if not text:
        return ("pass", "")
    status, sep, note = text.partition(":")
    status = status.strip().lower()
    if status in {"f", "fail"}:
        return ("fail", note.strip())
    if status in {"s", "skip"}:
        return ("skip", note.strip())
    if status in {"p", "pass"}:
        return ("pass", note.strip())
    return ("pass", text)


@dataclass
class VerifyReport:
    """Outcome of one verification run; rendered to markdown."""

    path: Path
    generated_at: str
    preflight: list[CheckResult]
    statuses: dict[int, str] = field(default_factory=dict)
    notes: dict[int, str] = field(default_factory=dict)

    @property
    def passed(self) -> int:
        return sum(1 for value in self.statuses.values() if value == "pass")

    @property
    def failed(self) -> int:
        return sum(1 for value in self.statuses.values() if value == "fail")

    @property
    def skipped(self) -> int:
        return sum(1 for value in self.statuses.values() if value == "skip")

    @property
    def ok(self) -> bool:
        """A run is ok only when every item passed."""
        return self.failed == 0 and len(self.statuses) == len(CHECKLIST)

    def render_markdown(self) -> str:
        lines = [
            "# Real-machine verification report",
            "",
            f"- Generated: {self.generated_at}",
            f"- Facade: lean-computer-use-mcp {__version__} "
            f"(pinned upstream {UPSTREAM_PINNED_VERSION})",
            f"- Result: {self.passed} passed, {self.failed} failed, "
            f"{self.skipped} skipped",
            "",
            "## Preflight",
            "",
        ]
        for check in self.preflight:
            suffix = f" - {check.detail}" if check.detail else ""
            lines.append(f"- `{check.name}`: {check.status}{suffix}")
        lines.append("")
        for item in CHECKLIST:
            status = self.statuses.get(item.id, "not-run")
            lines += [
                f"## {item.id}. {item.title}",
                "",
                f"- Status: **{status}**",
                f"- Notes: {self.notes.get(item.id) or '(none)'}",
            ]
            if status == "fail":
                lines += [f"- Next: {item.fail_hint}", ""]
            else:
                lines.append("")
        return "\n".join(lines)


def default_report_path(now: Callable[[], _dt.datetime] | None = None) -> Path:
    stamp = (now() if now else _dt.datetime.now()).strftime("%Y-%m-%d")
    return Path("benchmarks") / "results" / f"verification-{stamp}.md"


def _print_prompt(item: VerificationItem, index: int) -> None:
    print("=" * 72)
    print(f"[{index}/{len(CHECKLIST)}] {item.title}")
    print(f"Do:     {item.how}")
    print(f"Expect: {item.expect}")
    print(f"Fail:   {item.fail_hint}")
    print("Answer: [Enter]=pass, 'fail[: note]', 'skip[: note]'")


def run_verification(
    out_path: Path,
    *,
    answers: dict[int, str] | None = None,
    prompt: Callable[[str], str] | None = None,
    now: Callable[[], _dt.datetime] | None = None,
    preflight: Callable[[], list[CheckResult]] | None = None,
) -> VerifyReport:
    """Run the checklist.

    With ``answers`` given (tests) the run is non-interactive; otherwise each
    item is printed and answered through ``prompt``. The report is written to
    ``out_path`` (default ``benchmarks/results/verification-<date>.md``).
    """
    ask = prompt or input
    stamp = (now() if now else _dt.datetime.now()).strftime("%Y-%m-%d %H:%M")
    checks = preflight() if preflight else [
        check_binary("open-computer-use"),
        check_upstream_version("open-computer-use"),
    ]
    statuses: dict[int, str] = {}
    notes: dict[int, str] = {}
    for index, item in enumerate(CHECKLIST, start=1):
        if answers is None:
            _print_prompt(item, index)
            raw = ask("> ").strip()
        else:
            raw = answers.get(item.id, "").strip()
        status, note = parse_answer(raw)
        statuses[item.id] = status
        if note:
            notes[item.id] = note
    report = VerifyReport(
        path=out_path,
        generated_at=stamp,
        preflight=checks,
        statuses=statuses,
        notes=notes,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report.render_markdown(), encoding="utf-8")
    return report
