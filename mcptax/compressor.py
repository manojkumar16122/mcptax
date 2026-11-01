"""Tool-description compression.

Safety model: the LLM may only rewrite human-readable description strings.
Parameter names, types, enums, and the `required` list are copied from the
original schema and verified after every rewrite. Anything else is rejected.
"""
from __future__ import annotations

import copy
import json
import os
import re
import time
from typing import Any, Protocol

import httpx

SYSTEM_PROMPT = """You rewrite MCP tool descriptions to use fewer tokens while keeping them usable by an AI model.

Rules:
- Return ONLY a JSON object. No prose, no markdown fences.
- Shape: {"description": "<tool description>", "properties": {"<param>": {"description": "<param description>"}}}
- Use exactly the parameter names you are given. Do not add, rename, or omit parameters.
- Keep every fact needed to call the tool correctly: its purpose, when to use it, units, formats, defaults, limits, and side effects.
- Remove filler, repetition, marketing language, and examples that add no information.
- Be as short as possible while staying unambiguous."""


class CompressionError(RuntimeError):
    """A rewrite could not be produced or failed validation."""


class Compressor(Protocol):
    name: str

    def compress(self, tool: dict[str, Any]) -> dict[str, Any]: ...


# ---- shared helpers ----------------------------------------------------------

def parse_json_response(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip()).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise CompressionError("model response contained no JSON object")
    try:
        obj = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise CompressionError(f"model returned invalid JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise CompressionError("model response is not a JSON object")
    return obj


def structure_signature(tool: dict[str, Any]) -> dict[str, Any]:
    schema = tool.get("inputSchema") or {}
    props = schema.get("properties") or {}
    return {
        "name": tool.get("name"),
        "params": {k: (v.get("type") if isinstance(v, dict) else None) for k, v in props.items()},
        "required": sorted(schema.get("required") or []),
    }


def validate_structure(original: dict[str, Any], rewritten: dict[str, Any]) -> None:
    if structure_signature(original) != structure_signature(rewritten):
        raise CompressionError("rewrite changed tool structure (names, types, or required fields)")


def apply_rewrite(tool: dict[str, Any], rewrite: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of `tool` with only description text replaced."""
    new = copy.deepcopy(tool)

    description = rewrite.get("description")
    if isinstance(description, str) and description.strip():
        new["description"] = description.strip()

    schema = new.setdefault("inputSchema", {})
    props = schema.get("properties") or {}
    rewritten_props = rewrite.get("properties")
    if not isinstance(rewritten_props, dict):
        rewritten_props = {}

    for pname, pschema in props.items():
        if not isinstance(pschema, dict):
            continue
        entry = rewritten_props.get(pname)
        new_desc = entry.get("description") if isinstance(entry, dict) else None
        if isinstance(new_desc, str) and new_desc.strip():
            pschema["description"] = new_desc.strip()

    validate_structure(tool, new)
    return new


def _first_sentence(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    match = re.match(r"(.+?[.!?])(\s|$)", text)
    first = match.group(1) if match else text
    if len(first) <= limit:
        return first
    return first[: limit - 1].rstrip() + "…"


# ---- compressors -------------------------------------------------------------

class HeuristicCompressor:
    """Offline baseline: keeps the first sentence and truncates parameter descriptions."""

    name = "heuristic"

    def compress(self, tool: dict[str, Any]) -> dict[str, Any]:
        props_out: dict[str, Any] = {}
        props = (tool.get("inputSchema") or {}).get("properties") or {}
        for pname, pschema in props.items():
            if isinstance(pschema, dict) and pschema.get("description"):
                props_out[pname] = {"description": _first_sentence(pschema["description"], 80)}
        return {
            "description": _first_sentence(tool.get("description", ""), 160),
            "properties": props_out,
        }


class GroqCompressor:
    """Rewrites descriptions with a model served by Groq's OpenAI-compatible API."""

    name = "groq"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str = "https://api.groq.com/openai/v1",
        reasoning_effort: str | None = None,
        client: httpx.Client | None = None,
        retries: int = 1,
        backoff_seconds: float = 2.0,
    ) -> None:
        self.api_key = api_key or os.environ.get("GROQ_API_KEY", "")
        if not self.api_key and client is None:
            raise CompressionError("GROQ_API_KEY is not set. Add it to .env or use --compressor heuristic.")
        self.model = model or os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
        if reasoning_effort is None:
            reasoning_effort = os.environ.get("GROQ_REASONING_EFFORT", "low")
        self.reasoning_effort = reasoning_effort
        self.client = client or httpx.Client(base_url=base_url, timeout=60.0)
        self.retries = retries
        self.backoff_seconds = backoff_seconds

    def _chat(self, user_content: str) -> str:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0,
            "max_tokens": 2048,
        }
        if self.reasoning_effort:
            body["reasoning_effort"] = self.reasoning_effort
        response = self.client.post(
            "/chat/completions",
            json=body,
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        if response.status_code >= 400:
            raise CompressionError(f"Groq API error {response.status_code}: {response.text[:300]}")
        try:
            return response.json()["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, ValueError) as exc:
            raise CompressionError("unexpected response shape from Groq") from exc

    def compress(self, tool: dict[str, Any]) -> dict[str, Any]:
        schema = tool.get("inputSchema") or {}
        prompt_tool = {
            "name": tool.get("name"),
            "description": tool.get("description", ""),
            "parameters": {
                k: {kk: vv for kk, vv in v.items() if kk in ("type", "description", "enum", "default")}
                for k, v in (schema.get("properties") or {}).items()
                if isinstance(v, dict)
            },
            "required": schema.get("required", []),
        }
        payload = json.dumps(prompt_tool, ensure_ascii=False)

        last_error: CompressionError | None = None
        for attempt in range(self.retries + 1):
            try:
                return parse_json_response(self._chat(payload))
            except CompressionError as exc:
                last_error = exc
                if attempt < self.retries:
                    time.sleep(self.backoff_seconds * (attempt + 1))  # backs off on rate limits too
        assert last_error is not None
        raise last_error


def make_compressor(name: str) -> Compressor:
    name = name.lower()
    if name == "groq":
        return GroqCompressor()
    if name == "heuristic":
        return HeuristicCompressor()
    raise CompressionError(f"unknown compressor '{name}' (use 'groq' or 'heuristic')")
