"""Load MCP client config files and .env settings."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(RuntimeError):
    """Raised when the MCP config file is missing or malformed."""


@dataclass(frozen=True)
class ServerSpec:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


def load_dotenv(path: str = ".env") -> None:
    """Minimal .env loader. Existing environment variables take precedence."""
    env_file = Path(path)
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_servers(config_path: str) -> list[ServerSpec]:
    """Read a Claude Desktop / Claude Code style config with a `mcpServers` map.

    Remote (URL-based) servers and entries marked "disabled": true are skipped.
    """
    path = Path(config_path)
    if not path.exists():
        raise ConfigError(f"config file not found: {config_path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"invalid JSON in {config_path}: {exc}") from exc

    entries = data.get("mcpServers") or data.get("servers") or {}
    servers: list[ServerSpec] = []
    for name, cfg in entries.items():
        if cfg.get("disabled") or "command" not in cfg:
            continue
        servers.append(
            ServerSpec(
                name=name,
                command=cfg["command"],
                args=list(cfg.get("args", [])),
                env=dict(cfg.get("env", {})),
            )
        )
    return servers
