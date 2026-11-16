"""The graph app's HTTP API (backend/graph_notes/api.py), as the calls this server makes.

``ROUTES`` is everything this client can send. Deleting a graph forever is not in it,
and never will be: that stays with the person, in the web app. ``DELETE`` reaches one
address only, ``/graphs/<slug>``, which moves a graph to the trash, and every slug is
checked before it goes into a path, so no argument can turn into another address.

What a client that is not a browser has to send, found against the live app:
- a named user agent (Cloudflare answers error 1010 to Python's default one);
- ``origin`` and ``x-graph-notes: 1`` on every write, and ``content-type:
  application/json`` on every POST and PUT, even with an empty body (the API's
  same-origin rule, backend/graph_notes/auth.py);
- the session as ``cf-access-token``. Signed out, Access answers with a redirect to
  its sign-in page, which is never followed: it leads to one fresh sign-in and one retry.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlsplit

from .session import Session

USER_AGENT = "graph-notes-mcp/0.1"
TIMEOUT_SECONDS = 30
_S = r"[a-z0-9][a-z0-9-]{0,62}"
SLUG = re.compile(_S)
ROUTES = tuple((method, re.compile(pattern)) for method, pattern in (
    ("GET", r"/graphs"),
    ("GET", rf"/graphs/{_S}"),
    ("GET", rf"/graphs/{_S}/versions(\?before=[0-9]{{1,12}})?"),
    ("GET", rf"/graphs/{_S}/versions/[0-9]{{1,12}}"),
    ("GET", r"/trash"),
    ("POST", r"/graphs"),
    ("PUT", rf"/graphs/{_S}"),
    ("DELETE", rf"/graphs/{_S}"),  # to the trash; nothing else is ever deleted
    ("POST", rf"/graphs/{_S}/(undelete|restore|visibility)"),
    ("GET", r"/export"),  # the Obsidian download: a zip of every live graph of the person's
))


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes


class ApiError(Exception):
    """The app did not do what was asked. ``status`` is the HTTP status (0: no answer)."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # a redirect is Access's sign-in page: report it, never follow it


_opener = urllib.request.build_opener(_NoRedirect)


def urllib_send(method: str, url: str, headers: dict[str, str], body: bytes | None) -> Response:
    """One request, no redirects followed. Raises ``ApiError(0)`` when there is no answer."""
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with _opener.open(request, timeout=TIMEOUT_SECONDS) as reply:
            return Response(reply.status, reply.read())
    except urllib.error.HTTPError as refused:
        return Response(refused.code, refused.read())
    except (urllib.error.URLError, TimeoutError, OSError) as failure:
        reason = getattr(failure, "reason", failure)
        raise ApiError(0, f"could not reach {urlsplit(url).hostname}: {reason}") from None


@dataclass(frozen=True)
class Api:
    """Where the app is, who is asking, and how a request is sent (tests pass a stand-in)."""

    app_url: str  # e.g. https://akshayprabhakant.com/graph
    session: Session
    send: Callable[[str, str, dict[str, str], bytes | None], Response] = urllib_send


def origin(app_url: str) -> str:
    parts = urlsplit(app_url)
    return f"{parts.scheme}://{parts.netloc}"


def headers_for(method: str, app_url: str, token: str) -> dict[str, str]:
    headers = {"user-agent": USER_AGENT, "accept": "application/json", "cf-access-token": token}
    if method != "GET":
        headers |= {"origin": origin(app_url), "x-graph-notes": "1"}
    if method in ("POST", "PUT"):
        headers["content-type"] = "application/json"
    return headers


def signed_out(response: Response) -> bool:
    return 300 <= response.status < 400 or response.status == 401


