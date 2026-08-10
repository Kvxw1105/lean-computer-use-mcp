"""IME composition capture: sampler, step building, replay fallback."""

from __future__ import annotations

from pathlib import Path

from lean_computer_use_mcp.record.keys import VK_SPACE
from lean_computer_use_mcp.record.model import (
    InputEvent,
    RecordedStep,
    Recording,
)
from lean_computer_use_mcp.record.replay import ReplayRunner
from lean_computer_use_mcp.record.steps import build_steps
from lean_computer_use_mcp.record.win_hooks import ImeSampler
from lean_computer_use_mcp.server import LeanComputerUse
from lean_computer_use_mcp.upstream.fake_client import FakeUpstreamClient

FIXTURES = Path(__file__).parent.parent / "examples" / "fixtures"


def key(
    kind: str,
    vk: int,
    *,
    ts: float,
    ime_open: bool = False,
    composition: str = "",
    commit: str = "",
    window: str = "ChatGPT",
) -> InputEvent:
    return InputEvent(
        ts=ts,
        kind=kind,
        vk=vk,
        window_title=window,
        window_pid=123,
        window_rect=(0, 0, 1000, 800),
        ime_open=ime_open,
        ime_composition=composition,
        ime_commit=commit,
    )


def pinyin_stream(commit_text: str = "\u4f60\u597d", start: float = 100.0):
    """Typing 'nihao' + Space under a Chinese IME, one event per step."""
    events = []
    ts = start
    comp = ""
    for char in "nihao":
        comp += char
        events.append(key("key_down", ord(char.upper()), ts=ts, ime_open=True, composition=comp))
        ts += 0.1
        events.append(key("key_up", ord(char.upper()), ts=ts, ime_open=True, composition=comp))
        ts += 0.1
    # Space commits: the composition empties and the result string appears.
    events.append(key("key_down", VK_SPACE, ts=ts, ime_open=True, composition="nihao"))
    ts += 0.1
    events.append(
        key("key_up", VK_SPACE, ts=ts, ime_open=True, composition="", commit=commit_text)
    )
    return events


# --- ImeSampler (mocked Win32) ----------------------------------------------


