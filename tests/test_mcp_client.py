import pathlib
import sys

from mcptax.config import ServerSpec
from mcptax.mcp_client import StdioMCPClient

DEMO = pathlib.Path(__file__).resolve().parents[1] / "examples" / "demo_server.py"


def test_lists_tools_from_demo_server():
    spec = ServerSpec(name="demo", command=sys.executable, args=[str(DEMO)])
    with StdioMCPClient(spec, timeout=10) as client:
        client.initialize()
        tools = client.list_tools()
    names = {t["name"] for t in tools}
    assert {"read_file", "search_issues", "create_calendar_event"} <= names
    assert all("inputSchema" in t for t in tools)
