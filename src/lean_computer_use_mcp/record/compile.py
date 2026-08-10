"""Compile a recorded workflow into an editable, intent-based SKILL.md.

This mirrors the official Codex Record & Replay output: a readable text skill
(when to use / inputs / steps / how to verify) instead of a coordinate macro.
Generation is deterministic so a weak model never has to pay tokens for it;
users can then edit the Markdown by hand.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from lean_computer_use_mcp.record.model import CONTENT_ACTIONS, RecordedStep, Recording


def evidence_badges(step: RecordedStep) -> str:
    """Per-step evidence marks: [element] / [coords] / [window] / [uncertain].

    Shown by ``compile --library`` before the store confirmation so the user
    can judge how solid each step's recorded target is.
    """
    badges: list[str] = []
    if step.target is not None:
        badges.append("[element]")
    if step.x is not None:
        badges.append("[coords]")
    if step.window_title:
        badges.append("[window]")
    if step.uncertain:
        badges.append("[uncertain]")
    return " ".join(badges)


def _step_lines(step: RecordedStep) -> list[str]:
    lines = [step.describe()]
    if step.action == "click" and not step.matched and step.x is not None:
        lines.append(
            "  (no accessibility element matched at record time; replay re-locates "
            "by vision or uses the recorded point as a fallback)"
        )
    if step.action in CONTENT_ACTIONS and step.commit:
        lines.append("  (commit-like step: confirm with the user before replay)")
    if step.uncertain:
        lines.append(
            "  (uncertain: no semantic element was matched while recording; "
            "verify this step during replay)"
        )
    return lines


def compile_skill(
    recording: Recording,
    name: str | None = None,
    description: str | None = None,
) -> str:
    """Render one recording as an editable skill document (Markdown)."""
    skill_name = name or recording.name
    purpose = (description or recording.description).strip().rstrip(".") or (
        f"Repeat the demonstrated workflow in {recording.app}"
    )
    recorded_at = datetime.fromtimestamp(
        recording.started_at, tz=timezone.utc
    ).strftime("%Y-%m-%d %H:%M UTC")
    inputs: list[str] = []
    for index, step in enumerate(recording.steps, start=1):
        if step.action == "type_text" and step.value:
            inputs.append(
                f"- `value{index}`: the text to type (recorded example: {step.value!r})"
            )
        elif step.action == "press_key" and step.key:
            inputs.append(
                f"- `key{index}`: the key or shortcut to press (recorded: {step.key})"
            )
    if not inputs:
        inputs.append(
            "- no varying values were recorded; the steps run as demonstrated"
        )

    step_sections: list[str] = []
    for index, step in enumerate(recording.steps, start=1):
        step_sections.append(f"{index}. {chr(10).join(_step_lines(step))}")
    steps_md = chr(10).join(step_sections)

    last = recording.snapshots[-1] if recording.snapshots else None
    if last is not None:
        names = [node.name for node in last.elements[:5] if node.name]
        verify = (
            "The recording ended with these visible elements; use them as the "
            "success signal: " + ", ".join(repr(name) for name in names) + "."
        )
    else:
        verify = "Confirm the workflow completed exactly as in the demonstration."

    content = (
        "---\n"
        f"name: {skill_name}\n"
        f"description: Recorded workflow in {recording.app}: {purpose}. Use when the "
        "user asks to repeat this demonstrated workflow; ask for the values that "
        "differ from the recording.\n"
        "---\n\n"
        f"# {skill_name}\n\n"
        f"Recorded from a live demonstration in **{recording.app}** on {recorded_at}.\n\n"
        "## When to use\n\n"
        f"- {purpose}\n"
        f"- The target app is {recording.app} and the workflow matches the steps below.\n"
        "- The user can provide fresh values (file names, dates, text) for this run.\n\n"
        "## Inputs\n\n" + chr(10).join(inputs) + "\n\n"
        "## Steps\n\n" + steps_md + "\n\n"
        "## How to verify the result\n\n"
        f"- {verify}\n"
        "- Each step is re-located from the live accessibility tree (and vision "
        "when the tree is thin) instead of blind coordinates.\n"
        "- Coordinates recorded during the demo are only a fallback for "
        "custom-rendered UIs.\n"
        "- The final state should match the end of the demonstration.\n\n"
        "## Recorded details\n\n"
        f"- App: {recording.app}\n"
        f"- Steps: {len(recording.steps)} (of which "
        f"{sum(1 for step in recording.steps if step.action in CONTENT_ACTIONS)} "
        "are content-level)\n"
        f"- Element snapshots: {len(recording.snapshots)}\n"
        f"- Recording payload: {recording.metrics.text_chars} text characters, "
        f"{recording.metrics.image_bytes} image bytes stored, "
        f"{recording.metrics.nodes} nodes\n"
        "- Replay: `lean-computer-use replay --in <recording>.json --run`\n"
    )
    return content


def write_skill(
    recording: Recording,
    out_dir: str | Path,
    name: str | None = None,
    description: str | None = None,
) -> tuple[Path, Path]:
    """Write ``SKILL.md`` plus ``recording.json`` into ``out_dir``."""
    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    skill_path = target / "SKILL.md"
    recording_path = target / "recording.json"
    skill_path.write_text(compile_skill(recording, name, description), encoding="utf-8")
    recording.save(recording_path)
    return skill_path, recording_path
