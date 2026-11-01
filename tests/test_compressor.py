import copy
import json

import httpx
import pytest

import mcptax.compressor as compressor_module
from mcptax.compressor import (
    CompressionError,
    GroqCompressor,
    HeuristicCompressor,
    apply_rewrite,
    parse_json_response,
    validate_structure,
)

TOOL = {
    "name": "search_issues",
    "description": "Our state-of-the-art issue search engine lets you find issues. It is very powerful.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The free-text search query to match against issues."},
            "limit": {"type": "integer", "description": "Max results."},
        },
        "required": ["query"],
    },
}


def test_apply_rewrite_changes_only_descriptions():
    original = copy.deepcopy(TOOL)
    rewrite = {"description": "Search issues by text.",
               "properties": {"query": {"description": "Text to match."}}}
    out = apply_rewrite(TOOL, rewrite)
    assert out["description"] == "Search issues by text."
    assert out["inputSchema"]["properties"]["query"]["description"] == "Text to match."
    assert out["inputSchema"]["properties"]["limit"]["description"] == "Max results."
    assert out["inputSchema"]["required"] == ["query"]
    assert TOOL == original  # input is not mutated


def test_apply_rewrite_ignores_invented_parameters():
    rewrite = {"description": "x", "properties": {"bogus": {"description": "should be ignored"}}}
    out = apply_rewrite(TOOL, rewrite)
    assert "bogus" not in out["inputSchema"]["properties"]


def test_validate_structure_rejects_removed_parameter():
    broken = copy.deepcopy(TOOL)
    del broken["inputSchema"]["properties"]["limit"]
    with pytest.raises(CompressionError):
        validate_structure(TOOL, broken)


def test_parse_json_response_strips_fences():
    text = "```json\n{\"description\": \"ok\"}\n```"
    assert parse_json_response(text) == {"description": "ok"}


def test_parse_json_response_rejects_non_json():
    with pytest.raises(CompressionError):
        parse_json_response("sorry, I cannot help with that")


def test_heuristic_compressor_shortens_descriptions():
    out = HeuristicCompressor().compress(TOOL)
    assert len(out["description"]) < len(TOOL["description"])
    assert out["properties"]["query"]["description"]


def test_groq_compressor_sends_auth_model_and_parses_reply():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        content = json.dumps({"description": "Search issues by text.",
                              "properties": {"query": {"description": "Text to match."}}})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    client = httpx.Client(base_url="https://api.groq.com/openai/v1",
                          transport=httpx.MockTransport(handler))
    comp = GroqCompressor(api_key="test-key", model="openai/gpt-oss-120b",
                          reasoning_effort="low", client=client)
    out = comp.compress(TOOL)

    assert out["description"] == "Search issues by text."
    assert seen["auth"] == "Bearer test-key"
    assert seen["body"]["model"] == "openai/gpt-oss-120b"
    assert seen["body"]["reasoning_effort"] == "low"


def test_groq_compressor_retries_after_bad_json(monkeypatch):
    monkeypatch.setattr(compressor_module.time, "sleep", lambda _: None)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        content = "not json" if calls["n"] == 1 else json.dumps({"description": "Fixed."})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    client = httpx.Client(base_url="https://api.groq.com/openai/v1",
                          transport=httpx.MockTransport(handler))
    comp = GroqCompressor(api_key="k", client=client, retries=1, backoff_seconds=0)
    assert comp.compress(TOOL)["description"] == "Fixed."
    assert calls["n"] == 2
