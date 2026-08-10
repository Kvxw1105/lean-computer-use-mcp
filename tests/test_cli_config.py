"""CLI tests for the `config` command family (isolated config dir)."""

from __future__ import annotations

import json

from lean_computer_use_mcp.cli import main


def _run(monkeypatch, tmp_path, *argv: str) -> int:
    monkeypatch.setenv("LEAN_CU_CONFIG_DIR", str(tmp_path))
    return main(list(argv))


def test_config_add_list_remove_roundtrip(monkeypatch, tmp_path, capsys) -> None:
    assert (
        _run(
            monkeypatch,
            tmp_path,
            "config",
            "add",
            "--api-base",
            "https://a.test/v1",
            "--api-key",
            "key-a",
            "--model",
            "m-a",
        )
        == 0
    )
    assert _run(monkeypatch, tmp_path, "config", "add",
                "--api-base", "https://b.test/v1", "--api-key", "key-b") == 0

    assert _run(monkeypatch, tmp_path, "config", "list") == 0
    out = capsys.readouterr().out
    assert "a.test" in out
    assert "b.test" in out
    assert "***" in out  # masked
    assert "key-a" not in out  # raw key never printed

    assert _run(monkeypatch, tmp_path, "config", "remove", "--index", "0") == 0
    capsys.readouterr()  # clear the remove line
    assert _run(monkeypatch, tmp_path, "config", "list") == 0
    out = capsys.readouterr().out
    assert "a.test" not in out
    assert "b.test" in out


def test_config_reorder_changes_preferred(monkeypatch, tmp_path, capsys) -> None:
    for i, host in enumerate(["https://a.test/v1", "https://b.test/v1"]):
        _run(monkeypatch, tmp_path, "config", "add",
             "--api-base", host, "--api-key", f"key-{i}")
    assert _run(monkeypatch, tmp_path, "config", "reorder",
                "--index", "1", "--to", "0") == 0
    assert _run(monkeypatch, tmp_path, "config", "list") == 0
    out = capsys.readouterr().out
    assert out.index("-> [0] b.test") < out.index("   [1] a.test")


def test_config_add_requires_key(monkeypatch, tmp_path, capsys) -> None:
    assert (
        _run(monkeypatch, tmp_path, "config", "add", "--api-base", "https://a.test/v1")
        == 1
    )
    assert "requires --api-base and --api-key" in capsys.readouterr().err


def test_config_remove_out_of_range(monkeypatch, tmp_path, capsys) -> None:
    assert _run(monkeypatch, tmp_path, "config", "remove", "--index", "5") == 1
    assert "within range" in capsys.readouterr().err


def test_config_list_empty_prints_help(monkeypatch, tmp_path, capsys) -> None:
    assert _run(monkeypatch, tmp_path, "config", "list") == 0
    out = capsys.readouterr().out
    assert "No endpoints configured" in out
    assert "config-ui" in out


def test_config_file_is_plain_json(monkeypatch, tmp_path) -> None:
    _run(monkeypatch, tmp_path, "config", "add",
         "--api-base", "https://a.test/v1", "--api-key", "key-a")
    data = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert data["providers"][0]["api_base"] == "https://a.test/v1"


def test_config_remove_last_endpoint_requires_yes(monkeypatch, tmp_path, capsys) -> None:
    """Silently leaving the store with zero endpoints is how the shared-config
    incident happened; the CLI refuses unless --yes confirms it."""
    _run(monkeypatch, tmp_path, "config", "add",
         "--api-base", "https://a.test/v1", "--api-key", "key-a")
    assert _run(monkeypatch, tmp_path, "config", "remove", "--index", "0") == 1
    assert "refusing to remove the last configured endpoint" in capsys.readouterr().err
    data = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert len(data["providers"]) == 1  # untouched

    assert _run(monkeypatch, tmp_path, "config", "remove",
                "--index", "0", "--yes") == 0
    data = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert data["providers"] == []


def test_config_ops_preserve_concurrent_external_writes(monkeypatch, tmp_path) -> None:
    """CLI mutations must preserve endpoints another agent wrote to the shared
    store, even when those writes land while the CLI process is running."""
    from lean_computer_use_mcp.config_store import save_config

    _run(monkeypatch, tmp_path, "config", "add",
         "--api-base", "https://a.test/v1", "--api-key", "key-a")

    # another agent adds B and C directly to the shared store
    save_config(
        {
            "engine": "llm",
            "providers": [
                {"api_base": "https://a.test/v1", "api_key": "key-a"},
                {"api_base": "https://b.test/v1", "api_key": "key-b"},
                {"api_base": "https://c.test/v1", "api_key": "key-c"},
            ],
        },
        tmp_path / "config.json",
    )

    # our CLI then adds D and reorders — none of A/B/C may be lost
    assert _run(monkeypatch, tmp_path, "config", "add",
                "--api-base", "https://d.test/v1", "--api-key", "key-d") == 0
    assert _run(monkeypatch, tmp_path, "config", "reorder",
                "--index", "3", "--to", "0") == 0
    data = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    bases = [p["api_base"] for p in data["providers"]]
    assert bases == [
        "https://d.test/v1",
        "https://a.test/v1",
        "https://b.test/v1",
        "https://c.test/v1",
    ]

def test_cli_version_flag(capsys):
    import pytest

    from lean_computer_use_mcp import __version__
    from lean_computer_use_mcp import cli
    from lean_computer_use_mcp.diagnostics import UPSTREAM_PINNED_VERSION

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--version"])
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert f"lean-computer-use {__version__}" in out
    assert f"pinned upstream {UPSTREAM_PINNED_VERSION}" in out
