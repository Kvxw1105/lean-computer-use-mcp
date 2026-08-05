# Skills

## lean-computer-use-luna

Model-side orchestration for `lean-computer-use-mcp`. It supplies:

- task classification (`READ_ONLY`, `LOCAL_ACTION`, `COMMIT_ACTION`);
- observation presets matched to `cu_observe`;
- confirmation contract and post-confirmation re-observe rules;
- retry matrix and hard call budgets;
- compact output states (`DONE`, `NEEDS_CONFIRMATION`, `NOT_FOUND`, `BLOCKED`).

The upstream `open-computer-use` skill remains the reference for installation and cross-platform behavior. Do not load both skills for routine execution.

## Recorded skills

`lean-computer-use record` + `compile` produce editable skills under
`skills/recorded/<name>/` (a `SKILL.md` plus `recording.json`). Replay them
with `lean-computer-use replay --in <path> --run`; the steps are re-located in
the live tree and coordinates are only a fallback. See `docs/RECORDING.md`.