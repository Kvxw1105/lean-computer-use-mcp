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
    VisionProvider,
)
from lean_computer_use_mcp.vision.pool import ProviderPool
from urllib.parse import urlparse


def _host(api_base: str) -> str:
    return urlparse(api_base).hostname or api_base

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
        pool: ProviderPool | None = None,
    ) -> None:
        self.config = config or VisionConfig()
        self.transport = transport
        self._pool = pool

    def _providers(self) -> list[VisionProvider]:
        """Ordered provider list: explicit providers win, else the legacy
        single endpoint from api_base/api_key/model."""
        if self.config.providers:
            model = self.config.model or ""
            return [
                provider if provider.model else VisionProvider(provider.api_base, provider.api_key, model)
                for provider in self.config.providers
            ]
        if self.config.api_base and self.config.api_key and self.config.model:
            return [VisionProvider(self.config.api_base, self.config.api_key, self.config.model)]
        return []

    def _payload(
        self, image_bytes: bytes, hint: str, model: str
    ) -> tuple[dict[str, Any], float, float]:
        image = Image.open(io.BytesIO(image_bytes))
        original_width, original_height = image.size
        sent = _downscale(image, self.config.max_image_side)
        buffer = io.BytesIO()
        sent.convert("RGB").save(buffer, format="JPEG", quality=85)
        data_url = "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
        payload = {
            "model": model,
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

    def _post(self, payload: dict[str, Any], provider: VisionProvider) -> str:
        """One chat-completions call; raises ``httpx.HTTPError`` on failure."""
        import httpx

        url = provider.api_base.rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {provider.api_key}"}
        if self.transport is not None:
            with httpx.Client(transport=self.transport, timeout=self.config.timeout_seconds) as client:
                response = client.post(url, headers=headers, json=payload)
        else:
            with httpx.Client(timeout=self.config.timeout_seconds) as client:
                response = client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        try:
            return response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as exc:
            raise VisionEngineUnavailable(
                f"llm grounding returned an unexpected payload: {exc}"
            ) from exc

    def ground(self, image_bytes: bytes, hint: str = "") -> GroundingResult:
        started = time.perf_counter()
        providers = self._providers()
        if not providers:
            raise VisionEngineUnavailable(
                "llm grounding requires api_base, api_key, and model in VisionConfig "
                "(set LEAN_CU_VISION_API_BASE / LEAN_CU_VISION_API_KEY / LEAN_CU_VISION_MODEL "
                "or LEAN_CU_VISION_PROVIDERS)"
            )
        import httpx

        pool = self._pool or ProviderPool(providers)
        errors: list[str] = []
        last_exc: Exception | None = None
        for index, provider in pool.candidates():
            try:
                payload, scale_x, scale_y = self._payload(image_bytes, hint, provider.model)
                content = self._post(payload, provider)
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                pool.mark_failure(index, status)
                errors.append(f"host={_host(provider.api_base)} http {status}")
                last_exc = exc
                continue
            except httpx.HTTPError as exc:
                pool.mark_failure(index, None)
                errors.append(f"host={_host(provider.api_base)} transport error")
                last_exc = exc
                continue
            pool.mark_success(index)
            break
        else:
            raise VisionEngineUnavailable(
                f"llm grounding failed for all {len(providers)} provider(s): "
                + "; ".join(errors)
            ) from last_exc

        try:
            parsed = _parse_elements(content, self.config.confidence_threshold, self.config.max_elements)
        except (KeyError, IndexError, ValueError) as exc:
            raise VisionEngineUnavailable(f"llm grounding returned an unexpected payload: {exc}") from exc
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