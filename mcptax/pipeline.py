"""Pipeline stages: scan -> rewrite -> report."""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable

from . import config, db, tokens
from .compressor import CompressionError, Compressor, apply_rewrite
from .mcp_client import MCPError, StdioMCPClient

Log = Callable[[str], None]


def scan(conn: sqlite3.Connection, config_path: str, timeout: float = 20.0, log: Log = print) -> int:
    """Connect to every server in the config, list its tools, and store token costs."""
    specs = config.load_servers(config_path)
    if not specs:
        log("No runnable servers found in the config.")
        return 0

    succeeded = 0
    for spec in specs:
        log(f"Scanning {spec.name} ...")
        try:
            with StdioMCPClient(spec, timeout=timeout) as client:
                client.initialize()
                tools = client.list_tools()
        except MCPError as exc:
            log(f"  x {exc}")
            continue

        scanned = [(tool, tokens.tool_token_cost(tool)) for tool in tools]
        db.save_scan(conn, spec.name, spec.command, scanned)
        total = sum(cost for _, cost in scanned)
        log(f"  ok {len(tools)} tools, about {total:,} tokens per request")
        succeeded += 1
    return succeeded


def rewrite(
    conn: sqlite3.Connection,
    compressor: Compressor,
    force: bool = False,
    limit: int | None = None,
    delay: float = 0.0,
    log: Log = print,
) -> tuple[int, int]:
    """Compress every tool that has not been compressed yet. Returns (done, failed)."""
    rows = db.tools_to_compress(conn, force)
    if limit is not None:
        rows = rows[:limit]
    if not rows:
        log("Nothing to compress (use --force to redo existing rewrites).")
        return 0, 0

    done = failed = 0
    for index, row in enumerate(rows, start=1):
        original = {
            "name": row["name"],
            "description": row["description"],
            "inputSchema": json.loads(row["schema_json"]),
        }
        label = f"[{index}/{len(rows)}] {row['server']}/{row['name']}"
        try:
            candidate = apply_rewrite(original, compressor.compress(original))
        except CompressionError as exc:
            failed += 1
            log(f"  x {label}: {exc}")
            continue

        new_tokens = tokens.tool_token_cost(candidate)
        note = ""
        if new_tokens >= row["token_count"]:
            # Never store a rewrite that does not save tokens.
            candidate, new_tokens = original, row["token_count"]
            note = " (no gain, kept original)"

        db.save_compressed(
            conn,
            row["id"],
            candidate["description"],
            json.dumps(candidate["inputSchema"], ensure_ascii=False),
            new_tokens,
            compressor.name,
        )
        done += 1
        log(f"  ok {label}: {row['token_count']} -> {new_tokens} tokens{note}")
        if delay and index < len(rows):
            time.sleep(delay)
    return done, failed


def _pct(before: int, after: int) -> float:
    return round(100 * (before - after) / before, 1) if before else 0.0


def build_report(conn: sqlite3.Connection, compressor_name: str, model: str | None) -> dict[str, Any]:
    servers: dict[str, dict[str, Any]] = {}
    for row in db.all_tools(conn):
        srv = servers.setdefault(
            row["server"],
            {"name": row["server"], "command": row["command"], "tool_count": 0,
             "original_tokens": 0, "compressed_tokens": 0, "tools": []},
        )
        comp_tokens = (
            row["compressed_token_count"]
            if row["compressed_token_count"] is not None
            else row["token_count"]
        )
        srv["tools"].append(
            {
                "name": row["name"],
                "original_tokens": row["token_count"],
                "compressed_tokens": comp_tokens,
                "savings_pct": _pct(row["token_count"], comp_tokens),
                "compressed": row["compressed_schema_json"] is not None,
                "original_description": row["description"],
                "compressed_description": row["compressed_description"] or row["description"],
            }
        )
        srv["tool_count"] += 1
        srv["original_tokens"] += row["token_count"]
        srv["compressed_tokens"] += comp_tokens

    server_list = sorted(servers.values(), key=lambda s: -s["original_tokens"])
    for srv in server_list:
        srv["savings_pct"] = _pct(srv["original_tokens"], srv["compressed_tokens"])
        srv["tools"].sort(key=lambda t: -t["original_tokens"])

    original_total = sum(s["original_tokens"] for s in server_list)
    compressed_total = sum(s["compressed_tokens"] for s in server_list)
    return {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "compressor": compressor_name,
        "model": model,
        "totals": {
            "servers": len(server_list),
            "tools": sum(s["tool_count"] for s in server_list),
            "original_tokens": original_total,
            "compressed_tokens": compressed_total,
            "savings_pct": _pct(original_total, compressed_total),
        },
        "servers": server_list,
    }


def write_report(conn: sqlite3.Connection, out_path: str, compressor_name: str) -> dict[str, Any]:
    model = os.environ.get("GROQ_MODEL") if compressor_name == "groq" else None
    report = build_report(conn, compressor_name, model)
    Path(out_path).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report
