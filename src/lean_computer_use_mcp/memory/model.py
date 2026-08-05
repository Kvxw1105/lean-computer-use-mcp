"""Data model for the procedural memory layer.

A task is a sequence of atomic components. Components are identified by a
semantic fingerprint (app + action + role + normalized target name) instead
of a task id, so a new task can reuse components from many previous tasks.
Value-bearing steps are parameterized: the recorded concrete value (for
example "12") is replaced by a template placeholder so the same component
serves any value.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lean_computer_use_mcp.models import Frame
from lean_computer_use_mcp.record.model import RecordedStep

MEMORY_VERSION = 1

_SLUG_RE = re.compile(r"[^a-z0-9\u4e00-\u9fff]+")


def norm_name(name: str) -> str:
    """Normalize a target name for fingerprinting and matching."""
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def slugify(text: str, max_length: int = 48) -> str:
    """Filesystem-safe slug for component ids (keeps CJK characters)."""
    slug = _SLUG_RE.sub("-", norm_name(text)).strip("-")
    return slug[:max_length].rstrip("-") or "unnamed"


@dataclass
class Component:
    """One atomic, reusable UI action unit."""

    app: str
    action: str  # focus | click | scroll | type_text | press_key
    role: str = ""
    name: str = ""  # semantic target name ("" for focus/type_text)
    frame: Frame | None = None
    x: int | None = None  # recorded coordinate fallback
    y: int | None = None
    value_template: str | None = None  # type_text template, e.g. "{value}"
    key: str | None = None
    direction: str | None = None
    pages: float = 1.0
    preconditions: list[str] = field(default_factory=list)
    effect: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    description: str = ""  # LLM-refined one-line purpose (optional)
    hits: int = 0
    misses: int = 0
    created_at: float = 0.0
    last_used_at: float = 0.0

    @property
    def id(self) -> str:
        return fingerprint(self.app, self.action, self.role, self.name)

    def to_step(self) -> RecordedStep:
        """Rebuild a replayable step; the value placeholder becomes empty."""
        from lean_computer_use_mcp.record.model import ElementRef

        target = (
            ElementRef(role=self.role, name=self.name, frame=self.frame)
            if self.name
            else None
        )
        value = (
            self.value_template.replace("{value}", "") if self.value_template else None
        )
        return RecordedStep(
            action=self.action,
            window_title=self.app,
            target=target,
            x=self.x,
            y=self.y,
            value=value,
            key=self.key,
            direction=self.direction,
            pages=self.pages,
            matched=bool(target),
            value_placeholder=self.value_template is not None,
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "app": self.app,
            "action": self.action,
            "role": self.role,
            "name": self.name,
            "hits": self.hits,
            "misses": self.misses,
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
        }
        if self.frame is not None:
            result["frame"] = self.frame.to_dict()
        if self.x is not None:
            result["x"] = self.x
        if self.y is not None:
            result["y"] = self.y
        if self.value_template is not None:
            result["value_template"] = self.value_template
        if self.key is not None:
            result["key"] = self.key
        if self.direction is not None:
            result["direction"] = self.direction
            result["pages"] = self.pages
        if self.preconditions:
            result["preconditions"] = self.preconditions
        if self.effect:
            result["effect"] = self.effect
        if self.aliases:
            result["aliases"] = self.aliases
        if self.description:
            result["description"] = self.description
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Component":
        frame = data.get("frame")
        return cls(
            app=str(data.get("app", "")),
            action=str(data.get("action", "")),
            role=str(data.get("role", "")),
            name=str(data.get("name", "")),
            frame=Frame(**frame) if frame else None,
            x=data.get("x"),
            y=data.get("y"),
            value_template=data.get("value_template"),
            key=data.get("key"),
            direction=data.get("direction"),
            pages=float(data.get("pages", 1.0)),
            preconditions=[str(item) for item in data.get("preconditions", [])],
            effect=[str(item) for item in data.get("effect", [])],
            aliases=[str(item) for item in data.get("aliases", [])],
            description=str(data.get("description", "")),
            hits=int(data.get("hits", 0)),
            misses=int(data.get("misses", 0)),
            created_at=float(data.get("created_at", 0.0)),
            last_used_at=float(data.get("last_used_at", 0.0)),
        )


@dataclass
class TaskTemplate:
    """A learned task as an ordered sequence of component ids."""

    id: str
    name: str
    app: str
    description: str
    component_ids: list[str] = field(default_factory=list)
    hits: int = 0
    created_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "app": self.app,
            "description": self.description,
            "component_ids": list(self.component_ids),
            "hits": self.hits,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskTemplate":
        return cls(
            id=str(data.get("id", "")),
            name=str(data.get("name", "")),
            app=str(data.get("app", "")),
            description=str(data.get("description", "")),
            component_ids=[str(item) for item in data.get("component_ids", [])],
            hits=int(data.get("hits", 0)),
            created_at=float(data.get("created_at", 0.0)),
        )


@dataclass
class MemoryLibrary:
    """The procedural memory: components + templates + index aliases."""

    components: dict[str, Component] = field(default_factory=dict)
    templates: dict[str, TaskTemplate] = field(default_factory=dict)
    version: int = MEMORY_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "components": [
                component.to_dict() for component in self.components.values()
            ],
            "templates": [template.to_dict() for template in self.templates.values()],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemoryLibrary":
        library = cls(version=int(data.get("version", MEMORY_VERSION)))
        for item in data.get("components", []):
            component = Component.from_dict(item)
            library.components[component.id] = component
        for item in data.get("templates", []):
            template = TaskTemplate.from_dict(item)
            library.templates[template.id] = template
        return library

    @classmethod
    def load(cls, path: str | Path) -> "MemoryLibrary":
        raw = Path(path).read_text(encoding="utf-8")
        return cls.from_dict(json.loads(raw))

    def save(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def fingerprint(app: str, action: str, role: str, name: str) -> str:
    """Semantic identity of a component (not a task id)."""
    parts = [slugify(app), action]
    if role:
        parts.append(slugify(role))
    if name:
        parts.append(slugify(name))
    return "::".join(parts)
