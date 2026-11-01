#!/usr/bin/env python3
"""A tiny stdio MCP server with deliberately verbose tool descriptions, for demos and tests."""
import json
import sys

TOOLS = [
    {
        "name": "read_file",
        "description": (
            "This incredibly powerful and versatile tool allows you to read the contents of any file "
            "on the local filesystem. It is very useful whenever you need to look at a file. Please make "
            "sure the path is correct. It returns the full contents of the file as plain text."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "The absolute or relative filesystem path of the file that you want to read. "
                        "Make sure to provide a valid path that exists on disk."
                    ),
                },
                "max_bytes": {
                    "type": "integer",
                    "description": (
                        "The maximum number of bytes to read from the file. If you do not specify this, "
                        "the default limit of the tool will be applied to the request."
                    ),
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "search_issues",
        "description": (
            "Our state-of-the-art issue search engine lets you find issues across all of your tracked "
            "repositories. You can search using free text, and the results are ranked by relevance. "
            "This is a great choice for finding anything related to a bug or feature request."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "The free-text search query that will be matched against the titles and bodies "
                        "of issues. Use plain words for best results."
                    ),
                },
                "state": {
                    "type": "string",
                    "enum": ["open", "closed", "all"],
                    "description": (
                        "Filter results by issue state. Valid values are open, closed, or all. "
                        "The default value is open if you leave this field out."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "The maximum number of issues to return in a single response.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "create_calendar_event",
        "description": (
            "Create a new event on the user's primary calendar. This tool is designed to make scheduling "
            "effortless and seamless for busy professionals who need to coordinate many meetings at once."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "The title that will be shown for the calendar event in the user's calendar.",
                },
                "start_iso": {
                    "type": "string",
                    "description": (
                        "The start time of the event in ISO 8601 format including timezone offset, "
                        "for example 2026-11-02T10:00:00+05:30."
                    ),
                },
                "duration_minutes": {
                    "type": "integer",
                    "description": "How long the event lasts, in minutes. The default is 30 minutes.",
                },
            },
            "required": ["title", "start_iso"],
        },
    },
]


def send(message: dict) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def main() -> None:
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "id" not in msg:  # notifications expect no response
            continue

        method = msg.get("method")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": msg["id"], "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "demo-tools", "version": "0.1.0"},
            }})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": TOOLS}})
        else:
            send({"jsonrpc": "2.0", "id": msg["id"],
                  "error": {"code": -32601, "message": f"method not found: {method}"}})


if __name__ == "__main__":
    main()
