"""The MCP server: the graph app's features as tools at the level of notes and links.

A model using these tools needs no knowledge of the canvas format: a read returns
notes, links and clusters (``canvas.plain``), and an edit is applied to the stored
canvas here (``canvas.add_notes`` and the rest), which places new cards and leaves
everything else as it was.

Every change names the version it was made from. A change made from an older version
saves nothing: the tool says the graph changed and returns its current content, so the
app's outdated-save check (HTTP 409) is never worked around and newer work is never
overwritten.

There is no tool that deletes a graph forever, and ``client`` cannot send that request.
"""

from __future__ import annotations

import functools
import importlib.metadata
import json
import logging
import os
import re
import sys
import unicodedata
from typing import Annotated, Callable, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from . import canvas, client
from .client import Api, ApiError
from .guide import GUIDE, INSTRUCTIONS
from .session import Session, SignInError

APP_URL = "https://akshayprabhakant.com/graph"

READS = ToolAnnotations(readOnlyHint=True)
ADDS = ToolAnnotations(readOnlyHint=False, destructiveHint=False)
REPLACES = ToolAnnotations(readOnlyHint=False, destructiveHint=True)

Slug = Annotated[str, Field(description="The graph's slug (its permanent address), as list_graphs shows it.")]
Version = Annotated[int, Field(description=(
    "The graph's version number as you last saw it: from list_graphs, read_graph, or the result of your "
    "previous change. If the graph has changed since, nothing is saved and you are shown its current content."))]


class NewNote(BaseModel):
    text: str = Field(description="The note's Markdown text.")
    clusters: list[str] = Field(default_factory=list, description=(
        "Clusters to put the note in, e.g. ['backend', 'open-question']. Do not write them into the text."))
    key: str | None = Field(default=None, description=(
        "A short name of your choosing for this note, e.g. 'ingest', so that `links` in the same call can "
        "refer to it before it has an id."))
    near: str | None = Field(default=None, description=(
        "The id of an existing note this one relates to; it is placed beside it. Without it, the note goes "
        "beside the notes it is linked to, or that share one of its clusters."))


class NoteChange(BaseModel):
    id: str = Field(description="The id of the note to change.")
    text: str | None = Field(default=None, description="Its new Markdown text; leave out to keep the text.")
    clusters: list[str] | None = Field(default=None, description=(
        "The full list of clusters the note should be in (an empty list removes it from all); "
        "leave out to keep its clusters."))


class NewLink(BaseModel):
    from_note: str = Field(description="The id of the note the link starts at.")
    to_note: str = Field(description="The id of the note the link points to.")
    label: str | None = Field(default=None, description="A short label shown on the link, e.g. 'depends on'.")
    direction: Literal["forward", "both", "none"] = Field(default="forward", description=(
        "'forward': an arrow from from_note to to_note. 'both': arrows at both ends. 'none': a plain line."))


class NewGroupBox(BaseModel):
    label: str = Field(description=(
        "The box's label, e.g. 'Training'. It is shown on the box and names the cluster of the notes inside it."))
    notes: list[str] = Field(min_length=1, description=(
        "The ids of the notes (or other cards) the box holds: exactly these, and no others."))
    collapsed: bool = Field(default=False, description=(
        "True to have the box start collapsed in the web app: only its label shows, and its notes and their "
        "links are hidden until the reader opens it."))


class GroupBoxChange(BaseModel):
    id: str = Field(description="The id of the group box to change, as read_graph lists it in group_boxes.")
    label: str | None = Field(default=None, description="Its new label (this renames its cluster); leave out to keep it.")
    notes: list[str] | None = Field(default=None, description=(
        "The full set of notes (or other cards) it should hold from now on; the box is refitted around them. "
        "Leave out to keep what it holds."))
    collapsed: bool | None = Field(default=None, description=(
        "True to have it start collapsed in the web app, false to have it start open; leave out to keep it."))


class LinkOfNewNotes(NewLink):
    from_note: str = Field(description="Where the link starts: the `key` of a note in this call, or an existing note's id.")
    to_note: str = Field(description="Where the link points: the `key` of a note in this call, or an existing note's id.")


def slug_for(title: str) -> str:
    """The slug the web app gives a new graph with this title (frontend/lib/api.ts)."""
    plain = "".join(c for c in unicodedata.normalize("NFKD", title) if not "̀" <= c <= "ͯ")
    return re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")[:63].rstrip("-") or "graph"


def graph_url(app_url: str, slug: str) -> str:
    """The graph's page in the web app: the app's own address with its slash, ``/graph/?g=<slug>``.
    The bare ``/graph?g=<slug>`` is answered with a 308 redirect to it; linking the page itself saves that redirect."""
    return f"{app_url}/?g={slug}"


