"""Unit tests for LLM-assisted intent-to-component mapping."""

from __future__ import annotations

import json

import httpx
import pytest

from lean_computer_use_mcp.memory.llm_recall import (
    LlmRecallMapper,
    build_library_digest,
    parse_component_ids,
)
from lean_computer_use_mcp.memory.model import Component, MemoryLibrary


def _library() -> MemoryLibrary:
    library = MemoryLibrary()
    library.components["jianyingpro::click::pane::preview-canvas"] = Component(
        app="JianyingPro",
        action="click",
        role="pane",
        name="preview canvas",
        description="Selects the project preview.",
    )
    library.components["jianyingpro::click::edit::property-value"] = Component(
        app="JianyingPro",
        action="click",
        role="edit",
        name="property value",
        description="Edits the selected property value.",
    )
    library.components["jianyingpro::click::button::close"] = Component(
        app="JianyingPro",
        action="click",
        role="button",
        name="close",
        description="Closes the window.",
    )
    return library


def test_parse_component_ids_validates_and_dedupes() -> None:
    library = _library()
    content = json.dumps(
        {
            "components": [
                "jianyingpro::click::pane::preview-canvas",
                "jianyingpro::click::pane::preview-canvas",
                "jianyingpro::bogus::id",
                "jianyingpro::click::edit::property-value",
            ]
        }
    )
    ids = parse_component_ids(content, library)
    assert ids == [
        "jianyingpro::click::pane::preview-canvas",
        "jianyingpro::click::edit::property-value",
    ]


def test_parse_component_ids_handles_fences_and_garbage() -> None:
    library = _library()
    content = (
        "Here you go:\n```json\n"
        + json.dumps({"components": ["jianyingpro::click::button::close"]})
        + "\n```"
    )
    assert parse_component_ids(content, library) == [
        "jianyingpro::click::button::close"
    ]
    assert parse_component_ids("no plan", library) == []
    assert parse_component_ids('{"components": "not-a-list"}', library) == []


def test_build_library_digest_filters_by_app() -> None:
    library = _library()
    digest = build_library_digest(library, app="JianyingPro")
    assert "preview-canvas" in digest
    assert "| click | pane | preview canvas | Selects" in digest
    other = MemoryLibrary()
    other.components["other::click::button::x"] = Component(
        app="Other", action="click", role="button", name="x"
    )
    assert "other" not in digest
    assert "preview-canvas" not in build_library_digest(other, app="JianyingPro")


def test_mapper_composes_plan_from_llm() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["model"] == "gpt-5.6-luna"
        assert "Intent: ?????" in payload["messages"][1]["content"]
        body = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "components": [
                                    "jianyingpro::click::pane::preview-canvas",
                                    "jianyingpro::click::edit::property-value",
                                ]
                            }
                        )
                    }
                }
            ]
        }
        return httpx.Response(200, json=body)

    mapper = LlmRecallMapper(
        api_base="https://example.test/v1",
        api_key="test-key",
        model="gpt-5.6-luna",
        transport=httpx.MockTransport(handler),
    )
    plan = mapper.compose(_library(), "?????", app="JianyingPro")
    assert plan.kind == "components"
    assert plan.tentative is True
    assert len(plan.steps) == 2
    assert plan.steps[0].target is not None
    assert plan.steps[0].target.name == "preview canvas"
    assert plan.steps[0].x is None  # digest never exposes coordinates


def test_mapper_returns_empty_plan_on_empty_llm_answer() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '{"components": []}'}}]}
        )

    mapper = LlmRecallMapper(
        api_base="https://example.test/v1",
        api_key="test-key",
        model="gpt-5.6-luna",
        transport=httpx.MockTransport(handler),
    )
    plan = mapper.compose(_library(), "something unknown")
    assert plan.steps == []
    assert plan.score == 0.0


def test_mapper_requires_configuration() -> None:
    with pytest.raises(ValueError, match="requires api_base"):
        LlmRecallMapper(None, None, None).compose(_library(), "intent")
