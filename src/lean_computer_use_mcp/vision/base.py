from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from lean_computer_use_mcp.models import Frame


@dataclass(frozen=True)
class GroundedElement:
    """One element found by a vision engine, in screenshot pixel coordinates."""

    role: str
    text: str
    frame: Frame
    confidence: float = 1.0

    def to_dict(self) -> dict[str, object]:
        return {
            "role": self.role,
            "text": self.text,
            "frame": self.frame.to_dict(),
            "confidence": round(self.confidence, 3),
        }


@dataclass(frozen=True)
class GroundingResult:
    """Compact, text-only output of one vision-engine call."""

    engine: str
    elements: list[GroundedElement]
    image_width: int
    image_height: int
    image_bytes: int = 0
    latency_ms: float = 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "engine": self.engine,
            "image_size": {"width": self.image_width, "height": self.image_height},
            "image_bytes": self.image_bytes,
            "latency_ms": round(self.latency_ms, 1),
            "elements": [element.to_dict() for element in self.elements],
        }


@dataclass(frozen=True)
class VisionConfig:
    """Backend selection for the vision fallback."""

    engine: str = "none"  # none | winrt_ocr | rapidocr | llm | fake
    confidence_threshold: float = 0.5
    max_elements: int = 40
    api_base: str | None = None  # OpenAI-compatible endpoint for engine="llm"
    api_key: str | None = None
    model: str | None = None
    timeout_seconds: float = 60.0
    max_image_side: int = 1568  # screenshot downscale limit before the API call


class VisionEngineUnavailable(RuntimeError):
    """Raised when the configured vision backend is missing or unconfigured."""


@runtime_checkable
class VisionEngine(Protocol):
    """Analyze one screenshot and return a compact element table.

    Implementations must never return the raw image; output is text-only so a
    non-multimodal planner model can consume it.
    """

    name: str

    def ground(self, image_bytes: bytes, hint: str = "") -> GroundingResult:
        """Analyze one screenshot and return a compact element table."""