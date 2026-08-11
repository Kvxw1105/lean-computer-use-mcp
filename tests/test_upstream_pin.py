"""Upstream version pin + regression fixture hash checks (M4)."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
from pathlib import Path
from types import ModuleType

from lean_computer_use_mcp.diagnostics import (
    UPSTREAM_PINNED_VERSION,
    check_upstream_version,
    parse_version,
)

BENCHMARKS = Path(__file__).parent.parent / "benchmarks"


def _load_verify_pin() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "verify_pin", BENCHMARKS / "verify_pin.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- version parsing / matching ----------------------------------------------


def test_parse_version_extracts_triple() -> None:
    assert parse_version("0.3.1\n") == "0.3.1"
    assert parse_version("open-computer-use 0.3.1 (npm)") == "0.3.1"
    assert parse_version("v1.2.3-beta") == "1.2.3"
    assert parse_version("no version here") is None


def test_check_upstream_version_pinned_ok(monkeypatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: "C:/bin/occ.cmd")
    result = check_upstream_version(
        "open-computer-use", runner=lambda binary: "0.3.1\n"
    )
    assert result.status == "ok"
    assert result.data == {"version": "0.3.1", "pinned": UPSTREAM_PINNED_VERSION}
    assert "pinned 0.3.1" in result.detail


def test_check_upstream_version_mismatch_warns(monkeypatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: "C:/bin/occ.cmd")
    result = check_upstream_version(
        "open-computer-use", runner=lambda binary: "0.4.0\n"
    )
    assert result.status == "warn"
    assert "installed 0.4.0, pinned 0.3.1" in result.detail
    assert result.data["pinned"] == "0.3.1"


def test_check_upstream_version_unparseable_warns(monkeypatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: "C:/bin/occ.cmd")
    result = check_upstream_version("open-computer-use", runner=lambda binary: "??")
    assert result.status == "warn"
    assert result.data == {}


# --- fixture hash checks -----------------------------------------------------


def _write_pin(tmp_path: Path, root: Path, files: dict[str, str]) -> Path:
    entries = []
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        # The pin records the canonical LF hash (see _canonical_bytes): on
        # Windows, write_text may have produced CRLF, so normalize here too.
        canonical = path.read_bytes().replace(b"\r\n", b"\n")
        entries.append(
            {
                "path": name,
                "sha256": hashlib.sha256(canonical).hexdigest(),
                "bytes": path.stat().st_size,
            }
        )
    pin_path = tmp_path / "upstream-pin.json"
    pin_path.write_text(
        json.dumps({"pinned_version": "0.3.1", "fixtures": entries}, indent=2),
        encoding="utf-8",
    )
    return pin_path


def test_fixture_hashes_pass_when_intact(tmp_path) -> None:
    verify_pin = _load_verify_pin()
    pin_path = _write_pin(
        tmp_path, tmp_path / "root", {"fixtures/a.txt": "hello", "fixtures/b.txt": "world"}
    )
    assert verify_pin.check_fixture_hashes(verify_pin.load_pin(pin_path), tmp_path / "root") == []


def test_fixture_hashes_detect_drift_and_missing(tmp_path) -> None:
    verify_pin = _load_verify_pin()
    root = tmp_path / "root"
    pin_path = _write_pin(tmp_path, root, {"fixtures/a.txt": "hello"})
    (root / "fixtures" / "a.txt").write_text("tampered", encoding="utf-8")
    (root / "fixtures" / "b.txt").write_text("extra", encoding="utf-8")
    pin = verify_pin.load_pin(pin_path)
    pin["fixtures"].append({"path": "fixtures/ghost.txt", "sha256": "0" * 64, "bytes": 1})
    problems = verify_pin.check_fixture_hashes(pin, root)
    assert any("drifted" in problem for problem in problems)
    assert any("missing" in problem for problem in problems)


def test_verify_reports_pin_and_binary(monkeypatch, tmp_path) -> None:
    verify_pin = _load_verify_pin()
    root = tmp_path / "root"
    pin_path = _write_pin(tmp_path, root, {"fixtures/a.txt": "hello"})
    monkeypatch.setattr(
        verify_pin, "installed_version", lambda binary: "0.3.1"
    )
    ok, lines = verify_pin.verify(pin_path, root, binary="open-computer-use")
    assert ok is True
    assert any("fixture hashes" in line for line in lines)
    assert any("installed upstream 0.3.1 matches" in line for line in lines)


def test_verify_fails_on_mismatched_binary(monkeypatch, tmp_path) -> None:
    verify_pin = _load_verify_pin()
    root = tmp_path / "root"
    pin_path = _write_pin(tmp_path, root, {"fixtures/a.txt": "hello"})
    monkeypatch.setattr(verify_pin, "installed_version", lambda binary: "0.9.9")
    ok, lines = verify_pin.verify(pin_path, root, binary="open-computer-use")
    assert ok is False
    assert any("!= pinned" in line for line in lines)


def test_regenerate_updates_hashes(tmp_path) -> None:
    verify_pin = _load_verify_pin()
    root = tmp_path / "root"
    pin_path = _write_pin(tmp_path, root, {"fixtures/a.txt": "hello"})
    (root / "fixtures" / "a.txt").write_text("changed", encoding="utf-8")
    assert verify_pin.check_fixture_hashes(verify_pin.load_pin(pin_path), root)
    verify_pin.regenerate(pin_path, root)
    assert verify_pin.check_fixture_hashes(verify_pin.load_pin(pin_path), root) == []


def test_repo_pin_file_is_current() -> None:
    """The committed pin file must match the fixtures it references."""
    verify_pin = _load_verify_pin()

    problems = verify_pin.check_fixture_hashes(
        verify_pin.load_pin(verify_pin.DEFAULT_PIN), verify_pin.REPO_ROOT
    )
    assert problems == []


def test_fixture_hashes_tolerate_crlf_checkout(tmp_path) -> None:
    """A CRLF working copy (Windows core.autocrlf=true) must match the LF pin."""
    verify_pin = _load_verify_pin()
    root = tmp_path / "root"
    pin_path = _write_pin(tmp_path, root, {"fixtures/a.txt": "line1\nline2\n"})
    (root / "fixtures" / "a.txt").write_bytes(b"line1\r\nline2\r\n")
    problems = verify_pin.check_fixture_hashes(verify_pin.load_pin(pin_path), root)
    assert problems == []
