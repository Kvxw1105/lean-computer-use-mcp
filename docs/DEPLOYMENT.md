# Deployment Guide

This document explains how to install `lean-computer-use-mcp` on a fresh
Windows machine and register it as an MCP server in your local agent client.
It is the companion to the one-command helper script:

```powershell
.\scripts\deploy.ps1 -ProjectDir D:\repo\lean-computer-use-mcp -Client codex
```

## 1. Prerequisites

- Windows 10/11, 64-bit
- [uv](https://docs.astral.sh/uv/) on `PATH` (`winget install astral-sh.uv`)
- Git

## 2. Install the project

```powershell
git clone https://github.com/Kvxw1105/lean-computer-use-mcp
cd lean-computer-use-mcp
uv sync --all-extras
uv run pytest          # expect: 491 passed, 1 skipped
```

## 3. Choose the upstream engine (default: auto)

The facade talks to one of two desktop-control engines behind the scenes:

| Engine | Install | Notes |
|---|---|---|
| `cua-driver` (Hermes, **preferred**) | `winget install TryCua.Cua` or the installer from https://github.com/trycua/cua | Background-first input (no focus stealing), structured refusals, foreground escalation. Daemon auto-starts. |
| `open-computer-use` (npm) | `npm install -g open-computer-use` | The phase-1 upstream; fine as a fallback. |

`--upstream` accepts `auto` (default), `cua-driver`, or `open-computer-use`.

- `auto` resolves to **cua-driver whenever its binary is present**, otherwise
  open-computer-use. Run `lean-computer-use doctor` to see which engine the
  `auto` mode would pick today.
- You can pin explicitly: `--upstream cua-driver` forces Hermes and fails fast
  if the binary is missing.

## 4. Configure the vision endpoint

The facade vision engine (OCR -> optional LLM upgrade) is what text-only
models use to "see". Point it at your provider in `~/.lean-cu/config.json`:

```json
{
  "vision": {
    "engine": "llm",
    "providers": [
      { "api_base": "https://...", "api_key": "sk-...", "model": "..." }
    ]
  }
}
```

If you skip this, `cu_observe` falls back to the accessibility tree only.

## 5. Register as an MCP server

The registration **location depends on your client**. This is the #1 source
of "I do not see it" confusion - verify which client you run:

| Client | Config file | Section |
|---|---|---|
| Official Codex CLI/Desktop | `%USERPROFILE%\.codex\config.toml` | `[mcp_servers.lean-computer-use]` |
| **Codex++ / StepFun variant** | **`C:\AppData\.codex\config.toml`** | `[mcp_servers.lean-computer-use]` |
| zcode | `%USERPROFILE%\.zcode\cli\config.json` | `mcp.servers.lean-computer-use` |

> **Gotcha (verified 2026-08-11):** the StepFun/Codex++ desktop build uses
> `C:\AppData\.codex` as its config home, NOT `%USERPROFILE%\.codex`. Writing
> to the user-profile path silently does nothing for that client.

Manual TOML entry (official Codex or Codex++ variant):

```toml
[mcp_servers.lean-computer-use]
command = "C:\\Users\\<you>\\.local\\bin\\uv.exe"
args = ["run", "--project", "D:\\repo\\lean-computer-use-mcp", "lean-computer-use", "serve"]
```

Notes:

- Omit `--upstream` to use `auto` (Hermes when present). Add
  `--upstream cua-driver` to pin Hermes.
- Use absolute paths for both `command` and the `--project` arg.
- zcode also accepts a `timeoutMs` field (e.g. `60000`).

After registering: **fully quit and relaunch the client** (closing the window
is not enough). Then check the MCP servers list - `lean-computer-use` should
appear.

## 6. Verify

```powershell
# 1) prerequisites + engine resolution
lean-computer-use doctor --upstream auto

# 2) MCP handshake (initializes the server like a client would)
  @'
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"smoke","version":"1.0"}}}
{"jsonrpc":"2.0","method":"notifications/initialized"}
{"jsonrpc":"2.0","id":2,"method":"tools/list"}
  '@ | uv run --project <project-dir> lean-computer-use serve 2>$null
```

A healthy server replies with `serverInfo.name = "lean-computer-use"` and the
six `cu_*` tools.

## 7. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Server not in MCP list after restart | Config written to the wrong home | Check section 5 table; Codex++ variant uses `C:\AppData\.codex\config.toml` |
| `doctor` says cua-driver missing | Hermes engine not installed | Install TryCua.Cua, or let auto fall back |
| MCP server errors on connect | `uv` or project path wrong | Use absolute paths; run the handshake test above |
| `cu_observe` returns empty trees | Self-drawn app with no UIA tree | Configure the vision endpoint (section 4) |
| Vision refused / provider errors | Missing or wrong `~/.lean-cu/config.json` | Re-check providers and API key |

## 8. Next steps

- Run the real-desktop verification checklist: `docs/VERIFICATION.md`
- Check facade metrics: `cu_metrics` after any session
- Report issues upstream: https://github.com/Kvxw1105/lean-computer-use-mcp/issues