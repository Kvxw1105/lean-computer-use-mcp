from __future__ import annotations

import secrets
import time

from lean_computer_use_mcp.errors import StaleStateError
from lean_computer_use_mcp.models import StateSnapshot
from lean_computer_use_mcp.state.fingerprint import fingerprint


class StateStore:
    def __init__(self, ttl_seconds: int = 30, max_entries: int = 64) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._snapshots: dict[str, StateSnapshot] = {}
        self._current: dict[str, str] = {}

    def put(self, snapshot: StateSnapshot) -> StateSnapshot:
        snapshot.state_id = secrets.token_hex(6)
        snapshot.created_at = time.time()
        snapshot.fingerprint = fingerprint(snapshot)
        self._snapshots[snapshot.state_id] = snapshot
        self._current[snapshot.app] = snapshot.state_id
        self._purge()
        return snapshot

    def get(self, app: str, state_id: str) -> StateSnapshot:
        current_id = self._current.get(app)
        if current_id is None:
            raise StaleStateError(f"No state exists for app {app!r}")
        if state_id != current_id:
            raise StaleStateError(
                f"State changed for app {app!r}; re-observe before acting.",
                current_state_id=current_id,
            )
        snapshot = self._snapshots.get(current_id)
        if snapshot is None or snapshot.created_at is None:
            self.invalidate(app)
            raise StaleStateError(f"State for app {app!r} is missing")
        if time.time() - snapshot.created_at > self.ttl_seconds:
            self.invalidate(app)
            raise StaleStateError(f"State for app {app!r} expired")
        return snapshot

    def current(self, app: str) -> str | None:
        return self._current.get(app)

    def invalidate(self, app: str) -> None:
        state_id = self._current.pop(app, None)
        if state_id is not None:
            self._snapshots.pop(state_id, None)

    def stats(self) -> dict[str, int]:
        return {"apps": len(self._current), "snapshots": len(self._snapshots)}

    def _purge(self) -> None:
        now = time.time()
        expired = [
            state_id
            for state_id, snapshot in self._snapshots.items()
            if snapshot.created_at is not None and now - snapshot.created_at > self.ttl_seconds
        ]
        for state_id in expired:
            self._snapshots.pop(state_id, None)
        for app, state_id in list(self._current.items()):
            if state_id not in self._snapshots:
                self._current.pop(app, None)
        if len(self._snapshots) > self.max_entries:
            oldest = sorted(
                self._snapshots.items(), key=lambda item: item[1].created_at or 0
            )[: len(self._snapshots) - self.max_entries]
            for state_id, _ in oldest:
                self._snapshots.pop(state_id, None)