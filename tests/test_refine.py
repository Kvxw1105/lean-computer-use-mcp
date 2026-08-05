"""Tests for LLM-assisted memory refinement (aliases, merges, descriptions, generalizations)."""

from __future__ import annotations

import json

import httpx
import pytest

from lean_computer_use_mcp.cli import main
from lean_computer_use_mcp.memory.library import Memory
from lean_computer_use_mcp.memory.model import Component, MemoryLibrary, TaskTemplate
from lean_computer_use_mcp.memory.refine import (
    AliasSuggestion,
    DescriptionSuggestion,
    GeneralizationSuggestion,
    LlmRefiner,
    MergeSuggestion,
    RefineSuggestions,
    apply_suggestions,
    library_digest,
    parse_suggestions,
)

ZIH = "\u5b57\u53f7"  # ?? (CJK test label)
FONT_SIZE_ID = "jianying::click::button::font-size"
FONT_SIZE_CJK_ID = "jianying::click::button::" + ZIH
TYPE_TEXT_ID = "jianying::type_text"


def _library() -> MemoryLibrary:
    components = [
        Component(app="JianYing", action="click", role="button", name="Font size", hits=3),
        Component(app="JianYing", action="click", role="button", name=ZIH, hits=1),
        Component(app="JianYing", action="type_text", value_template="{value}", hits=2),
    ]
    library = MemoryLibrary(components={component.id: component for component in components})
    library.templates["subtitle-font-size-12"] = TaskTemplate(
        id="subtitle-font-size-12",
        name="Subtitle font size 12",
        app="JianYing",
        description="",
        component_ids=[component.id for component in components],
        hits=1,
    )
    library.templates["subtitle-font-size-18"] = TaskTemplate(
        id="subtitle-font-size-18",
        name="Subtitle font size 18",
        app="JianYing",
        description="",
        component_ids=[component.id for component in components],
        hits=1,
    )
    return library


def test_parse_suggestions_tolerates_fences_and_noise():
    text = """```json
    {
      "aliases": [{"component_id": "c1", "alias": "caption-size", "reason": "same control"}],
      "merges": [{"keep_id": "c2", "into_id": "c1"}],
      "descriptions": [{"component_id": "c1", "description": "Opens the text panel."}],
      "generalizations": [{"template_ids": ["t1", "t2"], "suggested_name": "Subtitle font size"}],
      "unexpected": true
    }
    ```"""
    suggestions = parse_suggestions(text)
    assert suggestions.aliases[0].alias == "caption-size"
    assert suggestions.merges[0].keep_id == "c2"
    assert suggestions.descriptions[0].description == "Opens the text panel."
    assert suggestions.generalizations[0].suggested_name == "Subtitle font size"
    assert suggestions.empty is False
    # Malformed entries are dropped, never raised.
    noisy = parse_suggestions('{"aliases": [{"component_id": "x"}], "merges": "nope"}')
    assert noisy.empty


def test_parse_suggestions_rejects_missing_json():
    with pytest.raises(ValueError):
        parse_suggestions("no json object here")


def test_library_digest_is_compact_and_semantic():
    digest = library_digest(_library())
    assert FONT_SIZE_ID in digest
    assert "subtitle-font-size-12" in digest
    assert digest.count("\n") < 15  # one line per component/template, no raw trees


def test_suggestions_roundtrip():
    suggestions = RefineSuggestions(
        aliases=[AliasSuggestion(FONT_SIZE_ID, "caption-size", "same control")],
        merges=[MergeSuggestion(FONT_SIZE_CJK_ID, FONT_SIZE_ID, "duplicate")],
        descriptions=[DescriptionSuggestion(FONT_SIZE_ID, "Opens the text panel.")],
        generalizations=[
            GeneralizationSuggestion(
                ["subtitle-font-size-12", "subtitle-font-size-18"], "Subtitle font size"
            )
        ],
    )
    rebuilt = RefineSuggestions.from_dict(suggestions.to_dict())
    assert rebuilt.to_dict() == suggestions.to_dict()


