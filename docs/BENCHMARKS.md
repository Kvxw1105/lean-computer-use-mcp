# Benchmarks

## Goal

Prove or disprove, with data, that the facade plus skill reduces model-visible context without sacrificing success.

## Method

For each scenario, run three configurations on the same Windows desktop state:

- A: upstream `open-computer-use` tools with default budgets.
- B: upstream tools driven by the `open-computer-use-luna` skill (skill only).
- C: `lean-computer-use-mcp` driven by the `lean-computer-use-luna` skill (facade + skill).

Record per task:

- tool calls by name;
- returned text characters per call;
- returned image count, dimensions, and Base64 bytes;
- returned node counts and truncation flags;
- stale-state rejections;
- vision-engine invocations, screenshot bytes, latency, and elements produced;
- success/failure and completion state (`DONE`, `NEEDS_CONFIRMATION`, `NOT_FOUND`, `BLOCKED`);
- wall-clock latency.

## Scenarios

Defined in `benchmarks/scenarios.json`:

| ID | Scenario | Key assertion |
|---|---|---|
| E1 | Known-app read-only | 0 `list_apps`, 1 observation, 0 actions |
| E2 | Unknown browser title | 1 `list_apps`, 1 observation |
| E3 | Confirmation across turns | Fresh observe after confirmation; old index never used |
| E4 | Commit timeout | No automatic retry; `COMMIT_UNCERTAIN` |
| E5 | Visible prompt injection | Summarize only; zero actions |
| E6 | Truncated complex UI | Escalate `control` -> `expand` once |
| E7 | Modal after action | Old state and confirmation expire |
| E8 | Ambiguous app selection | No random choice; ask user |
| E9 | UIA-blind app (JianYing) OCR fallback | Vision element table replaces the empty tree; zero raw-image tokens to the planner |

## Real-window result: JianYing (UIA-blind app), 2026-08-05

Windows 11, JianYingPro window, 1944x1296, WinRT OCR (`zh-Hans-CN` engine, local).

| Configuration | Tree text chars | Image to model | Vision elements | Latency (observe+OCR) |
|---|---:|---:|---:|---:|
| A: upstream `get_app_state` (default) | ~186 | 213,152 Base64 chars | - | ~750ms |
| C: `cu_observe(vision="on")` | 149 | 0 (screenshot cached locally) | 40 (text table ~4.9k chars) | 950ms |

Model-visible context reduction on the vision path: **~97.7%** (213,338 chars
downstream vs ~5,050 chars), because the screenshot never leaves the machine and the
planner only receives the compact element table. OCR wall time: ~200ms on CPU.

Note: the window capture returned a full-screen frame (the target window was not
focused during the run); coordinates in the element table are still in screenshot
pixel space, which is the space upstream `click` consumes.

## Acceptance thresholds

- Median model-visible context per successful task: at least 60% lower than configuration A.
- Success rate: no more than 3 percentage points below configuration A.
- Wrong-window actions: 0.
- Unconfirmed actions: 0.
- Ordinary control tasks: at most 2 observations and 1 visual payload.
- `list_apps`: at most 1 per task.

## Reporting

Each benchmark run produces a JSONL metrics file plus a summary:

```json
{
  "scenario": "E1",
  "config": "C",
  "pass": true,
  "text_chars": 1571,
  "image_payloads": 0,
  "calls": 1,
  "success": true
}
```

Publish aggregate tables with mean, median, P95, and per-scenario deltas.
## M1: real snapshot comparison (measured 2026-08-05)

Measured on the signed-in Windows desktop against the installed
`open-computer-use` CLI (`ChatGPT` window, app version 0.3.1). Read-only: no
desktop actions were performed. Regenerate with:

```sh
uv run python benchmarks/m1_real_compare.py --app ChatGPT
```

Results are appended as JSONL to `benchmarks/results/m1-<date>.jsonl`
(counts and sizes only; no screen text or image bytes).

| Snapshot | Budget | Text chars | Image Base64 chars | Nodes | Model-visible chars | Latency ms |
|---|---|---:|---:|---:|---:|---:|
| Upstream default | 1200 / 64 / max | 55,543 | 382,236 | 460 | 437,779 | 3,361 |
| Upstream compact (control) | 80 / 8 / 160 | 1,580 | 382,760 | 15 | 384,340 | 1,121 |
| Upstream compact (read) | 160 / 10 / 600 | 2,355 | 382,780 | 22 | 385,135 | 1,180 |
| Facade `cu_observe` controls | control | 820 payload | 0 | 3 returned | 820 | 1,122 |
| Facade `cu_observe` reading | read | 2,139 payload | 0 | 11 returned | 2,139 | 1,164 |
| Facade `cu_observe` visual | control | 382,598 payload | 381,780 | 3 returned | 764,378 | 1,124 |

Model-visible chars = text chars + image Base64 chars for upstream rows, and
the serialized response (JSON chars) + image data chars for facade rows.

Reduction vs upstream default:

| Metric | Controls | Reading |
|---|---:|---:|
| Text | 98.5% | 96.1% |
| Image payload (gate off) | 100.0% | 100.0% |
| Total model-visible | 99.8% | 99.5% |
| Nodes returned | 99.3% (460 -> 3) | 97.6% (460 -> 11) |

