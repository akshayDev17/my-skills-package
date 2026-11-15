"""The server as a harness runs it: ``python -m graph_notes_mcp`` on stdio, with a
``cloudflared`` on its PATH and the app on a real socket.

Read raw, not through an MCP client, to hold the process to the stdio rule: stdout
carries protocol messages and nothing else, even while ``cloudflared access login``
runs and prints the session, which also must not reach stderr.
"""

from __future__ import annotations

import json
import os
import queue
import stat
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from support import FakeEdge, fake_session

PROJECT = Path(__file__).resolve().parents[1]

# `cloudflared`, as far as the server uses it: `token` prints the saved session or fails;
# `login` hands back the saved session if there is one, or "opens a browser", saves a
# session and prints it, as the real one does.
CLOUDFLARED = """#!{python}
import pathlib, sys
here = pathlib.Path(__file__).parent
saved = here / ".cloudflared" / "graph-aud-token"
with open(here / "calls", "a") as calls:
    calls.write(" ".join(sys.argv[1:]) + "\\n")
if sys.argv[1:3] == ["access", "token"]:
    if not saved.exists():
        sys.stderr.write("Unable to find token for provided application. Please run login command to generate token.\\n")
        sys.exit(1)
    sys.stdout.write(saved.read_text())
elif sys.argv[1:3] == ["access", "login"] and saved.exists():
    sys.stdout.write("Successfully fetched your token:\\n\\n" + saved.read_text() + "\\n")
elif sys.argv[1:3] == ["access", "login"]:
    session = (here / "login-gives").read_text()
    saved.parent.mkdir(exist_ok=True)
    sys.stderr.write("A browser window should have opened at the following URL:\\nhttps://team.example/cdn-cgi/access/cli?noise\\n")
    saved.write_text(session)
    sys.stdout.write("Successfully fetched your token:\\n\\n" + session + "\\n")
"""


@pytest.fixture
def edge():
    """The stand-in app on a real port; ``edge.followed`` records a redirect that was followed."""
    httpd = HTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    origin = f"http://127.0.0.1:{httpd.server_port}"
    fake = FakeEdge(origin)
    fake.followed = []

    class Handler(BaseHTTPRequestHandler):
        def answer(self):
            if self.path.startswith("/access-sign-in"):
                fake.followed.append(self.path)
            length = int(self.headers.get("content-length") or 0)
            body = self.rfile.read(length) if length else None
            reply = fake.send(self.command, origin + self.path, dict(self.headers.items()), body)
            self.send_response(reply.status)
            if reply.status == 302:
                self.send_header("location", f"{origin}/access-sign-in")
            self.send_header("content-length", str(len(reply.body)))
            self.end_headers()
            self.wfile.write(reply.body)

        do_GET = do_POST = do_PUT = do_DELETE = answer

        def log_message(self, *args):
            pass

    httpd.RequestHandlerClass = Handler
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield fake
    httpd.shutdown()
    httpd.server_close()
    thread.join()


class Stdio:
    """``python -m graph_notes_mcp`` with ``bin_dir`` first on its PATH, spoken to line by line."""

    def __init__(self, app_url: str, bin_dir: Path):
        env = {"PATH": f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin", "HOME": str(bin_dir), "GRAPH_NOTES_URL": app_url}
        self.process = subprocess.Popen([sys.executable, "-m", "graph_notes_mcp"], cwd=PROJECT, env=env, text=True,
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.lines: queue.Queue[str] = queue.Queue()
        self.stdout: list[str] = []
        threading.Thread(target=self._read, daemon=True).start()
        self._next_id = 0

    def _read(self) -> None:
        for line in self.process.stdout:
            self.stdout.append(line)
            self.lines.put(line)

    def send(self, message: dict) -> None:
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def request(self, method: str, params: dict) -> dict:
        """The response to a request; any notifications that come first are read past."""
        self._next_id += 1
        self.send({"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params})
        while True:
            message = json.loads(self.lines.get(timeout=30))
            if message.get("id") == self._next_id:
                return message

    def call(self, tool: str, **arguments) -> dict:
        return self.request("tools/call", {"name": tool, "arguments": arguments})["result"]

    def close(self) -> str:
        """Ends the server by closing its stdin, as a harness does; returns its stderr."""
        self.process.stdin.close()
        self.process.wait(timeout=30)
        return self.process.stderr.read()


@pytest.fixture
def stdio(edge, tmp_path):
    script = tmp_path / "cloudflared"
    script.write_text(CLOUDFLARED.format(python=sys.executable))
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    server = Stdio(f"{edge.origin}/graph", tmp_path)
    hello = server.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                          "clientInfo": {"name": "raw-stdio-test", "version": "0"}})
    assert hello["result"]["serverInfo"]["name"] == "graph-notes" and "about_graphs" in hello["result"]["instructions"]
    server.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    yield server
    if server.process.poll() is None:
        server.process.kill()


def test_stdout_is_protocol_only_while_cloudflared_signs_in_and_the_session_is_seen_nowhere(stdio, edge, tmp_path):
    session = fake_session("stdio")
    edge.sessions.add(session)
    (tmp_path / "login-gives").write_text(session)  # nothing saved yet: the first call has to sign in

    made = stdio.call("create_graph", title="Over stdio")
    assert made["isError"] is False and made["structuredContent"]["slug"] == "over-stdio"
    added = stdio.call("add_notes", slug="over-stdio", version=1, notes=[{"text": "From a harness", "clusters": ["x"]}])
    assert added["structuredContent"]["version"] == 2
    read = stdio.call("read_graph", slug="over-stdio")["structuredContent"]
    assert [(n["text"], n["clusters"]) for n in read["notes"]] == [("From a harness", ["x"])]
    refused = stdio.call("add_notes", slug="over-stdio", version=1, notes=[{"text": "late"}])
    assert refused["isError"] is True and "Nothing was saved" in refused["content"][0]["text"]

    # Access ends the session mid-use: the redirect is not followed, one sign-in, one retry.
    renewed = fake_session("stdio-renewed")
    edge.sessions = {renewed}
    (tmp_path / "login-gives").write_text(renewed)
    assert [g["slug"] for g in stdio.call("list_graphs")["structuredContent"]["mine"]] == ["over-stdio"]

    stderr = stdio.close()
    assert stdio.process.returncode == 0

    app = f"{edge.origin}/graph"
    assert (tmp_path / "calls").read_text().splitlines() == [
        f"access token -app={app}", f"access login {app}", f"access token -app={app}",   # no session: sign in
        f"access token -app={app}", f"access login {app}", f"access token -app={app}",   # refused: sign in once more
    ]
    assert edge.followed == []
    assert len(stdio.stdout) >= 6
    for line in stdio.stdout:
        message = json.loads(line)  # every line of stdout is one JSON-RPC message
        assert message["jsonrpc"] == "2.0" and ("id" in message or "method" in message)
    everything = "".join(stdio.stdout) + stderr
    for secret in (session, renewed):
        assert secret not in everything and secret.split(".")[2] not in everything
    assert "Successfully fetched your token" not in everything and "A browser window should have opened" not in everything
    assert "opening the browser" in stderr  # the server's own notice, on stderr
