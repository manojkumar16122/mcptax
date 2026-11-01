"""SQLite persistence for scanned servers, tools, and their compressed versions."""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS servers (
    id          INTEGER PRIMARY KEY,
    name        TEXT UNIQUE NOT NULL,
    command     TEXT NOT NULL,
    scanned_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tools (
    id                      INTEGER PRIMARY KEY,
    server_id               INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    name                    TEXT NOT NULL,
    description             TEXT NOT NULL DEFAULT '',
    schema_json             TEXT NOT NULL,
    token_count             INTEGER NOT NULL,
    compressed_description  TEXT,
    compressed_schema_json  TEXT,
    compressed_token_count  INTEGER,
    compressor              TEXT,
    compressed_at           TEXT,
    UNIQUE (server_id, name)
);
"""


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def save_scan(
    conn: sqlite3.Connection,
    server_name: str,
    command: str,
    scanned: Iterable[tuple[dict[str, Any], int]],
) -> None:
    """Replace a server's tools with a fresh scan. Rescanning clears previous compression."""
    with conn:
        conn.execute(
            """INSERT INTO servers (name, command, scanned_at) VALUES (?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET command = excluded.command,
                                               scanned_at = excluded.scanned_at""",
            (server_name, command, _now()),
        )
        server_id = conn.execute(
            "SELECT id FROM servers WHERE name = ?", (server_name,)
        ).fetchone()["id"]
        conn.execute("DELETE FROM tools WHERE server_id = ?", (server_id,))
        conn.executemany(
            """INSERT INTO tools (server_id, name, description, schema_json, token_count)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (
                    server_id,
                    tool.get("name", ""),
                    tool.get("description") or "",
                    json.dumps(tool.get("inputSchema") or {}, ensure_ascii=False),
                    tokens,
                )
                for tool, tokens in scanned
            ],
        )


def tools_to_compress(conn: sqlite3.Connection, force: bool) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT t.id, t.name, t.description, t.schema_json, t.token_count, s.name AS server
           FROM tools t JOIN servers s ON s.id = t.server_id
           WHERE ? OR t.compressed_schema_json IS NULL
           ORDER BY s.name, t.name""",
        (1 if force else 0,),
    ).fetchall()


def save_compressed(
    conn: sqlite3.Connection,
    tool_id: int,
    description: str,
    schema_json: str,
    token_count: int,
    compressor: str,
) -> None:
    with conn:
        conn.execute(
            """UPDATE tools SET compressed_description = ?, compressed_schema_json = ?,
                                compressed_token_count = ?, compressor = ?, compressed_at = ?
               WHERE id = ?""",
            (description, schema_json, token_count, compressor, _now(), tool_id),
        )


def all_tools(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT s.name AS server, s.command, t.name, t.description, t.schema_json,
                  t.token_count, t.compressed_description, t.compressed_schema_json,
                  t.compressed_token_count
           FROM tools t JOIN servers s ON s.id = t.server_id
           ORDER BY s.name, t.name"""
    ).fetchall()
