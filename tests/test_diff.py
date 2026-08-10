from lean_computer_use_mcp.diff.engine import diff
from lean_computer_use_mcp.models import StateSnapshot
from lean_computer_use_mcp.parse.tree_parser import parse_state


def _snapshot(app: str, text: str) -> StateSnapshot:
    title, focused, controls = parse_state(text)
    return StateSnapshot(
        app=app,
        window_title=title,
        focused_element=focused,
        controls=controls,
        raw_text=text,
        text_chars=len(text),
        truncated_tree=False,
        truncated_text=False,
    )


def test_modal_is_detected(control_state_text, after_modal_state_text):
    delta = diff(
        _snapshot("ChatGPT", control_state_text),
        _snapshot("ChatGPT", after_modal_state_text),
    )
    assert delta.modal_detected is True
    assert any(node.role == "对话框" for node in delta.added)
    assert delta.focused_changed is True
