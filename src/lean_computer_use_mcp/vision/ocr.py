from __future__ import annotations

import io
import time
from typing import Any

from PIL import Image

from lean_computer_use_mcp.models import Frame
from lean_computer_use_mcp.vision.base import (
    GroundedElement,
    GroundingResult,
    VisionConfig,
    VisionEngine,
    VisionEngineUnavailable,
)


def _image_size(image_bytes: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(image_bytes)) as image:
        return image.width, image.height


class WinRTOCREngine:
    """OCR through Windows.Media.Ocr (winrt) with Chinese-first language selection.

    Uses the optional ``winrt`` packages directly instead of ``screen-ocr`` because
    screen-ocr 0.7.0 does not support the winrt 3.x ``available_recognizer_languages``
    API. Falls back to the user-profile engine when no Chinese OCR pack exists.
    """

    name = "winrt_ocr"

    _PREFERRED_LANGUAGE_TAGS = ("zh-hans-cn", "zh-cn", "zh")

    def __init__(self, config: VisionConfig | None = None) -> None:
        self.config = config or VisionConfig()
        self._engine: Any | None = None
        self._executor: Any | None = None

    def _ensure_engine(self) -> tuple[Any, Any]:
        """Return (ocr engine, executor). Lazily imports winrt and picks a language."""
        if self._engine is not None:
            return self._engine, self._executor
        try:
            import winrt.windows.graphics.imaging as imaging  # noqa: F401
            import winrt.windows.media.ocr as ocr
            import winrt.windows.storage.streams as streams  # noqa: F401
            from winrt.windows.globalization import Language  # noqa: F401
        except ImportError as exc:
            raise VisionEngineUnavailable(
                "winrt_ocr requires optional winrt packages "
                "(pip install screen-ocr[winrt] winrt-windows-globalization)"
            ) from exc
        from concurrent.futures import ThreadPoolExecutor

        engine = None
        for language in ocr.OcrEngine.available_recognizer_languages:
            tag = language.language_tag.lower()
            if tag in self._PREFERRED_LANGUAGE_TAGS:
                engine = ocr.OcrEngine.try_create_from_language(language)
                break
        if engine is None:
            engine = ocr.OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            raise VisionEngineUnavailable("no Windows OCR engine available")
        self._engine = engine
        self._executor = ThreadPoolExecutor(max_workers=1)
        return engine, self._executor

    @staticmethod
    def _run_async(engine: Any, image: Image.Image) -> Any:
        """Run Windows OCR on a dedicated thread (thread-mode safe)."""
        import asyncio

        import winrt.windows.graphics.imaging as imaging
        import winrt.windows.storage.streams as streams

        async def recognize() -> Any:
            data_writer = streams.DataWriter()
            data_writer.write_bytes(image.convert("RGBA").tobytes())
            bitmap = imaging.SoftwareBitmap(
                imaging.BitmapPixelFormat.RGBA8, image.width, image.height
            )
            bitmap.copy_from_buffer(data_writer.detach_buffer())
            del data_writer
            return await engine.recognize_async(bitmap)

        return asyncio.run(recognize())

    def ground(self, image_bytes: bytes, hint: str = "") -> GroundingResult:
        started = time.perf_counter()
        engine, executor = self._ensure_engine()
        width, height = _image_size(image_bytes)
        result = executor.submit(
            self._run_async, engine, Image.open(io.BytesIO(image_bytes))
        ).result()
        elements: list[GroundedElement] = []
        for line in result.lines:
            for word in line.words:
                text = word.text.strip()
                if not text:
                    continue
                elements.append(
                    GroundedElement(
                        role="text",
                        text=text,
                        frame=Frame(
                            x=int(word.bounding_rect.x),
                            y=int(word.bounding_rect.y),
                            width=int(word.bounding_rect.width),
                            height=int(word.bounding_rect.height),
                        ),
                        confidence=1.0,
                    )
                )
                if len(elements) >= self.config.max_elements:
                    break
            if len(elements) >= self.config.max_elements:
                break
        return GroundingResult(
            engine=self.name,
            elements=elements,
            image_width=width,
            image_height=height,
            image_bytes=len(image_bytes),
            latency_ms=(time.perf_counter() - started) * 1000,
        )

class RapidOCREngine:
    """OCR through PP-OCRv5 (ONNX) via the optional `rapidocr-onnxruntime` package.

    CPU-only, <15MB, strong Chinese/English coverage; a good privacy-friendly
    alternative when WinRT is unavailable.
    """

    name = "rapidocr"

    def __init__(self, config: VisionConfig | None = None) -> None:
        self.config = config or VisionConfig()

    def ground(self, image_bytes: bytes, hint: str = "") -> GroundingResult:
        started = time.perf_counter()
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as exc:
            raise VisionEngineUnavailable(
                "rapidocr requires the optional 'rapidocr-onnxruntime' package "
                "(pip install 'lean-computer-use-mcp[vision]')"
            ) from exc
        engine = RapidOCR()
        width, height = _image_size(image_bytes)
        result = engine(image_bytes)
        elements: list[GroundedElement] = []
        for entry in result or []:
            quad, text, score = entry[0], entry[1], entry[2]
            if not text or score is None:
                continue
            confidence = float(score)
            if confidence < self.config.confidence_threshold:
                continue
            xs = [point[0] for point in quad]
            ys = [point[1] for point in quad]
            x, y = int(min(xs)), int(min(ys))
            box_width = int(max(xs)) - x
            box_height = int(max(ys)) - y
            elements.append(
                GroundedElement(
                    role="text",
                    text=str(text),
                    frame=Frame(x=x, y=y, width=box_width, height=box_height),
                    confidence=confidence,
                )
            )
            if len(elements) >= self.config.max_elements:
                break
        return GroundingResult(
            engine=self.name,
            elements=elements,
            image_width=width,
            image_height=height,
            image_bytes=len(image_bytes),
            latency_ms=(time.perf_counter() - started) * 1000,
        )


def build_engine(config: VisionConfig | None = None) -> VisionEngine:
    """Return the engine named by ``VisionConfig.engine``.

    Raises ``VisionEngineUnavailable`` for unknown or unconfigured backends so
    callers can degrade to the UIA-only path instead of crashing.
    """
    cfg = config or VisionConfig()
    if cfg.engine == "winrt_ocr":
        return WinRTOCREngine(cfg)
    if cfg.engine == "rapidocr":
        return RapidOCREngine(cfg)
    if cfg.engine == "fake":
        from lean_computer_use_mcp.vision.fake import FakeVisionEngine

        return FakeVisionEngine(cfg)
    if cfg.engine == "fake_llm":
        from lean_computer_use_mcp.vision.fake import FakeLLMGroundingEngine

        return FakeLLMGroundingEngine(cfg)
    if cfg.engine == "llm":
        from lean_computer_use_mcp.vision.llm import LLMGroundingEngine

        return LLMGroundingEngine(cfg)
    raise VisionEngineUnavailable(
        f"no vision engine configured (engine={cfg.engine!r}); "
        "set LEAN_CU_VISION_ENGINE=winrt_ocr|rapidocr or pass a VisionConfig"
    )
