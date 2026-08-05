from __future__ import annotations

import base64
import io
import json
import re
import time
from typing import Any

from PIL import Image

from lean_computer_use_mcp.models import Frame
from lean_computer_use_mcp.vision.base import (
    GroundedElement,
    GroundingResult,
    VisionConfig,
    VisionEngineUnavailable,
)

_SYSTEM_PROMPT = """You are a screen-parsing engine for desktop UI automation.
Analyze the screenshot and return ONLY one JSON object with this exact shape:
{"elements": [{"role": "button|text|icon|input|slider|menu_item|checkbox|other", "text": "short label or empty", "x": 0, "y": 0, "width": 0, "height": 0, "confidence": 0.0}]}
Rules:
- Coordinates are pixel coordinates in the screenshot you were given (x, y = top-left of the box).
- Cover every interactive element that matches the task hint; include text labels for buttons when visible.
- One element per box; never merge separate buttons.
- Confidence 0.0-1.0 for how sure you are the box is right.
- No markdown, no commentary, no code fences."""


def _downscale(image: Image.Image, max_side: int) -> Image.Image:
    """Shrink the screenshot so the API call stays cheap; coordinates are rescaled later."""
    width, height = image.size
    longest = max(width, height)
    if longest <= max_side:
        return image
    scale = max_side / longest
    return image.resize((max(1, round(width * scale)), max(1, round(height * scale))), Image.LANCZOS)


def _parse_elements(text: str, threshold: float, max_elements: int) -> list[GroundedElement]:
    """Parse the model's JSON reply into elements; tolerate fences and prose."""
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise VisionEngineUnavailable("llm grounding returned no JSON object")
    data = json.loads(text[start : end + 1])
    elements: list[GroundedElement] = []
    for item in data.get("elements", []):
        try:
            x = int(item["x"])
            y = int(item["y"])
            width = int(item["width"])
            height = int(item["height"])
        except (KeyError, ValueError, TypeError):
            continue
        confidence = float(item.get("confidence", 1.0) or 1.0)
        if confidence < threshold:
            continue
        elements.append(
            GroundedElement(
                role=str(item.get("role", "element")),
                text=str(item.get("text", "")),
                frame=Frame(x=x, y=y, width=width, height=height),
                confidence=min(max(confidence, 0.0), 1.0),
            )
        )
        if len(elements) >= max_elements:
            break
    return elements


class LLMGroundingEngine:
    """Grounding tier: any OpenAI-compatible multimodal API (GPT-4o, Qwen-VL, ...).

    Sends a downscaled JPEG plus a task hint, receives a compact element table,
    and rescales the returned boxes back to the original screenshot space.
    """

    name = "llm"

    def __init__(
        self,
        config: VisionConfig | None = None,
        transport: Any | None = None,
    ) -> None:
        self.config = config or VisionConfig()
        self.transport = transport

    def _payload(self, image_bytes: bytes, hint: str) -> tuple[dict[str, Any], float, float]:
        image = Image.open(io.BytesIO(image_bytes))
        original_width, original_height = image.size
        sent = _downscale(image, self.config.max_image_side)
        buffer = io.BytesIO()
        sent.convert("RGB").save(buffer, format="JPEG", quality=85)
        data_url = "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
        payload = {
            "model": self.config.model,
            "temperature": 0,
            "max_tokens": 2048,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": f"Task hint: {hint}"},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
        }
        return payload, original_width / sent.width, original_height / sent.height

    def ground(self, image_bytes: bytes, hint: str = "") -> GroundingResult:
        started = time.perf_counter()
        if not self.config.api_base or not self.config.api_key or not self.config.model:
            raise VisionEngineUnavailable(
                "llm grounding requires api_base, api_key, and model in VisionConfig "
                "(set LEAN_CU_VISION_API_BASE / LEAN_CU_VISION_API_KEY / LEAN_CU_VISION_MODEL)"
            )
        import httpx

        payload, scale_x, scale_y = self._payload(image_bytes, hint)
        url = self.config.api_base.rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        try:
            if self.transport is not None:
                with httpx.Client(transport=self.transport, timeout=self.config.timeout_seconds) as client:
                    response = client.post(url, headers=headers, json=payload)
            else:
                with httpx.Client(timeout=self.config.timeout_seconds) as client:
                    response = client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
        except httpx.HTTPError as exc:
            raise VisionEngineUnavailable(f"llm grounding request failed: {exc}") from exc
        except (KeyError, IndexError, ValueError) as exc:
            raise VisionEngineUnavailable(f"llm grounding returned an unexpected payload: {exc}") from exc

        parsed = _parse_elements(content, self.config.confidence_threshold, self.config.max_elements)
        elements = [
            GroundedElement(
                role=element.role,
                text=element.text,
                frame=Frame(
                    x=round(element.frame.x * scale_x),
                    y=round(element.frame.y * scale_y),
                    width=round(element.frame.width * scale_x),
                    height=round(element.frame.height * scale_y),
                ),
                confidence=element.confidence,
            )
            for element in parsed
        ]
        with Image.open(io.BytesIO(image_bytes)) as image:
            width, height = image.size
        return GroundingResult(
            engine=self.name,
            elements=elements,
            image_width=width,
            image_height=height,
            image_bytes=len(image_bytes),
            latency_ms=(time.perf_counter() - started) * 1000,
        )