from __future__ import annotations

import io

import httpx
import pytest
from PIL import Image

from lean_computer_use_mcp.vision.base import VisionConfig, VisionEngineUnavailable
from lean_computer_use_mcp.vision.llm import LLMGroundingEngine, _parse_elements, _downscale


def _png_bytes(width: int = 2000, height: int = 1000) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color=(20, 20, 20)).save(buffer, format="PNG")
    return buffer.getvalue()


def _engine(handler, config: VisionConfig | None = None) -> LLMGroundingEngine:
    transport = httpx.MockTransport(handler)
    cfg = config or VisionConfig(
        engine="llm",
        api_base="https://example.test/v1",
        api_key="test-key",
        model="vision-model",
    )
    return LLMGroundingEngine(cfg, transport=transport)


def test_llm_requires_full_config() -> None:
    with pytest.raises(VisionEngineUnavailable):
        LLMGroundingEngine(VisionConfig(engine="llm", api_base="https://x", api_key="k")).ground(_png_bytes())


def test_llm_parses_elements_and_rescales_coordinates() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read()
        assert b"vision-model" in body
        assert b"data:image/jpeg;base64" in body
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"elements": [{"role": "button", "text": "Export", '
                                '"x": 100, "y": 50, "width": 20, "height": 10, "confidence": 0.95}]}'
                            )
                        }
                    }
                ]
            },
        )

    engine = _engine(handler)
    result = engine.ground(_png_bytes(), hint="find export")
    assert result.engine == "llm"
    assert result.image_width == 2000
    assert result.image_height == 1000
    assert len(result.elements) == 1
    element = result.elements[0]
    assert element.role == "button"
    assert element.text == "Export"
    # 2000px image was downscaled to 1568px, so boxes scale back up by ~1.276
    assert element.frame.x == 128
    assert element.frame.y == 64
    assert element.frame.width == 26
    assert element.frame.height == 13


def test_llm_strips_code_fences() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                '```json\n{"elements": [{"role": "text", "text": "OK", '
                                '"x": 1, "y": 2, "width": 3, "height": 4, "confidence": 1.0}]}\n```'
                            )
                        }
                    }
                ]
            },
        )

    result = _engine(handler).ground(_png_bytes())
    assert len(result.elements) == 1
    assert result.elements[0].text == "OK"


def test_llm_filters_low_confidence() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"elements": ['
                                '{"role": "button", "text": "A", "x": 0, "y": 0, "width": 1, "height": 1, "confidence": 0.9},'
                                '{"role": "button", "text": "B", "x": 0, "y": 0, "width": 1, "height": 1, "confidence": 0.2}'
                                "]}"
                            )
                        }
                    }
                ]
            },
        )

    engine = _engine(handler, VisionConfig(
        engine="llm", api_base="https://x", api_key="k", model="m", confidence_threshold=0.5
    ))
    result = engine.ground(_png_bytes())
    assert [element.text for element in result.elements] == ["A"]


def test_llm_invalid_json_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "not json at all"}}]})

    with pytest.raises(VisionEngineUnavailable):
        _engine(handler).ground(_png_bytes())


def test_llm_http_error_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with pytest.raises(VisionEngineUnavailable):
        _engine(handler).ground(_png_bytes())


def test_llm_unexpected_payload_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    with pytest.raises(VisionEngineUnavailable):
        _engine(handler).ground(_png_bytes())


def test_downscale_keeps_small_images() -> None:
    image = Image.new("RGB", (800, 600))
    assert _downscale(image, 1568) is image


def test_parse_elements_requires_json_object() -> None:
    with pytest.raises(VisionEngineUnavailable):
        _parse_elements("hello world", 0.5, 40)

def test_llm_accepts_rgba_screenshots() -> None:
    import io

    import httpx
    from PIL import Image

    from lean_computer_use_mcp.vision.base import VisionConfig
    from lean_computer_use_mcp.vision.llm import LLMGroundingEngine

    buffer = io.BytesIO()
    Image.new("RGBA", (64, 48), (20, 20, 20, 255)).save(buffer, format="PNG")
    data = buffer.getvalue()

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read()
        assert b"data:image/jpeg;base64" in body
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"elements": [{"role": "text", "text": "OK", '
                                '"x": 0, "y": 0, "width": 1, "height": 1, "confidence": 1.0}]}'
                            )
                        }
                    }
                ]
            },
        )

    engine = LLMGroundingEngine(
        VisionConfig(engine="llm", api_base="https://x", api_key="k", model="m"),
        transport=httpx.MockTransport(handler),
    )
    result = engine.ground(data)
    assert len(result.elements) == 1