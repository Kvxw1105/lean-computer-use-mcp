from __future__ import annotations

import hashlib

from lean_computer_use_mcp.models import StateSnapshot


def fingerprint(snapshot: StateSnapshot) -> str:
    lines = [f"{snapshot.app}|{snapshot.window_title}|{snapshot.focused_element}"]
    for node in snapshot.controls:
        frame = node.frame.to_dict() if node.frame else {}
        lines.append(
            "|".join(
                [
                    node.index,
                    node.role,
                    node.name,
                    str(node.value),
                    str(node.actions),
                    str(frame),
                ]
            )
        )
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:16]