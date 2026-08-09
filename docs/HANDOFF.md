# Handoff: lean-computer-use-mcp

> Written 2026-08-09. Read this first, then `docs/DESIGN.md`,
> `docs/PROTOCOL.md`, `docs/MEMORY.md`, `docs/RECORDING.md`,
> `docs/VISION.md`, `docs/SECURITY.md`, `docs/BENCHMARKS.md`, `AGENTS.md`.

## Identity

- GitHub (public): https://github.com/Kvxw1105/lean-computer-use-mcp
- Branch: `main` (single source of truth). Current HEAD: `b7b53c0`.
- Dev machine: `C:\Users\???\Documents\Codex\2026-08-04\windows-codex-computer-use-windows-codex\lean-computer-use-mcp` (Windows 11, 2880x1800 @ 200% DPI).
- Tests: `uv sync --all-extras` then `uv run pytest` -> 181 passed, 1 skipped.

## One-line identity

A low-context, state-safe MCP facade over Open Computer Use
(`iFurySt/open-codex-computer-use`) so cheap non-multimodal agents (e.g. Luna
class) can reliably operate Windows desktop GUI, with record/replay and
procedural memory.

## Architecture (5 layers)

Agent (Codex/Proma) -> Skill `lean-computer-use-luna` (usage policy) ->
MCP facade `lean-computer-use-mcp` (compress/safety/memory) -> upstream
Open Computer Use (execution) -> Windows UIA + screenshots.

The facade owns: compact top-K control output, `state_id` freshness +
stale rejection, local screenshot cache (0 image bytes to the model by
default), post-action deltas, per-call metrics, vision fallback (local OCR ->
optional LLM multimodal), record/compile/replay, atomic procedural memory.

## Done & verified (with data)

- M1: upstream default snapshot 437,779 model-visible chars -> facade
  `cu_observe` 820 chars = **99.8% reduction**; 0 image bytes to model.
- E10: real JianYing 12-step subtitle workflow record -> compile -> replay:
  22 calls, 8,301 model-visible chars vs 9,631,138 equivalent upstream
  (**99.91% lower**); 34.6 MB screenshots kept local; 12/12 steps.
- E12: same task second run via `recall --llm`: 6 calls, 2,274 chars
  (**72.6% lower** than first replay), 3/3 steps.
- V2: WinRT OCR fallback for UIA-blind apps (JianYing): 40 elements ~200ms,
  ~97.7% context reduction. Coordinate actions `cu_act(x,y,drag)` + real
  input path (SetCursorPos+SendInput) - upstream PostMessage clicks are
  ignored by JianYing; real input steals focus, opt-in only.
- V3: OpenAI-compatible multimodal grounding engine (`engine="llm"`),
  downscale 1568px/JPEG85, coordinate rescaling; `vision=auto` escalates
  OCR -> LLM with cooldown throttle.
- V3.1 (`b7b53c0`): multi-endpoint failover `LEAN_CU_VISION_PROVIDERS` JSON
  list; `ProviderPool` in `vision/pool.py`: auth failures (401/403) cooldown
  10 min, transient (timeout/429/5xx/connect) 30 s; working provider kept,
  preferred retried after cooldown; keys never logged (host only).
- R1-R3: record (global hooks, no screenshots), compile to editable
  SKILL.md, replay with element-first matching + coordinate fallback;
  atomic components + intent recall + library feedback; `compile --llm`
  semantic naming, `recall --llm` intent mapping, `refine` curation.
- Overlay: four thin layered edge windows (14px band, ~40x fewer px/frame),
  24fps travelling wave (2.5 waves, 0.5Hz, +/-15% alpha); WS_EX_TOPMOST
  must be in create-time ex-style (SetWindowPos topmost silently no-ops on
  this system); per-strip UpdateLayeredWindow destination fixes.
- Safety: `state_id` gate (missing/expired/non-current -> STALE_STATE,
  zero upstream calls), commit-like one-shot (COMMIT_UNCERTAIN, never auto
  retry), cu_batch max_actions=3, focus required for type/press_key.

## Key files

