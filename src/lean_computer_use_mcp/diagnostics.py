"""Doctor diagnostics: binary, upstream version, DPI, target window state.

Every check is a small pure function with injectable backends so unit tests
need no subprocesses and no live desktop. The CLI command `doctor` renders
the report; `--app <name>` adds target-window checks (visibility, ambiguity,
occlusion, window DPI).
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Callable

from lean_computer_use_mcp.errors import AppNotFoundError, LeanComputerUseError
from lean_computer_use_mcp.upstream.win_input import CtypesWin32Input, WindowStatus

#: Upstream CLI version the fixtures and benchmarks were captured against.
#: Keep ``benchmarks/upstream-pin.json`` in sync when this changes.
UPSTREAM_PINNED_VERSION = "0.3.1"

_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def parse_version(text: str) -> str | None:
    """Extract the first ``MAJOR.MINOR.PATCH`` triple from arbitrary text."""
    match = _VERSION_RE.search(text)
    return match.group(0) if match else None


@dataclass
class CheckResult:
    name: str
    status: str  # ok | warn | fail | skip
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class DoctorReport:
    checks: list[CheckResult]

    @property
    def ok(self) -> bool:
        """A report is ok unless at least one check hard-fails."""
        return all(check.status != "fail" for check in self.checks)

    def render(self) -> str:
        lines = []
        for check in self.checks:
            suffix = f" - {check.detail}" if check.detail else ""
            lines.append(f"{check.name}: {check.status}{suffix}")
        return "\n".join(lines)


def _run_version(binary: str) -> str:
    """Run `<binary> --version` and return stdout/stderr text."""
    resolved = shutil.which(binary)
    if resolved is None:
        raise FileNotFoundError(binary)
    cmd = [resolved, "--version"]
    if resolved.lower().endswith((".cmd", ".bat")):
        cmd = ["cmd", "/c", *cmd]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )
    return (proc.stdout or proc.stderr or "").strip()


def _process_dpi_awareness() -> int:
    """Set per-monitor DPI awareness (the facade's coordinate contract), then
    report the effective awareness: 0 unaware, 1 system, 2 per-monitor."""
    import ctypes

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:  # noqa: BLE001 - already aware or unavailable
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001
            pass
    value = ctypes.c_int()
    result = ctypes.windll.shcore.GetProcessDpiAwareness(None, ctypes.byref(value))
    if result != 0:
        raise OSError(f"GetProcessDpiAwareness returned 0x{result & 0xFFFFFFFF:08x}")
    return value.value


def _window_status(app: str) -> WindowStatus:
    return CtypesWin32Input().window_status(app)


def _dpi_for_window(hwnd: int) -> int:
    import ctypes

    dpi = ctypes.windll.user32.GetDpiForWindow(ctypes.c_void_p(int(hwnd)))
    return int(dpi)


def check_binary(binary: str) -> CheckResult:
    resolved = shutil.which(binary)
    if resolved is None:
        return CheckResult("upstream_binary", "fail", f"{binary} not found on PATH")
    return CheckResult("upstream_binary", "ok", f"{binary} -> {resolved}")


def check_upstream_version(
    binary: str,
    runner: Callable[[str], str] | None = None,
    pinned: str = UPSTREAM_PINNED_VERSION,
) -> CheckResult:
    if shutil.which(binary) is None:
        return CheckResult("upstream_version", "skip", "binary missing")
    run = runner or _run_version
    try:
        output = run(binary)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return CheckResult(
            "upstream_version", "warn", f"could not read version: {exc}"
        )
    version = parse_version(output or "")
    if not version:
        return CheckResult("upstream_version", "warn", "no version string")
    if version != pinned:
        return CheckResult(
            "upstream_version",
            "warn",
            f"installed {version}, pinned {pinned} - "
            "regenerate fixtures before trusting results",
            {"version": version, "pinned": pinned},
        )
    return CheckResult(
        "upstream_version",
        "ok",
        f"{version} (pinned {pinned})",
        {"version": version, "pinned": pinned},
    )


def check_dpi_awareness(
    getter: Callable[[], int] | None = None,
) -> CheckResult:
    if sys.platform != "win32":
        return CheckResult("dpi_awareness", "skip", "Windows only")
    read = getter or _process_dpi_awareness
    try:
        value = read()
    except Exception as exc:  # noqa: BLE001
        return CheckResult("dpi_awareness", "warn", f"could not read: {exc}")
    if value == 2:
        return CheckResult(
            "dpi_awareness",
            "ok",
            "per-monitor (screenshot-pixel coordinates map to physical pixels)",
            {"awareness": value},
        )
    if value == 1:
        return CheckResult(
            "dpi_awareness",
            "warn",
            "system DPI aware - coordinate mismatch risk on mixed-DPI setups",
            {"awareness": value},
        )
    return CheckResult(
        "dpi_awareness",
        "fail",
        "DPI unaware - real clicks will misplace",
        {"awareness": value},
    )


def check_window(
    app: str, status_getter: Callable[[str], WindowStatus] | None = None
) -> CheckResult:
    if sys.platform != "win32":
        return CheckResult("window", "skip", "Windows only")
    get = status_getter or _window_status
    try:
        status = get(app)
    except AppNotFoundError as exc:
        return CheckResult(
            "window", "fail", f"no visible window for {app!r} ({exc})"
        )
    except LeanComputerUseError as exc:
        return CheckResult("window", "warn", f"cannot inspect: {exc}")
    main = status.main
    data = {
        "window_count": len(status.candidates),
        "main_title": main.title,
        "rect": {
            "left": main.info.left,
            "top": main.info.top,
            "width": main.info.width,
            "height": main.info.height,
        },
        "occluded": main.occluded,
        "covered_by": list(main.covered_by),
        "ambiguous": status.ambiguous,
        "candidates": [c.title for c in status.candidates],
    }
    details = [
        f"{len(status.candidates)} window(s), main {main.title!r} "
        f"{main.info.width}x{main.info.height}"
    ]
    flag = "ok"
    if status.ambiguous:
        flag = "warn"
        details.append(
            f"{len(status.candidates)} windows match: "
            + ", ".join(c.title for c in status.candidates)
        )
    if main.occluded:
        flag = "warn"
        details.append(
            "main window fully covered by: " + ", ".join(main.covered_by)
        )
    return CheckResult("window", flag, "; ".join(details), data)


def check_window_dpi(
    app: str,
    status_getter: Callable[[str], WindowStatus] | None = None,
    dpi_getter: Callable[[int], int] | None = None,
) -> CheckResult:
    if sys.platform != "win32":
        return CheckResult("window_dpi", "skip", "Windows only")
    get = status_getter or _window_status
    read_dpi = dpi_getter or _dpi_for_window
    try:
        status = get(app)
        dpi = read_dpi(status.main.info.hwnd)
    except AppNotFoundError:
        return CheckResult("window_dpi", "skip", "no target window")
    except (LeanComputerUseError, OSError) as exc:
        return CheckResult("window_dpi", "warn", f"could not read: {exc}")
    if not dpi or dpi <= 0:
        return CheckResult("window_dpi", "warn", "DPI unavailable for the window")
    return CheckResult(
        "window_dpi",
        "ok",
        f"{dpi} ({round(dpi / 96 * 100)}%)",
        {"dpi": dpi},
    )


def run_doctor(binary: str, app: str | None = None) -> DoctorReport:
    checks = [check_binary(binary), check_upstream_version(binary)]
    if app:
        checks.append(check_window(app))
        checks.append(check_window_dpi(app))
    else:
        checks.append(
            CheckResult("window", "skip", "pass --app <name> to inspect a target window")
        )
        checks.append(
            CheckResult("window_dpi", "skip", "pass --app <name> to inspect a target window")
        )
    checks.append(check_dpi_awareness())
    return DoctorReport(checks)
