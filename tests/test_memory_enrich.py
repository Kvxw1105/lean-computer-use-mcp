"""Unit tests for LLM semantic enrichment of recorded steps.

The network client is tested with a fake transport (``httpx.MockTransport``)
so no real endpoint is needed; parsing and merge logic are platform-neutral.
"""

from __future__ import annotations

import json

import httpx
import pytest

from lean_computer_use_mcp.memory.enrich import (
    EnrichmentResult,
    LlmEnricher,
    StepLabel,
    build_digest,
    enrich_recording,
    parse_labels,
)
from lean_computer_use_mcp.record.model import ElementRef, Recording, RecordedStep


def _recording() -> Recording:
    steps = [
        RecordedStep(
            action="click",
            window_title="JianyingPro",
            x=1681,
            y=488,
        ),
        RecordedStep(
            action="click",
            window_title="JianyingPro",
            target=ElementRef(role="button", name="font-size"),
            x=100,
            y=200,
        ),
        RecordedStep(
            action="type_text",
            window_title="JianyingPro",
            value="12",
        ),
    ]
    return Recording(
        name="subtitle-font-size",
        app="JianyingPro",
        description="Reduce the subtitle font size",
        started_at=0.0,
        steps=steps,
    )


def test_parse_labels_accepts_valid_and_rejects_bad() -> None:
    content = json.dumps(
        {
            "steps": [
                {"index": 1, "role": "text", "name": "preview subtitle", "description": "The preview canvas."},
                {"index": 2, "role": "button", "name": "font-size", "description": "Opens the font size control."},
                {"index": 99, "role": "button", "name": "ghost"},
                {"index": 3, "role": "mystery", "name": "bad-role"},
                {"index": 3, "role": "edit", "name": "size input", "description": "The numeric size field."},
            ]
        }
    )
    result = parse_labels(content, step_count=3)
    assert [label.index for label in result.labels] == [1, 2, 3]
    assert result.labels[2].name == "size input"
    assert 99 in result.skipped  # out of range
    assert 3 in result.skipped  # duplicate index


def test_parse_labels_recovers_json_from_fence_and_garbage() -> None:
    content = "Sure! Here is the JSON:\n```json\n{\"steps\": [{\"index\": 1, \"role\": \"button\", \"name\": \"ok\"}]}\n```"
    result = parse_labels(content, step_count=2)
    assert result.named == 1
    assert parse_labels("not json at all", step_count=2).named == 0


def test_parse_labels_rejects_unknown_roles() -> None:
    result = parse_labels(
        json.dumps({"steps": [{"index": 1, "role": "widget", "name": "x"}]}),
        step_count=1,
    )
    assert result.named == 0
    assert 1 in result.skipped


def test_enrich_recording_only_fills_missing_targets() -> None:
    recording = _recording()
    result = EnrichmentResult(
        labels=[
            StepLabel(index=1, role="text", name="preview subtitle", description="The preview canvas."),
            StepLabel(index=2, role="slider", name="size slider", description="Overwrites nothing."),
            StepLabel(index=3, role="edit", name="size input", description="The numeric size field."),
        ]
    )
    enriched = enrich_recording(recording, result)
    assert enriched.steps[0].target is not None
    assert enriched.steps[0].target.role == "text"
    assert enriched.steps[0].target.name == "preview subtitle"
    # Existing semantic target is kept, never overwritten.
    assert enriched.steps[1].target.name == "font-size"
    assert enriched.steps[1].target.role == "button"
    assert enriched.steps[2].target is not None
    assert enriched.steps[2].target.name == "size input"
    # Original recording untouched (immutability by copy).
    assert _recording().steps[0].target is None


def test_enrich_recording_overrides_window_only_target() -> None:
    recording = _recording()
    recording.steps[0] = RecordedStep(
        action="click",
        window_title="JianyingPro",
        target=ElementRef(role="window", name="JianyingPro"),
        x=1681,
        y=488,
    )
    result = EnrichmentResult(
        labels=[
            StepLabel(
                index=1, role="text", name="preview subtitle",
                description="The preview canvas.",
            )
        ]
    )
    enriched = enrich_recording(recording, result)
    assert enriched.steps[0].target.name == "preview subtitle"
    assert enriched.steps[0].target.role == "text"


def test_enrich_recording_noop_without_labels() -> None:
    recording = _recording()
    assert enrich_recording(recording, EnrichmentResult()) is recording


def test_build_digest_is_compact_and_contains_no_window_titles() -> None:
    digest = build_digest(_recording())
    assert "JianyingPro" in digest
    assert "xy=(1681,488)" in digest
    assert "value='12'" in digest
    assert "window_title" not in digest
    assert digest.count("\n") < 30


def test_llm_enricher_posts_to_chat_completions() -> None:
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        body = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "steps": [
                                    {
                                        "index": 1,
                                        "role": "text",
                                        "name": "preview subtitle",
                                        "description": "The preview canvas.",
                                    },
                                    {
                                        "index": 3,
                                        "role": "edit",
                                        "name": "size input",
                                        "description": "The numeric size field.",
                                    },
                                ]
                            }
                        )
                    }
                }
            ]
        }
        return httpx.Response(200, json=body)

    enricher = LlmEnricher(
        api_base="https://example.test/v1",
        api_key="test-key",
        model="gpt-5.6-luna",
        transport=httpx.MockTransport(handler),
    )
    result = enricher.enrich(_recording())
    assert [label.index for label in result.labels] == [1, 3]
    payload = calls[0]
    assert payload["model"] == "gpt-5.6-luna"
    assert payload["messages"][0]["role"] == "system"
    assert "Workflow: 3 steps" in payload["messages"][1]["content"]


def test_llm_enricher_requires_configuration() -> None:
    with pytest.raises(ValueError, match="requires api_base"):
        LlmEnricher(None, None, None).enrich(_recording())
