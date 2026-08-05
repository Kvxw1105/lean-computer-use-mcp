from __future__ import annotations

import re

from lean_computer_use_mcp.models import ControlNode, Frame

_WINDOW_RE = re.compile(r'^Window:\s+"(?P<title>.*)",\s+App:\s+')
_FOCUSED_RE = re.compile(r"^The focused UI element is\s+(?P<focused>.+?)\.?$")
_LINE_RE = re.compile(r"^(?P<index>\d+)\s+(?P<role>\S+)\s?(?P<rest>.*)$")
_FRAME_VALUES_RE = re.compile(
    r"x:\s*(?P<x>\d+),\s*y:\s*(?P<y>\d+),\s*width:\s*(?P<width>\d+),\s*height:\s*(?P<height>\d+)"
)

_FRAME_MARKER = " Frame: {"
_ACTIONS_MARKER = " Secondary Actions: "
_VALUE_MARKER = " Value: "


def parse_state(text: str) -> tuple[str, str | None, list[ControlNode]]:
    """Parse upstream accessibility text into (window title, focused element, controls)."""
    title = ""
    focused: str | None = None
    controls: list[ControlNode] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        window_match = _WINDOW_RE.match(stripped)
        if window_match:
            title = window_match.group("title")
            continue
        focused_match = _FOCUSED_RE.match(stripped)
        if focused_match:
            focused = focused_match.group("focused").strip()
            continue
        line_match = _LINE_RE.match(stripped)
        if not line_match:
            continue
        depth = len(raw) - len(raw.lstrip())
        node = _parse_control(
            index=line_match.group("index"),
            role=line_match.group("role"),
            rest=line_match.group("rest"),
            depth=depth,
        )
        if node is not None:
            controls.append(node)
    return title, focused, controls


def detect_truncation(text: str) -> tuple[bool, bool]:
    tree_truncated = "..." in text or "truncated" in text.lower()
    text_truncated = text.rstrip().endswith("...")
    return tree_truncated, text_truncated


def _parse_control(index: str, role: str, rest: str, depth: int) -> ControlNode | None:
    frame: Frame | None = None
    frame_pos = rest.find(_FRAME_MARKER)
    if frame_pos != -1:
        frame_text = rest[frame_pos + len(_FRAME_MARKER) :]
        end = frame_text.find("}")
        if end != -1:
            frame_text = frame_text[:end]
        frame_match = _FRAME_VALUES_RE.search(frame_text)
        if frame_match:
            frame = Frame(
                x=int(frame_match.group("x")),
                y=int(frame_match.group("y")),
                width=int(frame_match.group("width")),
                height=int(frame_match.group("height")),
            )
        rest = rest[:frame_pos].rstrip()

    actions: list[str] = []
    actions_pos = rest.find(_ACTIONS_MARKER)
    if actions_pos != -1:
        actions_text = rest[actions_pos + len(_ACTIONS_MARKER) :]
        actions = [part.strip() for part in actions_text.split(",") if part.strip()]
        rest = rest[:actions_pos].rstrip()

    value: str | None = None
    value_pos = rest.find(_VALUE_MARKER)
    if value_pos != -1:
        value = rest[value_pos + len(_VALUE_MARKER) :].strip() or None
        rest = rest[:value_pos].rstrip()

    name = rest.strip()
    return ControlNode(index=index, role=role, name=name, depth=depth, value=value, actions=actions, frame=frame)