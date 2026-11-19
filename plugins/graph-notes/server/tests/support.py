"""Shared by the offline tests: the app without Cloudflare or a network.

``FakeEdge`` stands in for Cloudflare in front of the *real* API (backend/graph_notes,
over in-memory SQLite, the same code the Worker runs): it refuses Python's default
user agent with error 1010 and answers a request without a valid session with Access's
redirect, as the live edge does, then signs the request the way Access does and hands
it to ``serve``. So the same-origin rule, the outdated-save check and every route are
the app's own, not a copy of them. Its image bucket is the app's own stand-in for R2,
``LocalBucket``, in memory.

``FakeCloudflared`` stands in for the ``cloudflared`` binary behind ``Session.run``.
"""

from __future__ import annotations

import asyncio
import base64
import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from fastmcp import Client

from pictures import png

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
sys.path.insert(0, str(BACKEND))

from dev_access import claims_for, jwks, mint, shared_key  # noqa: E402
from graph_notes import store  # noqa: E402
from graph_notes.api import ApiRequest, is_upload, iso, serve  # noqa: E402
from graph_notes.auth import AccessConfig, KeyCache  # noqa: E402
from graph_notes.local_bucket import LocalBucket  # noqa: E402
from graph_notes.sqlite_binding import SqliteD1  # noqa: E402

from graph_notes_mcp.client import Api, Response  # noqa: E402
from graph_notes_mcp.server import build_server  # noqa: E402
from graph_notes_mcp.session import Session, expired  # noqa: E402

APP_URL = "https://graph.test/graph"
ORIGIN = "https://graph.test"
TEAM = "team.cloudflareaccess.test"
AUD = "aud-graph-notes"
OWNER = "owner@example.com"
OTHER = "someone.else@example.com"
FIXTURES = BACKEND / "test" / "fixtures"