def summary(app_url: str, row: dict) -> dict:
    """A graph as the model sees it in a list: no canvas, and nobody's email."""
    shown = {"slug": row["slug"], "title": row["title"], "version": row["version"]["n"],
             "saved_at": row["version"]["saved_at"], "visibility": row["visibility"], "mine": row["mine"],
             "url": graph_url(app_url, row["slug"])}
    if row["visibility"] == "public":
        shown["public_url"] = f"{app_url}/public?g={row['slug']}"
    if row.get("deleted_at"):
        shown["trashed_at"] = row["deleted_at"]
    return shown


def build_server(api: Api) -> FastMCP:
    """The server over ``api``; tests pass an ``Api`` whose ``send`` is a stand-in for the app."""
    mcp = FastMCP("graph-notes", instructions=INSTRUCTIONS, version=importlib.metadata.version("graph-notes-mcp"))

    def tool(annotations: ToolAnnotations) -> Callable:
        """Registers a tool whose expected failures reach the model as a message it can act on."""
        def register(fn: Callable) -> Callable:
            @functools.wraps(fn)
            def reporting(*args, **kwargs):
                try:
                    return fn(*args, **kwargs)
                except (ApiError, SignInError, canvas.EditError) as failure:
                    raise ToolError(api.session.redacted(str(failure)), log_level=logging.WARNING) from None
            mcp.tool(annotations=annotations)(reporting)
            return fn
        return register

    def read(slug: str) -> dict:
        return client.read_graph(api, slug)

    def shown(graph: dict) -> dict:
        return {**summary(api.app_url, graph), **canvas.plain(graph["canvas"])}

    def changed(sent: int, now: dict) -> ToolError:
        """What a change made from an outdated version answers with: the graph as it is ``now``."""
        return ToolError(
            f"Nothing was saved: '{now['slug']}' has changed since version {sent} and is now at version "
            f"{now['version']['n']}. This is its current content; make your change again from it, with "
            f"that version, if it still applies.\n{json.dumps(shown(now), ensure_ascii=False)}",
            log_level=logging.WARNING)

    def saved(slug: str, sent: int, save: Callable[[], dict]) -> dict:
        """The summary after ``save``, or the ``changed`` report when the app refuses it as outdated (409)."""
        try:
            return summary(api.app_url, save())
        except ApiError as failure:
            if failure.status != 409:
                raise
        raise changed(sent, read(slug))

    def edit(slug: str, version: int, change: Callable[[dict], tuple[dict, dict]]) -> dict:
        """Applies ``change`` (canvas -> (canvas, what to report)) to version ``version`` of my graph and saves it."""
        graph = read(slug)
        if not graph["mine"]:
            raise ToolError(f"'{slug}' is someone else's public graph: you can read it and copy it, but not change it.")
        if graph["version"]["n"] != version:
            raise changed(version, graph)
        after, report = change(graph["canvas"])
        return {**saved(slug, version, lambda: client.save_graph(api, slug, version, canvas=after)), **report}

    # ── learning what there is ─────────────────────────────────────────────

    @tool(READS)
    def about_graphs() -> str:
        """Explains what a graph is (notes, links, clusters, group boxes, versions, the trash, public and
        private) and how to use these tools. Read it once before working with graphs."""
        return GUIDE

    @tool(READS)
    def list_graphs() -> dict:
        """Lists the person's graphs, and other people's public graphs (read-only), each with its slug,
        title, current version and whether it is private or public."""
        rows = [summary(api.app_url, row) for row in client.list_graphs(api)]
        return {"mine": [r for r in rows if r["mine"]], "public_from_others": [r for r in rows if not r["mine"]]}

    @tool(READS)
    def read_graph(
        slug: Slug,
        at_version: Annotated[int | None, Field(description=(
            "Leave out for the current graph. An older version number (from list_history) shows the graph "
            "as it was then."))] = None,
    ) -> dict:
        """Returns what a graph contains: its notes (id, text, clusters), the links between them, its
        clusters, its group boxes (id, label, the notes each holds, whether it starts collapsed) and its
        current version number. Call this before changing a graph."""
        if at_version is None:
            return shown(read(slug))
        old = client.read_version(api, slug, at_version)
        return {"slug": old["slug"], "title": old["title"], "version": old["version"]["n"],
                "saved_at": old["version"]["saved_at"], "is_current": False, **canvas.plain(old["canvas"])}

    @tool(READS)
    def list_history(
        slug: Slug,
        before: Annotated[int | None, Field(description="Continue the list below this version number.")] = None,
    ) -> dict:
        """Lists a graph's saved versions, newest first (up to 100): when each was saved and what kind of
        change it was. Use read_graph with at_version to look at one, restore_version to go back to it."""
        versions = client.list_versions(api, slug, before)
        return {"slug": slug, "versions": [
            {"version": v["n"], "saved_at": v["saved_at"], "change": v["action"], "title": v["title"],
             **({"note": v["note"]} if v.get("note") else {})}
            for v in versions]}

    @tool(READS)
    def list_trash() -> dict:
        """Lists the graphs in the trash. restore_from_trash brings one back."""
        return {"trash": [summary(api.app_url, row) for row in client.list_trash(api)]}

    # ── graphs ─────────────────────────────────────────────────────────────

    @tool(ADDS)
    def create_graph(
        title: Annotated[str, Field(description="The new graph's title (at most 120 characters).")],
        slug: Annotated[str | None, Field(description=(
            "Its permanent address: lowercase letters, digits and '-'. Leave out to derive it from the title."))] = None,
    ) -> dict:
        """Creates a new, empty, private graph and returns its slug and version. Add notes with add_notes."""
        return summary(api.app_url, client.create_graph(api, slug or slug_for(title), title))

    @tool(ADDS)
    def copy_graph(
        slug: Slug,
        title: Annotated[str, Field(description="The title of the copy.")],
        new_slug: Annotated[str | None, Field(description="The copy's address; leave out to derive it from the title.")] = None,
    ) -> dict:
        """Makes a new private graph with the same notes, links and layout as an existing one (one of the
        person's, or someone else's public graph). The original is not changed; history is not copied."""
        source = read(slug)
        made = client.create_graph(api, new_slug or slug_for(title), title)
        try:
            return summary(api.app_url, client.save_graph(api, made["slug"], made["version"]["n"], canvas=source["canvas"]))
        except ApiError as failure:
            raise ApiError(failure.status, (
                f"'{made['slug']}' was created but copying into it failed, so it is empty: {failure.message}")) from None

    @tool(ADDS)
    def rename_graph(slug: Slug, version: Version, title: Annotated[str, Field(description="The new title.")]) -> dict:
        """Changes a graph's title. Its slug (address) stays the same."""
        return saved(slug, version, lambda: client.save_graph(api, slug, version, title=title))

    @tool(REPLACES)
    def set_visibility(slug: Slug, version: Version, visibility: Literal["public", "private"]) -> dict:
        """Makes a graph public (anyone can view its current version, read-only) or private again. Only do
        this when the person asks for it."""
        return saved(slug, version, lambda: client.set_visibility(api, slug, version, visibility))

    @tool(REPLACES)
    def restore_version(
        slug: Slug,
        version: Version,
        restore_to: Annotated[int, Field(description="The older version number to bring back (from list_history).")],
    ) -> dict:
        """Makes an older version of a graph its newest version again (content and title). The versions in
        between stay in the history, so this can itself be undone."""
        return saved(slug, version, lambda: client.restore_version(api, slug, version, restore_to))

    @tool(REPLACES)
    def trash_graph(slug: Slug) -> dict:
        """Moves a graph to the trash. It keeps its history and can be brought back with
        restore_from_trash. (No tool here deletes a graph forever.)"""
        return summary(api.app_url, client.trash_graph(api, slug))

    @tool(ADDS)
    def restore_from_trash(slug: Slug) -> dict:
        """Brings a graph back out of the trash, with its whole history."""
        return summary(api.app_url, client.undelete_graph(api, slug))

    # ── notes and links ────────────────────────────────────────────────────

    @tool(ADDS)
    def add_notes(
        slug: Slug,
        version: Version,
        notes: Annotated[list[NewNote], Field(min_length=1)],
        links: Annotated[list[LinkOfNewNotes], Field(description=(
            "Links from or to the new notes, saved in the same version. Give them here rather than in a later "
            "link_notes call: notes that arrive with their links are laid out to follow them."))] = [],
    ) -> dict:
        """Adds notes to a graph, with the links between them, all in one saved version, and returns the
        new ids. The server chooses where each note goes on the canvas: notes linked to each other are
        laid out left to right along their links, beside the notes they relate to."""
        def change(doc: dict) -> tuple[dict, dict]:
            after, added, linked = canvas.add_notes(doc, [n.model_dump() for n in notes], [l.model_dump() for l in links])
            return after, {"added": added, **({"links_added": linked} if linked else {})}
        return edit(slug, version, change)

    @tool(REPLACES)
    def update_notes(slug: Slug, version: Version, notes: Annotated[list[NoteChange], Field(min_length=1)]) -> dict:
        """Changes the text and/or the clusters of existing notes, all in one saved version. Everything
        else about each note (its place, size and colour) stays as it is."""
        def change(doc: dict) -> tuple[dict, dict]:
            after, notices = canvas.update_notes(doc, [note.model_dump() for note in notes])
            return after, {"updated": [note.id for note in notes], **({"notices": notices} if notices else {})}
        return edit(slug, version, change)

    @tool(REPLACES)
    def remove_notes(slug: Slug, version: Version, note_ids: Annotated[list[str], Field(min_length=1)]) -> dict:
        """Removes notes (or other cards) from a graph, and the links attached to them, in one saved
        version. The previous version stays in the history."""
        def change(doc: dict) -> tuple[dict, dict]:
            after, links = canvas.remove_cards(doc, note_ids)
            return after, {"removed": note_ids, "links_removed": links}
        return edit(slug, version, change)

    @tool(ADDS)
    def link_notes(slug: Slug, version: Version, links: Annotated[list[NewLink], Field(min_length=1)]) -> dict:
        """Links notes that are already in the graph: an arrow from one note to another, with an optional
        label. All the links go in one saved version; their new ids are returned. No note moves."""
        def change(doc: dict) -> tuple[dict, dict]:
            after, added = canvas.add_links(doc, [link.model_dump() for link in links])
            return after, {"added": added}
        return edit(slug, version, change)

    @tool(REPLACES)
    def unlink_notes(slug: Slug, version: Version, link_ids: Annotated[list[str], Field(min_length=1)]) -> dict:
        """Removes links (by link id, as read_graph shows them) in one saved version. The notes stay."""
        return edit(slug, version, lambda doc: (canvas.remove_links(doc, link_ids), {"removed": link_ids}))

    # ── group boxes ────────────────────────────────────────────────────────

    def boxed(after: dict, boxes: list[dict], moved: list[str], notices: list[str], key: str) -> tuple[dict, dict]:
        return after, {key: boxes, "moved": moved, **({"notices": notices} if notices else {})}

    @tool(REPLACES)
    def add_group_boxes(slug: Slug, version: Version, boxes: Annotated[list[NewGroupBox], Field(min_length=1)]) -> dict:
        """Draws labelled group boxes, each around the notes you name, all in one saved version. A box
        holds exactly the notes inside it, so the server sizes each box to its notes and, when other
        notes or another box are in the way, moves notes so that each box holds only its own and no two
        boxes overlap. The result lists the new boxes and every note that moved."""
        def change(doc: dict) -> tuple[dict, dict]:
            return boxed(*canvas.add_group_boxes(doc, [b.model_dump() for b in boxes]), "added")
        return edit(slug, version, change)

    @tool(REPLACES)
    def update_group_boxes(slug: Slug, version: Version, boxes: Annotated[list[GroupBoxChange], Field(min_length=1)]) -> dict:
        """Changes group boxes, all in one saved version: renames them (a renamed box stays where it is and
        only widens when the new label needs more room), changes which notes each holds
        (the box is refitted around them, moving notes as add_group_boxes does), or sets whether each
        starts collapsed. What you leave out stays as it is. The result lists every note that moved."""
        def change(doc: dict) -> tuple[dict, dict]:
            return boxed(*canvas.update_group_boxes(doc, [b.model_dump() for b in boxes]), "updated")
        return edit(slug, version, change)

    @tool(REPLACES)
    def remove_group_boxes(slug: Slug, version: Version, box_ids: Annotated[list[str], Field(min_length=1)]) -> dict:
        """Removes group boxes (by id, as read_graph lists them in group_boxes) in one saved version. The
        notes inside stay where they are, with their links; they leave only the boxes' clusters."""
        def change(doc: dict) -> tuple[dict, dict]:
            after, links = canvas.remove_group_boxes(doc, box_ids)
            return after, {"removed": box_ids, **({"links_removed": links} if links else {})}
        return edit(slug, version, change)

    return mcp


def search_path(path: str) -> str:
    """``PATH`` with the places Homebrew and the usual installers put ``cloudflared`` added
    after what it already names: a harness started from the desktop, not from a shell,
    runs its servers with a bare PATH. (No empty entry: that would mean the current directory.)"""
    return os.pathsep.join(dict.fromkeys([*filter(None, path.split(os.pathsep)), "/opt/homebrew/bin", "/usr/local/bin"]))


def main() -> None:
    """Runs the server on stdio. ``GRAPH_NOTES_URL`` points it at another deployment of the app."""
    app_url = os.environ.get("GRAPH_NOTES_URL", APP_URL).rstrip("/")
    os.environ["PATH"] = search_path(os.environ.get("PATH", ""))
    notices = logging.StreamHandler(sys.stderr)  # stdout is the protocol's
    notices.setFormatter(logging.Formatter("%(message)s"))
    logging.getLogger("graph_notes_mcp").addHandler(notices)
    logging.getLogger("graph_notes_mcp").setLevel(logging.INFO)
    build_server(Api(app_url, Session(app_url))).run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
