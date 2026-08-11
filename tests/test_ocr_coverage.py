"""Coverage for WinRT and RapidOCR engines with stubbed optional deps."""

from __future__ import annotations

import builtins
import io
import sys
import types

import pytest
from PIL import Image

from lean_computer_use_mcp.vision.base import VisionConfig, VisionEngineUnavailable
from lean_computer_use_mcp.vision.ocr import RapidOCREngine, WinRTOCREngine, build_engine


def _png_bytes(width: int = 64, height: int = 48) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color=(30, 30, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


# --- fake winrt runtime -----------------------------------------------------


class _Rect:
    def __init__(self, x, y, width, height):
        self.x = x
        self.y = y
        self.width = width
        self.height = height


class _Word:
    def __init__(self, text, x=0, y=0, width=10, height=10):
        self.text = text
        self.bounding_rect = _Rect(x, y, width, height)


class _Line:
    def __init__(self, words):
        self.words = words


class _OcrResult:
    def __init__(self, lines):
        self.lines = lines


class FakeWinrtOcrEngine:
    def __init__(self, result):
        self._result = result
        self.bitmaps = []

    async def recognize_async(self, bitmap):
        self.bitmaps.append(bitmap)
        return self._result


class _OcrEngineFactory:
    def __init__(self, languages, profile_engine, result):
        self.available_recognizer_languages = languages
        self._profile_engine = profile_engine
        self._result = result
        self.created: list[str] = []

    def try_create_from_language(self, language):
        self.created.append(language.language_tag)
        return FakeWinrtOcrEngine(self._result)

    def try_create_from_user_profile_languages(self):
        self.created.append("profile")
        return self._profile_engine


class _Language:
    def __init__(self, tag):
        self.language_tag = tag


class _DataWriter:
    def __init__(self):
        self._bytes = b""

    def write_bytes(self, data):
        self._bytes = data

    def detach_buffer(self):
        return self._bytes


class _SoftwareBitmap:
    def __init__(self, pixel_format, width, height):
        self.pixel_format = pixel_format
        self.width = width
        self.height = height
        self.buffer = None

    def copy_from_buffer(self, buf):
        self.buffer = buf


class _BitmapPixelFormat:
    RGBA8 = "rgba8"


def _install_winrt(monkeypatch, factory, result=None):
    """Inject a fake winrt package tree; returns (ocr_module, engine)."""
    modules: dict[str, types.ModuleType] = {}
    for name in (
        "winrt",
        "winrt.windows",
        "winrt.windows.graphics",
        "winrt.windows.graphics.imaging",
        "winrt.windows.media",
        "winrt.windows.media.ocr",
        "winrt.windows.storage",
        "winrt.windows.storage.streams",
        "winrt.windows.globalization",
    ):
        module = types.ModuleType(name)
        monkeypatch.setitem(sys.modules, name, module)
        modules[name] = module

    ocr_module = modules["winrt.windows.media.ocr"]
    ocr_module.OcrEngine = factory
    imaging_module = modules["winrt.windows.graphics.imaging"]
    imaging_module.SoftwareBitmap = _SoftwareBitmap
    imaging_module.BitmapPixelFormat = _BitmapPixelFormat
    streams_module = modules["winrt.windows.storage.streams"]
    streams_module.DataWriter = _DataWriter
    modules["winrt.windows.globalization"].Language = _Language

    engine = FakeWinrtOcrEngine(result if result is not None else _OcrResult([]))
    return ocr_module, engine


def _install_rapidocr(monkeypatch, engine):
    module = types.ModuleType("rapidocr_onnxruntime")
    module.RapidOCR = lambda: engine
    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", module)


def _install_blocked_import(monkeypatch, blocked_name: str) -> None:
    real_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name == blocked_name or name.startswith(blocked_name + "."):
            raise ImportError(f"{blocked_name} blocked for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)


# --- WinRT engine -----------------------------------------------------------


def test_winrt_import_error_mid_chain_raises(monkeypatch):
    _install_blocked_import(monkeypatch, "winrt.windows.globalization")
    with pytest.raises(VisionEngineUnavailable, match="requires optional winrt"):
        WinRTOCREngine()._ensure_engine()


def test_winrt_picks_preferred_language_and_caches(monkeypatch):
    factory = _OcrEngineFactory(
        [_Language("en-us"), _Language("zh-hans-cn"), _Language("ja-jp")],
        profile_engine=FakeWinrtOcrEngine(_OcrResult([])),
        result=_OcrResult([]),
    )
    _install_winrt(monkeypatch, factory)
    engine = WinRTOCREngine()
    first = engine._ensure_engine()
    second = engine._ensure_engine()  # cached: no re-import/selection
    assert first[0] is second[0]  # same OCR engine object
    assert first[1] is second[1]  # same executor
    assert factory.created == ["zh-hans-cn"]  # preferred tag chosen, loop broke


def test_winrt_falls_back_to_user_profile_languages(monkeypatch):
    profile_engine = FakeWinrtOcrEngine(_OcrResult([]))
    factory = _OcrEngineFactory(
        [_Language("en-us"), _Language("fr-fr")],
        profile_engine=profile_engine,
        result=_OcrResult([]),
    )
    _install_winrt(monkeypatch, factory)
    engine, _executor = WinRTOCREngine()._ensure_engine()
    assert engine is profile_engine
    assert factory.created == ["profile"]


def test_winrt_raises_when_no_engine_available(monkeypatch):
    factory = _OcrEngineFactory(
        [_Language("en-us")], profile_engine=None, result=_OcrResult([])
    )
    _install_winrt(monkeypatch, factory)
    with pytest.raises(VisionEngineUnavailable, match="no Windows OCR engine"):
        WinRTOCREngine()._ensure_engine()


def test_winrt_ground_extracts_words_and_skips_empty(monkeypatch):
    lines = [
        _Line([_Word("Hello", 0, 0, 50, 20), _Word("", 60, 0, 10, 10)]),
        _Line([_Word("\u4f60\u597d", 0, 30, 40, 20)]),
    ]
    factory = _OcrEngineFactory(
        [_Language("zh-hans-cn")],
        profile_engine=None,
        result=_OcrResult(lines),
    )
    _install_winrt(monkeypatch, factory, result=_OcrResult(lines))
    engine = WinRTOCREngine(config=VisionConfig(engine="winrt_ocr"))
    result = engine.ground(_png_bytes())
    assert result.engine == "winrt_ocr"
    assert [element.text for element in result.elements] == ["Hello", "\u4f60\u597d"]
    assert (
        result.elements[0].frame.x,
        result.elements[0].frame.y,
        result.elements[0].frame.width,
        result.elements[0].frame.height,
    ) == (0, 0, 50, 20)
    assert result.image_width == 64
    assert result.image_height == 48
    assert result.latency_ms >= 0


def test_winrt_ground_respects_max_elements(monkeypatch):
    lines = [
        _Line([_Word(str(index)) for index in range(10)]),
        _Line([_Word("tail")]),
    ]
    factory = _OcrEngineFactory(
        [_Language("zh-hans-cn")],
        profile_engine=None,
        result=_OcrResult(lines),
    )
    _install_winrt(monkeypatch, factory, result=_OcrResult(lines))
    engine = WinRTOCREngine(config=VisionConfig(engine="winrt_ocr", max_elements=3))
    result = engine.ground(_png_bytes())
    assert [element.text for element in result.elements] == ["0", "1", "2"]


# --- RapidOCR engine --------------------------------------------------------


class FakeRapidEngine:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def __call__(self, image_bytes):
        self.calls += 1
        return self.result


def _quad(x1, y1, x2, y2):
    return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]


def test_rapidocr_ground_boxes_and_filters(monkeypatch):
    engine = FakeRapidEngine(
        [
            (_quad(10, 20, 60, 40), "label", 0.99),
            (_quad(10, 50, 30, 60), "low", 0.3),
            (_quad(10, 70, 30, 80), "", 0.9),
            (_quad(10, 90, 30, 100), "none-score", None),
        ]
    )
    _install_rapidocr(monkeypatch, engine)
    result = RapidOCREngine(
        config=VisionConfig(engine="rapidocr", confidence_threshold=0.5)
    ).ground(_png_bytes())
    assert [element.text for element in result.elements] == ["label"]
    assert (
        result.elements[0].frame.x,
        result.elements[0].frame.y,
        result.elements[0].frame.width,
        result.elements[0].frame.height,
    ) == (10, 20, 50, 20)
    assert engine.calls == 1


def test_rapidocr_ground_empty_result(monkeypatch):
    engine = FakeRapidEngine(None)
    _install_rapidocr(monkeypatch, engine)
    result = RapidOCREngine().ground(_png_bytes())
    assert result.elements == []


def test_rapidocr_ground_respects_max_elements(monkeypatch):
    entries = [(_quad(0, 0, 10, 10), f"t{index}", 0.9) for index in range(6)]
    _install_rapidocr(monkeypatch, FakeRapidEngine(entries))
    result = RapidOCREngine(
        config=VisionConfig(engine="rapidocr", max_elements=2)
    ).ground(_png_bytes())
    assert [element.text for element in result.elements] == ["t0", "t1"]


# --- engine factory ---------------------------------------------------------


def test_build_engine_winrt_and_rapidocr_branches():
    assert isinstance(build_engine(VisionConfig(engine="winrt_ocr")), WinRTOCREngine)
    assert isinstance(build_engine(VisionConfig(engine="rapidocr")), RapidOCREngine)
