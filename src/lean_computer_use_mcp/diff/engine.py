from __future__ import annotations

from lean_computer_use_mcp.models import ControlNode, DeltaResult, StateSnapshot

_DIALOG_ROLES = {"dialog", "对话框", "alert", "警告"}
_DIALOG_MARKERS = ("对话框", "确认", "提示", "警告")


def diff(old: StateSnapshot, new: StateSnapshot) -> DeltaResult:
    old_map = {_stable_key(node): node for node in old.controls}
    new_map = {_stable_key(node): node for node in new.controls}

    added = [node for key, node in new_map.items() if key not in old_map]
    removed = [node for key, node in old_map.items() if key not in new_map]
    changed = [
        (before, after)
        for key, before in old_map.items()
        if (after := new_map.get(key)) is not None
        and (
            before.value != after.value
            or before.actions != after.actions
            or before.frame != after.frame
        )
    ]

    modal_detected = any(
        node.role in _DIALOG_ROLES or any(marker in (node.name or "") for marker in _DIALOG_MARKERS)
        for node in added
    ) or any(marker in (new.window_title or "") for marker in _DIALOG_MARKERS)

    return DeltaResult(
        window_title_changed=old.window_title != new.window_title,
        focused_changed=old.focused_element != new.focused_element,
        added=added,
        removed=removed,
        changed=changed,
        modal_detected=modal_detected,
        truncated_tree=new.truncated_tree,
        truncated_text=new.truncated_text,
    )


def _stable_key(node: ControlNode) -> tuple[str, str, str | None]:
    frame = tuple(sorted(node.frame.to_dict().items())) if node.frame else None
    return (node.role, node.name, frame)
