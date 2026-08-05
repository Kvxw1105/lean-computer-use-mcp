from __future__ import annotations

import time

from lean_computer_use_mcp.models import Frame
from lean_computer_use_mcp.vision.base import (
    GroundedElement,
    GroundingResult,
    VisionConfig,
)


class FakeVisionEngine:
    """Deterministic engine for unit tests and offline demos.

    Returns canned elements unless the caller overrides them. The image size is
    derived from the actual screenshot bytes so coordinate-space behavior is
    testable end to end.
    """

    name = "fake"

    def __init__(
        self,
        config: VisionConfig | None = None,
        elements: list[GroundedElement] | None = None,
    ) -> None:
        self.config = config or VisionConfig()
        self.elements = elements or [
            GroundedElement(role="text", text="Export", frame=Frame(1820, 24, 44, 28), confidence=0.96),
            GroundedElement(role="text", text="Drafts", frame=Frame(100, 60, 60, 24), confidence=0.91),
        ]

    def ground(self, image_bytes: bytes, hint: str = "") -> GroundingResult:
        from lean_computer_use_mcp.vision.ocr import _image_size

        started = time.perf_counter()
        width, height = _image_size(image_bytes)
        elements = [
            element
            for element in self.elements
            if element.confidence >= self.config.confidence_threshold
        ][: self.config.max_elements]
        return GroundingResult(
            engine=self.name,
            elements=elements,
            image_width=width,
            image_height=height,
            image_bytes=len(image_bytes),
            latency_ms=(time.perf_counter() - started) * 1000,
        )


class FakeLLMGroundingEngine(FakeVisionEngine):
    """Deterministic LLM-tier engine (name 'fake_llm') for escalation tests.

    Behaves like the real LLM engine from the caller's perspective: records the
    intent hint and returns semantic controls instead of raw OCR fragments.
    """

    name = "fake_llm"

    def __init__(
        self,
        config: VisionConfig | None = None,
        elements: list[GroundedElement] | None = None,
    ) -> None:
        default = [
            GroundedElement(role="button", text="导出", frame=Frame(1820, 24, 44, 28), confidence=0.98),
            GroundedElement(role="input", text="字号", frame=Frame(2706, 666, 109, 46), confidence=0.97),
            GroundedElement(role="slider", text="", frame=Frame(2231, 684, 439, 9), confidence=0.95),
            GroundedElement(role="button", text="字幕", frame=Frame(639, 87, 83, 76), confidence=0.99),
        ]
        super().__init__(config, elements or default)
        self.calls: list[str] = []

    def ground(self, image_bytes: bytes, hint: str = "") -> GroundingResult:
        self.calls.append(hint)
        return super().ground(image_bytes, hint)