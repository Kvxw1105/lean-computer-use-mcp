# Handoff: lean-computer-use-mcp

> Written 2026-08-09, refreshed 2026-08-10. Read this first, then `docs/DESIGN.md`,
> `docs/PROTOCOL.md`, `docs/MEMORY.md`, `docs/RECORDING.md`,
> `docs/VISION.md`, `docs/SECURITY.md`, `docs/BENCHMARKS.md`, `AGENTS.md`.

## Identity

- GitHub (public): https://github.com/Kvxw1105/lean-computer-use-mcp
- Branch: `main` (single source of truth). Current HEAD: `87912be`.
- Dev machine: `C:\Users\???\Documents\Codex\2026-08-04\windows-codex-computer-use-windows-codex\lean-computer-use-mcp` (Windows 11, 2880x1800 @ 200% DPI).
- Tests: `uv sync --all-extras` then `uv run pytest` -> 205 passed (baseline).

## One-line identity

A low-context, state-safe MCP facade over Open Computer Use
(`iFurySt/open-codex-computer-use`) so cheap non-multimodal agents (e.g. Luna
class) can reliably operate Windows desktop GUI, with record/replay and
procedural memory.

## Onboarding (new machine, no file transfers needed)

Everything needed to build, test, and run lives in this repo. Keys and
personal data are machine-local by design (never in the repo).

1. `git clone https://github.com/Kvxw1105/lean-computer-use-mcp.git`
2. `cd lean-computer-use-mcp && uv sync --all-extras`
3. `uv run pytest` (expect ~205 passed; the real-upstream tests skip if
   the `open-computer-use` binary is missing - unit tests still pass).
4. Read this file, then `docs/DESIGN.md`, `docs/PROTOCOL.md`,
   `docs/MEMORY.md`, `docs/RECORDING.md`, `docs/VISION.md`,
   `docs/SECURITY.md`, `docs/BENCHMARKS.md`, `AGENTS.md`.
5. Configure the vision endpoint on THIS machine (keys never leave the
   machine; there is nothing to download):
   - GUI: `lean-computer-use config-ui` -> browser panel -> add base URL /
     key / model (multi-endpoint failover supported) -> save. The panel is a
     new-Chinese dashboard (ink night / rice-paper day themes).
   - CLI: `lean-computer-use config --help` (add/remove/reorder/test).
   - Store: `~/.lean-cu/config.json`; env vars (`LEAN_CU_VISION_*`) act as
     temporary overrides while set.
6. Demo without a desktop: `uv run lean-computer-use serve --fake`.
7. Real desktop runs need Windows + the upstream `open-computer-use`
   binary + the target app (e.g. JianYingPro) running and foregrounded.

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

- Config UI: local web panel + `config` CLI + `~/.lean-cu/config.json`
  store (`Settings` prefers the file, env is an override); multi-endpoint
  failover is configured through it; the panel UI went through three
  design passes and is now a new-Chinese dashboard (vertical rail
  navigation, stamp-style provider cards with numerals, floating action
  dock, ink-night / rice-paper-day themes, reduced-motion + focus-visible
  support).
- Config concurrency (`2026-08-10`): the store is shared by every agent/
  process on the machine; writes are serialized by a cross-process lock
  (`~/.lean-cu/config.json.lock`, msvcrt/fcntl, never deleted) and all
  mutations (CLI add/remove/reorder, panel save) re-read the file under
  that lock via `update_config`, so a stale snapshot can never clobber
  endpoints another agent saved. The panel echoes `loaded_at` (file mtime)
  and merges instead of replacing: endpoints added after the panel loaded
  are preserved. `config remove` refuses to silently leave zero endpoints
  (needs `--yes`).
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

1. ~~Local config file + `config` CLI + web panel~~ DONE (`config-ui` opens
   a Chinese browser panel; `config list|add|remove|reorder|test` CLI; store
   `~/.lean-cu/config.json`; `Settings` prefers the file, env is override).
2. ~~IME/Chinese input recording~~ DONE (record captures composed IME text;
   steps land in `recording.json` as `type_text`; see docs/RECORDING.md).
3. ~~Replay auto-recovery~~ DONE (STALE_STATE re-observes once with the same
   preset/vision and retries the step; hard cap 3, then the plan fails).
4. ~~Drag-step recording~~ DONE (press-move-release groups into `drag` steps
   with from/to screenshot-pixel coordinates; see docs/RECORDING.md).
5. Cross-app workflow orchestration: pattern documented in
   docs/WORKFLOWS.md (agent-layer skill chaining); real-machine chain
   verification pending - see docs/VERIFICATION.md.
6. ~~M4 release engineering~~ DONE (v0.2.0; install + MCP registration +
   skill packaging in docs/PACKAGING.md; success-rate matrix and pinned
   upstream fixture hashes run as CI release gates).
7. Social publishing pilot: still open; needs a user-machine session to
   record "JianYing export -> platform upload" and find real blockers.
8. ~~Extend ProviderPool~~ DONE (`memory/enrich.py`, `memory/refine.py`,
   `memory/llm_recall.py` route text-LLM calls through `vision/pool.py`
   ProviderPool with 401/403 cooldown and automatic rotation).
## Working constraints

- Never commit screenshots, personal data, real accessibility trees, API
  keys, recordings, or metrics (gitignore covers /recordings/, /memory/,
  /metrics/, benchmarks/results/).