def send(api: Api, method: str, path: str, body: dict | None = None) -> Response:
    """The app's answer to ``method path``, signed in (once more when the session has
    lapsed). Raises ``ApiError`` for a request outside ``ROUTES`` or a refused sign-in."""
    if not any(m == method and pattern.fullmatch(path) for m, pattern in ROUTES):
        raise ApiError(0, f"{method} {path} is not something this server does")
    data = json.dumps(body if body is not None else {}).encode() if method in ("POST", "PUT") else None
    url = f"{api.app_url}/api{path}"
    token = api.session.token()
    response = api.send(method, url, headers_for(method, api.app_url, token), data)
    if signed_out(response):
        token = api.session.renew(token)
        response = api.send(method, url, headers_for(method, api.app_url, token), data)
        if signed_out(response):
            raise ApiError(response.status, (
                "the app refused a fresh sign-in as well. Try again to sign in once more; if it keeps "
                f"refusing, open {api.app_url} in a browser to check that this account may use it."))
    return response


def call(api: Api, method: str, path: str, body: dict | None = None) -> dict:
    """The JSON the app answers ``method path`` with. Raises ``ApiError`` for anything
    but success, ``SignInError`` when no session can be had."""
    response = send(api, method, path, body)
    try:
        answer = json.loads(response.body)
    except ValueError:
        answer = None
    if not isinstance(answer, dict):
        raise ApiError(response.status, f"the app answered HTTP {response.status} with something that is not JSON")
    if response.status >= 400:
        raise ApiError(response.status, str(answer.get("error") or f"HTTP {response.status}"))
    return answer


def checked_slug(slug: str) -> str:
    if not isinstance(slug, str) or not SLUG.fullmatch(slug):
        raise ApiError(0, f"'{slug}' is not a graph's slug: lowercase letters, digits and '-', at most 63, "
                          "starting with a letter or digit")
    return slug


def list_graphs(api: Api) -> list[dict]:
    return call(api, "GET", "/graphs")["graphs"]


def list_trash(api: Api) -> list[dict]:
    return call(api, "GET", "/trash")["graphs"]


def read_graph(api: Api, slug: str) -> dict:
    """The graph's summary with its ``canvas``: one of mine, or someone's public one."""
    return call(api, "GET", f"/graphs/{checked_slug(slug)}")


def list_versions(api: Api, slug: str, before: int | None = None) -> list[dict]:
    """Up to 100 versions, newest first; ``before`` continues below that version."""
    return call(api, "GET", f"/graphs/{checked_slug(slug)}/versions" + (f"?before={int(before)}" if before else ""))["versions"]


def read_version(api: Api, slug: str, n: int) -> dict:
    return call(api, "GET", f"/graphs/{checked_slug(slug)}/versions/{int(n)}")


def create_graph(api: Api, slug: str, title: str) -> dict:
    return call(api, "POST", "/graphs", {"slug": checked_slug(slug), "title": title})


def save_graph(api: Api, slug: str, base: int, *, canvas: dict | None = None, title: str | None = None) -> dict:
    """Saves over version ``base`` only: the app answers 409 when the graph has moved on."""
    change = {key: value for key, value in (("canvas", canvas), ("title", title)) if value is not None}
    return call(api, "PUT", f"/graphs/{checked_slug(slug)}", {"base": base, **change})


def trash_graph(api: Api, slug: str) -> dict:
    return call(api, "DELETE", f"/graphs/{checked_slug(slug)}")


def undelete_graph(api: Api, slug: str) -> dict:
    return call(api, "POST", f"/graphs/{checked_slug(slug)}/undelete")


def restore_version(api: Api, slug: str, base: int, version: int) -> dict:
    return call(api, "POST", f"/graphs/{checked_slug(slug)}/restore", {"base": base, "version": version})


def set_visibility(api: Api, slug: str, base: int, visibility: str) -> dict:
    return call(api, "POST", f"/graphs/{checked_slug(slug)}/visibility", {"base": base, "visibility": visibility})


def export_zip(api: Api) -> bytes:
    """The web app's "Download for Obsidian": a zip of every live graph of the person's."""
    response = send(api, "GET", "/export")
    if response.status == 200 and response.body[:4] == b"PK\x03\x04":
        return response.body
    try:
        message = json.loads(response.body).get("error")
    except (ValueError, AttributeError):
        message = None
    raise ApiError(response.status, message or f"the app answered HTTP {response.status} with something that is not a zip")
