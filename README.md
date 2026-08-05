# lean-computer-use-mcp

Low-context, state-safe MCP facade over [Open Computer Use](https://github.com/iFurySt/open-codex-computer-use) for inexpensive agent models such as GPT-5.6 Luna.

> Status: M1 verified against the real Windows upstream (`cu_find_app`, `cu_observe`, metrics, `cu_act` stale-rejection and real-action paths, including a JianYing subtitle resize). V2 vision fallback and `vision=auto` LLM escalation are live. Record & Replay (demonstrate a workflow once, replay it cheaply) is implemented as CLI commands. Not yet recommended for production use.

## Why this project exists

Open Computer Use works, but every snapshot includes a screenshot and every action returns a full refreshed UI state. On Windows we measured:

| Payload | Size |
|---|---:|
| Default `get_app_state` tree text | ~54,000 characters |
| Compact `READ` tree text | ~2,300 characters |
| Screenshot (Base64) | ~405,000 characters, unchanged between presets |

A skill can reduce how often a model observes, but it cannot remove screenshots, action-returned full states, or duplicated tool schemas from the model's context. This project puts a bounded proxy between the model and the upstream server so the model sees only what it needs to complete the task.

Measured on the real desktop (ChatGPT window, 2026-08-05): the default upstream
snapshot costs ~437,779 model-visible characters (55,543 text + 382,236 image
Base64) and 460 nodes; the facade's `cu_observe` returns an 820-character
payload with 3 controls and no image, a **99.8% reduction in model-visible
context**. See [docs/BENCHMARKS.md](docs/BENCHMARKS.md) for the full table and
reproduction commands.

## Procedural memory (atomic components)

Beyond whole-task replay, `compile --library` and `recall` learn **atomic
components** (e.g. `jianying::click::button::font-size`) and task templates,
then compose new tasks from old building blocks. Replay feeds results back:
successes raise popularity and teach effects, failures raise staleness.
`refine` lets the model curate the library (aliases, merges, descriptions,
template generalizations) with a human-reviewed apply step.
See [docs/MEMORY.md](docs/MEMORY.md).

## Record & Replay

Demonstrate a workflow once, then replay it with far less context:

```sh
lean-computer-use record --app JianYing --out recordings/font-size.json
lean-computer-use compile --in recordings/font-size.json --out-dir skills/recorded/subtitle-font-size
lean-computer-use replay --in recordings/font-size.json --run
```

The recorder captures mouse/keyboard events plus periodic element snapshots
(no screenshots), compiles an editable, intent-based `SKILL.md` (like the
official macOS-only Codex Record & Replay), and replay re-locates targets in
the live tree - coordinates are only a fallback for custom-rendered UIs.
See [docs/RECORDING.md](docs/RECORDING.md).

## Architecture

```mermaid
flowchart LR
    Model[Low-cost model e.g. Luna] --> Skill[lean-computer-use-luna skill]
    Skill --> Facade[lean-computer-use-mcp]
    Facade --> Cache[Local state + image cache]
    Facade --> Upstream[open-computer-use MCP/CLI]
    Upstream --> Windows[Windows UIA / screenshot]
```

The facade owns:

- compact, query-relevant accessibility output instead of full trees;
- `state_id`-based freshness and stale-state rejection;
- local screenshot caching and on-demand cropping;
- delta summaries after actions instead of full refreshed states;
- per-call metrics for honest before/after cost measurement.

## Repository layout

```text
docs/            DESIGN, PROTOCOL, SECURITY, BENCHMARKS
src/             Python MCP server (incl. record/compile/replay CLI)
tests/           unit tests and fixtures
skills/          Codex skill that drives the facade
benchmarks/      benchmark scenario definitions
config/          example agent configuration
```

## Development

```sh
git clone https://github.com/<you>/lean-computer-use-mcp.git
cd lean-computer-use-mcp
uv sync --all-extras
uv run pytest
```

Run a demo server with a fake upstream client (no desktop access):

```sh
uv run lean-computer-use serve --fake
```

## Documentation

- [Design](docs/DESIGN.md)
- [Protocol](docs/PROTOCOL.md)
- [Security](docs/SECURITY.md)
- [Benchmarks](docs/BENCHMARKS.md)

## License

MIT