Notes:

- Compact budgets reduce raw tree text by ~95-96%, but the screenshot still
  ships with every upstream snapshot; only the facade's screenshot gate removes
  the image payload from model context.
- Visual mode is deliberately expensive (full screenshot forwarded). M3 adds
  on-demand cropping; until then, the skill requests visuals at most once per
  task.
- The upstream default tree size varies with window content (38k-56k chars
  observed); the facade rows are the values the model actually receives.

## M1 follow-up: UIA coverage by app class (measured 2026-08-05)

JianYing Pro 8.9.0.13361 was launched and probed read-only after
restoring its main window. Its editor exposes **no UIA children at all**:

| App | Class | UIA nodes | Text chars | Named controls |
|---|---:|---:|---:|---:|
| ChatGPT (web app) | Chromium | 460 | 55,543 | rich (buttons, menus, fields) |
| JianYing editor | custom-rendered | 1 (window only) | 186 | 0 |

The JianYing screenshot is a real full-frame UI (1944x1296, ~94k unique colors),
so the pixels exist, but the accessibility tree is empty. A text-only model
cannot enumerate any button, field, or timeline element in this app class.
What remains possible without vision:

- `press_key` shortcuts when the window has focus (no element resolution needed);
- coordinate `click` / `drag` when coordinates come from a human or a multimodal
  model reading the screenshot;
- standard Windows file dialogs (well-covered by UIA) for import/export.

Conclusion: the text-tree route only works for apps that expose UIA elements
(standard widgets, Electron/Chromium, WinUI). Vision-rendered editors need the
screenshot path plus either a multimodal model or human-supplied coordinates.

## Real-window result: LLM grounding engine (JianYing editor), 2026-08-06

Same machine and app class as the OCR row above, but with the remote LLM
vision engine (`gpt-5.6-luna` via OpenAI-compatible `/chat/completions`).
The screenshot is downsampled to 1568px on the longest edge (JPEG q85) and
element coordinates are scaled back into the original screenshot space before
returning.

| Configuration | Vision elements | Image bytes sent to engine | Engine latency | Semantic result |
|---|---:|---:|---:|---|
| C: `cu_observe(vision="on")` | 9 | ~130 KB | ~17s (network) | Buttons, slider, and input field identified; coordinates correct |

Comparison with the local OCR path on the same window:

- OCR: 40 raw text fragments, ~200ms, no network; cannot classify control
  types and may split one control into several fragments.
- LLM: 9 semantic controls, ~17s, network-bound; groups fragments into typed
  elements (button/slider/input) with normalized coordinates.
- Either way the screenshot goes to the vision engine, never to the planner
  model; model-visible context stays at the compact element table.

Design implication: prefer the local OCR path and escalate to the LLM engine
only when the OCR table cannot ground the requested action (see
`docs/VISION.md` for the escalation policy).
## Real-window action test: JianYing click delivery (measured 2026-08-06)

Same machine and app class as the LLM grounding row above, but with real
`cu_act` clicks. Window: 1453x865 logical / 2906x1730 physical (200% HiDPI),
title `剪映专业版`, UIA tree still empty (facade sees 3 parsed nodes = window
only). Upstream click source confirms x/y are offsets in screenshot pixel
space added to the physical window bounds, delivered as `PostMessage`
WM_MOUSEMOVE/LBUTTONDOWN/LBUTTONUP.

| Attempt | Method | Target (screenshot px) | Upstream result | Visible change |
|---|---|---|---|---|
| 1 | upstream `click` | preview subtitle (1681, 488) | ok=true | none (9 shots byte-identical) |
| 2 | upstream `click` | timeline clip (395, 1147) | ok=true | none |
| 3 | real input (SetCursorPos + SendInput) | preview subtitle (1681, 488) | - | right panel switched 草稿参数 -> 文本 |

Font-size step, after the panel opened via real input: number box 15 -> 12
(click, Ctrl+A, type, Enter). Preview subtitle box shrank 604x132 -> 597x106 px.

Facade metrics for the session (per call):

| Tool | Text chars | Image bytes | Nodes | Latency ms |
|---|---:|---:|---:|---:|
| `cu_find_app` | 12-2,347 | 0 | 0-17 | ~750 |
| `cu_observe` (vision=on) | 382 | 1,423,307 | 3 | ~1.4s |
| `cu_act` (click) | 382 | 1,423,307 | 3 | ~5.6s (freshness gate + action + post snapshot) |
| `cu_act` rejected | STALE_STATE | 0 upstream actions executed | | 0 |

Implications:

- For UIA-blind custom-rendered apps, upstream `ok=true` does not mean the app
  processed the action; JianYing ignores PostMessage-synthesized mouse events.
  Post-action verification must use screenshot diff plus the vision element
  table, not the upstream result flag alone.
- Real input injection (SetCursorPos + SendInput) reaches these apps, but it
  moves the real cursor and steals foreground focus, so it must be an explicit
  opt-in click method, not the default.
- The facade's state gate behaved correctly: a stale `state_id` (new process,
  empty store) was rejected with zero upstream action calls.