class FakeImm32:
    def __init__(self):
        self.composition = ""
        self.result = ""
        self.open = True
        self.contexts = 0
        self.releases = 0

    def ImmGetContext(self, focus):
        self.contexts += 1
        return 100

    def ImmGetOpenStatus(self, imc):
        return self.open

    def ImmGetCompositionStringW(self, imc, flag, buf, size):
        text = self.composition if flag == 0x0008 else self.result
        if buf is None:
            return len(text) * 2
        if size <= 0:
            return 0
        chars = text[: size // 2]
        for index, char in enumerate(chars):
            buf[index] = char
        buf[len(chars)] = "\x00"
        return len(chars) * 2

    def ImmReleaseContext(self, focus, imc):
        self.releases += 1
        return True


class FakeUser32:
    def __init__(self, focus=5):
        self.focus = focus
        self.gui_calls = 0

    def GetForegroundWindow(self):
        return 7

    def GetWindowThreadProcessId(self, hwnd, out):
        out._obj.value = 42  # byref() hands us a CArgObject wrapper
        return 42

    def GetGUIThreadInfo(self, tid, gui):
        self.gui_calls += 1
        gui._obj.hwndFocus = self.focus
        return True


def test_ime_sampler_reports_composition_and_commit():
    imm = FakeImm32()
    sampler = ImeSampler(user32=FakeUser32(), imm32=imm)
    imm.composition = "nihao"
    assert sampler.sample() == (True, "nihao", "")
    imm.composition = ""
    imm.result = "\u4f60\u597d"
    assert sampler.sample() == (True, "", "\u4f60\u597d")
    # Repeated identical result is not re-reported (raw keys cover it).
    assert sampler.sample() == (True, "", "")


def test_ime_sampler_ime_closed():
    imm = FakeImm32()
    imm.open = False
    sampler = ImeSampler(user32=FakeUser32(), imm32=imm)
    assert sampler.sample() == (False, "", "")
    assert imm.releases == 1  # context always released


def test_ime_sampler_failure_is_safe():
    class BrokenImm:
        def ImmGetContext(self, focus):
            raise OSError("boom")

    sampler = ImeSampler(user32=FakeUser32(), imm32=BrokenImm())
    assert sampler.sample() == (False, "", "")


# --- step building -----------------------------------------------------------


def test_ime_stream_builds_type_text_with_composed_text():
    steps = build_steps(pinyin_stream(), [])
    typing = [step for step in steps if step.action == "type_text"]
    assert len(typing) == 1
    step = typing[0]
    assert step.value == "\u4f60\u597d"  # the real Chinese text
    assert step.ime_text == "\u4f60\u597d"
    assert step.ime_keys == ["n", "i", "h", "a", "o", "Space"]
    assert step.uncertain is False


def test_ime_without_sampled_text_keeps_raw_keys():
    events = pinyin_stream(commit_text="")  # sampling never recovered text
    steps = build_steps(events, [])
    typing = [step for step in steps if step.action == "type_text"]
    assert len(typing) == 1
    step = typing[0]
    assert step.value is None
    assert step.ime_text is None
    assert step.ime_keys == ["n", "i", "h", "a", "o", "Space"]
    assert step.uncertain is True  # replay must fall back to raw keys


def test_ime_session_flushes_on_mouse_click():
    events = pinyin_stream()
    events.append(
        InputEvent(
            ts=200.0,
            kind="mouse_down",
            x=100,
            y=100,
            button="left",
            window_title="ChatGPT",
            window_pid=123,
            window_rect=(0, 0, 1000, 800),
        )
    )
    steps = build_steps(events, [])
    assert [step.action for step in steps] == ["type_text", "click"]
    assert steps[0].value == "\u4f60\u597d"


def test_ime_session_ends_when_keys_no_longer_ime():
    events = pinyin_stream()
    events.append(
        key("key_down", ord("X"), ts=300.0, ime_open=False)
    )
    events.append(key("key_up", ord("X"), ts=300.1, ime_open=False))
    steps = build_steps(events, [])
    assert [step.action for step in steps] == ["type_text", "type_text"]
    assert steps[0].value == "\u4f60\u597d"
    assert steps[1].value == "x"


def test_ime_two_words_accumulate_one_step():
    events = pinyin_stream(commit_text="\u4f60\u597d", start=100.0)
    events += pinyin_stream(commit_text="\u4e16\u754c", start=200.0)
    steps = build_steps(events, [])
    typing = [step for step in steps if step.action == "type_text"]
    assert len(typing) == 1
    assert typing[0].value == "\u4f60\u597d\u4e16\u754c"


def test_latin_typing_unchanged_without_ime():
    events = [
        key("key_down", ord("A"), ts=1.0, ime_open=False),
        key("key_down", ord("B"), ts=1.1, ime_open=False),
    ]
    steps = build_steps(events, [])
    assert len(steps) == 1
    assert steps[0].action == "type_text"
    assert steps[0].value == "ab"
    assert steps[0].ime_keys is None


# --- round-trip + replay -----------------------------------------------------


def test_ime_fields_survive_recording_roundtrip(tmp_path):
    recording = Recording(
        name="ime",
        app="ChatGPT",
        description="",
        started_at=1.0,
        steps=[
            RecordedStep(
                action="type_text",
                window_title="ChatGPT",
                value="\u4f60\u597d",
                ime_text="\u4f60\u597d",
                ime_keys=["n", "i", "Space"],
            )
        ],
    )
    path = tmp_path / "recording.json"
    recording.save(path)
    loaded = Recording.load(path)
    assert loaded.steps[0].ime_text == "\u4f60\u597d"
    assert loaded.steps[0].ime_keys == ["n", "i", "Space"]


class KeyRecordingUpstream(FakeUpstreamClient):
    def __init__(self, fixture_dir):
        super().__init__(fixture_dir)
        self.key_presses: list[str] = []

    def act_with_refresh(
        self, app, tool, args, max_tree_nodes, max_tree_depth, text_limit
    ):
        if tool == "press_key":
            self.key_presses.append(args.get("key"))
        return self._read_text("state_chatgpt_after_modal.txt"), None, {"fake": True}


def test_replay_ime_raw_keys_presses_each_key():
    upstream = KeyRecordingUpstream(FIXTURES)
    server = LeanComputerUse(upstream, FakeSettings())
    recording = Recording(
        name="ime",
        app="ChatGPT",
        description="",
        started_at=1.0,
        steps=[
            RecordedStep(
                action="type_text",
                window_title="ChatGPT",
                value=None,
                ime_keys=["n", "i", "h", "a", "o", "Space"],
                uncertain=True,
            )
        ],
    )
    runner = ReplayRunner(server, confirm=lambda index, step: True)
    result = runner.run(recording, dry_run=False)
    assert result.ok is True
    assert upstream.key_presses == ["n", "i", "h", "a", "o", "Space"]


def test_replay_ime_with_text_uses_type_text_path():
    upstream = KeyRecordingUpstream(FIXTURES)
    server = LeanComputerUse(upstream, FakeSettings())
    recording = Recording(
        name="ime",
        app="ChatGPT",
        description="",
        started_at=1.0,
        steps=[
            RecordedStep(
                action="type_text",
                window_title="ChatGPT",
                value="\u4f60\u597d",
                ime_text="\u4f60\u597d",
                ime_keys=["n", "i", "Space"],
            )
        ],
    )
    runner = ReplayRunner(server, confirm=lambda index, step: True)
    result = runner.run(recording, dry_run=False)
    assert result.ok is True
    assert upstream.key_presses == []  # composed text wins over raw keys


def FakeSettings():

    from lean_computer_use_mcp.config import Settings

    return Settings()
