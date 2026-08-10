"""Replay auto-recovery: STALE_STATE triggers re-observe + retry (hard cap)."""

from __future__ import annotations

from pathlib import Path

from lean_computer_use_mcp.models import Frame
from lean_computer_use_mcp.record.model import ElementRef, RecordedStep, Recording
from lean_computer_use_mcp.record.replay import ReplayRunner
from lean_computer_use_mcp.server import LeanComputerUse
from lean_computer_use_mcp.upstream.fake_client import FakeUpstreamClient

FIXTURES = Path(__file__).parent.parent / "examples" / "fixtures"

MINIMIZE = ElementRef(
    role="button", name="\u6700\u5c0f\u5316", frame=Frame(2532, 13, 120, 96)
)


class StaleAtUpstream(FakeUpstreamClient):
    """Returns a different tree on selected reads to force STALE_STATE."""

    def __init__(self, fixture_dir: Path, stale_at: set[int]) -> None:
        super().__init__(fixture_dir)
        self.stale_at = stale_at
        self.reads = 0
        self.actions = 0

    def get_app_state(self, app, max_tree_nodes, max_tree_depth, text_limit):
        self.reads += 1
        if self.reads in self.stale_at:
            return self._read_text("state_chatgpt_after_modal.txt"), None
        return self._read_text("state_chatgpt_control.txt"), None

    def act_with_refresh(
        self, app, tool, args, max_tree_nodes, max_tree_depth, text_limit
    ):
        self.actions += 1
        return self._read_text("state_chatgpt_after_modal.txt"), None, {"fake": True}


def _single_click_recording() -> Recording:
    return Recording(
        name="one-click",
        app="ChatGPT",
        description="One content step",
        started_at=1700000000.0,
        steps=[
            RecordedStep(
                action="click",
                window_title="ChatGPT",
                target=MINIMIZE,
                matched=True,
            )
        ],
    )


def test_replay_recovers_from_single_stale(settings):
    upstream = StaleAtUpstream(FIXTURES, stale_at={2})  # first act-time gate flips
    server = LeanComputerUse(upstream, settings)
    runner = ReplayRunner(server, confirm=lambda index, step: True)
    result = runner.run(_single_click_recording(), dry_run=False)
    assert result.ok is True
    outcome = result.outcomes[0]
    assert outcome.ok is True
    assert outcome.stale_retries == 1
    assert outcome.resolution == "element"
    assert upstream.actions == 1  # only the retried attempt executed
    after = result.metrics_after
    assert after["observe_calls"] == 2  # initial observe + recovery re-observe
    assert after["action_calls"] == 2  # stale act (rejected) + retried act


def test_replay_fails_after_hard_cap_exceeded(settings):
    upstream = StaleAtUpstream(FIXTURES, stale_at={2, 4, 6, 8})
    server = LeanComputerUse(upstream, settings)
    runner = ReplayRunner(server, confirm=lambda index, step: True)
    result = runner.run(_single_click_recording(), dry_run=False)
    assert result.ok is False
    outcome = result.outcomes[0]
    assert outcome.ok is False
    assert outcome.error == "STALE_STATE"
    assert outcome.stale_retries == 3  # initial + 3 retries, then gave up
    assert upstream.actions == 0  # never executed
    assert result.metrics_after["observe_calls"] == 4


def test_replay_respects_custom_hard_cap(settings):
    upstream = StaleAtUpstream(FIXTURES, stale_at={2, 4, 6})
    server = LeanComputerUse(upstream, settings)
    runner = ReplayRunner(
        server, confirm=lambda index, step: True, max_stale_retries=1
    )
    result = runner.run(_single_click_recording(), dry_run=False)
    assert result.ok is False
    assert result.outcomes[0].stale_retries == 1
    assert result.metrics_after["observe_calls"] == 2


def test_replay_does_not_retry_non_stale_errors(settings):
    class MissingTargetUpstream(FakeUpstreamClient):
        def act_with_refresh(
            self, app, tool, args, max_tree_nodes, max_tree_depth, text_limit
        ):
            raise AssertionError("must not execute")

    server = LeanComputerUse(MissingTargetUpstream(FIXTURES), settings)
    recording = Recording(
        name="missing",
        app="ChatGPT",
        description="Target not in the tree",
        started_at=1700000000.0,
        steps=[
            RecordedStep(
                action="click",
                window_title="ChatGPT",
                target=ElementRef(role="button", name="NotThere"),
                matched=True,
            )
        ],
    )
    runner = ReplayRunner(server, confirm=lambda index, step: True)
    result = runner.run(recording, dry_run=False)
    assert result.ok is False
    outcome = result.outcomes[0]
    assert outcome.error == "ELEMENT_NOT_FOUND"
    assert outcome.stale_retries == 0
    assert result.metrics_after["observe_calls"] == 1  # no recovery re-observe


def test_replay_recovery_reuses_same_preset_and_vision(settings, monkeypatch):
    upstream = StaleAtUpstream(FIXTURES, stale_at={2})
    server = LeanComputerUse(upstream, settings)
    calls: list[dict] = []
    original_observe = server.observe

    def recording_observe(*args, **kwargs):
        calls.append(kwargs)
        return original_observe(*args, **kwargs)

    monkeypatch.setattr(server, "observe", recording_observe)
    runner = ReplayRunner(server, confirm=lambda index, step: True)
    result = runner.run(_single_click_recording(), dry_run=False)
    assert result.ok is True
    assert len(calls) == 2
    for kwargs in calls:
        assert kwargs["preset"] == "control"
        assert kwargs["vision"] == "auto"
        assert kwargs["output_mode"] == "controls"
