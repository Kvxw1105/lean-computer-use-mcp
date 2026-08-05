"""Procedural memory: model, extraction, retrieval, learning feedback."""

from __future__ import annotations

from pathlib import Path

from lean_computer_use_mcp.memory.extract import (
    component_id_from_step,
    extract_components,
    resolve_template,
)
from lean_computer_use_mcp.memory.library import Memory
from lean_computer_use_mcp.memory.model import (
    Component,
    MemoryLibrary,
    fingerprint,
    norm_name,
    slugify,
)
from lean_computer_use_mcp.memory.retrieve import (
    analyze,
    compose_plan,
    name_overlap,
    search,
)
from lean_computer_use_mcp.models import Frame
from lean_computer_use_mcp.record.model import (
    ElementRef,
    ElementTable,
    Recording,
    RecordedStep,
)

FIXTURES = Path(__file__).parent.parent / "examples" / "fixtures"


def _component(
    app="JianYing",
    action="click",
    role="button",
    name="Text",
    hits=0,
    misses=0,
    aliases=None,
):
    return Component(
        app=app,
        action=action,
        role=role,
        name=name,
        aliases=list(aliases or []),
        hits=hits,
        misses=misses,
    )


def _recording() -> Recording:
    return Recording(
        name="subtitle-font-size",
        app="JianYing",
        description="Reduce the subtitle font size in the text panel.",
        started_at=1700000000.0,
        snapshots=[
            ElementTable(
                ts=1700000001.0,
                window_title="JianYing",
                window_pid=1,
                elements=[
                    __import__(
                        "lean_computer_use_mcp.models", fromlist=["ControlNode"]
                    ).ControlNode(
                        index="1",
                        role="button",
                        name="Text",
                        frame=Frame(10, 20, 80, 30),
                    )
                ],
                text_chars=10,
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
                    role="edit", name="Font size", frame=Frame(120, 52, 56, 26)
                ),
                x=148,
                y=65,
                matched=True,
            ),
            RecordedStep(action="type_text", window_title="JianYing", value="12"),
            RecordedStep(action="type_text", window_title="JianYing", value="18"),
            RecordedStep(
                action="press_key", window_title="JianYing", key="Enter", commit=True
            ),
        ],
    )


def test_fingerprint_and_slug():
    assert (
        fingerprint("JianYing", "click", "button", "Font size")
        == "jianying::click::button::font-size"
    )
    assert fingerprint("JianYing", "click", "button", "Font size") == fingerprint(
        "jianying", "click", "button", " font size "
    )
    assert fingerprint("剪映", "click", "按钮", "字号") == "剪映::click::按钮::字号"
    assert norm_name("  Font Size  ") == "font size"
    assert slugify("subtitle-font-size") == "subtitle-font-size"


def test_component_id_stable_and_value_parameterized():
    first = _component(name="Font size")
    second = Component(app="JianYing", action="click", role="button", name="font size")
    assert first.id == second.id
    typed = Component(app="JianYing", action="type_text", value_template="{value}")
    assert typed.id == fingerprint("JianYing", "type_text", "", "")


def test_extract_components_dedupe_and_template():
    components, template = extract_components(_recording())
    actions = [component.action for component in components]
    assert actions.count("type_text") == 1  # 12 and 18 share one component
    assert components[0].action == "focus"
    assert len(template.component_ids) == 5
    assert template.component_ids[0] == components[0].id
    click = components[1]
    assert click.preconditions == ["Text"]  # present in the first snapshot
    steps = resolve_template(dict((c.id, c) for c in components), template)
    assert [step.action for step in steps] == [
        "focus",
        "click",
        "click",
        "type_text",
        "press_key",
    ]


