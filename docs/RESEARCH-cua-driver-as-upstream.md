# Research: cua-driver as a stronger upstream (background, non-intrusive)

> Date: 2026-08-11. Status: research + local smoke test only, no code changes.
> Context: product goal is a computer-use experience better than any single
> vendor. Kimi Code and Hermes both ship "background" computer use that does
> not steal the mouse or foreground focus. This document records what they
> actually do and why `trycua/cua`'s `cua-driver` is a credible drop-in (or
> partial) replacement for our current `open-computer-use` upstream.

## TL;DR

- Hermes' computer_use is a thin wrapper over the open-source **cua-driver**
  (`trycua/cua`, MIT, Rust, 21k stars, active). Kimi Code's built-in computer
  use is a **closed binary** (v0.34.0, win32 zip, no source in the repo), so it
  is a design reference only.
- "Not stealing the mouse/foreground" is not a single trick. All three
  implementations converge on one layered contract:
  1. **Semantic background actions first** (UIA InvokePattern/TogglePattern/
     ValuePattern, AT-SPI Action.DoAction) — no cursor move, no focus steal.
  2. **Targeted window-message input** for pixel paths (PostMessage/SendMessage
     to the target pid/window), never global HID injection.
  3. **Explicit refusal + escalation**: when an app only accepts real
     foreground input, return a structured `background_unavailable` /
     `background_occluded` refusal and let the caller opt into
     `bring_to_front` / `dispatch:"foreground"`. Never silently steal focus.
- We verified cua-driver 0.19.3 on this machine: installs sudo-free via a one
  line script, `doctor` works, daemon + `cua-driver call <tool> '<json>'` works,
  structured JSON output, UIA trees, DPI-aware screen size (2880x1800 @ 2.0).
  JianyingPro (self-drawn) still yields an empty UIA tree (`elements_complete:
  false`) — the same gap our screenshot-fingerprint layer already covers.

## cua-driver facts (verified)

- Repo: https://github.com/trycua/cua (MIT). Docs: https://cua.ai/docs.
- Binary: `cua-driver` (Rust, cross-platform macOS/Windows/Linux). Latest
  checked: 0.19.3 (2026-08-11).
- Install (Windows PowerShell, no admin, junction-based atomic upgrades):
  `irm https://cua.ai/driver/install.ps1 | iex`
  Layout: `%LOCALAPPDATA%\Programs\Cua\cua-driver\bin` -> junction ->
  `%USERPROFILE%\.cua-driver\packages\releases\<version>-<target>`.
- Surface: `cua-driver mcp` (stdio MCP server, owns runtime on Windows/Linux,
  exits on stdin EOF), `cua-driver serve` (long-running daemon, named pipe
  `\\.\pipe\cua-driver`), `cua-driver call <tool> '<json>'` (positional JSON or
  stdin — PowerShell 5.1 strips quotes, pipe JSON via stdin), `cua-driver
  doctor`, `list-tools`, `describe`, `status`, `stop`, `telemetry disable`.
- Permission modes (fixed at daemon start): `standard` (default), `bounded`
  (manifest), `unrestricted` (dangerous flag). Agents cannot change the mode.
- Telemetry is opt-out (`cua-driver telemetry disable`); we disabled it on this
  machine.

## Windows non-intrusive mechanism (from docs + tool docs)

- `launch_app`: launches hidden with `SW_SHOWNOACTIVATE`, never brings the
  target to the foreground.
- `press_key` / `type_text`: delivered to the target pid's top-level window via
  `PostMessage(WM_KEYDOWN/WM_KEYUP)` / `WM_CHAR` — no global HID.
- `set_value`: UIA ValuePattern. `invoke_menu`: accessibility API path.
- `click`/`drag`/`double_click`/`right_click`/`scroll`: element-token (AX path,
  works on backgrounded/hidden windows) or window-local pixel coordinates from
  `get_window_state`.
- `bring_to_front`: the only foreground-stealing tool, explicitly named, with
  post-activation verification.
- Structured refusals: `background_unavailable`, `background_occluded`,
  `window_id_not_found`, `window_owner_pid_mismatch`, `browser_input_trust_
  unavailable` etc. are part of the contract — no silent success.
- `get_window_state`: returns BOTH a structured `elements` array (element_index,
  element_token, role, label, value, frame{x,y,w,h}, parent_index, depth) and a
  markdown tree plus a screenshot; supports `max_elements`/`max_depth`/`query`
  to bound huge trees; `include_screenshot:false` for cheap re-indexing.
- Browser routes (`browser_*`): CDP bound to an exact (pid, window_id), proven
  for Chrome/Edge on Windows: snapshot, nav, ref-bound typing, trusted
  background click, DOM click, file assignment, downloads.
- `verify_state`: deterministic bounded predicates against one exact window —
  exactly the post-action verification our real-input fallback needs.
- Platform support level for Windows: **Supported** (canonical coverage:
  Electron, Tauri, WPF, WinUI 3, WebView2; Chromium background gestures and
  elevated-integrity boundaries return structured refusals).

