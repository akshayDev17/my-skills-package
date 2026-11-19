"""The plugin as Claude Code starts it, offline.

Runs the exact command in the plugin's .mcp.json (``uv run ... --project
${CLAUDE_PLUGIN_ROOT}/server graph-notes-mcp``, its environment in
${CLAUDE_PLUGIN_DATA}), with both filled in as Claude Code does, talks to it over stdio with a real MCP client, and has it read a graph
from a stand-in app on a local port, signed in through a stand-in ``cloudflared``. So the
wiring this repo adds (the manifest, the command, the bundled lock and entry point) is
checked without the private graph-notes backend or the live app.

    uv run --frozen --project plugins/graph-notes/server pytest plugins/graph-notes/test
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

PLUGIN = Path(__file__).resolve().parents[1]


def part(value: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()


SESSION = f"{part({'alg': 'RS256'})}.{part({'exp': int(time.time()) + 3600})}.c2lnbmF0dXJl"

# `cloudflared access token` prints the saved session; `login` would open a browser, so it
# is recorded and fails: a run that reaches it has a bug.
CLOUDFLARED = f"""#!{sys.executable}
import pathlib, sys
here = pathlib.Path(__file__).parent
with open(here / "calls", "a") as calls:
    calls.write(" ".join(sys.argv[1:]) + "\\n")
if sys.argv[1:3] == ["access", "token"]:
    sys.stdout.write({SESSION!r})
else:
    sys.exit(1)
"""

GRAPH = {"slug": "plan", "title": "Plan", "version": {"n": 3, "saved_at": "2026-11-15T00:00:00Z"},
         "visibility": "private", "mine": True}
CANVAS = {"nodes": [{"id": "a", "type": "text", "text": "Hello\n#idea", "x": 0, "y": 0, "width": 260, "height": 120},
                    {"id": "b", "type": "text", "text": "World", "x": 400, "y": 0, "width": 260, "height": 120}],
          "edges": [{"id": "e", "fromNode": "a", "toNode": "b", "toEnd": "arrow", "label": "leads to"}]}
ROUTES = {"/graph/api/graphs": {"graphs": [GRAPH]}, "/graph/api/graphs/plan": {**GRAPH, "canvas": CANVAS}}


@pytest.fixture
def app():
    """The stand-in app: two routes, and only for the stand-in session and a named user agent."""
    seen: list[tuple[str, str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.command, self.path, self.headers.get("user-agent", "")))
            body = ROUTES.get(self.path)
            status = 200 if body and self.headers.get("cf-access-token") == SESSION else 404
            payload = json.dumps(body if status == 200 else {"error": "not here"}).encode()
            self.send_response(status)
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}/graph", seen
    httpd.shutdown()
    httpd.server_close()
    thread.join()


def plugin_command(data: Path) -> tuple[str, list[str], dict[str, str]]:
    """The graph-notes server's command line and environment from .mcp.json, as Claude Code
    expands them for a plugin whose data directory is ``data``."""
    server = json.loads((PLUGIN / ".mcp.json").read_text())["mcpServers"]["graph-notes"]
    expand = lambda arg: arg.replace("${CLAUDE_PLUGIN_ROOT}", str(PLUGIN)).replace("${CLAUDE_PLUGIN_DATA}", str(data))
    return (expand(server["command"]), [expand(arg) for arg in server["args"]],
            {name: expand(value) for name, value in server.get("env", {}).items()})


def test_mcp_json_names_only_the_bundled_server(tmp_path):
    command, args, env = plugin_command(tmp_path)
    assert command == "uv" and "graph-notes-mcp" in args
    # The environment lives in the data directory, which survives updates; the plugin root does not.
    assert env == {"UV_PROJECT_ENVIRONMENT": f"{tmp_path}/venv"}

    servers = json.loads((PLUGIN / ".mcp.json").read_text())["mcpServers"]
    assert set(servers) == {"graph-notes"}
    unexpanded = servers["graph-notes"]
    assert unexpanded["args"][unexpanded["args"].index("--project") + 1] == "${CLAUDE_PLUGIN_ROOT}/server"
    values = [unexpanded["command"], *unexpanded["args"], *unexpanded.get("env", {}).values()]
    assert not [v for v in values if Path(v).is_absolute() or "graph-notes/mcp-server" in v], values


def test_the_plugin_command_serves_the_graph_tools(app, tmp_path):
    app_url, seen = app
    fake = tmp_path / "cloudflared"
    fake.write_text(CLOUDFLARED)
    fake.chmod(0o755)
    command, args, plugin_env = plugin_command(tmp_path / "data")
    uv_dir = str(Path(shutil.which("uv")).parent)
    env = {"PATH": os.pathsep.join([str(tmp_path), uv_dir, "/usr/bin", "/bin"]), "HOME": os.environ["HOME"],
           "GRAPH_NOTES_URL": app_url, **plugin_env}

    async def run():
        async with Client(StdioTransport(command=command, args=args, env=env, cwd=str(tmp_path))) as mcp:
            tools = {tool.name for tool in await mcp.list_tools()}
            guide = (await mcp.call_tool("about_graphs", {})).data
            listed = (await mcp.call_tool("list_graphs", {})).data
            graph = (await mcp.call_tool("read_graph", {"slug": "plan"})).data
            return tools, guide, listed, graph

    tools, guide, listed, graph = asyncio.run(asyncio.wait_for(run(), timeout=180))

    assert tools == {
        "about_graphs", "list_graphs", "read_graph", "list_history", "list_trash", "create_graph", "copy_graph",
        "rename_graph", "set_visibility", "restore_version", "trash_graph", "restore_from_trash", "add_notes",
        "update_notes", "remove_notes", "link_notes", "unlink_notes", "update_links", "style_notes", "move_notes",
        "add_group_boxes", "update_group_boxes", "remove_group_boxes", "list_images", "attach_image", "download_for_obsidian"}
    assert "what a graph is" in guide
    assert [g["slug"] for g in listed["mine"]] == ["plan"] and listed["public_from_others"] == []
    assert graph["version"] == 3
    assert graph["notes"] == [{"id": "a", "text": "Hello", "clusters": ["idea"]},
                              {"id": "b", "text": "World", "clusters": []}]
    assert graph["links"] == [{"id": "e", "from_note": "a", "to_note": "b", "direction": "forward", "label": "leads to"}]
    assert {path for _, path, _ in seen} == {"/graph/api/graphs", "/graph/api/graphs/plan"}
    assert all(agent.startswith("graph-notes-mcp/") for _, _, agent in seen)
    assert (tmp_path / "data" / "venv").is_dir()  # the environment is built in the data directory
    calls = (tmp_path / "calls").read_text().splitlines()
    assert calls and all(call.startswith("access token ") for call in calls), calls  # never a browser sign-in