def test_retrieve_scores():
    library = MemoryLibrary(
        components={
            c.id: c
            for c in [
                _component(name="Text", hits=3),
                _component(name="Font size", hits=1),
                _component(name="Export", aliases=["upload", "publish"], hits=0),
            ]
        }
    )
    ranked = search(library, "reduce subtitle font size", app="JianYing")
    names = [component.name for component, _score in ranked]
    assert "Font size" in names
    assert ranked[0][0].name == "Font size"  # intent mentions font size
    assert ranked[0][1] > 1.0
    assert name_overlap("font size", "Font size") == 2.0
    assert name_overlap("字号", "字号") == 2.0  # CJK exact
    assert analyze("调小字号")[1]  # CJK bigrams extracted


def test_retrieve_app_filter_and_popularity():
    library = MemoryLibrary(
        components={
            c.id: c
            for c in [
                _component(app="JianYing", name="Text", hits=0),
                _component(app="ChatGPT", name="Text", hits=5),
            ]
        }
    )
    ranked = search(library, "text", app="JianYing")
    assert len(ranked) == 1
    assert ranked[0][0].app == "JianYing"
    # Same name, different app: app filter changes the winner.
    ranked_all = search(library, "text")
    assert ranked_all[0][0].app == "ChatGPT"  # popularity breaks the tie


def test_compose_reuses_template_when_confident():
    library = MemoryLibrary()
    components, template = extract_components(_recording())
    for component in components:
        library.components[component.id] = component
    library.templates[template.id] = template
    plan = compose_plan(library, "reduce the subtitle font size", app="JianYing")
    assert plan.kind == "template"
    assert plan.tentative is False
    assert len(plan.steps) == 5
    assert plan.source == "subtitle-font-size"
    weak = compose_plan(library, "check the weather")
    assert weak.kind == "components" and weak.tentative is True


def test_memory_learn_and_feedback(tmp_path):
    path = tmp_path / "components.json"
    memory = Memory(path)
    learned = memory.learn(_recording())
    assert learned["components_added"] == 5
    assert memory.stats()["components"] == 5
    assert memory.stats()["templates"] == 1
    # Re-learning the same workflow: no new components, template hits++.
    learned2 = memory.learn(_recording())
    assert learned2["components_added"] == 0
    step = _recording().steps[1]
    memory.record_use("JianYing", step, True, effect_names=["Text panel", "Text"])
    component = memory.library.components.get(component_id_from_step("JianYing", step))
    assert component is not None
    assert component.hits == 2  # 1 from learn + 1 from replay
    assert "Text panel" in component.effect
    bad = _recording().steps[3]
    memory.record_use("JianYing", bad, False)
    bad_component = memory.library.components.get(
        component_id_from_step("JianYing", bad)
    )
    assert bad_component.misses == 1
    # Persistence round-trip.
    reloaded = Memory(path)
    assert reloaded.stats()["components"] == 5
    assert reloaded.library.components[component.id].effect == ["Text panel", "Text"]


def test_memory_feedback_lowers_retrieval_rank(tmp_path):
    from lean_computer_use_mcp.memory.model import fingerprint

    path = tmp_path / "components.json"
    memory = Memory(path)
    memory.learn(_recording())
    click_step = _recording().steps[1]
    stale_id = component_id_from_step("JianYing", click_step)

    def score_of(ranked, component_id):
        return next((score for comp, score in ranked if comp.id == component_id), None)

    before = memory.search("text", app="JianYing")
    for _ in range(4):
        memory.record_use("JianYing", click_step, False)
    after = memory.search("text", app="JianYing")
    assert score_of(before, stale_id) is not None
    assert score_of(after, stale_id) is not None
    assert score_of(after, stale_id) < score_of(
        before, stale_id
    )  # staleness lowered the score
    # A fresh same-name component now outranks the stale one.
    fresh = Component(
        app="JianYing", action="click", role="button", name="Text", hits=0
    )
    memory.library.components[fingerprint("JianYing", "click", "button", "Text")] = (
        fresh
    )
    memory.save()
    ranked = memory.search("text", app="JianYing")
    assert ranked[0][0] is fresh
