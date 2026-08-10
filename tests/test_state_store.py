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


def test_get_unknown_app_is_stale():
    store = StateStore(ttl_seconds=60)
    with pytest.raises(StaleStateError) as exc:
        store.get("Ghost", "deadbeef")
    assert "No state exists" in str(exc.value)


def test_get_missing_snapshot_is_stale():
    store = StateStore(ttl_seconds=60)
    store.put(_snapshot("Notepad"))
    store._current["Notepad"] = "ghost-id"  # snapshot purged out-of-band
    with pytest.raises(StaleStateError) as exc:
        store.get("Notepad", "ghost-id")
    assert "missing" in str(exc.value)
    assert store.current("Notepad") is None  # invalidated


def test_put_purges_expired_and_evicts_over_capacity(monkeypatch):
    import lean_computer_use_mcp.state.store as store_module

    fake_clock = {"now": 1000.0}
    monkeypatch.setattr(store_module.time, "time", lambda: fake_clock["now"])
    store = StateStore(ttl_seconds=10, max_entries=2)
    store.put(_snapshot("A"))
    fake_clock["now"] += 11
    store.put(_snapshot("B"))  # purges expired A, keeps B
    assert store.current("A") is None
    assert store.stats() == {"apps": 1, "snapshots": 1}
    store.put(_snapshot("C"))
    store.put(_snapshot("D"))
    assert store.stats() == {"apps": 3, "snapshots": 2}  # capacity enforced


def test_invalidate_removes_app_and_snapshot():
    store = StateStore(ttl_seconds=60)
    snapshot = store.put(_snapshot("Notepad"))
    store.invalidate("Notepad")
    assert store.current("Notepad") is None
    assert snapshot.state_id not in store._snapshots
