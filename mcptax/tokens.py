"""Token counting. Uses tiktoken when installed, otherwise a ~4 chars/token estimate."""
from __future__ import annotations

import json
from typing import Any

try:
    import tiktoken as _tiktoken
except ImportError:  # optional dependency
    _tiktoken = None

_ENCODER: Any = None


def _encoder() -> Any:
    global _ENCODER
    if _ENCODER is None and _tiktoken is not None:
        try:
            _ENCODER = _tiktoken.get_encoding("cl100k_base")
        except Exception:  # offline or blocked download
            return None
    return _ENCODER


def count_tokens(text: str) -> int:
    if not text:
        return 0
    encoder = _encoder()
    if encoder is not None:
        return len(encoder.encode(text))
    return max(1, len(text) // 4)


def tool_payload(tool: dict[str, Any]) -> str:
    """Serialize a tool roughly the way it is sent to a model. This is an estimate."""
    return json.dumps(
        {
            "name": tool.get("name", ""),
            "description": tool.get("description", ""),
            "input_schema": tool.get("inputSchema", {}),
        },
        separators=(",", ":"),
        ensure_ascii=False,
    )


def tool_token_cost(tool: dict[str, Any]) -> int:
    return count_tokens(tool_payload(tool))
