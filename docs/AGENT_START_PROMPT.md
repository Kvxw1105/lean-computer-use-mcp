# 接手提示词（模板）：lean-computer-use-mcp

> 本文件是项目仓库自带的接手入口模板。任何 Agent（ZCode / Codex / 其他）
> 拿到本仓库后，先读本文件，再按序读文档。使用方式：把本文件复制为
> `AGENT_START_PROMPT.md` 放到工作区根（或直接粘贴给 Agent），按实际环境
> 填好"环境事实"一节。最近更新 2026-08-11。

## 0. 这是什么

一个低上下文、状态安全的 MCP 门面层，封装桌面 Computer Use（open-computer-use /
Hermes cua-driver 双后端），让廉价单模态模型也能稳定操控 Windows 桌面 GUI，
含录制/回放、原子记忆、视觉兜底。GitHub 公开，main 直推。

## 1. 位置与状态（以 origin/main 为准）

- GitHub（单一事实来源）：`https://github.com/Kvxw1105/lean-computer-use-mcp`
- 本机路径：clone 到任意目录（建议 `D:\D-Project\2-lean-computer-use-mcp` 或
  `C:\Users\<you>\...\lean-computer-use-mcp`），独立 .venv
- 测试：`uv sync --all-extras` + `uv run pytest`（最近实测 **491 passed, 1 skipped**，
  ruff 干净；以实际 checkout 为准）
- 工作区干净、HEAD = origin/main

## 2. 先读这些（按顺序）

1. `docs/HANDOFF.md` — 权威交接（含 2026-08-11 深夜交接补充：引擎格局/验收/待办/两台机器环境）
2. `docs/DESIGN.md` → `docs/PROTOCOL.md`（模型可见输出契约，改动必须同步）→
   `docs/MEMORY.md` → `docs/RECORDING.md` → `docs/VISION.md` → `docs/SECURITY.md` →
   `docs/BENCHMARKS.md` → `docs/DEPLOYMENT.md`（新机器部署）→ `docs/VERIFICATION.md`（真机验证清单）
3. `AGENTS.md` — 项目开发规范（含 git 工作流约定）
4. 源码：`src/lean_computer_use_mcp/` server.py（cu_* 工具）→ upstream/（cli_client /
   cua_client / fake_client / win_input）→ state/ → parse/ → diff/ → media/ → vision/ →
   record/ → memory/ → metrics/ → config_store.py

## 3. 模型约束（单模态铁律）

**默认假设：模型是 DeepSeek Flash 类，纯文本，不能看图。**
- 视觉兜底走门面 `vision/` 引擎（OCR → 可选 LLM 升级，输出 compact text table）
- 坐标操作由门面/上游负责，模型只消费文本元素表和坐标
- 涉及多模态验证（截图→模型确认）只写代码+单测（mock），真实验证交用户本机

## 4. 引擎格局（如实）

| 引擎 | 状态 |
|---|---|
| open-computer-use（npm 0.3.1） | auto 兜底；无 cua-driver 的机器上唯一后端 |
| Hermes cua-driver（trycua/cua, MIT, 0.19.3） | auto 首选；后台优先输入、结构化拒绝、前台升级路径；仅在装有它的机器上生效 |
| Kimi Computer Use | 未接入（闭源） |

默认 `--upstream auto`：有 cua-driver 用 Hermes，否则回退 npm。显式固定用
`--upstream cua-driver`。`doctor` 有 `upstream_resolution` 检查项。

## 5. 待办清单（按优先级，随进度勾选/追加）

1. **确认 CI 转绿**（`ea76dfd` 已修 doctor 测试 mock 缺口并推送；GitHub Actions 复查）
2. **真机验证剩余 3 项**（docs/VERIFICATION.md，需用户在场）：IME 拼音组合、
   replay stale 注入、跨应用链
3. **暴露 cua `delivery_mode: foreground`**（适配器 `_build_call_args` 未传；
   自绘应用合成点击不可靠，cua 差异化价值点）+ 单测
4. **发布收尾**：PyPI 发布 + npm 包装（docs/PACKAGING.md 已就绪）
5. 长期：Kimi 融合（未做）；"不抢鼠标/不抢前台"体验门面层实现；Windows 专属模块
   覆盖率已 100%（win_input/win_hooks/ocr/overlay），随改随保

## 6. 环境事实（按实际机器填写，别搞混）

- **开发机（示例：张凯文）**：cua-driver 0.19.3（daemon 运行、telemetry disable）；
  Codex++ 变体，配置主目录 `C:\AppData\.codex`（非 %USERPROFILE%\.codex）；
  MCP 已注册 3 个；git push 需代理 127.0.0.1:7897
- **验收机（示例：kvxkf）**：无 cua-driver（auto 回退 npm）；npm 0.3.1 在 PATH；
  视觉端点 `~/.lean-cu/config.json`（engine=llm, 1 provider）已配置
- 通用：PowerShell→python 管道会损坏中文，补丁脚本用 \uXXXX 转义

## 7. 工作流（必须遵守）

- 每完成一项：`uv run pytest` 全绿 → conventional commit（body 列改动+测试结果）→
  push main → 报 commit hash；一次只做一项
- 永不提交：截图、真实 UIA 树、API key、录制文件、metrics（.gitignore 已覆盖）
- 门面绝不伪造用户确认；破坏性/提交类动作需用户知情；Windows 专属行为放 Windows client
- 真机无法验证 → 单测覆盖 + 文档标注"真实验证待用户本机"，不要停下等待

## 8. 完成汇报格式

每项：commit hash + 测试增量 + PROTOCOL/BENCHMARKS 更新点 + 哪些"代码+单测完成，
真实验证待用户本机"。
