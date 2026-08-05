# Lean Computer Use MCP — 项目入口

## 这是什么

一个低上下文、有状态安全校验的 MCP 代理层，运行在低成本模型与 open-computer-use 之间，目标是显著降低 Computer Use 的 Token/上下文消耗，同时消除陈旧索引误操作。

## 当前状态

- M0 骨架：设计文档、协议、安全模型、基准方案、Python MCP 服务骨架，29 个单元测试。
- M1 真实上游验证（2026-08-05，ChatGPT 窗口）：
  - `cu_find_app` / `cu_observe` 已在真实 open-computer-use 上验证通过。
  - `cu_metrics` 记录文本字符数、图片字节数、节点数、延迟与错误类型。
  - 上游默认快照 vs facade 快照：模型可见上下文 437,779 字符 → 820 字符，降幅 99.8%（详见 docs/BENCHMARKS.md）。
  - `cu_act` 的 state_id 真实校验完成：非当前 / 伪造 / 过期 state_id 均在真实桌面上被拒绝，且 0 次桌面动作执行；动作前增加实时指纹门校验。
- M1 完成：经确认的真实桌面动作验证通过（Hide sidebar 点击，可逆，状态变更与预期一致）。
- M2 待办：真实 UI 上的连续动作 / 批处理验证与引擎校验；未发布 GitHub。

## 文档入口

- docs/DESIGN.md — 产品与技术设计
- docs/PROTOCOL.md — 四个工具的协议细节
- docs/SECURITY.md — 安全边界与确认模型
- docs/BENCHMARKS.md — 基准测试方法与验收门槛

## 新建 Codex 任务时使用的提示词

```
这是一个新任务，项目根目录是：
C:\Users\张凯文\Documents\Codex\2026-08-04\windows-codex-computer-use-windows-codex\lean-computer-use-mcp

请先阅读 PROJECT_BRIEF.md、README.md、docs/DESIGN.md，然后继续推进 M1：
1. 用真实 open-computer-use 验证 cu_find_app 与 cu_observe；
2. 用 cu_metrics 记录文本字符数、图片字节数和节点数；
3. 对比上游默认快照与 facade 快照，给出可验证的降幅数据；
4. 完成 cu_act 的 state_id 真实校验，但任何桌面动作前必须先征求我的确认。
```