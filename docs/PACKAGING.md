# Packaging & distribution (M4)

How to install, register, and bundle `lean-computer-use-mcp` on a clean
machine, and how to ship recorded skills between machines.

## Install

The package ships a console entry point (`lean-computer-use`) and has no
dependency on the upstream CLI at install time - it only needs
`open-computer-use` at runtime, and even that is optional (`serve --fake`,
`doctor` report it as missing).

From the repository (development):

```sh
uv sync --all-extras
uv run pytest          # full suite: 459 passed, 1 skipped (2026-08-11)
```

From a clean machine (no checkout needed):

```sh
# pip
pip install "git+https://github.com/Kvxw1105/lean-computer-use-mcp"

# uv (installs the console script into a managed tool env)
uv tool install "git+https://github.com/Kvxw1105/lean-computer-use-mcp"

# ephemeral, no install at all
uvx --from "git+https://github.com/Kvxw1105/lean-computer-use-mcp"     lean-computer-use doctor
```

Smoke test without a desktop:

```sh
uvx --from . lean-computer-use --help
uvx --from . lean-computer-use serve --fake   # answers the MCP initialize handshake
```

The initialize response identifies the facade in `result.instructions`
(`lean-computer-use-mcp v<version>`); `result.serverInfo.version` is the MCP
SDK's version, not the facade's.

## Registering the MCP server

The server speaks MCP over stdio. Client schemas differ:

- **zcode** (`~/.zcode/cli/config.json`): servers are **nested** under
  `mcp.servers` (not top-level `mcpServers`), the command is
  `uv.exe run --project <PROJECT_DIR> lean-computer-use serve`, and a
  `"timeoutMs": 60000` avoids slow-start kills.
- **Claude Desktop** (`%APPDATA%\Claude\claude_desktop_config.json`): the
  object is top-level `mcpServers`; `uvx --from <url> lean-computer-use serve`
  works without a checkout.

See [examples/mcp-server-config.sample.json](../examples/mcp-server-config.sample.json)
for both shapes with placeholders. Vision API endpoints are configured in
`~/.lean-cu/config.json` (`lean-computer-use config` / `config-ui`), never in
the client config; keys stay on the machine.

## Skill & plugin packaging

`compile` turns a recording into a portable skill:

```text
skills/recorded/<name>/SKILL.md       # human/model-readable steps
<out-dir>/recording.json              # structured replay source (no images)
```

- Skills are text-only and safe to copy to another machine or commit;
  `recording.json` holds element tables and coordinates, never screenshots
  or raw accessibility trees.
- Replaying a skill on another machine requires the same app to be running
  there; steps resolve by role/name first and fall back to recorded
  coordinates through the real-input path.
- `compile --library <file>` also produces the procedural-memory library
  (`memory/components.json`); ship it together with the skill when you want
  `recall` composition on the target machine.

MCP "plugins" are just a registered server: point the client at
`lean-computer-use serve` (see above) and the `cu_*` tools appear. There is
no plugin binary to build; the wheel is the artifact:

```sh
uv build          # dist/lean_computer_use_mcp-0.2.0.tar.gz + .whl (verified 2026-08-11)
```

The package is **not yet published to PyPI**; installs come from git until the
first release. Publishing is `uv build && uv publish` after the real-machine
checklist (docs/VERIFICATION.md) passes.

## Verification

`benchmarks/success_matrix.py` is the release gate: it runs the facade
scenarios in fake mode on any machine (no desktop, no keys) and asserts
zero image bytes reach model-visible output:

```sh
uv run python benchmarks/success_matrix.py
```

The upstream CLI is **pinned** (currently `0.3.1`, see
`diagnostics.UPSTREAM_PINNED_VERSION`): `doctor` warns when the installed
version drifts, and `benchmarks/verify_pin.py` checks that the regression
fixtures still match their recorded hashes (CI runs this on every push; the
binary check is opt-in because CI has no desktop):

```sh
uv run python benchmarks/verify_pin.py
uv run python benchmarks/verify_pin.py --binary open-computer-use
```

After an upstream upgrade: verify the new version, update the pin, and
regenerate fixture hashes with `--regenerate`.

Real-desktop runs (`--real`) are documented in `docs/BENCHMARKS.md`.