- `src/lean_computer_use_mcp/server.py` - cu_* MCP tools
- `src/lean_computer_use_mcp/upstream/` - CLI client + fake client
- `src/lean_computer_use_mcp/state/` - snapshots, fingerprint, TTL
- `src/lean_computer_use_mcp/parse/` - tree -> ControlNode top-K
- `src/lean_computer_use_mcp/diff/` - action deltas
- `src/lean_computer_use_mcp/media/` - local screenshot cache
- `src/lean_computer_use_mcp/vision/` - ocr.py, llm.py, pool.py, base.py
- `src/lean_computer_use_mcp/record/` - recorder, steps, compile, replay,
  overlay, win_hooks, keys
- `src/lean_computer_use_mcp/memory/` - library, extract, enrich, retrieve,
  planner, llm_recall, refine
- `src/lean_computer_use_mcp/metrics/` - JSONL per-call metrics
- `skills/lean-computer-use-luna/SKILL.md` - agent usage policy
- `config/codex.example.toml` - MCP server env example
- `benchmarks/` + `benchmarks/results/` (gitignored) - evidence JSONL

## Environment (machine-local, never commit)

- Proxy for git/LLM calls: `HTTPS_PROXY=http://127.0.0.1:7897`
- Vision: `LEAN_CU_VISION_ENGINE=llm`,
  `LEAN_CU_VISION_MODEL=gpt-5.6-luna`,
  `LEAN_CU_VISION_PROVIDERS` = JSON list with the user's channel endpoint(s)
  (api_base/key/model per entry; keys stay in env, never in repo).
- Optional: `LEAN_CU_ACT_OVERLAY=1`, `LEAN_CU_METRICS_PATH=...`.
- Real desktop runs need the target app (JianYingPro) running; restore +
  foreground the window before replay.

## User context & long-term goals

- User is the primary operator; wants this for: GUI automation of desktop
  apps (video editors like JianYing), recorded "skills"/experience that
  reuse atomic steps, automated social-media publishing (image/video posts),
  low token cost with cheap models (DeepSeek Flash / Luna), visual fallback
  through a multimodal engine when the planner model is not multimodal.
- Product ambition: personal use now, possibly open source / commercial
  later. Generic parts are public on GitHub already.
- User communicates in Chinese; keep responses in Chinese. English for
  public docs (AGENTS.md).

## Known gaps & recommended next actions (priority order)

1. **Local config file + `lean-computer-use config` command** (user asked;
   GUI was deferred): move vision providers into `~/.lean-cu/config.toml`
   (or similar), add CLI subcommand to list/add/remove/reorder/test
   endpoints without setx + process restart.
2. **IME/Chinese input recording** (R2): capture IME-composed text during
   record so Chinese typing steps survive; currently must be hand-patched.
3. **Replay auto-recovery**: on STALE_STATE, re-observe once and retry the
   step (with a hard cap), instead of failing the whole plan.
4. **Drag-step recording** (R2) for timeline/upload interactions.
5. **Cross-app workflow orchestration**: e.g. JianYing export skill then
   browser publish skill chained by the agent; document the pattern.
6. **M4 release engineering**: install command, skill/plugin packaging,
   success-rate benchmark matrix vs upstream baseline (acceptance:
   <=3pp success drop), pinned upstream version + regression fixtures.
7. **Social publishing pilot**: record "JianYing export -> platform upload"
   with the user, identify real blockers (file dialog, drag, verification).
8. **Extend ProviderPool** to `memory/enrich.py`, `memory/refine.py`,
   `memory/llm_recall.py` (text LLM calls still single-endpoint).

## Working constraints

- Never commit screenshots, personal data, real accessibility trees, API
  keys, recordings, or metrics (gitignore covers /recordings/, /memory/,
  /metrics/, benchmarks/results/).
- Facade never manufactures user confirmation; desktop actions on real apps
  need user awareness (this user pre-authorized demos like overlay show and
  JianYing replay; new destructive/commit actions still ask).
- Windows-specific behavior lives in the Windows client; parsing/diff stay
  platform-neutral. CJK literals in code are written as `\u` escapes when
  patching via scripts (stdin encoding on this machine mangles CJK).
- On this machine, editing files via `apply_patch` is unavailable; use
  `@'...'@ | python -` heredocs. PowerShell blocks recursive Remove-Item;
  delete via Python with verified paths.
