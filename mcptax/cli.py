"""Command-line interface: `mcptax scan | rewrite | report | run`."""
from __future__ import annotations

import argparse
import os
import sys
from typing import Sequence

from . import __version__, db, pipeline
from .compressor import CompressionError, make_compressor
from .config import ConfigError, load_dotenv
from .mcp_client import MCPError


def _default_compressor() -> str:
    return os.environ.get("COMPRESSOR", "heuristic").lower()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcptax",
        description="Measure and shrink the token cost of your MCP servers.",
    )
    parser.add_argument("--version", action="version", version=f"mcptax {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_db(p: argparse.ArgumentParser) -> None:
        p.add_argument("--db", default="mcptax.db", help="SQLite database path (default: mcptax.db)")

    def add_compressor(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--compressor",
            choices=["groq", "heuristic"],
            default=_default_compressor(),
            help="groq uses an LLM; heuristic is offline (default: $COMPRESSOR or heuristic)",
        )

    scan_p = sub.add_parser("scan", help="connect to MCP servers and measure tool token costs")
    scan_p.add_argument("--config", required=True, help="MCP config JSON with an mcpServers map")
    scan_p.add_argument("--timeout", type=float, default=20.0, help="seconds to wait per server")
    add_db(scan_p)

    rewrite_p = sub.add_parser("rewrite", help="compress tool descriptions")
    add_db(rewrite_p)
    add_compressor(rewrite_p)
    rewrite_p.add_argument("--force", action="store_true", help="recompress tools already processed")
    rewrite_p.add_argument("--limit", type=int, default=None, help="only process the first N tools")
    rewrite_p.add_argument("--delay", type=float, default=0.0, help="seconds to pause between API calls")

    report_p = sub.add_parser("report", help="write report.json for the dashboard")
    add_db(report_p)
    add_compressor(report_p)
    report_p.add_argument("--out", default="report.json", help="output path (default: report.json)")

    run_p = sub.add_parser("run", help="scan, rewrite, and report in one step")
    run_p.add_argument("--config", required=True)
    run_p.add_argument("--timeout", type=float, default=20.0)
    run_p.add_argument("--force", action="store_true")
    run_p.add_argument("--limit", type=int, default=None)
    run_p.add_argument("--delay", type=float, default=0.0)
    run_p.add_argument("--out", default="report.json")
    add_db(run_p)
    add_compressor(run_p)

    return parser


def _print_summary(report: dict) -> None:
    t = report["totals"]
    print()
    print(f"Servers: {t['servers']}   Tools: {t['tools']}")
    print(f"Tokens per request: {t['original_tokens']:,} -> {t['compressed_tokens']:,} "
          f"({t['savings_pct']}% saved)")
    for srv in report["servers"]:
        print(f"  {srv['name']:<24} {srv['original_tokens']:>7,} -> {srv['compressed_tokens']:>7,}  "
              f"({srv['savings_pct']}%)")


def _cmd_scan(conn, args) -> int:
    count = pipeline.scan(conn, args.config, timeout=args.timeout)
    print(f"\nScanned {count} server(s).")
    return 0 if count else 1


def _cmd_rewrite(conn, args) -> int:
    compressor = make_compressor(args.compressor)
    done, failed = pipeline.rewrite(
        conn, compressor, force=args.force, limit=args.limit, delay=args.delay
    )
    print(f"\nCompressed {done} tool(s), {failed} failed.")
    return 0 if failed == 0 else 2


def _cmd_report(conn, args) -> int:
    report = pipeline.write_report(conn, args.out, args.compressor)
    print(f"Wrote {args.out}")
    _print_summary(report)
    return 0


def _cmd_run(conn, args) -> int:
    print("== scan ==")
    if pipeline.scan(conn, args.config, timeout=args.timeout) == 0:
        return 1
    print("\n== rewrite ==")
    compressor = make_compressor(args.compressor)
    done, failed = pipeline.rewrite(
        conn, compressor, force=args.force, limit=args.limit, delay=args.delay
    )
    print(f"\nCompressed {done} tool(s), {failed} failed.")
    print("\n== report ==")
    report = pipeline.write_report(conn, args.out, args.compressor)
    print(f"Wrote {args.out}")
    _print_summary(report)
    return 0 if failed == 0 else 2


def main(argv: Sequence[str] | None = None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)
    handlers = {"scan": _cmd_scan, "rewrite": _cmd_rewrite, "report": _cmd_report, "run": _cmd_run}
    conn = None
    try:
        conn = db.connect(args.db)
        return handlers[args.command](conn, args)
    except (ConfigError, MCPError, CompressionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
