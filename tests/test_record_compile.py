"""Recording -> editable SKILL.md compilation."""

from __future__ import annotations

from lean_computer_use_mcp.models import ControlNode, Frame
from lean_computer_use_mcp.record.compile import compile_skill, write_skill
from lean_computer_use_mcp.record.model import (
    ElementRef,
    ElementTable,
    Recording,
    RecordingMetrics,
    RecordedStep,
)


def _recording() -> Recording:
    return Recording(
        name="font-size-demo",
        app="JianYing",
        description="Reduce the subtitle font size in the text panel.",
        started_at=1700000000.0,
        snapshots=[
            ElementTable(
                ts=1700000001.0,
                window_title="JianYing",
                window_pid=100,
                elements=[
                    ControlNode(
                        index="1",
                        role="button",
                        name="Text",
                        frame=Frame(10, 20, 80, 30),
                    ),
                    ControlNode(
                        index="2",
                        role="button",
                        name="Font size",
                        frame=Frame(100, 20, 60, 30),
                    ),
                ],
                text_chars=1200,
                image_bytes=0,
            )
        ],
        steps=[
            RecordedStep(action="focus", window_title="JianYing"),
            RecordedStep(
                action="click",
                window_title="JianYing",
                target=ElementRef(
                    role="button", name="Text", frame=Frame(10, 20, 80, 30)
                ),
                x=50,
                y=35,
                matched=True,
            ),
            RecordedStep(
                action="click",
                window_title="JianYing",
                target=ElementRef(
                    role="button", name="Font size", frame=Frame(100, 20, 60, 30)
                ),
                x=130,
                y=35,
                matched=True,
            ),
            RecordedStep(
                action="type_text", window_title="JianYing", value="12", matched=False
            ),
            RecordedStep(
                action="press_key", window_title="JianYing", key="Enter", commit=True
            ),
        ],
        metrics=RecordingMetrics(
            events=12, steps=5, snapshots=1, text_chars=900, image_bytes=0, nodes=2
        ),
    )


def test_compile_skill_sections_and_metrics():
    recording = _recording()
    md = compile_skill(recording)
    assert md.startswith("---\nname: font-size-demo")
    for section in (
        "## When to use",
        "## Inputs",
        "## Steps",
        "## How to verify the result",
        "## Recorded details",
    ):
        assert section in md
    assert "Click 'Text' (button)" in md
    assert "'12'" in md
    assert "Press Enter on" in md
    assert "(commit-like step" in md
    assert "JianYing" in md
    assert ".." not in md.split("description:")[1].split("\n")[0]
    # Metrics assertion: the skill text itself is the model-visible payload.
    assert len(md) > 200
    assert recording.metrics.image_bytes == 0


def test_compile_skill_inputs_from_varying_values():
    recording = _recording()
    md = compile_skill(
        recording, name="subtitle-font", description="Shrink subtitle text"
    )
    assert md.startswith("---\nname: subtitle-font")
    assert "Shrink subtitle text" in md
    assert "`value4`" in md  # type_text step
    assert "`key5`" in md  # press_key step


def test_write_skill_round_trip(tmp_path):
    recording = _recording()
    skill_path, recording_path = write_skill(
        recording, tmp_path / "skills" / "font-size-demo"
    )
    assert skill_path.exists() and recording_path.exists()
    loaded = Recording.load(recording_path)
    assert loaded.name == "font-size-demo"
    assert [step.action for step in loaded.steps] == [
        "focus",
        "click",
        "click",
        "type_text",
        "press_key",
    ]
    assert loaded.snapshots[0].elements[0].name == "Text"
    assert loaded.metrics.nodes == 2
