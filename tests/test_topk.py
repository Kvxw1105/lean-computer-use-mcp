from lean_computer_use_mcp.parse.topk import filter_controls
from lean_computer_use_mcp.parse.tree_parser import parse_state


def test_control_mode_removes_containers(control_state_text):
    _, _, controls = parse_state(control_state_text)
    selected = filter_controls(controls, output_mode="controls", intent="最小化", max_results=10)
    assert selected
    assert all(control.role != "区域" for control in selected)
    assert any("最小化" in (control.name or "") for control in selected)


def test_reading_mode_keeps_named_nodes(control_state_text):
    _, _, controls = parse_state(control_state_text)
    selected = filter_controls(controls, output_mode="reading", max_results=50)
    assert selected
    assert all(control.name or control.value for control in selected)