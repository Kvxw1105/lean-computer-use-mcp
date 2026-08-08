"""Event -> step building: semantic targets, coordinate fallback, typing."""

from __future__ import annotations

from lean_computer_use_mcp.models import ControlNode, Frame
from lean_computer_use_mcp.record.keys import VK_RETURN, VK_SHIFT
from lean_computer_use_mcp.record.model import ElementTable, InputEvent
from lean_computer_use_mcp.record.steps import (
    build_steps,
    is_commit_name,
    match_element,
    point_in_frame,
)

RECT = (100, 200, 1100, 900)
TITLE = "Demo Window"


def _node(
    index: str, role: str, name: str, x: int, y: int, w: int, h: int
) -> ControlNode:
    return ControlNode(
        index=index, role=role, name=name, frame=Frame(x=x, y=y, width=w, height=h)
    )


def _table(ts: float = 100.0) -> ElementTable:
    return ElementTable(
        ts=ts,
        window_title=TITLE,
        window_pid=42,
        elements=[
            _node("1", "按钮", "文本", 10, 20, 80, 30),
            _node("2", "按钮", "字号", 100, 20, 60, 30),
            _node("3", "按钮", "发布", 300, 400, 90, 36),
        ],
        text_chars=500,
        image_bytes=0,
        window_rect=RECT,
    )


def _click(
    ts: float, x: int, y: int, title: str = TITLE, rect: tuple = RECT
) -> InputEvent:
    return InputEvent(
        ts=ts,
        kind="mouse_down",
        x=x,
        y=y,
        button="left",
        window_title=title,
        window_pid=42,
        window_rect=rect,
    )


def test_point_in_frame_pure():
    frame = Frame(x=10, y=20, width=80, height=30)
    assert point_in_frame(50, 35, frame)
    assert not point_in_frame(200, 200, frame)
    assert point_in_frame(95, 20, frame, margin=20)


def test_match_element_prefers_containing_smallest():
    table = _table()
    hit = match_element(table.elements, 50, 35)
    assert hit is not None and hit.name == "文本"
    near = match_element(table.elements, 12, 22)  # inside too
    assert near is not None and near.name == "文本"


def test_match_element_nearest_within_margin():
    table = _table()
    hit = match_element(table.elements, 130, 45)  # 15px from 字号 frame edge
    assert hit is not None and hit.name == "字号"
    assert match_element(table.elements, 1000, 800) is None


def test_is_commit_name_hints():
    assert is_commit_name("发布")
    assert is_commit_name("Submit report")
    assert is_commit_name("删除文件")
    assert not is_commit_name("字号")


def test_click_matched_and_unmatched():
    table = _table()
    steps = build_steps(
        [
            _click(101.0, 100 + 50, 200 + 35),  # inside 文本
            _click(102.0, 100 + 700, 200 + 600),  # empty area
        ],
        [table],
    )
    assert [step.action for step in steps] == ["click", "click"]
    first, second = steps
    assert first.matched is True
    assert first.target is not None and first.target.name == "文本"
    assert first.x == 50 and first.y == 35
    assert second.matched is False
    assert second.target is None
    assert second.x == 700 and second.y == 600


def test_left_right_modifier_variants_are_modifiers():
    from lean_computer_use_mcp.record.keys import (
        VK_LCONTROL,
        VK_LSHIFT,
        is_modifier,
        vk_name,
    )

    assert is_modifier(VK_LCONTROL) and is_modifier(VK_LSHIFT)
    assert vk_name(VK_LCONTROL) == "Control"
    assert vk_name(VK_LSHIFT) == "Shift"


def test_stop_combo_with_left_modifiers_is_filtered():
    from lean_computer_use_mcp.record.keys import VK_LCONTROL, VK_LSHIFT

    table = _table()
    events = [
        InputEvent(ts=1.0, kind="key_down", vk=VK_LCONTROL, window_title=TITLE),
        InputEvent(ts=1.1, kind="key_down", vk=VK_LSHIFT, window_title=TITLE),
        InputEvent(ts=1.2, kind="key_down", vk=0x52, window_title=TITLE),  # R
        InputEvent(ts=1.3, kind="key_up", vk=VK_LCONTROL, window_title=TITLE),
        InputEvent(ts=1.4, kind="key_up", vk=VK_LSHIFT, window_title=TITLE),
        InputEvent(ts=2.0, kind="key_down", vk=0x41, window_title=TITLE),  # a
    ]
    steps = build_steps(events, [table])
    # The stop hotkey (Control+Shift+R) is filtered; only the 'a' remains.
    assert [(s.action, s.value) for s in steps] == [("type_text", "a")]


