# Changelog

All notable changes to lean-computer-use-mcp. Versions follow the package
`__version__` and the pinned upstream CLI (see `UPSTREAM_PINNED_VERSION`).

## 0.2.0 (2026-08-11)

Phase-2 hardening and release engineering. Test suite: 459 passed, 1 skipped
(ruff clean; CI runs pytest on Windows + Ubuntu with LF fixtures).

### Fixes after the phase-2 review (P1)
- Fixture pin is line-ending safe: `.gitattributes` forces LF for
  `examples/fixtures/` and `verify_pin` hashes CRLF-normalized bytes, so
  Windows checkouts no longer fail the pin check. (`1c26a4b`)
- Screenshot fingerprint folds in the window screen rect, so a self-drawn-app
  window moved at the same size now invalidates stale state (signal `image`).
  (`1c26a4b`)
- IME recording re-samples composition after fast short pinyin so late
  commit/composition text is not lost; raw key sequences remain the replay
  fallback. (`1f92da6`)

### Tools & facade
- `cu_window`: list/activate/maximize with occlusion detection (occlusion is
  a status, not an error) and ambiguity handling that returns candidates
  instead of guessing. (`a23a858`)
- Real-input fallback: when the upstream real-click path fails, the facade
  degrades to its own `CtypesWin32Input` under the same coordinate contract;
  upstream errors are structured with a `reason` field. (`3921f91`)
- `doctor`: binary, pinned upstream version, DPI awareness, target-window
  visibility/ambiguity checks. (`bd19743`, `ae77bd0`)
- `serve` initialize response advertises the facade version in
  `instructions`. (`bc67d40`)
- CLI `--version` prints the facade and pinned-upstream versions.

### Recording & replay
- IME capture: Chinese composition text lands in `type_text` steps; raw key
  sequences are kept as a replay fallback. (`87bee8e`)
- Drag recording: press-move-release becomes one `drag` step with from/to
  coordinates; sub-threshold jitter stays a click. (`8f5e4ce`)
- Replay auto-recovery: STALE_STATE re-observes and retries up to 3 times
  per step. (`212af5c`)
- Screenshot fingerprint gate for trivial-tree (self-drawn) apps so stale
  detection cannot be defeated by an empty UIA tree. (`4baf9d1`)

### Memory
- Text-LLM curation (enrich / refine / recall --llm) routes through the
  vision `ProviderPool` failover: 401/403 cool down 10 minutes, transient
  failures 30 seconds, keys never logged. (`78fc77d`)

### Release engineering
- `docs/PACKAGING.md`, MCP registration sample
  (`examples/mcp-server-config.sample.json`), success-rate matrix
  (`benchmarks/success_matrix.py`, 8/8 fake mode), upstream version pin +
  fixture hash manifest (`benchmarks/verify_pin.py`), cross-app workflow
  pattern (`docs/WORKFLOWS.md`), real-machine verification checklist
  (`docs/VERIFICATION.md`). (`bc67d40`, `ae77bd0`, `5b5eed9`)
- CI now runs the success matrix and fixture-hash checks on every push.

## 0.1.0 (2026-08-04 to 2026-08-08)

Phase-1 base: low-context, state-safe MCP facade over Open Computer Use.

- `cu_find_app` / `cu_observe` / `cu_act` / `cu_batch` with per-app state
  store, freshness gates, and STALE_STATE rejections; metrics JSONL with
  text/image/node counters.
- Vision fallback chain (OCR -> optional LLM escalation) with
  multi-endpoint provider failover and local config store
  (`~/.lean-cu/config.json`), CLI + web panel (`config` / `config-ui`).
- Record -> compile -> replay CLI with live step stream, recording overlay,
  commit-like step confirmation, and a procedural memory library
  (components, templates, recall, LLM-assisted curation).
- Measured: 99.8% model-visible context reduction vs the default upstream
  snapshot; E10 12/12 real replay; E12 72.6% lower second-task context.
