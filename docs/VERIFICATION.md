# Real-machine verification checklist

Everything in this document needs a live Windows desktop with the pinned
upstream CLI (`open-computer-use` 0.3.1) and a signed-in application. Run the
items top to bottom; each block says what to do, what to expect, and where to
look when it fails. This is the only part of the project that cannot be
verified in CI or on a fake upstream.

Setup:

```sh
uv sync --all-extras
uv run python benchmarks/verify_pin.py --binary open-computer-use
# expect: pinned 0.3.1, fixture hashes ok, installed upstream matches
```

## Guided runner

`lean-computer-use verify` walks the ten items below interactively
(`Enter` = pass, `fail: <note>`, `skip: <note>`), runs a preflight
(upstream binary + pinned version), and writes the dated report to
`benchmarks/results/verification-<date>.md` (gitignored) with the fail hint
for every failed item:

```sh
uv run lean-computer-use verify
```

Use it instead of hand-editing the report file. The items below remain the
source of truth for what each check means and where to look on failure.

## 1. Install & registration smoke

- `uvx --from . lean-computer-use --version` prints `0.2.0 (pinned upstream 0.3.1)`.
- Register the server in `~/.zcode/cli/config.json` (`mcp.servers`,
  `uv.exe run --project <repo> lean-computer-use serve`, `timeoutMs: 60000`,
  see `examples/mcp-server-config.sample.json`), then ask the agent to call
  `cu_find_app` and `cu_observe`. Expect real apps with visible windows.
- Failure: server dies on startup -> run `lean-computer-use serve` in a
  terminal and read the traceback; the facade must not require a desktop for
  `--version`/`doctor`.

## 2. doctor

- `uv run lean-computer-use doctor --app explorer`
- Expect: `upstream_binary: ok`, `upstream_version: ok 0.3.1 (pinned 0.3.1)`,
  `window: ok` (or `warn` with ambiguity/occlusion hints), `window_dpi: ok`
  (96/144 ...), `dpi_awareness: ok per-monitor`.
- Failure: version mismatch -> upgrade or re-pin; window `fail` -> the app
  really has no visible window.

## 3. IME Chinese input recording

- `lean-computer-use record --app <editor>` (e.g. a chat box), type a Chinese
  sentence with the IME (pinyin -> candidates -> space), stop with
  Ctrl+Shift+R.
- Open the recording JSON: the typing step has `action: "type_text"` and
  `value` = the composed Chinese text (and `ime_text`/`ime_keys`).
- If `value` is null: composition sampling failed; the step still carries
  `ime_keys` and replay falls back to the raw key sequence. Report this to
  the maintainers with the IME (e.g. Microsoft Pinyin / Sogou).

## 4. Drag recording

- Record a drag in a timeline/upload surface (JianYing timeline or a file
  upload zone): press, move >= 3 px, release.
- The recording JSON contains one `action: "drag"` step with `x`/`y` ->
  `to_x`/`to_y`; a press without movement stays `click`.
- Replay the recording with `replay --run --yes`: the drag executes through
  the facade's coordinate drag action.

## 5. Real-input fallback (T2)

- On a self-drawn app (JianYing), run a replay whose clicks go through
  `click_method="real"` while the upstream real-click path is healthy, then
  look at the `cu_act` responses: `real_input.path` is `upstream`.
- To exercise the fallback, point the facade at a broken upstream real-input
  (e.g. temporarily rename the upstream binary) and repeat: expect
  `real_input.path: "fallback"` plus `real_input.upstream_error`, and the
  metrics row with `real_input_fallback`.
- Honest check: after a fallback click, verify visually (screenshot diff /
  element table) that the action actually landed - the upstream `ok` flag
  alone is not trustworthy for custom-rendered apps.

## 6. Window tools: ambiguity & occlusion

- Open two windows of the same app (e.g. two browser windows with the same
  title). `cu_window(app, "list")` returns >= 2 candidates and
  `ambiguous: true`; `cu_window(app, "activate")` without a title returns
  `AMBIGUOUS_TARGET` with the candidate list (never a random pick).
- Cover the target window with another window: `cu_window(app, "list")`
  reports `occluded: true` on the main candidate (`covered_by` lists the
  cover). This is a status, not an error.

## 7. Screenshot fingerprint gating (T4, self-drawn apps)

- On JianYing: observe twice without touching the app -> second `cu_act`
  with the first `state_id` must NOT be stale.
- Resize or restructure the window (new panel), then `cu_act` with the old
  `state_id` -> expect `STALE_STATE` (signal `tree` or `image`).
- Move the window at the same size, then `cu_act` with the old `state_id` ->
  expect `STALE_STATE` (signal `image`): the image fingerprint folds in the
  window screen rect, so a translated window invalidates even when the
  pixels are identical.

## 8. Replay auto-recovery (T5)

- Record a short workflow, then change the app state (open a modal) and
  `replay --run`. Expect STALE_STATE -> re-observe -> retry, printed as
  `(auto-recovered Nx)`; a persistent stale condition fails the step after
  3 retries (hard cap), it never retries silently forever.

## 9. Cross-app chain (WORKFLOWS.md pattern)

- Record two skills (e.g. JianYing export -> browser upload), replay them
  back to back, and hand the exported file path through the agent context.
- Expect each skill to focus its own app and keep its own confirmation
  prompts; the second skill receives the path from the first replay's
  observed state.

## 10. End-to-end metrics honesty

- Run a full record -> compile -> replay with `--metrics-path metrics.jsonl`
  and check: replay rows carry `image_bytes: 0` (screenshots stay local),
  `text_chars > 0`, and `nodes > 0`; no row contains `STALE_STATE` unless the
  app really changed between observe and act.

After each item, append the result (pass/fail + notes) to
`benchmarks/results/verification-<date>.md` (gitignored) so the next release
can compare regressions.
