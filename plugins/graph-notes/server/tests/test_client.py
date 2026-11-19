"""What goes on the wire: the headers a client that is not a browser must send, on every
request the tools make, and the real sender (urllib) against a real socket."""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from graph_notes_mcp import client
from graph_notes_mcp.client import USER_AGENT, ApiError, urllib_send
from support import ORIGIN, App, tour


@pytest.fixture(scope="module")
def toured() -> App:
    app = App()
    results = tour(app)
    assert sum(1 for r in results if not r.is_error) >= 20 and sum(1 for r in results if r.is_error) >= 8
    return app


def test_every_request_names_its_client_and_carries_the_session(toured):
    assert len(toured.edge.requests) > 30
    for method, url, headers, _ in toured.edge.requests:
        assert headers["user-agent"] == USER_AGENT and not USER_AGENT.startswith("Python")
        assert headers["cf-access-token"] == toured.session_value
        assert url.startswith(f"{ORIGIN}/graph/api/")


def test_every_write_carries_the_origin_and_the_write_header_and_json(toured):
    writes = [r for r in toured.edge.requests if r[0] != "GET"]
    assert {method for method, *_ in writes} == {"POST", "PUT", "DELETE"}
    uploads = [r for r in writes if r[0] == "POST" and r[1].endswith("/images")]
    assert uploads
    for method, url, headers, body in writes:
        assert headers["origin"] == ORIGIN and headers["x-graph-notes"] == "1", (method, url)
        if (method, url, headers, body) in uploads:  # the file itself, with its type, length and hash
            assert headers["content-type"] == "image/png" and body.startswith(b"\x89PNG"), url
            assert headers["content-length"] == str(len(body)) and len(headers["x-content-sha256"]) == 64
        elif method in ("POST", "PUT"):
            assert headers["content-type"] == "application/json", (method, url)
            assert isinstance(json.loads(body), dict), (method, url)  # a JSON body, even an empty one
    bodiless = [json.loads(body) for method, url, _, body in writes if url.endswith("/undelete")]
    assert bodiless == [{}]


def test_reads_send_no_write_headers(toured):
    for method, _, headers, body in toured.edge.requests:
        if method == "GET":
            assert "origin" not in headers and "x-graph-notes" not in headers and "content-type" not in headers and body is None


def test_the_app_refuses_a_write_without_those_headers_so_the_test_above_means_something(toured):
    for missing in ("origin", "x-graph-notes", "content-type"):
        headers = client.headers_for("POST", "https://graph.test/graph", toured.session_value)
        del headers[missing]
        reply = toured.edge.send("POST", f"{ORIGIN}/graph/api/graphs", headers, b'{"slug": "x", "title": "x"}')
        assert reply.status == 403, missing
    no_agent = {**client.headers_for("GET", "https://graph.test/graph", toured.session_value), "user-agent": "Python-urllib/3.13"}
    assert toured.edge.send("GET", f"{ORIGIN}/graph/api/graphs", no_agent, None) == client.Response(403, b"error code: 1010")


def test_a_whole_tour_of_the_tools_deletes_only_to_the_trash(toured):
    deletes = [url for method, url, *_ in toured.edge.requests if method == "DELETE"]
    assert deletes and all(re.fullmatch(rf"{ORIGIN}/graph/api/graphs/[a-z0-9-]+", url) for url in deletes)
    assert not any("/api/trash/" in url for _, url, *_ in toured.edge.requests)


@pytest.mark.parametrize("path", ["/trash/some-graph", "/graphs/x/../trash/x", "/graphs/x?forever=1", "/trash"])
def test_no_route_of_the_client_reaches_delete_forever(path):
    app = App()
    with pytest.raises(ApiError):
        client.call(app.api, "DELETE", path)
    assert app.edge.requests == []


def test_an_answer_that_is_not_json_is_an_error_without_its_body():
    app = App()
    app.edge.send = lambda *request: client.Response(403, b"<html>error code: 1010 secret-looking-body</html>")
    api = client.Api(app.api.app_url, app.api.session, send=app.edge.send)
    with pytest.raises(ApiError) as refused:
        client.list_graphs(api)
    assert refused.value.status == 403 and refused.value.message == "the app answered HTTP 403 with something that is not JSON"


# ── the real sender ────────────────────────────────────────────────────────

class Recorder(BaseHTTPRequestHandler):
    seen: list[tuple[str, str, dict[str, str], bytes]] = []

    def answer(self):
        length = int(self.headers.get("content-length") or 0)
        Recorder.seen.append((self.command, self.path, {k.lower(): v for k, v in self.headers.items()}, self.rfile.read(length)))
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("location", f"http://127.0.0.1:{self.server.server_port}/followed")
            self.end_headers()
            return
        status, payload = (409, b'{"error": "conflict"}') if self.path == "/conflict" else (200, b'{"ok": true}')
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = do_POST = do_PUT = do_DELETE = answer

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    Recorder.seen = []
    httpd = HTTPServer(("127.0.0.1", 0), Recorder)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()
    httpd.server_close()
    thread.join()


def test_urllib_sends_exactly_the_headers_it_is_given_and_not_pythons_user_agent(server):
    headers = client.headers_for("PUT", f"{server}/graph", "a.session.value")
    reply = urllib_send("PUT", f"{server}/graph/api/graphs/x", headers, b'{"base": 1}')
    assert reply == client.Response(200, b'{"ok": true}')
    method, path, sent, body = Recorder.seen[0]
    assert (method, path, body) == ("PUT", "/graph/api/graphs/x", b'{"base": 1}')
    assert sent["user-agent"] == USER_AGENT and sent["cf-access-token"] == "a.session.value"
    assert sent["origin"] == server and sent["x-graph-notes"] == "1" and sent["content-type"] == "application/json"


def test_urllib_sends_a_delete_without_a_body(server):
    urllib_send("DELETE", f"{server}/graph/api/graphs/x", client.headers_for("DELETE", f"{server}/graph", "t.t.t"), None)
    method, _, sent, body = Recorder.seen[0]
    assert method == "DELETE" and body == b"" and sent["x-graph-notes"] == "1" and "content-type" not in sent


def test_urllib_never_follows_a_redirect_so_the_session_goes_nowhere_else(server):
    reply = urllib_send("GET", f"{server}/redirect", client.headers_for("GET", f"{server}/graph", "t.t.t"), None)
    assert reply.status == 302
    assert [path for _, path, _, _ in Recorder.seen] == ["/redirect"]
    assert client.signed_out(reply)


def test_urllib_returns_a_refusal_with_its_body(server):
    reply = urllib_send("GET", f"{server}/conflict", {"user-agent": USER_AGENT}, None)
    assert reply == client.Response(409, b'{"error": "conflict"}')


def test_no_answer_at_all_is_an_error_that_names_only_the_host():
    with pytest.raises(ApiError) as failure:
        urllib_send("GET", "http://127.0.0.1:1/graph/api/graphs", {"cf-access-token": "a.session.value"}, None)
    assert failure.value.status == 0 and failure.value.message.startswith("could not reach 127.0.0.1: ")
    assert "a.session.value" not in failure.value.message and failure.value.__cause__ is None