## Local verification record (2026-08-11, this machine)

- `cua-driver --version` -> 0.19.3; `doctor --json` -> ok except one warn:
  "interactive session: input desktop is not the user Default desktop"
  (remote/RDP-ish session; GUI input on the real desktop must be verified by
  the user locally). UIA CoCreateInstance ok; EnumWindows 24 windows.
- `call list_apps '{}'` -> structured apps incl. JianyingPro.exe, quark.exe,
  ChatGPT.exe, Notepad.exe (this machine's real apps).
- `call list_windows '{}'` -> window_id/pid/title/bounds/minimized/is_on_screen
  incl. minimized JianyingPro main window.
- `call get_accessibility_tree '{}'` -> desktop process/window snapshot.
- `call get_window_state '{"pid":14192,"window_id":367070896,
  "include_screenshot":false}'` (Notepad) -> 29 elements, structured array with
  frames and tokens.
- `call get_window_state '{"pid":40756,"window_id":48958436,...}'`
  (JianyingPro) -> 2 elements, `elements_complete: false` — self-drawn app
  still exposes no useful UIA tree. Window frame + screenshot remain available
  for the pixel path; our screenshot-fingerprint fallback (T4) covers stale
  detection there.
- `call get_screen_size` -> 2880x1800 @ scale 2.0 (DPI-native).
- Daemon lifecycle: `serve --no-overlay`, `status`, `stop` all work.
- No click/type_text/drag were executed on real apps (read-only smoke test
  only; interactive-session warn also blocks reliable real-input checks here).

## Comparison: current upstream vs cua-driver

| Dimension | open-computer-use (npm 0.3.1) | cua-driver 0.19.3 |
|---|---|---|
| License / provenance | MIT / npm | MIT / GitHub Releases, sudo-free installer |
| Windows maturity | PowerShell runtime, we hit DPI + GBK + PostMessage gaps | Supported level, structured JSON, DPI-native |
| Output format | CLI text lines (regex parse, GBK probing) | Structured JSON (elements/windows/errors) |
| Background contract | partial | Contract-level (default background, explicit bring_to_front, structured refusals) |
| Input delivery | our own ctypes/SendInput fallback | UIA patterns -> targeted PostMessage; explicit escalation |
| Window management | our find_main_window (T1) | list_windows / set_window_frame / bring_to_front |
| Verification | none | verify_state |
| Browser | none | browser_* CDP background routes (proven) |
| Record/replay | our record/ (win_hooks) | start_recording / replay_trajectory |
| Session limits | silent | doctor reports interactive-session state |

## Impact on our architecture

Our facade value layer is upstream-agnostic and stays: OCR/LLM text element
tables for non-multimodal models, screenshot fingerprints + occlusion
detection, IME/Chinese input recording, replay auto-recovery, ProviderPool,
metrics/token controls, config_store, protocol rules.

Adapter work (a future T10):
- New `UpstreamClient` implementation calling `cua-driver call <tool> '<json>'`
  (or long-lived stdio `cua-driver mcp`); fake_client unchanged.
- Map pid/window_id anchoring onto our app-name flow (list_windows ->
  pick window -> get_window_state); this is a strict upgrade over
  find_main_window guessing.
- Map structured errors onto our error codes; expose `background_unavailable`
  as a state (like occlusion), not a failure.
- Keep the screenshot path: cua returns screenshots for window states; our
  vision/engine (OCR -> optional LLM upgrade) remains the model-visible
  compact text table.
- Optional: `verify_state` for real-input post-checks; browser_* for the
  "Chrome web page" scenario (our verified-reliable path today).

## Risks / boundaries

- Real-desktop input verification must happen on the user's machine
  (interactive session warn on this remote box; real machine verification
  already 7/10 in docs/VERIFICATION.md).
- JianyingPro-class self-drawn apps have no UIA tree on cua-driver either;
  our screenshot fingerprint + pixel path remains the answer, and cua's
  refusal semantics make the "background unavailable" boundary explicit.
- Kimi Code is closed; only its product promises and kimi-chrome (CDP-based)
  are references. No code to reuse.
- cua-driver is a moving target (weekly releases); pin the version in our
  packaging and vendor the contract tests.

## Recommended next steps (for the next development phase)

1. `feat(upstream): add CuaUpstreamClient` adapter with a fake-upstream test
   suite; keep open-computer-use as fallback config (`upstream: open-computer-use
   | cua-driver`).
2. Map cua's structured refusals onto our error/state contract; update
   docs/PROTOCOL.md with the background/foreground ladder.
3. Adopt `verify_state` for real-input fallback verification (T2 completion)
   and screenshot-fingerprint stays for empty-tree apps (T4).
4. Evaluate `start_recording`/`replay_trajectory` against our record/replay
   layer; keep ours where IME/Chinese handling is superior.
5. Packaging: vendor the pinned cua-driver installer into our install flow
   (`lean-computer-use doctor` can probe `cua-driver` presence/version).