def test_apply_suggestions_applies_all_kinds(tmp_path):
    path = tmp_path / "components.json"
    memory = Memory(path)
    memory.library = _library()
    suggestions = RefineSuggestions(
        aliases=[AliasSuggestion(FONT_SIZE_ID, "caption-size")],
        descriptions=[DescriptionSuggestion(FONT_SIZE_ID, "Opens the text panel.")],
        merges=[MergeSuggestion(FONT_SIZE_CJK_ID, FONT_SIZE_ID)],
        generalizations=[
            GeneralizationSuggestion(
                ["subtitle-font-size-12", "subtitle-font-size-18"], "Subtitle font size"
            )
        ],
    )
    applied = apply_suggestions(memory, suggestions)
    assert applied == {"aliases": 1, "merges": 1, "descriptions": 1, "generalizations": 1}
    components = memory.library.components
    assert "caption-size" in components[FONT_SIZE_ID].aliases
    assert components[FONT_SIZE_ID].description == "Opens the text panel."
    assert FONT_SIZE_CJK_ID not in components
    assert ZIH in components[FONT_SIZE_ID].aliases
    templates = memory.library.templates
    assert "subtitle-font-size-12" not in templates
    assert "subtitle-font-size-18" not in templates
    generalized = templates["subtitle-font-size"]
    # The CJK component was merged away, so templates no longer reference it.
    assert generalized.component_ids == [FONT_SIZE_ID, TYPE_TEXT_ID]
    assert generalized.hits == 2
    # Persistence round-trip.
    reloaded = Memory(path)
    assert "caption-size" in reloaded.library.components[FONT_SIZE_ID].aliases
    assert reloaded.library.templates["subtitle-font-size"].hits == 2


def test_apply_suggestions_ignores_invalid_ids():
    memory = Memory(None)
    memory.library = _library()
    suggestions = RefineSuggestions(
        aliases=[AliasSuggestion("missing::id", "x")],
        merges=[MergeSuggestion("a", "b")],
        descriptions=[DescriptionSuggestion("nope", "desc")],
        generalizations=[GeneralizationSuggestion(["t1", "missing-template"], "X")],
    )
    assert apply_suggestions(memory, suggestions) == {
        "aliases": 0,
        "merges": 0,
        "descriptions": 0,
        "generalizations": 0,
    }
    assert len(memory.library.components) == 3
    assert len(memory.library.templates) == 2


def test_llm_refiner_requires_config():
    with pytest.raises(ValueError, match="requires api_base, api_key and model"):
        LlmRefiner(None, None, None).refine(MemoryLibrary())


def test_llm_refiner_returns_parsed_suggestions():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        payload = json.loads(request.content)
        assert payload["messages"][0]["role"] == "system"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "aliases": [
                                        {
                                            "component_id": FONT_SIZE_ID,
                                            "alias": "caption-size",
                                        }
                                    ]
                                }
                            )
                        }
                    }
                ]
            },
        )

    refiner = LlmRefiner(
        "https://api.example/v1",
        "test-key",
        "model-x",
        transport=httpx.MockTransport(handler),
    )
    suggestions = refiner.refine(_library())
    assert suggestions.aliases[0].alias == "caption-size"


def test_llm_refiner_surfaces_upstream_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    refiner = LlmRefiner(
        "https://api.example/v1",
        "k",
        "m",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ValueError, match="refinement request failed"):
        refiner.refine(_library())


def test_cli_refine_apply_file(tmp_path, capsys):
    path = tmp_path / "components.json"
    memory = Memory(path)
    memory.library = _library()
    memory.save()
    suggestions_path = tmp_path / "suggestions.json"
    suggestions_path.write_text(
        json.dumps(
            RefineSuggestions(
                aliases=[AliasSuggestion(FONT_SIZE_ID, "caption-size")]
            ).to_dict(),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    code = main(
        ["refine", "--library", str(path), "--apply-file", str(suggestions_path)]
    )
    assert code == 0
    assert "aliases=1" in capsys.readouterr().out
    assert "caption-size" in Memory(path).library.components[FONT_SIZE_ID].aliases


def test_cli_refine_empty_library(tmp_path, capsys):
    path = tmp_path / "empty.json"
    Memory(path).save()
    code = main(["refine", "--library", str(path)])
    assert code == 0
    assert "nothing to refine" in capsys.readouterr().out
