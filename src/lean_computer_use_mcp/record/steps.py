"""Turn raw input events plus element snapshots into intent-based steps.

Platform-neutral: ``build_steps`` only needs ``InputEvent``/``ElementTable``
data, so the whole pipeline is unit-testable without a desktop. Steps prefer
semantic targets (element role/name) and keep screenshot-pixel coordinates as
a fallback for custom-rendered UIs.
"""

from __future__ import annotations

from lean_computer_use_mcp.models import ControlNode, Frame
from lean_computer_use_mcp.record.keys import (
    VK_RETURN,
    VK_SPACE,
    combo_name,
    is_modifier,
    is_printable,
    vk_name,
    vk_to_char,
)
from lean_computer_use_mcp.record.model import (
    ElementRef,
    ElementTable,
    InputEvent,
    RecordedStep,
)

#: Click-match tolerance in screenshot pixels when no frame contains the point.
_CLICK_MARGIN = 24
#: Coalescing windows (seconds) for wheel and typing events.
_SCROLL_COALESCE = 0.35
_TYPE_GAP = 2.0
_AUTO_REPEAT_GAP = 0.06

#: Target names that look commit-like; such steps demand confirmation at replay.
_COMMIT_HINTS = (
    "submit",
    "send",
    "publish",
    "发布",
    "提交",
    "发送",
    "保存",
    "save",
    "confirm",
    "确定",
    "delete",
    "删除",
    "approve",
    "购买",
    "付款",
    "buy",
    "pay",
)


def is_commit_name(name: str) -> bool:
    lowered = name.lower()
    return any(hint in lowered for hint in _COMMIT_HINTS)


def point_in_frame(x: int, y: int, frame: Frame, margin: int = 0) -> bool:
    """True when (x, y) is inside the frame, expanded by ``margin`` pixels."""
    return (
        frame.x - margin <= x < frame.x + frame.width + margin
        and frame.y - margin <= y < frame.y + frame.height + margin
    )


def match_element(
    elements: list[ControlNode], x: int, y: int, margin: int = _CLICK_MARGIN
) -> ControlNode | None:
    """Best element for a click point.

    Prefers the smallest frame that contains the point; otherwise the nearest
    frame within ``margin`` pixels (nearest by center distance).
    """
    containing = [
        node
        for node in elements
        if node.frame is not None and point_in_frame(x, y, node.frame)
    ]
    if containing:
        return min(containing, key=lambda node: node.frame.width * node.frame.height)  # type: ignore[union-attr]
    nearest: tuple[ControlNode, float] | None = None
    for node in elements:
        if node.frame is None:
            continue
        cx = node.frame.x + node.frame.width / 2
        cy = node.frame.y + node.frame.height / 2
        distance = ((cx - x) ** 2 + (cy - y) ** 2) ** 0.5
        if distance <= margin and (nearest is None or distance < nearest[1]):
            nearest = (node, distance)
    return nearest[0] if nearest else None


def _latest_table(
    snapshots: list[ElementTable],
    title: str,
    pid: int,
    ts: float,
    tolerance: float = 3.0,
) -> ElementTable | None:
    """Nearest snapshot for this window around ``ts``.

    Prefers the latest table taken at or before the event; when the event is
    earlier than the first snapshot (common at recording start) the earliest
    snapshot within ``tolerance`` seconds is used.
    """
    matched = [table for table in snapshots if table.window_title == title]
    if pid:
        matched = [
            table
            for table in matched
            if not table.window_pid or table.window_pid == pid
        ]
    before = [table for table in matched if table.ts <= ts]
    if before:
        return max(before, key=lambda table: table.ts)
    near = [table for table in matched if abs(table.ts - ts) <= tolerance]
    if near:
        return min(near, key=lambda table: table.ts)
    return None


