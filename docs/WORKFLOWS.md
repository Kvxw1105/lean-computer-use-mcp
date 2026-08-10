# Cross-app workflow orchestration

A recorded skill is **single-app by design**: `record` captures one
foreground window, every step carries that app's `window_title`, and `replay`
focuses the same app before each content step. Cross-app tasks (e.g. "export
from JianYing, then publish in a browser") are composed by the **driving
agent**, not by the recorder. This document describes the pattern.

## Pattern: chain single-app skills at the agent layer

1. **Record one skill per app.** Each recording is self-contained:
   `record` on the JianYing window -> `skills/recorded/jianying-export`;
   `record` on the browser window -> `skills/recorded/browser-publish`.
2. **Replay them in order.** The agent calls the facade per skill:
   `replay --run` for skill A, reads the result (completed steps, final
   state, file path), then `replay --run` for skill B.
3. **Pass data through the agent's context.** State does not cross apps at
   the facade level: `state_id` is scoped per app and per `cu_observe`. The
   export file path produced by skill A is read by the agent from the
   observed controls / vision table and typed into skill B's file dialog.
4. **Keep confirmation on every commit step.** Each skill's commit-like
   steps (publish/send) prompt independently; `--yes` on one skill does not
   pre-approve the other.

## Why not a multi-app recording?

- `record/win_hooks.py` tags events with the **foreground window at event
  time**; a workflow that switches apps mid-recording produces steps with
  mixed `window_title`s. Replay cannot focus two apps from one recording
  without a `window_title`-scoped focus step, and cross-app value passing
  (file dialogs, drag from one window to another) has no honest semantic
  representation in a linear step list.
- State gates (`STALE_STATE`) compare per-app fingerprints; a single
  recording spanning apps would either bypass gates or force per-step app
  re-observation with no benefit over two recordings.

## Worked example: JianYing export -> browser publish

| Step | Skill A (JianYing) | Skill B (browser) |
|---|---|---|
| Focus | `cu_window activate` on JianYing | `cu_window activate` on the browser |
| Action | Export (real-input clicks, file dialog) | Upload file (drag or dialog), fill title, publish |
| Handoff | Agent reads the exported file path from the last `cu_observe` | Agent types/enters the path into the dialog |
| Confirmation | Export button prompts | Publish button prompts |

The two skills stay independently testable: each can be replayed alone, and
a regression in one never blocks the other.

## Verification

- Single-app replay is covered by the fake-upstream tests
  (`tests/test_record_replay.py`) and the M4 success matrix
  (`benchmarks/success_matrix.py` S6/S7).
- Chain ordering is a pure agent decision; there is no facade feature to
  unit-test. On a real desktop, verify with two recorded skills back to
  back and confirm the handoff value (e.g. the exported file path) is read
  from the live state, never from memory.
- Real verification for this pattern is pending on the user's machine
  (see `docs/VERIFICATION.md`).
