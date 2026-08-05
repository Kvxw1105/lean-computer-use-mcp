from lean_computer_use_mcp.parse.tree_parser import detect_truncation, parse_state


def test_parse_realistic_state(control_state_text):
    title, focused, controls = parse_state(control_state_text)
    assert title == "ChatGPT"
    assert focused is not None
    assert "打开个人资料菜单" in focused
    assert any(control.role == "按钮" and control.name == "最小化" for control in controls)
    assert all(control.index.isdigit() for control in controls)


def test_parse_extracts_frames(control_state_text):
    _, _, controls = parse_state(control_state_text)
    window = next(control for control in controls if control.role == "window")
    assert window.frame is not None
    assert window.frame.width == 2906


def test_truncation_heuristic():
    tree_truncated, text_truncated = detect_truncation("...\n")
    assert tree_truncated is True
    assert text_truncated is True