def build_steps(
    events: list[InputEvent],
    snapshots: list[ElementTable],
    stop_combo: tuple[str, str, str] = ("Control", "Shift", "R"),
) -> list[RecordedStep]:
    """Build intent steps from a raw event stream.

    ``stop_combo`` names are filtered out so the recorder's own stop hotkey
    never becomes part of the workflow.
    """
    ordered = sorted(events, key=lambda event: event.ts)
    tables = sorted(snapshots, key=lambda table: table.ts)
    steps: list[RecordedStep] = []
    modifiers: set[str] = set()
    pending: list[str] = []
    pending_window = ""
    pending_started = 0.0
    last_wheel_ts = 0.0
    last_key_ts = 0.0
    last_scroll_index: int | None = None
    skip_vk: set[int] = set()
    # IME session state: an active IME swallows printable keys into one
    # type_text step carrying the composed text (ime_text) plus the original
    # key sequence (ime_keys) as the replay fallback.
    ime_active = False
    ime_window = ""
    ime_commits: list[str] = []
    ime_keys: list[str] = []
    ime_composition = ""

    def flush_type() -> None:
        nonlocal pending, pending_window
        if not pending:
            return
        steps.append(
            RecordedStep(
                action="type_text",
                window_title=pending_window,
                value="".join(pending),
            )
        )
        pending = []
        pending_window = ""

    def flush_ime() -> None:
        nonlocal ime_active, ime_window, ime_commits, ime_keys, ime_composition
        if not ime_active:
            return
        value = "".join(ime_commits) or None
        steps.append(
            RecordedStep(
                action="type_text",
                window_title=ime_window,
                value=value,
                ime_text=value,
                ime_keys=list(ime_keys) if ime_keys else None,
                uncertain=value is None,
            )
        )
        ime_active = False
        ime_window = ""
        ime_commits = []
        ime_keys = []
        ime_composition = ""

    def click_target(
        event: InputEvent,
    ) -> tuple[ElementRef | None, int | None, int | None, bool]:
        offset = event.offset()
        if offset is None:
            return None, None, None, False
        ox, oy = offset
        table = _latest_table(tables, event.window_title, event.window_pid, event.ts)
        if table is None or (
            event.window_rect
            and table.window_rect
            and table.window_rect != event.window_rect
        ):
            return None, ox, oy, False
        node = match_element(table.elements, ox, oy)
        if node is None:
            return None, ox, oy, False
        return (
            ElementRef(role=node.role, name=node.name, frame=node.frame),
            ox,
            oy,
            True,
        )

    for event in ordered:
        if event.kind == "key_down":
            if is_modifier(event.vk or 0):
                modifiers.add(vk_name(event.vk or 0))
                continue
            if {"Control", "Shift"} <= modifiers and combo_name(
                modifiers, event.vk or 0
            ) == "+".join(stop_combo):
                skip_vk.add(event.vk or 0)
                continue
            if event.vk in skip_vk:
                continue
            if {"Control", "Alt", "Win"} & modifiers:
                if event.ime_open:
                    ime_keys.append(combo_name(modifiers, event.vk or 0))
                    ime_composition = event.ime_composition
                    continue
                flush_type()
                key = combo_name(modifiers, event.vk or 0)
                steps.append(
                    RecordedStep(
                        action="press_key",
                        window_title=event.window_title,
                        key=key,
                        commit=key.endswith("Enter"),
                    )
                )
                continue
            if ime_active and not event.ime_open:
                flush_ime()
            if event.ime_open:
                flush_type()
                if not ime_active:
                    ime_active = True
                    ime_window = event.window_title
                elif event.window_title != ime_window:
                    flush_ime()
                    ime_active = True
                    ime_window = event.window_title
                if ime_composition and not event.ime_composition:
                    if event.ime_commit:
                        ime_commits.append(event.ime_commit)
                    ime_composition = ""
                else:
                    ime_composition = event.ime_composition
                if event.vk == VK_SPACE:
                    raw = "Space"
                else:
                    raw = (
                        vk_to_char(event.vk or 0, shift="Shift" in modifiers)
                        or vk_name(event.vk or 0)
                    )
                ime_keys.append(raw)
                continue
            if event.window_title != pending_window and pending:
                flush_type()
            if event.vk in (VK_RETURN,) or not is_printable(event.vk or 0):
                flush_type()
                key = vk_name(event.vk or 0)
                if key not in ("Shift", "Control", "Alt", "Win"):
                    combo = combo_name(modifiers, event.vk or 0)
                    steps.append(
                        RecordedStep(
                            action="press_key",
                            window_title=event.window_title,
                            key=combo,
                            commit=combo.endswith("Enter"),
                        )
                    )
                continue
            char = vk_to_char(event.vk or 0, shift="Shift" in modifiers)
            now = event.ts
            if pending and now - pending_started > _TYPE_GAP:
                flush_type()
            if (
                pending
                and pending[-1] == char
                and now - last_key_ts <= _AUTO_REPEAT_GAP
            ):
                continue
            if not pending:
                pending_window = event.window_title
                pending_started = now
            pending.append(char or "")
            last_key_ts = now
            continue
        if event.kind == "key_up":
            if is_modifier(event.vk or 0):
                modifiers.discard(vk_name(event.vk or 0))
                continue
            if event.vk in skip_vk:
                skip_vk.discard(event.vk or 0)
                continue
            if ime_active and not event.ime_open:
                flush_ime()
            elif event.ime_open:
                if ime_composition and not event.ime_composition:
                    if event.ime_commit:
                        ime_commits.append(event.ime_commit)
                    ime_composition = ""
                else:
                    ime_composition = event.ime_composition
            continue
        if event.kind == "mouse_down":
            if event.offset() is None:
                continue
            flush_ime()
            flush_type()
            flush_type()
            if event.button != "left":
                continue
            target, ox, oy, matched = click_target(event)
            steps.append(
                RecordedStep(
                    action="click",
                    window_title=event.window_title,
                    target=target,
                    x=ox,
                    y=oy,
                    matched=matched,
                    commit=bool(target and is_commit_name(target.name)),
                    uncertain=not matched,
                )
            )
            continue
        if event.kind == "wheel":
            flush_type()
            notches = max(1, min(5, abs(event.wheel_delta) // 120 or 1))
            if (
                last_scroll_index is not None
                and event.ts - last_wheel_ts <= _SCROLL_COALESCE
                and steps[last_scroll_index].action == "scroll"
                and steps[last_scroll_index].window_title == event.window_title
            ):
                existing = steps[last_scroll_index]
                existing.pages = min(5.0, existing.pages + notches)
                last_wheel_ts = event.ts
                continue
            target, _ox, _oy, _matched = click_target(event)
            steps.append(
                RecordedStep(
                    action="scroll",
                    window_title=event.window_title,
                    target=target,
                    direction="up" if event.wheel_delta > 0 else "down",
                    pages=float(notches),
                )
            )
            last_scroll_index = len(steps) - 1
            last_wheel_ts = event.ts
            continue
        # mouse_up and anything else: no step on its own
    flush_ime()
    flush_type()
    return steps
