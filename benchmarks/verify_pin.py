"""Verify the pinned upstream version and fixture hashes (M4 release gate).

The facade's fixtures and benchmarks are captured against a pinned
open-computer-use CLI version (see ``diagnostics.UPSTREAM_PINNED_VERSION``).
This script checks that:

1. every fixture in ``benchmarks/upstream-pin.json`` still matches its
   recorded sha256 (a drift means the upstream changed its output shape).
   Fixture hashes are computed on CRLF-normalized bytes so Windows
   checkouts (``core.autocrlf=true``) and LF checkouts agree;
2. with ``--binary``, the installed upstream version matches the pin.

Usage:
    uv run python benchmarks/verify_pin.py
    uv run python benchmarks/verify_pin.py --binary open-computer-use
    uv run python benchmarks/verify_pin.py --regenerate   # re-hash fixtures

Exit code 0 when every check passes; 1 otherwise. CI runs the fixture-hash
check on every push (no upstream binary needed).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PIN = REPO_ROOT / "benchmarks" / "upstream-pin.json"

_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def parse_version(text: str) -> str | None:
    """Extract the first ``MAJOR.MINOR.PATCH`` triple from arbitrary text."""
    match = _VERSION_RE.search(text)
    return match.group(0) if match else None


def load_pin(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _canonical_bytes(data: bytes) -> bytes:
    """Normalize CRLF to LF before hashing so every checkout agrees.

    Fixtures are text files. A Windows checkout with ``core.autocrlf=true``
    converts them to CRLF, which would change the sha256 versus the
    canonical LF blob. ``.gitattributes`` forces LF for new checkouts; this
    normalization keeps the pin meaningful even for stale checkouts.
    """
    return data.replace(b"\r\n", b"\n")


def check_fixture_hashes(pin: dict, root: Path) -> list[str]:
    """Return a list of problems; empty means every fixture hash matches."""
    problems: list[str] = []
    for entry in pin.get("fixtures", []):
        path = root / entry["path"]
        if not path.exists():
            problems.append(f"missing fixture: {entry['path']}")
            continue
        digest = hashlib.sha256(_canonical_bytes(path.read_bytes())).hexdigest()
        if digest != entry["sha256"]:
            problems.append(
                f"fixture drifted: {entry['path']} (recorded {entry['sha256'][:12]}..., "
                f"actual {digest[:12]}...) - regenerate after an upstream upgrade"
            )
    return problems


def installed_version(binary: str) -> str | None:
    """Run ``<binary> --version``; returns None when unavailable/unparseable."""
    import shutil

    resolved = shutil.which(binary)
    if resolved is None:
        return None
    cmd = [resolved, "--version"]
    if resolved.lower().endswith((".cmd", ".bat")):
        cmd = ["cmd", "/c", *cmd]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", timeout=15, check=False
    )
    return parse_version(proc.stdout or proc.stderr or "")


def verify(
    pin_path: Path = DEFAULT_PIN,
    root: Path = REPO_ROOT,
    binary: str | None = None,
) -> tuple[bool, list[str]]:
    """Run all checks; returns (ok, human-readable lines)."""
    pin = load_pin(pin_path)
    pinned = str(pin.get("pinned_version", ""))
    lines = [f"pinned upstream version: {pinned}"]
    problems = check_fixture_hashes(pin, root)
    if problems:
        lines.extend(f"  FAIL {problem}" for problem in problems)
    else:
        lines.append(f"  ok fixture hashes: {len(pin.get('fixtures', []))} files match")
    if binary is not None:
        actual = installed_version(binary)
        if actual is None:
            lines.append(f"  skip upstream binary {binary!r} not found on PATH")
        elif actual == pinned:
            lines.append(f"  ok installed upstream {actual} matches the pin")
        else:
            lines.append(
                f"  FAIL installed upstream {actual} != pinned {pinned}"
            )
            problems.append(f"installed upstream {actual} != pinned {pinned}")
    return not problems, lines


def regenerate(pin_path: Path = DEFAULT_PIN, root: Path = REPO_ROOT) -> None:
    """Recompute fixture hashes and write them back into the pin file."""
    pin = load_pin(pin_path)
    for entry in pin["fixtures"]:
        path = root / entry["path"]
        entry["sha256"] = hashlib.sha256(_canonical_bytes(path.read_bytes())).hexdigest()
        entry["bytes"] = path.stat().st_size
    Path(pin_path).write_text(
        json.dumps(pin, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pin", default=str(DEFAULT_PIN), help="Pin JSON path")
    parser.add_argument("--root", default=str(REPO_ROOT), help="Repo root")
    parser.add_argument(
        "--binary", default=None, help="Also check the installed upstream version"
    )
    parser.add_argument(
        "--regenerate",
        action="store_true",
        help="Recompute fixture hashes and rewrite the pin file",
    )
    args = parser.parse_args()

    if args.regenerate:
        regenerate(Path(args.pin), Path(args.root))
        print(f"regenerated fixture hashes in {args.pin}")
        return 0

    ok, lines = verify(Path(args.pin), Path(args.root), args.binary)
    for line in lines:
        print(line)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