def test_click_uncertain_flag():
    table = _table()
    steps = build_steps(
        [
            _click(101.0, 100 + 50, 200 + 35),  # matched
            _click(102.0, 100 + 700, 200 + 600),  # empty area
        ],
        [table],
    )
    assert steps[0].uncertain is False
    assert steps[1].uncertain is True
    assert steps[1].matched is False


def test_click_commit_flag_from_name():
    table = _table()
    steps = build_steps([_click(101.0, 100 + 320, 200 + 420)], [table])
    assert steps[0].commit is True  # 发布


def test_click_without_rect_is_skipped():
    event = InputEvent(
        ts=1.0, kind="mouse_down", x=50, y=60, button="left", window_title=TITLE
    )
    steps = build_steps([event], [_table()])
    assert steps == []


def test_typing_grouped_and_enter_flushes():
    events = [
        InputEvent(ts=1.0, kind="key_down", vk=0x41, window_title=TITLE),  # a
        InputEvent(ts=1.1, kind="key_down", vk=0x42, window_title=TITLE),  # b
        InputEvent(ts=1.2, kind="key_down", vk=VK_RETURN, window_title=TITLE),
    ]
    steps = build_steps(events, [_table()])
    assert [(s.action, s.value, s.key) for s in steps] == [
        ("type_text", "ab", None),
        ("press_key", None, "Enter"),
    ]
    assert steps[1].commit is True


def test_shift_uppercase():
    events = [
        InputEvent(ts=1.0, kind="key_down", vk=VK_SHIFT, window_title=TITLE),
        InputEvent(ts=1.1, kind="key_down", vk=0x41, window_title=TITLE),
        InputEvent(ts=1.2, kind="key_up", vk=VK_SHIFT, window_title=TITLE),
        InputEvent(ts=1.3, kind="key_down", vk=0x41, window_title=TITLE),
    ]
    steps = build_steps(events, [_table()])
    assert steps[0].value == "Aa"


def test_combo_press_key():
    events = [
        InputEvent(ts=1.0, kind="key_down", vk=0x11, window_title=TITLE),  # Control
        InputEvent(ts=1.1, kind="key_down", vk=0x53, window_title=TITLE),  # S
        InputEvent(ts=1.2, kind="key_up", vk=0x53, window_title=TITLE),
        InputEvent(ts=1.3, kind="key_up", vk=0x11, window_title=TITLE),
    ]
    steps = build_steps(events, [_table()])
    assert [(s.action, s.key) for s in steps] == [("press_key", "Control+S")]


def test_stop_combo_filtered():
    events = [
        InputEvent(ts=1.0, kind="key_down", vk=0x11, window_title=TITLE),  # Control
        InputEvent(ts=1.1, kind="key_down", vk=VK_SHIFT, window_title=TITLE),
        InputEvent(ts=1.2, kind="key_down", vk=0x52, window_title=TITLE),  # R
        InputEvent(ts=1.3, kind="key_up", vk=0x52, window_title=TITLE),
        InputEvent(ts=1.4, kind="key_up", vk=VK_SHIFT, window_title=TITLE),
        InputEvent(ts=1.5, kind="key_up", vk=0x11, window_title=TITLE),
    ]
    steps = build_steps(events, [_table()])
    assert steps == []


def test_wheel_coalesced_into_scroll():
    events = [
        _click(1.0, 100 + 150, 200 + 50),
        InputEvent(
            ts=2.0,
            kind="wheel",
            wheel_delta=-120,
            window_title=TITLE,
            window_pid=42,
            window_rect=RECT,
        ),
        InputEvent(
            ts=2.2,
            kind="wheel",
            wheel_delta=-120,
            window_title=TITLE,
            window_pid=42,
            window_rect=RECT,
        ),
        InputEvent(
            ts=3.0,
            kind="wheel",
            wheel_delta=120,
            window_title=TITLE,
            window_pid=42,
            window_rect=RECT,
        ),
    ]
    steps = build_steps(events, [_table()])
    scrolls = [step for step in steps if step.action == "scroll"]
    assert len(scrolls) == 2
    assert scrolls[0].direction == "down" and scrolls[0].pages == 2.0
    assert scrolls[1].direction == "up" and scrolls[1].pages == 1.0


def test_typing_gap_flushes():
    events = [
        InputEvent(ts=1.0, kind="key_down", vk=0x41, window_title=TITLE),
        InputEvent(ts=1.1, kind="key_down", vk=0x42, window_title=TITLE),
        InputEvent(ts=4.0, kind="key_down", vk=0x43, window_title=TITLE),
    ]
    steps = build_steps(events, [_table()])
    assert [step.value for step in steps] == ["ab", "c"]
