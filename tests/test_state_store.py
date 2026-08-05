import pytest

from lean_computer_use_mcp.errors import StaleStateError
from lean_computer_use_mcp.models import StateSnapshot
from lean_computer_use_mcp.state.store import StateStore


def _snapshot(app: str = "Notepad") -> StateSnapshot:
    return StateSnapshot(
        app=app,
        window_title="Untitled - Notepad",
        focused_element=None,
        controls=[],
        raw_text="",
        text_chars=0,
        truncated_tree=False,
        truncated_text=False,
    )


def test_put_and_get_returns_snapshot():
    store = StateStore(ttl_seconds=60)
    snapshot = store.put(_snapshot())
    assert snapshot.state_id
    assert store.get("Notepad", snapshot.state_id) is snapshot


def test_non_current_state_is_stale():
    store = StateStore(ttl_seconds=60)
    first = store.put(_snapshot())
    second = store.put(_snapshot())
    with pytest.raises(StaleStateError) as exc:
        store.get("Notepad", first.state_id)
    assert exc.value.current_state_id == second.state_id


def test_expired_state_is_stale(monkeypatch):
    import lean_computer_use_mcp.state.store as store_module

    fake_clock = {"now": 1000.0}
    monkeypatch.setattr(store_module.time, "time", lambda: fake_clock["now"])
    store = StateStore(ttl_seconds=10)
    snapshot = store.put(_snapshot())
    fake_clock["now"] += 11
    with pytest.raises(StaleStateError):
        store.get("Notepad", snapshot.state_id)