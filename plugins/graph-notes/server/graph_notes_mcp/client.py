"""The graph app's HTTP API (backend/graph_notes/api.py), as the calls this server makes.

``ROUTES`` is everything this client can send. Deleting a graph forever is not in it,
and never will be: that stays with the person, in the web app. Nor is Delete image, the
one way an image leaves a graph's library, which is the same kind of act. ``DELETE``
reaches one address only, ``/graphs/<slug>``, which moves a graph to the trash, and every
slug and image file is checked before it goes into a path, so no argument can turn into
another address.

What a client that is not a browser has to send, found against the live app:
- a named user agent (Cloudflare answers error 1010 to Python's default one);
- ``origin`` and ``x-graph-notes: 1`` on every write, and ``content-type:
  application/json`` on every POST and PUT, even with an empty body (the API's
  same-origin rule, backend/graph_notes/auth.py). The one exception is an image upload,
  whose body is the file itself, sent with its image type, its length and its SHA-256;
- the session as ``cf-access-token``. Signed out, Access answers with a redirect to
  its sign-in page, which is never followed: it leads to one fresh sign-in and one retry.
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable
from urllib.parse import quote, urlsplit

from .session import Session

USER_AGENT = "graph-notes-mcp/0.1"
TIMEOUT_SECONDS = 30
_S = r"[a-z0-9][a-z0-9-]{0,62}"
SLUG = re.compile(_S)
_F = r"img_[0-9a-f]{16}\.(?:png|jpg|webp|gif)"
IMAGE_FILE = re.compile(_F)
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
    ("GET", r"/export/images"),  # and the images of those graphs' libraries, which it adds
    ("GET", rf"/graphs/{_S}/images"),  # a graph's image library
    ("POST", rf"/graphs/{_S}/images"),  # an upload into it: the file itself
    ("GET", rf"/graphs/{_S}/images/{_F}"),  # one image's bytes; no DELETE: Delete image stays in the web app
    ("POST", rf"/graphs/{_S}/images/copy"),  # images copied in from another graph, as a paste does
))


@dataclass(frozen=True)
class Upload:
    """A file sent as an image upload's body: the raw bytes with their type and name."""
    content_type: str
    data: bytes
    name: str


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


def headers_for(method: str, app_url: str, token: str, upload: Upload | None = None) -> dict[str, str]:
    headers = {"user-agent": USER_AGENT, "accept": "application/json", "cf-access-token": token}
    if method != "GET":
        headers |= {"origin": origin(app_url), "x-graph-notes": "1"}
    if method in ("POST", "PUT"):
        headers["content-type"] = "application/json"
    if upload is not None:
        headers |= {"content-type": upload.content_type, "content-length": str(len(upload.data)),
                    "x-content-sha256": hashlib.sha256(upload.data).hexdigest(),
                    "x-image-name": quote(upload.name, safe="")}
    return headers


def signed_out(response: Response) -> bool:
    return 300 <= response.status < 400 or response.status == 401


def send(api: Api, method: str, path: str, body: dict | None = None, upload: Upload | None = None) -> Response:
    """The app's answer to ``method path``, signed in (once more when the session has
    lapsed). The body is ``body`` as JSON, or the file ``upload``. Raises ``ApiError`` for
    a request outside ``ROUTES`` or a refused sign-in."""
    if not any(m == method and pattern.fullmatch(path) for m, pattern in ROUTES):
        raise ApiError(0, f"{method} {path} is not something this server does")
    if upload is not None:
        data = upload.data
    else:
        data = json.dumps(body if body is not None else {}).encode() if method in ("POST", "PUT") else None
    url = f"{api.app_url}/api{path}"
    token = api.session.token()
    response = api.send(method, url, headers_for(method, api.app_url, token, upload), data)
    if signed_out(response):
        token = api.session.renew(token)
        response = api.send(method, url, headers_for(method, api.app_url, token, upload), data)
        if signed_out(response):
            raise ApiError(response.status, (
                "the app refused a fresh sign-in as well. Try again to sign in once more; if it keeps "
                f"refusing, open {api.session.app_url} in a browser and sign in to check that this account may use it."))
    return response


def answer_of(response: Response) -> dict:
    """The JSON object in ``response``. Raises ``ApiError`` for anything but success."""
    try:
        answer = json.loads(response.body)
    except ValueError:
        answer = None
    if not isinstance(answer, dict) and response.status == 413:  # Cloudflare's own refusal, before the app
        raise ApiError(413, "Cloudflare doesn't accept uploads over 100 MB on this site's plan")
    if not isinstance(answer, dict):
        raise ApiError(response.status, f"the app answered HTTP {response.status} with something that is not JSON")
    if response.status >= 400:
        raise ApiError(response.status, str(answer.get("error") or f"HTTP {response.status}"))
    return answer


def call(api: Api, method: str, path: str, body: dict | None = None) -> dict:
    """The JSON the app answers ``method path`` with. Raises ``ApiError`` for anything
    but success, ``SignInError`` when no session can be had."""
    return answer_of(send(api, method, path, body))


def checked_slug(slug: str) -> str:
    if not isinstance(slug, str) or not SLUG.fullmatch(slug):
        raise ApiError(0, f"'{slug}' is not a graph's slug: lowercase letters, digits and '-', at most 63, "
                          "starting with a letter or digit")
    return slug


def checked_file(file: str) -> str:
    if not isinstance(file, str) or not IMAGE_FILE.fullmatch(file):
        raise ApiError(0, f"'{file}' is not an image file of a graph's library (img_ + 16 hex digits + .png, .jpg, .webp or .gif)")
    return file


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


def list_images(api: Api, slug: str) -> list[dict]:
    """My graph's image library, newest first."""
    return call(api, "GET", f"/graphs/{checked_slug(slug)}/images")["images"]


def upload_image(api: Api, slug: str, upload: Upload) -> tuple[dict, bool]:
    """``(entry, added)``: the file in my graph's library; ``added`` is False when the library
    already had those bytes, and ``entry`` is then the one it had."""
    response = send(api, "POST", f"/graphs/{checked_slug(slug)}/images", upload=upload)
    return answer_of(response), response.status == 201


def copy_images(api: Api, slug: str, source: str, files: list[str]) -> dict:
    """``{copied: {file: path in slug}, unavailable: [file]}``: ``files`` of ``source``'s library
    copied into my graph ``slug``'s (at most 15 a call)."""
    return call(api, "POST", f"/graphs/{checked_slug(slug)}/images/copy",
                {"from": checked_slug(source), "files": [checked_file(f) for f in files]})


def export_images(api: Api) -> list[dict]:
    """``[{path, bytes}]``: every image of my live graphs' libraries, as the Obsidian download adds them."""
    return call(api, "GET", "/export/images")["images"]


def image_bytes(api: Api, slug: str, file: str) -> bytes | None:
    """One image of my graph's library, or None when it is not there (deleted meanwhile)."""
    response = send(api, "GET", f"/graphs/{checked_slug(slug)}/images/{checked_file(file)}")
    if response.status == 200:
        return response.body
    if response.status == 404:
        return None
    try:
        message = json.loads(response.body).get("error")
    except (ValueError, AttributeError):
        message = None
    raise ApiError(response.status, message or f"the app answered HTTP {response.status} for an image")