- The config store is shared by all agents/processes on the machine: never
  write `~/.lean-cu/config.json` with read-then-save; use `update_config`
  so concurrent writers merge instead of clobbering.
- Facade never manufactures user confirmation; desktop actions on real apps
  need user awareness (this user pre-authorized demos like overlay show and
  JianYing replay; new destructive/commit actions still ask).
- Windows-specific behavior lives in the Windows client; parsing/diff stay
  platform-neutral. CJK literals in code are written as `\u` escapes when
  patching via scripts (stdin encoding on this machine mangles CJK).
- On this machine, editing files via `apply_patch` is unavailable; use
  `@'...'@ | python -` heredocs. PowerShell blocks recursive Remove-Item;
  delete via Python with verified paths.

---

# 2026-08-11 深夜交接补充（HEAD 752ba17 → ea76dfd）

## 引擎格局（三家上游，如实）

| 引擎 | 状态 |
|---|---|
| open-computer-use（npm 0.3.1） | 一期默认上游，现为 auto 兜底 |
| Hermes cua-driver（trycua/cua, MIT, 0.19.3） | ✅ 已接入且为 **auto 首选**：接口+动作集完整覆盖 open-computer-use；后台优先输入（不抢焦点）、结构化拒绝、前台升级路径。仅另一台开发机（张凯文）安装；本机 kvxkf 未装 |
| Kimi Computer Use | ❌ 未接入（闭源；WebBridge 是浏览器控制，非桌面 CU） |

- 默认引擎 = `auto`：有 cua-driver 用 Hermes，否则回退 open-computer-use；`doctor` 新增
  `upstream_resolution` 检查项；显式固定用 `--upstream cua-driver`。
- 修复过隐藏 bug：cua 分支曾把 `settings.upstream_binary`（默认 open-computer-use）
  传给 CuaUpstreamClient；现 cua 分支固定 `"cua-driver"`，`upstream_binary` 只作用于 npm 后端。

## 验收记录（2026-08-11，kvxkf 本机只读验收 + 一处修复）

- HEAD `ea76dfd` = 752ba17 + 本机修复（见下）；`origin/main` 一致，工作区干净
- `uv run pytest` → **491 passed, 1 skipped**（无 cua-driver 的机器上也全绿）；ruff 干净
- doctor 实测（无 cua-driver 机器）：`upstream_resolution: ok - auto -> open-computer-use (npm backend)`；
  `cua_driver: warn - not found (optional backend)`——回退路径正确，缺失只是 warn
- 上轮审查建议已全部落实：
  - P1-1 fixture 行尾 pin → `1c26a4b`（.gitattributes + 归一化）
  - P1-2 截图指纹缺窗口 rect → 同提交修复
  - P2-3 IME 短组合丢文本 → `1f92da6`（delayed re-sample）
  - 覆盖率：cli_client/win_input/win_hooks/ocr/overlay 达 100%（6c02f5b/d84b7cc/d07bbbc/2508df1）
- **本次修复 `ea76dfd`（CI 红 6 次的根因）**：`tests/test_doctor.py` 两个 cua-driver 探测测试
  漏 mock `shutil.which`/`_resolve_binary`，无 cua-driver 环境（本机+CI）必红 → CI 全红 6 次
  未披露。修复后 489 → 491；CI 需确认转绿。
  - 教训：新增"探测可选后端"类测试时，存在性检查（shutil.which）必须 mock；
    且交接文档必须披露 CI 状态（之前只写"本机实测"）。

## 待办清单（按优先级）

1. **确认 CI 转绿**（ea76dfd 已推送；若仍有红，查 ubuntu pytest 步骤）
2. **真机验证剩余 3 项**（docs/VERIFICATION.md，需用户在场）：IME 拼音组合、replay stale 注入、跨应用链
3. **暴露 cua `delivery_mode: foreground`**（适配器 `_build_call_args` 未传；
   自绘应用合成点击不可靠，这是 cua 差异化价值点）+ 单测
4. **发布收尾**：PyPI 发布 + npm 包装（docs/PACKAGING.md 已就绪，35be53f）
5. Kimi 融合未做（闭源）；"不抢鼠标/不抢前台"体验在门面层实现并真机验证

## 环境事实（两台机器，别搞混）

- **开发机（张凯文）**：cua-driver 0.19.3 已装、daemon 运行、telemetry disable；Codex++ 变体，
  配置主目录 `C:\AppData\.codex`（非 %USERPROFILE%\.codex）——坑已沉淀为全局技能
  `xw-lean-computer-use-mcp`；MCP 已注册 3 个（firecrawl-mcp/open-computer-use/lean-computer-use）
- **验收机（kvxkf，本仓库在 C:\Users\kvxkf\ZCodeProject\lean-computer-use-mcp）**：
  无 cua-driver（auto 回退 npm 后端）；npm 0.3.1 在 PATH；视觉端点 `~/.lean-cu/config.json`
  已配置；ZCode 的 mcp.servers 已注册 lean-computer-use（LEAN_CU_ACT_OVERLAY=1）
- 通用：git push 需代理 127.0.0.1:7897（开发机）；PowerShell→python 管道会损坏中文，
  补丁脚本用 \uXXXX 转义
