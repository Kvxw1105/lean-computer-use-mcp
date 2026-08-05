from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lean_computer_use_mcp.vision.base import GroundedElement


@dataclass(frozen=True)
class Frame:
    x: int
    y: int
    width: int
    height: int

    def to_dict(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}


@dataclass
class ControlNode:
    index: str
    role: str
    name: str
    depth: int = 0
    value: str | None = None
    actions: list[str] = field(default_factory=list)
    frame: Frame | None = None

    @property
    def stable_key(self) -> tuple[str, str]:
        return (self.role, self.name)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "index": self.index,
            "role": self.role,
            "name": self.name,
            "depth": self.depth,
            "actions": self.actions,
        }
        if self.value is not None:
            result["value"] = self.value
        if self.frame is not None:
            result["frame"] = self.frame.to_dict()
        return result


@dataclass
class AppInfo:
    name: str
    running: bool = True
    visible_windows: int = 0
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class StateSnapshot:
    app: str
    window_title: str
    focused_element: str | None
    controls: list[ControlNode]
    raw_text: str
    text_chars: int
    truncated_tree: bool
    truncated_text: bool
    image_path: str | None = None
    image_bytes: int = 0
    state_id: str | None = None
    created_at: float | None = None
    fingerprint: str = ""
    budget: tuple[int, int, int | str] | None = None
    vision_elements: list[GroundedElement] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "state_id": self.state_id,
            "app": self.app,
            "window_title": self.window_title,
            "focused_element": self.focused_element,
            "controls": [c.to_dict() for c in self.controls],
            "text_chars": self.text_chars,
            "truncated": {"tree": self.truncated_tree, "text": self.truncated_text},
            "image_path": self.image_path,
            "image_bytes": self.image_bytes,
            "fingerprint": self.fingerprint,
            "vision_elements": [element.to_dict() for element in self.vision_elements],
        }


@dataclass
class DeltaResult:
    window_title_changed: bool
    focused_changed: bool
    added: list[ControlNode]
    removed: list[ControlNode]
    changed: list[tuple[ControlNode, ControlNode]]
    modal_detected: bool
    truncated_tree: bool
    truncated_text: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "window_title_changed": self.window_title_changed,
            "focused_changed": self.focused_changed,
            "added": [c.to_dict() for c in self.added],
            "removed": [c.to_dict() for c in self.removed],
            "changed": [
                {"before": before.to_dict(), "after": after.to_dict()}
                for before, after in self.changed
            ],
            "modal_detected": self.modal_detected,
            "truncated": {"tree": self.truncated_tree, "text": self.truncated_text},
        }