def fake_session(tag: str, expires_in: float = 3600) -> str:
    """A string shaped like an Access session (a JWT with an ``exp``), unique per ``tag``."""
    def part(value: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()
    return f"{part({'alg': 'RS256'})}.{part({'exp': int(time.time() + expires_in), 'tag': tag})}.c2lnbmF0dXJl{tag}"


def fixture(slug: str) -> dict:
    return json.loads((FIXTURES / f"{slug}.canvas").read_text(encoding="utf-8"))


async def _value(value):
    return value


class FakeEdge:
    """Cloudflare (the 1010 rule and Access) in front of the real API over SQLite."""

    def __init__(self, origin: str = ORIGIN):
        self.origin = origin
        connection = sqlite3.connect(":memory:", check_same_thread=False)  # tools run on worker threads
        self.db = SqliteD1(connection)
        self.bucket = LocalBucket()
        for migration in sorted((BACKEND / "migrations").glob("*.sql")):
            self.db.executescript(migration.read_text())
        self.access = AccessConfig(TEAM, frozenset({AUD}), frozenset({origin}))
        self.keys = KeyCache(lambda: _value(jwks(shared_key())))
        self.sessions: set[str] = set()   # the sessions Access accepts
        self.requests: list[tuple[str, str, dict[str, str], bytes | None]] = []
        self.before_put = []              # each runs once, just before the next PUT reaches the API
        self._lock = threading.Lock()

    def seed(self, slug: str, canvas: dict, owner: str = OWNER, visibility: str = "private") -> None:
        """``canvas`` as ``owner``'s graph at version 1."""
        asyncio.run(store.create(self.db, slug, slug.replace("-", " ").title(), store.items_for(canvas, []),
                                 owner, iso(time.time())))
        if visibility == "public":
            self.as_user("POST", f"/graphs/{slug}/visibility", {"base": 1, "visibility": "public"}, who=owner)

    def as_user(self, method: str, path: str, body: dict | None = None, who: str = OWNER) -> dict:
        """A request straight to the API as ``who`` (another tab, another person): no edge in front."""
        headers = {"cf-access-jwt-assertion": mint(shared_key(), claims_for(who, TEAM, AUD, time.time()))}
        if method != "GET":
            headers |= {"origin": self.origin, "x-graph-notes": "1", "content-type": "application/json"}
        with self._lock:
            reply = asyncio.run(serve(ApiRequest(method, f"/graph/api{path}", {}, headers, json.dumps(body or {})),
                                      self.db, self.bucket, self.access, self.keys, time.time()))
        assert reply.status < 400, reply.body
        return reply.body

    def canvas(self, slug: str) -> dict:
        return self.as_user("GET", f"/graphs/{slug}")["canvas"]

    def send(self, method: str, url: str, headers: dict[str, str], body: bytes | None) -> Response:
        """What ``client.urllib_send`` would get back from the live app for this request."""
        headers = {name.lower(): value for name, value in headers.items()}
        self.requests.append((method, url, headers, body))
        if not headers.get("user-agent") or headers["user-agent"].startswith("Python-urllib"):
            return Response(403, b"error code: 1010")
        if headers.get("cf-access-token") not in self.sessions:
            return Response(302, b"<html><head><title>302 Found</title></head></html>")
        if method == "PUT" and self.before_put:
            self.before_put.pop(0)()
        assert url.startswith(self.origin), url
        path, _, query = url[len(self.origin):].partition("?")
        params = dict(pair.split("=", 1) for pair in query.split("&") if pair)
        headers["cf-access-jwt-assertion"] = mint(shared_key(), claims_for(OWNER, TEAM, AUD, time.time()))
        # An image upload is the file itself, as the Worker hands R2 the request's stream.
        upload = is_upload(method, path)
        text, stream = ("", body or None) if upload else ((body or b"").decode(), None)
        with self._lock:
            reply = asyncio.run(serve(ApiRequest(method, path, params, headers, text, stream),
                                      self.db, self.bucket, self.access, self.keys, time.time()))
        payload = reply.body if isinstance(reply.body, bytes) else json.dumps(reply.body).encode()
        return Response(reply.status, payload)


class FakeCloudflared:
    """``cloudflared access token`` and ``access login`` as ``subprocess.run`` would run them.

    ``saved`` is the session on disk, a file in ``store``. ``login`` hands back a saved
    session that has not expired, as the real one does; otherwise it makes a new one and
    has ``edge`` accept it, unless ``login_works`` is False (the person never finished)
    or ``installed`` is.
    """

    def __init__(self, edge: FakeEdge, store: Path, saved: str | None = None):
        self.edge = edge
        self.file = store / "graph.test-graph-aud-graph-notes-token"
        self.saved = saved
        self.installed = True
        self.login_works = True
        self.calls: list[tuple[list[str], dict]] = []
        self.logins = 0

    @property
    def saved(self) -> str | None:
        return self.file.read_text() if self.file.exists() else None

    @saved.setter
    def saved(self, value: str | None) -> None:
        if value is None:
            self.file.unlink(missing_ok=True)
        else:
            self.file.write_text(value)

    def __call__(self, argv: list[str], **kwargs) -> subprocess.CompletedProcess:
        self.calls.append((argv, kwargs))
        if not self.installed:
            raise FileNotFoundError(2, "No such file or directory: 'cloudflared'")
        if argv[:3] == ["cloudflared", "access", "token"]:
            assert kwargs["stdout"] == subprocess.PIPE
            return subprocess.CompletedProcess(argv, 0 if self.saved else 1, stdout=self.saved or "")
        assert argv[:3] == ["cloudflared", "access", "login"], argv
        self.logins += 1
        if not self.login_works:
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"], output="Please open the following URL")
        if self.saved and not expired(self.saved, time.time()):
            return subprocess.CompletedProcess(argv, 0)
        self.saved = fake_session(f"login{self.logins}")
        self.edge.sessions.add(self.saved)
        return subprocess.CompletedProcess(argv, 0)


class App:
    """The MCP server over a ``FakeEdge``, signed in with one valid saved session."""

    def __init__(self, signed_in: bool = True):
        self.edge = FakeEdge()
        self.session_value = fake_session("first")
        if signed_in:
            self.edge.sessions.add(self.session_value)
        self._store = tempfile.TemporaryDirectory()
        self.downloads = tempfile.TemporaryDirectory()  # where download_for_obsidian saves
        store = Path(self._store.name)
        self.cloudflared = FakeCloudflared(self.edge, store, self.session_value if signed_in else None)
        self.api = Api(APP_URL, Session(APP_URL, run=self.cloudflared, store=store), send=self.edge.send)
        self.server = build_server(self.api)

    def call(self, tool: str, **arguments) -> dict | str:
        """The tool's result through a real MCP client session; raises ``ToolError`` as a client sees it."""
        return asyncio.run(self._call(tool, arguments))

    def result(self, tool: str, **arguments):
        """The whole ``CallToolResult``, errors included (never raises for a failed tool)."""
        return asyncio.run(self._call(tool, arguments, raw=True))

    async def _call(self, tool: str, arguments: dict, raw: bool = False):
        async with Client(self.server) as mcp_client:
            result = await mcp_client.call_tool(tool, arguments, raise_on_error=not raw)
            return result if raw else result.data


def tour(app: App) -> list:
    """Every tool once, and the ways a call fails, as ``CallToolResult``s: what a harness would see."""
    results = []

    def do(tool: str, **arguments):
        results.append(app.result(tool, **arguments))
        return results[-1].data if not results[-1].is_error else None

    do("about_graphs")
    do("create_graph", title="Tour")
    added = do("add_notes", slug="tour", version=1, notes=[{"text": "One", "clusters": ["a"], "key": "one"}, {"text": "Two", "key": "two"}],
               links=[{"from_note": "one", "to_note": "two"}])
    one, two = (note["id"] for note in added["added"])
    linked = do("link_notes", slug="tour", version=2, links=[{"from_note": two, "to_note": one, "label": "back"}])
    do("update_notes", slug="tour", version=3, notes=[{"id": two, "text": "Two, edited", "clusters": ["b"]}])
    do("unlink_notes", slug="tour", version=4, link_ids=[linked["added"][0]["id"]])
    boxed = do("add_group_boxes", slug="tour", version=5, boxes=[{"label": "Pair", "notes": [one, two], "collapsed": True}])
    do("update_group_boxes", slug="tour", version=6, boxes=[{"id": boxed["added"][0]["id"], "label": "Two", "notes": [two]}])
    do("remove_group_boxes", slug="tour", version=7, box_ids=[boxed["added"][0]["id"]])
    do("remove_notes", slug="tour", version=8, note_ids=[one])
    do("rename_graph", slug="tour", version=9, title="Tour, renamed")
    do("set_visibility", slug="tour", version=10, visibility="public")
    do("set_visibility", slug="tour", version=11, visibility="private")
    do("restore_version", slug="tour", version=12, restore_to=3)
    do("copy_graph", slug="tour", title="Tour copy")
    do("list_graphs")
    do("read_graph", slug="tour")
    do("read_graph", slug="tour", at_version=2)
    do("list_history", slug="tour")
    do("trash_graph", slug="tour")
    do("list_trash")
    do("restore_from_trash", slug="tour")
    # and the failures: outdated, missing, malformed, refused by the app
    do("add_notes", slug="tour", version=1, notes=[{"text": "late"}])
    do("rename_graph", slug="tour", version=1, title="late")
    do("update_notes", slug="tour", version=15, notes=[{"id": "nope", "text": "x"}])
    do("add_group_boxes", slug="tour", version=15, boxes=[{"label": "Nowhere", "notes": ["nope"]}])
    do("read_graph", slug="missing")
    do("read_graph", slug="../trash/tour")
    do("create_graph", title="Tour", slug="tour")
    do("create_graph", title=" ", slug="blank")
    do("restore_version", slug="tour", version=15, restore_to=99)
    do("trash_graph", slug="tour-copy")
    do("trash_graph", slug="tour-copy")
    do("add_notes", slug="tour", version="not a number", notes=[])
    # how notes and links look, and links changed in place (on the restored version 3)
    do("style_notes", slug="tour", version=15, notes=[{"id": two, "color": "green", "shape": "pill"}])
    do("update_links", slug="tour", version=16, links=[{"id": added["links_added"][0]["id"], "label": "on to", "direction": "backward",
                                                        "line": "dotted", "from_side": "bottom", "to_side": "top"}])
    do("style_notes", slug="tour", version=17, notes=[{"id": two, "color": "blue"}])
    # pictures: a file on this computer attached, listed, copied with its graph, and a stale attach
    picture = Path(app.downloads.name) / "board.png"
    picture.write_bytes(png(8, 6))
    do("create_graph", title="Pictures")
    card = do("add_notes", slug="pictures", version=1, notes=[{"text": "Board"}])["added"][0]["id"]
    do("attach_image", slug="pictures", version=2, note_id=card, path=str(picture))
    do("attach_image", slug="pictures", version=2, note_id=card, path=str(picture))
    do("list_images", slug="pictures")
    do("copy_graph", slug="pictures", title="Pictures copy")
    do("download_for_obsidian", folder=app.downloads.name)
    do("download_for_obsidian", folder=f"{app.downloads.name}/no-such-folder")
    return results
