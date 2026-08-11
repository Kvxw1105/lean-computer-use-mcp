# Protocol

## 0. Backends

The facade talks to a desktop-automation backend behind one interface
(`UpstreamClient`). Two backends are supported; the configured one is a
deployment choice, not a model-visible one.

- **open-computer-use** (default): the upstream this project was built on.
  Output and errors as described below.
- **cua-driver** (optional): the open-source background computer-use runtime
  from `trycua/cua` (MIT; also the engine behind Hermes' computer_use).
  Model-visible element tables are rendered in the same format, so all
  downstream behavior (state gate, delta, vision fallback, replay) is
  identical. Behavioral differences:

  - Actions are background-first: no cursor move, no focus steal. The only
    focus-stealing path is the explicit `cu_window activate` tool.
  - Apps/windows are resolved by pid/window; the largest visible window wins
    (same rule as `find_main_window`).
  - When a target only accepts real foreground input, the backend refuses
    with a structured refusal instead of failing silently. The facade maps it
    to `UPSTREAM_ERROR` with `reason` one of `background_unavailable` /
    `background_occluded` / `window_not_found` / `stale_snapshot` / `timeout`.
    A `background_unavailable` result is a state, not a crash: re-observe and
    decide whether an explicit foreground path is acceptable.
  - `cu_observe` screenshots come from the backend at native DPI (no DPI
    scaling offsets); the coordinate contract (window-local screenshot pixels)
    is unchanged.


### 1.1 `cu_find_app`

Inputs:

| Field | Type | Default | Meaning |
|---|---|---|---|
| `query` | string | null | Optional name/term filter |

Response:

```json
{
  "apps": [
    {"name": "Microsoft Edge", "running": true, "visible_windows": 2},
    {"name": "ChatGPT", "running": true, "visible_windows": 1}
  ]
}
```

Only running apps with at least one visible window are returned by default.

### 1.2 `cu_observe`

Inputs:

| Field | Type | Default | Meaning |
|---|---|---|---|
| `app` | string | required | Target app |
| `intent` | string | "" | Free-text purpose used for control ranking |
| `output_mode` | string | "controls" | `controls`, `reading`, `visual`, `full` |
| `include_screenshot` | bool | false | Return image data (cropped when possible) |
| `max_results` | int | 20 | Max controls returned |
| `preset` | string | null | `control`, `read`, `expand`, `deep` |
| `vision` | string | "auto" | `auto` (vision when the UIA tree is trivial, with OCR->LLM auto-escalation), `on` (always), `off` (never) |

Preset budgets:

| Preset | nodes | depth | text |
|---|---|---|---|
| `control` | 80 | 8 | 160 |
| `read` | 160 | 10 | 600 |
| `expand` | 320 | 16 | 900 |
| `deep` | 800 | 28 | 1200 |

Response:

```json
{
  "ok": true,
  "state_id": "a1b2c3d4",
  "app": "ChatGPT",
  "window_title": "ChatGPT",
  "focused_element": "按钮 打开个人资料菜单",
  "controls": [
    {"index": "12", "role": "按钮", "name": "最小化", "actions": ["Invoke", "ScrollIntoView"]},
    {"index": "13", "role": "按钮", "name": "恢复", "actions": ["Invoke", "ScrollIntoView"]}
  ],
  "truncated": {"tree": false, "text": false},
  "screenshot": {"path": "C:/Users/<you>/AppData/Local/Temp/lean-cu-xxx/a1.png", "bytes": 405000, "data": null},
  "vision": {
    "engine": "winrt_ocr",
    "triggered": true,
    "reason": "empty_tree",
    "elements": [{"role": "text", "text": "Export", "frame": {"x": 1820, "y": 24, "width": 44, "height": 28}, "confidence": 0.96}],
    "image_bytes": 94210,
    "latency_ms": 180
  }
}
```

`include_screenshot=false` always returns `data: null`. `output_mode="visual"` implies `include_screenshot=true` and returns a cropped image when a target frame is known.

`vision` is only populated when a vision engine is configured (`LEAN_CU_VISION_ENGINE`: `winrt_ocr` | `rapidocr` | `llm` | `fake`). It never contains image data: elements are a compact text table in the same screenshot pixel space that `cu_act` coordinates use. `llm` coordinates are rescaled from the downscaled image back to the original screenshot space. When no engine is configured, `"engine": null, "triggered": false` is returned and the call still succeeds.

In `auto` mode, when the base engine returns fewer than `LEAN_CU_VISION_UPGRADE_MIN_ELEMENTS` elements (default 3), the facade may escalate to the engine named by `LEAN_CU_VISION_UPGRADE_ENGINE` (default `none`; set to `llm` to enable). Escalation is throttled: at most one upgrade per `LEAN_CU_VISION_UPGRADE_COOLDOWN_SECONDS` (default 60) per process. On escalation the response uses the upgrade table and adds `"reason": "auto_escalate"`, `"escalated": true`, `"escalated_from": <base engine>`, plus an `"upgrade"` object; a suppressed escalation (cooldown or unavailable engine) is reported in `"upgrade": {"suppressed": true, "reason": ...}` and the base table is kept. The intent is passed to the upgrade engine as the grounding hint. Metrics add `vision_upgrades` per call and `vision_upgrade_calls` in the summary.

### 1.3 `cu_act`

Inputs:

| Field | Type | Default | Meaning |
|---|---|---|---|
| `app` | string | required | Target app |
| `state_id` | string | required | Snapshot the plan was made against |
| `action` | string | required | `click`, `drag`, `set_value`, `scroll`, `type_text`, `press_key`, `secondary_action` |
| `element_index` | string | null | Required for indexed actions |
| `value` | string | null | For `set_value` / `type_text` |
| `key` | string | null | For `press_key` |
| `direction` | string | null | For `scroll` |
| `pages` | float | 1.0 | For `scroll` |
| `click_method` | string | null | `auto`, `accessibility`, `app_post`, `real` |
| `mouse_button` | string | null | `left`, `right`, `middle` |
| `secondary_action` | string | null | For `secondary_action` |
| `x` | int | null | For `click`: screenshot pixel X (mutually exclusive with `element_index`) |
| `y` | int | null | For `click`: screenshot pixel Y |
| `from_x`, `from_y` | int | null | For `drag`: start point, screenshot pixels |
| `to_x`, `to_y` | int | null | For `drag`: end point, screenshot pixels (all four `from_*`/`to_*` required) |
| `commit` | bool | false | Marks a commit-like action; facade returns `COMMIT_UNCERTAIN` on ambiguity |


`click_method="real"` is a Windows-only opt-in that injects real input
(`SetCursorPos` + `SendInput`) instead of upstream window messages. It exists
because custom-rendered apps (for example JianYing) ignore synthesized
`PostMessage` clicks even when upstream reports `ok=true`. Requirements:

- `x`/`y` only; `element_index` is rejected for `real` clicks.
- The target window is resolved by process name or window title, and the
  largest visible titled window wins (guards against ghost windows).
- Coordinates stay in screenshot pixel space; the physical window rect is
  added internally, so HiDPI (200%) screenshots map correctly.
- It moves the real cursor and may steal foreground focus: the skill must
  request it explicitly, never by default.
- **Facade-level fallback**: if the upstream real path fails (no Win32
  backend, window not found, timeout, Win32 error), the facade automatically
  retries the same click through its own DPI-aware `CtypesWin32Input` using
  the identical screenshot-pixel coordinate contract. Coordinates outside
  the window rect are rejected (`REAL_INPUT_FAILED`, `reason:
  "out_of_bounds"`) before any input is injected.
- The success response adds a `real_input` object when
  `click_method="real"`:

  ```json
  {"real_input": {"path": "upstream", "upstream_error": null}}
  {"real_input": {"path": "fallback", "upstream_error": "real-input click requires the Windows client (no win_input backend)"}}
  ```

  `path` is `upstream` (the configured client executed the click) or
  `fallback` (the facade's own backend executed it after an upstream
  failure). Metrics rows for fallback clicks set `real_input_fallback: true`.

Errors: `REAL_INPUT_UNAVAILABLE` when no Win32 input backend exists,
`APP_NOT_FOUND` (`reason: "window_not_found"`) when no matching window is
visible, `REAL_INPUT_FAILED` (`reason: "out_of_bounds"` or
`"win32_error"`) when the click cannot execute, `UPSTREAM_ERROR` (`reason:
"timeout"` on upstream timeouts). The state gate and metrics accounting are
identical to other `click` paths: a stale `state_id` is rejected before any
input is injected, and the post-action snapshot records the same
text/image/node metrics.

Response on success:

```json
{
  "ok": true,
  "state_id": "e5f6a7b8",
  "action": "set_value",
  "state_changed": true,
  "delta": {
    "window_title_changed": false,
    "focused_changed": true,
    "added": [{"index": "20", "role": "编辑", "name": "搜索"}],
    "removed": [],
    "changed": [{"index": "12", "role": "按钮", "name": "最小化"}],
    "modal_detected": false,
    "truncated": {"tree": false, "text": false}
  }
}
```

Response on stale state:

```json
{
  "ok": false,
  "error": "STALE_STATE",
  "current_state_id": "e5f6a7b8",
  "signal": "tree",
  "message": "State for app ChatGPT changed; re-observe before acting."
}
```

`signal` names which fingerprint rejected the plan: `"tree"` (accessibility
tree changed) or `"image"` (screenshot fingerprint changed). `"image"` is
only possible for apps with a trivial tree (<= 2 controls, the same threshold
as the vision fallback): self-drawn apps such as JianYing expose an empty or
constant UIA tree, so a local perceptual screenshot hash (9x8 grayscale
dHash, computed and compared on this machine only - never sent to the model
and never written to metrics) takes over the freshness gate. For ordinary
apps the tree fingerprint is authoritative and screenshot noise (cursor
blink, animation) never rejects a plan.

### 1.4 `cu_batch`

Inputs:

| Field | Type | Default | Meaning |
|---|---|---|---|
| `app` | string | required | Target app |
| `state_id` | string | required | Initial snapshot |
| `steps` | array | required | List of `cu_act`-style actions (without `app`/`state_id`) |
| `max_actions` | int | 3 | Server-enforced hard cap |
| `fail_fast` | bool | true | Stop on first non-`ok` step |

Response:

```json
{
  "ok": true,
  "completed": 2,
  "state_id": "c9d0e1f2",
  "results": [
    {"step": 1, "ok": true, "action": "set_value"},
    {"step": 2, "ok": true, "action": "click"}
  ],
  "delta": {"window_title_changed": false, "modal_detected": false},
  "stopped_reason": null
}
```

### 1.5 `cu_window`

Windows-only window-level management. No `state_id` is required: window
management is a window-level action, not a content-level one (it does not
touch controls inside the app).

Inputs:

| Field | Type | Default | Meaning |
|---|---|---|---|
| `app` | string | required | Target app (process name or title substring) |
| `action` | string | required | `list`, `activate`, `maximize` |
| `title` | string | null | Optional case-insensitive title substring to pick one of several windows |

Response (`list`):

```json
{
  "ok": true,
  "action": "list",
  "app": "ChatGPT",
  "main": {"title": "ChatGPT", "hwnd": 12345, "rect": {"left": 0, "top": 0, "width": 1200, "height": 800}, "occluded": false, "covered_by": []},
  "candidates": [
    {"title": "ChatGPT", "hwnd": 12345, "rect": {"left": 0, "top": 0, "width": 1200, "height": 800}, "occluded": false, "covered_by": []}
  ],
  "ambiguous": false
}
```

- `candidates` lists every matching visible window, largest area first.
- `main` is the largest candidate (the `find_main_window` strategy).
- `occluded` is a **status, not an error**: true when another window's rect
  fully covers this window (z-order heuristic, best effort). `covered_by`
  lists the covering window titles. A covered window can still be activated.
- `ambiguous` is true when several windows match; `activate`/`maximize`
  **never guess** among them.

Response (`activate` / `maximize` success):

```json
{
  "ok": true,
  "action": "activate",
  "app": "ChatGPT",
  "window": {"title": "ChatGPT", "hwnd": 12345, "rect": {"left": 0, "top": 0, "width": 1200, "height": 800}, "occluded": false, "covered_by": []},
  "was_occluded": true,
  "candidates": [],
  "ambiguous": false,
  "message": "was fully covered by Notepad; brought to foreground"
}
```

Response (ambiguous target, no execution):

```json
{
  "ok": false,
  "error": "AMBIGUOUS_TARGET",
  "action": "activate",
  "app": "ChatGPT",
  "candidates": [{"title": "ChatGPT"}, {"title": "ChatGPT - Settings"}],
  "message": "2 windows match app 'ChatGPT' title ''; pass a unique window-title substring to pick one."
}
```

`activate` restores (`SW_RESTORE`) and foregrounds the window; `maximize`
restores, maximizes and foregrounds it. On the fake client both are no-ops
that return the fake window. Errors: `APP_NOT_FOUND`, `AMBIGUOUS_TARGET`,
`UNSUPPORTED_ACTION`, `REAL_INPUT_UNAVAILABLE`.

### 1.6 `cu_metrics`

Response:

```json
{
  "calls": 12,
  "observe_calls": 5,
  "action_calls": 6,
  "errors": 1,
  "text_chars": 18000,
  "image_bytes": 0,
  "image_payloads": 0,
  "nodes": 240,
  "stale_rejections": 1,
  "avg_latency_ms": 420,
  "apps": 2,
  "snapshots": 3
}
```

`nodes` is the total parsed node count across recorded calls; `avg_latency_ms`
is the mean per-call latency. `apps` and `snapshots` reflect the local state
store. Metrics contain counts and sizes only, never screen text or image bytes.

## 2. State lifecycle

1. `cu_observe` creates snapshot `S1` with fingerprint `F1`, budget, and TTL.
2. `cu_act` first validates `S1` is current and unexpired in the store.
3. Before executing, the facade takes one live read at the snapshot's budget and
   compares fingerprints. Any change (navigation, modal, window move, focus
   change, visible content) rejects with `STALE_STATE` and the live snapshot
   becomes the new current state. The action never runs on a changed tree.
   When the tree is trivial (<= 2 controls), the screenshot fingerprint is
   compared instead, so empty/constant UIA trees cannot defeat the gate.
4. On a match, the action runs together with its own in-process snapshot (the
   upstream resolves `element_index` only against a snapshot captured in the
   same process). A post-action snapshot captured at the same budget becomes
   `S2`; `S1` is no longer current.
5. Any later `cu_act` with `S1` returns `STALE_STATE` with `S2`'s id.
6. The skill must re-observe after user confirmation, navigation, modal changes, or any failed action before planning again.

## 3. Delta contract

Deltas are best-effort heuristics, not a formal UI diff. They compare controls by `(role, name, value, frame)` and report:

- `added` / `removed` / `changed` controls;
- focused-element and window-title changes;
- `modal_detected` when a dialog-like control appears or the title changes to a dialog.

When the facade cannot prove completeness, it returns `truncated: {"tree": true}` and the model should escalate to a larger preset before acting.

## 4. Metrics record (JSONL)

```json
{
  "ts": "2026-08-05T00:00:00Z",
  "tool": "cu_observe",
  "app": "ChatGPT",
  "output_mode": "controls",
  "text_chars": 1571,
  "image_bytes": 0,
  "image_payloads": 0,
  "nodes": 15,
  "truncated": false,
  "latency_ms": 420,
  "error": null
}
```

`cu_find_app` records `text_chars` as the serialized response length and
`nodes` as the number of apps returned. Failed calls record `error` with the
facade error code (for example `STALE_STATE`, `UPSTREAM_ERROR`); `text_chars`
and `nodes` are zero for failures that never reached the upstream.

## 5. Error responses

All tools return HTTP-like structured objects instead of throwing where possible:

```json
{
  "ok": false,
  "error": "APP_NOT_FOUND",
  "message": "No running visible window for app: Notepad",
  "reason": "window_not_found"
}
```

`reason` is an optional machine-readable cause (one of `window_not_found`,
`timeout`, `out_of_bounds`, `win32_error`, ...) so a cheap model can route
recovery without parsing free text; it is omitted when no specific cause
applies. Error codes are defined in [DESIGN.md](DESIGN.md#9-error-taxonomy).


## 6. Record & Replay (CLI, outside MCP)

`record` / `compile` / `replay` are CLI commands; they reuse the same state
and metrics machinery as the MCP tools.

Replay drives the facade with fixed semantics:

- One `cu_observe` per content-level step: `output_mode="controls"`,
  `preset="control"`, `vision="auto"`, `max_results=40`, intent = recorded
  target name.
- Target resolution order: recorded `(role, name)` against live controls ->
  recorded name against vision elements (click at vision frame center via
  `click_method="real"`) -> recorded screenshot-pixel coordinates via
  `click_method="real"`.
- Recorded `drag` steps always replay by coordinates (`from_x`/`from_y` ->
  `to_x`/`to_y`, screenshot pixels): the facade's drag action has no
  element-index path and targets custom-rendered surfaces (timelines, file
  uploads).
- `type_text`/`press_key` without a resolved element use the facade's
  focus-based path with the fresh `state_id`; `drag` steps focus the window
  first, then call `cu_act` with the four coordinates.
- Confirmation: `focus` steps never prompt (window-level); all other steps
  prompt by default (`--run`), `--yes` pre-confirms, declining fails the step
  without touching the desktop.
- Metrics: every replayed step lands in `cu_observe`/`cu_act` rows; the
  session itself is recorded as `cu_record` and compilation as
  `cu_skill_compile` (text characters / image bytes / nodes).