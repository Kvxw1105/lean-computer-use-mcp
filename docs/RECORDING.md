# Record, Compile & Replay (Windows)

Status: implemented as CLI commands + a `record/` library; unit-tested with
fake hooks and the fake upstream. Live recording is Windows-only; replay works
on any platform where the facade runs.

## What this is

An equivalent of the official Codex "Record & Replay" skill for Windows.
A user demonstrates a workflow once; the recorder turns the demonstration into
an editable, intent-based skill; later runs replay it with far less context
than a fresh model run would need.

The official feature (macOS only) does **not** store a pixel macro. It
observes actions and window content, then drafts a text skill describing when
to use the workflow, what inputs it needs, the steps, and how to verify the
result. Replay uses the skill as context and re-locates targets live.

This project mirrors that design:

| Stage | Official Codex | lean-computer-use-mcp |
|---|---|---|
| Capture | screen + window observation | global mouse/keyboard hooks + periodic UIA element snapshots |
| Artifact | editable SKILL.md text | editable `SKILL.md` + JSON `recording.json` (no screenshots) |
| Replay | model + Computer Use adapt | facade `cu_observe` + `cu_act`, element-first, coordinates only as fallback |
| Privacy | avoid recording secrets | same: hooks capture coordinates and window titles, never screenshots or clipboard |

## CLI usage

```sh
# 1. Demonstrate the workflow (stop with Ctrl+Shift+R or --seconds)
lean-computer-use record --app JianYing --out recordings/font-size.json --description "Reduce subtitle font size"

# 2. Compile an editable skill
lean-computer-use compile --in recordings/font-size.json --out-dir skills/recorded/subtitle-font-size

# 3. Preview, then execute (content-level steps ask for confirmation)
lean-computer-use replay --in recordings/font-size.json --dry-run
lean-computer-use replay --in recordings/font-size.json --run
# --yes pre-confirms all content-level steps; window focus never prompts
```

## How the recorder works

- A low-level `WH_MOUSE_LL` / `WH_KEYBOARD_LL` hook captures mouse clicks,
  wheel deltas and key events with timestamps and the foreground window
  title/rect (`record/win_hooks.py`, Windows-only).
- A screen-edge glow (blue-purple, click-through, always-on-top) is shown
  while a live session records, so you always know the demonstration is being
  captured (`record/overlay.py`, Windows-only). It is a plain layered popup
  window: it never consumes input and never shows up in recordings (they are
  text-only). Disable it with `--no-overlay` or by using `--fake`.
- Live feedback: every recognized step is printed with a `[live]` prefix as
  soon as the event stream supports it, so a fast demonstration is never
  silently missed. Typing steps flush after a short pause (the same rule the
  final artifact uses).
- Action-triggered snapshots: mouse clicks and wheel actions wake the sampler
  immediately (throttled to ~0.4 s), so a click always lands near a fresh
  element table instead of waiting for the next scheduled snapshot.
- `uncertain` flag: a click recorded without any matching accessibility
  element is marked `uncertain` in `recording.json`, shown in the compile
  evidence report and annotated in the compiled `SKILL.md`.
- A sampler thread reads the app's UIA tree every few seconds
  (`record/recorder.py`); only parsed controls plus character/byte counts are
  stored - raw trees and screenshots are never persisted.
- `record/steps.py` converts events into intent steps:
  - clicks are matched to the element frame containing the point (semantic
    target + coordinates as fallback);
  - wheel events are coalesced into scroll steps;
  - printable keys are grouped into `type_text`, combos (Ctrl+S) become
    `press_key`, Enter is a commit-like press;
  - the recorder's own stop hotkey is filtered out.

Limitations (v1): Latin keyboard layout only; IME-composed text (e.g. Chinese
input) is not captured - add it to the generated `SKILL.md` or `recording.json`
by hand; drag steps are not recorded; cross-app workflows replay per-app.

## Library store confirmation

`compile --library <file>` prints one evidence line per step before storing
anything:

```text
  2. Click 'Text' (button)  [element] [coords] [window]
```

Badges are `[element]` (semantic target matched), `[coords]` (recorded
coordinate fallback), `[window]` (window context) and `[uncertain]` (no
semantic element at record time). The CLI then asks `Store this recording in
the library? [y/N]` and only learns components/templates after an explicit
`y` (use `--yes` to skip the prompt for scripting). Declining leaves the
library untouched.

## Execution indicator (agent acting)

`serve` can glow while `cu_act`/`cu_batch` executes an action so you can see
when an agent is controlling the desktop. It is **off by default**; enable it
with `serve --act-overlay` or `LEAN_CU_ACT_OVERLAY=1`. The overlay is hidden
around every upstream snapshot (state reads and post-action refreshes), so
agent-visible trees, screenshots and state fingerprints stay unpolluted; only
the in-flight refresh image byte count may include the glow, and those bytes
are never stored or shown.

## Replay semantics

- Every content-level step re-observes with `cu_observe` (preset `control`,
  `vision=auto`, `max_results=40`) so state gates and metrics apply.
- The recorded target is matched by role/name against the live tree; when the
  tree is thin, vision elements are used and the click goes through the
  real-input path.
- If no semantic match exists, recorded coordinates are replayed through the
  facade's `click_method="real"` (this is how custom-rendered UIs such as
  JianYing are handled).
- Window focus steps (`focus_window`) never prompt; content-level steps prompt
  by default and `--yes` pre-confirms the whole plan. Declining a step fails
  that step without touching the desktop.

## Cost model

One recorded skill replaces repeated discovery runs. A replay step costs one
`cu_observe` (control preset, no screenshot) + one `cu_act` (delta only),
instead of a full upstream snapshot (~437 KB model-visible per observation on
the benchmarked ChatGPT window) plus screenshots. The skill text is the only
new context per run; `recording.json` holds zero image bytes by design.

Run `--metrics-path metrics.jsonl` on record/compile/replay to get verifiable
numbers: `text_chars`, `image_bytes`, `nodes` are recorded for `cu_record`,
`cu_skill_compile` and every `cu_observe`/`cu_act` during replay.

## From task memory to procedural memory

`compile --library <file>` also distills the recording into atomic components
and a task template (see `docs/MEMORY.md`). `recall --intent ...` then maps a
new intent onto learned components, and `replay --library` feeds every
execution back (hits/misses/effects).

## Files

- `src/lean_computer_use_mcp/record/` - model, keys, steps, compile, replay,
  recorder, win_hooks.
- `examples/fixtures/recording_font_size.json` - sanitized example recording.
- Tests: `tests/test_record_*.py`, `tests/test_real_input_click.py` (focus).