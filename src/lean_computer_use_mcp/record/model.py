"""Platform-neutral data model for recorded workflows.

A :class:`Recording` is an ordered list of intent-based steps plus compact
element tables that were visible while the user demonstrated a workflow. No
screenshots or raw accessibility trees are stored: :class:`ElementTable`
keeps only parsed controls plus the character/byte counts required for
metrics (see AGENTS.md).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lean_computer_use_mcp.models import ControlNode, Frame

RECORDING_VERSION = 1

#: Actions that only touch window placement; these never need confirmation.
WINDOW_ACTIONS = frozenset({"focus"})
#: Actions that change application content; these need explicit confirmation.
CONTENT_ACTIONS = frozenset({"click", "scroll", "type_text", "press_key"})


@dataclass(frozen=True)
class InputEvent:
    """One raw input event captured by a hook (mouse/keyboard)."""

    ts: float
    kind: str  # mouse_down | mouse_up | wheel | key_down | key_up
    x: int | None = None
    y: int | None = None
    button: str | None = None
    wheel_delta: int = 0
    vk: int | None = None
    window_title: str = ""
    window_pid: int = 0
    window_rect: tuple[int, int, int, int] | None = None

    def offset(self) -> tuple[int, int] | None:
        """Map screen coords to screenshot-pixel offsets for this event."""
        if self.x is None or self.y is None or self.window_rect is None:
            return None
        left, top, _right, _bottom = self.window_rect
        return (self.x - left, self.y - top)


@dataclass(frozen=True)
class ElementRef:
    """Semantic target for a recorded step; coordinates are a fallback."""

    role: str
    name: str
    frame: Frame | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"role": self.role, "name": self.name}
        if self.frame is not None:
            result["frame"] = self.frame.to_dict()
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ElementRef":
        frame = data.get("frame")
        return cls(
            role=str(data.get("role", "")),
            name=str(data.get("name", "")),
            frame=Frame(**frame) if frame else None,
        )


@dataclass
class ElementTable:
    """Compact element snapshot; never contains raw tree text or images."""

    ts: float
    window_title: str
    window_pid: int
    elements: list[ControlNode] = field(default_factory=list)
    text_chars: int = 0
    image_bytes: int = 0
    window_rect: tuple[int, int, int, int] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "window_title": self.window_title,
            "window_pid": self.window_pid,
            "elements": [node.to_dict() for node in self.elements],
            "text_chars": self.text_chars,
            "image_bytes": self.image_bytes,
            "window_rect": list(self.window_rect) if self.window_rect else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ElementTable":
        rect = data.get("window_rect")
        return cls(
            ts=float(data["ts"]),
            window_title=str(data.get("window_title", "")),
            window_pid=int(data.get("window_pid", 0)),
            elements=[control_from_dict(item) for item in data.get("elements", [])],
            text_chars=int(data.get("text_chars", 0)),
            image_bytes=int(data.get("image_bytes", 0)),
            window_rect=tuple(rect) if rect else None,
        )


@dataclass
class RecordedStep:
    """One intent-based step of a recorded workflow.

    ``target`` is the semantic element (preferred at replay); ``x``/``y`` are
    screenshot-pixel offsets kept as a fallback for custom-rendered UIs.
    """

    action: str  # focus | click | scroll | type_text | press_key
    window_title: str
    target: ElementRef | None = None
    x: int | None = None
    y: int | None = None
    value: str | None = None
    key: str | None = None
    direction: str | None = None
    pages: float = 1.0
    matched: bool = False
    commit: bool = False
    value_placeholder: bool = False  # value came from a template, not a recording

    @property
    def is_content(self) -> bool:
        return self.action in CONTENT_ACTIONS

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "action": self.action,
            "window_title": self.window_title,
            "matched": self.matched,
            "commit": self.commit,
        }
        if self.target is not None:
            result["target"] = self.target.to_dict()
        if self.x is not None:
            result["x"] = self.x
        if self.y is not None:
            result["y"] = self.y
        if self.value is not None:
            result["value"] = self.value
        if self.key is not None:
            result["key"] = self.key
        if self.direction is not None:
            result["direction"] = self.direction
            result["pages"] = self.pages
        if self.value_placeholder:
            result["value_placeholder"] = True
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RecordedStep":
        target = data.get("target")
        return cls(
            action=str(data.get("action", "")),
            window_title=str(data.get("window_title", "")),
            target=ElementRef.from_dict(target) if target else None,
            x=data.get("x"),
            y=data.get("y"),
            value=data.get("value"),
            key=data.get("key"),
            direction=data.get("direction"),
            pages=float(data.get("pages", 1.0)),
            matched=bool(data.get("matched", False)),
            commit=bool(data.get("commit", False)),
            value_placeholder=bool(data.get("value_placeholder", False)),
        )

    def describe(self) -> str:
        """One-line human description used by the CLI and the SKILL template."""
        target = self.target
        where = f" {target.name!r} ({target.role})" if target and target.name else ""
        if self.action == "focus":
            return f"Bring {self.window_title or 'the window'} to the foreground"
        if self.action == "click":
            if target and target.name:
                return f"Click{where}"
            return f"Click at ({self.x}, {self.y})"
            return f"Click at ({self.x}, {self.y})"
        if self.action == "scroll":
            return f"Scroll {self.direction or ''}{where}".strip()
        if self.action == "type_text":
            if self.value_placeholder:
                return f"Type <value> into{where}"
            return f"Type {self.value!r} into{where}"
        if self.action == "press_key":
            return f"Press {self.key} on{where}"
        return f"Run {self.action}"


@dataclass
class RecordingMetrics:
    """Honest cost counters for one recording lifecycle."""

    events: int = 0
    steps: int = 0
    snapshots: int = 0
    snapshot_errors: int = 0
    text_chars: int = 0
    image_bytes: int = 0
    nodes: int = 0
    duration_ms: int = 0

    def to_dict(self) -> dict[str, int]:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RecordingMetrics":
        return cls(
            **{
                key: int(value)
                for key, value in data.items()
                if key in cls.__dataclass_fields__
            }
        )


@dataclass
class Recording:
    """A recorded demonstration, serializable to JSON for replay."""

    name: str
    app: str
    description: str
    started_at: float
    snapshots: list[ElementTable] = field(default_factory=list)
    steps: list[RecordedStep] = field(default_factory=list)
    metrics: RecordingMetrics = field(default_factory=RecordingMetrics)
    version: int = RECORDING_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "name": self.name,
            "app": self.app,
            "description": self.description,
            "started_at": self.started_at,
            "snapshots": [table.to_dict() for table in self.snapshots],
            "steps": [step.to_dict() for step in self.steps],
            "metrics": self.metrics.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Recording":
        return cls(
            version=int(data.get("version", RECORDING_VERSION)),
            name=str(data.get("name", "recorded-skill")),
            app=str(data.get("app", "")),
            description=str(data.get("description", "")),
            started_at=float(data.get("started_at", 0.0)),
            snapshots=[
                ElementTable.from_dict(item) for item in data.get("snapshots", [])
            ],
            steps=[RecordedStep.from_dict(item) for item in data.get("steps", [])],
            metrics=RecordingMetrics.from_dict(data.get("metrics", {})),
        )

    @classmethod
    def load(cls, path: str | Path) -> "Recording":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def save(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def control_from_dict(data: dict[str, Any]) -> ControlNode:
    """Rehydrate a ControlNode from its ``to_dict`` payload."""
    frame = data.get("frame")
    return ControlNode(
        index=str(data.get("index", "")),
        role=str(data.get("role", "")),
        name=str(data.get("name", "")),
        depth=int(data.get("depth", 0)),
        value=data.get("value"),
        actions=[str(item) for item in data.get("actions", [])],
        frame=Frame(**frame) if frame else None,
    )
