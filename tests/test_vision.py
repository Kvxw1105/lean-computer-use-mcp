from __future__ import annotations

import builtins
import io

import pytest
from PIL import Image

from lean_computer_use_mcp.models import Frame
from lean_computer_use_mcp.vision.base import (
    GroundedElement,
    VisionConfig,
    VisionEngine,
    VisionEngineUnavailable,
)
from lean_computer_use_mcp.vision.fake import FakeVisionEngine
from lean_computer_use_mcp.vision.ocr import (
    RapidOCREngine,
    WinRTOCREngine,
    build_engine,
)


def _png_bytes(width: int = 64, height: int = 48) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color=(30, 30, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_fake_engine_returns_compact_text_table() -> None:
    engine = FakeVisionEngine()
    result = engine.ground(_png_bytes())
    assert result.engine == "fake"
    assert result.image_width == 64
    assert result.image_height == 48
    assert result.image_bytes > 0
    assert len(result.elements) == 2
    dumped = result.to_dict()
    assert "image" not in dumped  # never ship raw pixels to the planner
    assert dumped["elements"][0]["frame"] == {"x": 1820, "y": 24, "width": 44, "height": 28}


def test_fake_engine_filters_low_confidence() -> None:
    engine = FakeVisionEngine(config=VisionConfig(engine="fake", confidence_threshold=0.95))
    result = engine.ground(_png_bytes())
    assert len(result.elements) == 1


def test_fake_engine_observes_max_elements() -> None:
    elements = [
        GroundedElement(role="text", text=str(index), frame=Frame(index, 0, 10, 10))
        for index in range(10)
    ]
    engine = FakeVisionEngine(config=VisionConfig(engine="fake", max_elements=3), elements=elements)
    result = engine.ground(_png_bytes())
    assert len(result.elements) == 3


def test_build_engine_fake() -> None:
    engine = build_engine(VisionConfig(engine="fake"))
    assert isinstance(engine, FakeVisionEngine)


def test_build_engine_none_raises() -> None:
    with pytest.raises(VisionEngineUnavailable):
        build_engine(VisionConfig(engine="none"))


def test_fake_engine_satisfies_protocol() -> None:
    assert isinstance(FakeVisionEngine(), VisionEngine)


@pytest.mark.parametrize(
    ("module_name", "engine"),
    [("winrt", WinRTOCREngine()), ("rapidocr_onnxruntime", RapidOCREngine())],
)
def test_ocr_engines_degrade_cleanly_without_dependency(
    monkeypatch: pytest.MonkeyPatch, module_name: str, engine: object
) -> None:
    real_import = builtins.__import__

    def blocked_import(name: str, *args: object, **kwargs: object) -> object:
        if name == module_name or name.startswith(module_name + "."):
            raise ImportError(f"{module_name} blocked for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)
    with pytest.raises(VisionEngineUnavailable):
        engine.ground(_png_bytes())  # type: ignore[attr-defined]