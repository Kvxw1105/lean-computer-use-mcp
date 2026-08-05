from __future__ import annotations

import re

from lean_computer_use_mcp.models import ControlNode

_CONTAINER_ROLES = {"区域", "窗口", "window", "pane", "组", "group", "文档", "document"}
_WORD_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]{2,}")


def filter_controls(
    nodes: list[ControlNode],
    output_mode: str = "controls",
    intent: str = "",
    max_results: int = 20,
) -> list[ControlNode]:
    if output_mode == "full":
        return nodes[:max_results]
    if output_mode in {"controls", "visual"}:
        candidates = [
            node
            for node in nodes
            if node.role not in _CONTAINER_ROLES
        ]
    else:
        candidates = [node for node in nodes if node.name or node.value]

    terms = _tokenize(intent)

    def score(node: ControlNode) -> int:
        haystack = f"{node.role} {node.name} {node.value or ''}".lower()
        return sum(1 for term in terms if term in haystack)

    def index_key(node: ControlNode) -> int:
        try:
            return int(node.index)
        except ValueError:
            return 0

    ranked = sorted(
        candidates,
        key=lambda node: (score(node), len(node.name or ""), index_key(node)),
        reverse=True,
    )
    return ranked[:max_results]


def _tokenize(text: str) -> list[str]:
    return [token.lower() for token in _WORD_RE.findall(text)]