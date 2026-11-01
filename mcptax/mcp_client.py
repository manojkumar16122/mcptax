"""Minimal MCP client over stdio (newline-delimited JSON-RPC 2.0)."""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from collections import deque
from typing import Any

from .config import ServerSpec

PROTOCOL_VERSION = "2024-11-05"


class MCPError(RuntimeError):
    """The server could not be started, timed out, or returned an error."""


class StdioMCPClient:
    def __init__(self, spec: ServerSpec, timeout: float = 20.0) -> None:
        self.spec = spec
        self.timeout = timeout
        self._next_id = 0
        self._inbox: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._stderr_tail: deque[str] = deque(maxlen=20)

        # shutil.which resolves Windows shims such as npx.cmd.
        command = shutil.which(spec.command) or spec.command
        env = {**os.environ, **spec.env}
        try:
            self.proc = subprocess.Popen(
                [command, *spec.args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
        except OSError as exc:
            raise MCPError(f"could not start '{spec.name}' ({spec.command}): {exc}") from exc

        threading.Thread(target=self._pump_stdout, daemon=True).start()
        threading.Thread(target=self._pump_stderr, daemon=True).start()

    # ---- background readers -------------------------------------------------

    def _pump_stdout(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                self._inbox.put(json.loads(line))
            except json.JSONDecodeError:
                continue  # some servers log non-JSON lines to stdout; ignore them
        self._inbox.put(None)  # sentinel: stdout closed

    def _pump_stderr(self) -> None:
        assert self.proc.stderr is not None
        for line in self.proc.stderr:
            self._stderr_tail.append(line.rstrip())

    # ---- JSON-RPC ------------------------------------------------------------

    def _stderr_hint(self) -> str:
        tail = " | ".join(self._stderr_tail)
        return tail[-300:] if tail else "no stderr output"

    def _send(self, message: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        try:
            self.proc.stdin.write(json.dumps(message) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise MCPError(f"'{self.spec.name}' closed its input: {self._stderr_hint()}") from exc

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self._send(message)

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._next_id += 1
        request_id = self._next_id
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        self._send(message)

        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MCPError(f"timed out waiting for '{method}' from '{self.spec.name}'")
            try:
                incoming = self._inbox.get(timeout=remaining)
            except queue.Empty:
                raise MCPError(f"timed out waiting for '{method}' from '{self.spec.name}'") from None
            if incoming is None:
                raise MCPError(
                    f"'{self.spec.name}' exited before answering '{method}': {self._stderr_hint()}"
                )
            if incoming.get("id") != request_id:
                continue  # notification or unrelated message
            if "error" in incoming:
                raise MCPError(f"'{self.spec.name}' returned an error for '{method}': {incoming['error']}")
            return incoming.get("result", {})

    # ---- MCP operations -----------------------------------------------------

    def initialize(self) -> dict[str, Any]:
        result = self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "mcptax", "version": "0.1.0"},
            },
        )
        self.notify("notifications/initialized")
        return result

    def list_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {} if cursor is None else {"cursor": cursor}
            result = self.request("tools/list", params)
            tools.extend(result.get("tools", []))
            cursor = result.get("nextCursor")
            if not cursor:
                return tools

    def close(self) -> None:
        if self.proc.poll() is None:
            try:
                if self.proc.stdin:
                    self.proc.stdin.close()
            except OSError:
                pass
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()

    def __enter__(self) -> "StdioMCPClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
