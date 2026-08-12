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

### Standby mode (global hotkey, no `--app` needed)

```sh
# Wait for Ctrl+Shift+Space, record the foreground window, then wait again
lean-computer-use record --standby
# Pick a different hotkey (e.g. when Ctrl+Shift+Space is already taken)
lean-computer-use record --standby --hotkey ctrl+alt+space
```

- The hotkey is registered system-wide (`RegisterHotKey`), so it fires while
  any app has focus. Press it to record the current foreground window;
  `Ctrl+Shift+R` stops and saves, then standby resumes.
- The combination is user-configurable (`--hotkey ctrl+shift+space` is the
  default; modifiers `ctrl`/`shift`/`alt`/`win`, keys: letters, digits,
  `f1`-`f24`, `space`, `enter`, `tab`, `esc`, arrows, ...). At least one
  modifier is required.
- **Conflicts**: when the combination is already registered by another
  program, `RegisterHotKey` fails and standby prints the conflict with a
  working alternative - it never silently steals the key.
- `Ctrl+C` quits standby. `--fake` runs one dry pass and exits (used by
  tests); Windows-only for real recording.

## How the recorder works

- A low-level `WH_MOUSE_LL` / `WH_KEYBOARD_LL` hook captures mouse clicks,
  wheel deltas and key events with timestamps and the foreground window
  title/rect (`record/win_hooks.py`, Windows-only).
- IME capture: every key event also samples the foreground window's input
  context (`ImmGetCompositionStringW` for the composition and result
  strings, Windows-only). While the IME is open, the step builder groups the
  keys into one `type_text` step whose value is the real composed text
  (e.g. `??` for pinyin `nihao` + Space) and keeps the original key
  sequence (`ime_keys`) as the replay fallback. When sampling cannot recover
  text (IME closed at replay time, repeated identical commits), the step
  keeps `value` empty and replay presses the original keys in order, which
  is semantically equivalent for Chinese input.
- A screen-edge glow (blue-purple, click-through, always-on-top) is shown
  while a live session records, so you always know the demonstration is being
  captured (`record/overlay.py`, Windows-only). The glow is animated: a soft
  wave (default 2.5 waves, ~0.5 Hz cycle, +/-15% alpha) travels continuously
  around the four edges so the active state reads as alive without being
  noisy. It renders as four thin layered edge windows (top/bottom/left/right,
  14 px band) instead of one full-screen bitmap, so each 24 fps frame is only
  ~130k px (~40x fewer than a full-screen frame at 2880x1800) and updates are
  reliable. The windows are created with `WS_EX_TOPMOST` in the create-time
  ex-style (some systems ignore `SetWindowPos(HWND_TOPMOST)` silently). It
  never consumes input and never shows up in recordings (they are text-only).
  Disable it with `--no-overlay` or by using `--fake`.
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
  - a left press followed by moves of at least 3 screenshot pixels (jitter
    guard) is one `drag` step carrying `x`/`y` -> `to_x`/`to_y`; a press with
    no real movement stays a `click`; moves while any other button is held
    are ignored;
  - during a drag, `mouse_move` events closer than 2px or faster than
    30ms are merged into one recorded event, so long timeline/upload
    drags stay small in `recording.json` while the step keeps exact
    press/release coordinates (the release position always comes from
    the `mouse_up` event);
  - printable keys are grouped into `type_text`, combos (Ctrl+S) become
    `press_key`, Enter is a commit-like press;
  - the recorder's own stop hotkey is filtered out.

Limitations (v1): IME text is captured best-effort via composition sampling
(real Chinese IME verification is pending on a user's machine; the raw-key
fallback covers sampling gaps); drag recording works on the hook level
(press-move-release) and real-desktop drag verification is pending on a
user's machine; cross-app workflows replay per-app.

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
when an agent is controlling the desktop; `replay --run --act-overlay` and
`recall --run --act-overlay` show the same animated glow while steps execute.
It is **off by default**; enable it with `--act-overlay` or
`LEAN_CU_ACT_OVERLAY=1`. The overlay is hidden
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
- A `STALE_STATE` rejection is not fatal: the runner re-observes the app
  (same `preset="control"`, `vision="auto"`, `max_results=40`) and retries
  the step with the fresh `state_id`, up to a hard cap of 3 retries per
  step (`max_stale_retries` on `ReplayRunner`). Still-stale steps fail the
  run and are reported with the retry count.
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
