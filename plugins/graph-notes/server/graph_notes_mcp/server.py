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
Nor is there one that deletes an image from a graph's library (Delete image, in the web
app), or that downloads anything from a web address: a picture comes in only from a file
on this computer that the person or the model names (``attach_image``), or as a copy of
one another graph already holds.
"""

from __future__ import annotations

import datetime
import functools
import importlib.metadata
import io
import json
import logging
import os
import re
import sys
import unicodedata
import zipfile
from pathlib import Path
from typing import Annotated, Callable, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from . import canvas, client, images
from . import workflows as flows
from .client import Api, ApiError
from .guide import GUIDE, INSTRUCTIONS
from .session import Session, SignInError

APP_URL = "https://akshayprabhakant.com/graph"


def sign_in_url(app_url: str) -> str:
    """Where ``cloudflared`` finds the app's Access application: its sign-in page, the one page the
    Access login always guards. ``/graph`` itself shows the public graphs to anyone once the owner
    lets it open signed out, and then answers with no login for ``cloudflared`` to read the
    application from (docs/adr/0002-signed-out-graph-is-the-public-gallery.md)."""
    return f"{app_url}/signin"

READS = ToolAnnotations(readOnlyHint=True)
ADDS = ToolAnnotations(readOnlyHint=False, destructiveHint=False)
REPLACES = ToolAnnotations(readOnlyHint=False, destructiveHint=True)

Slug = Annotated[str, Field(description="The graph's slug (its permanent address), as list_graphs shows it.")]
Version = Annotated[int, Field(description=(
    "The graph's version number as you last saw it: from list_graphs, read_graph, or the result of your "
    "previous change. If the graph has changed since, nothing is saved and you are shown its current content."))]


WORKFLOW_TAGS = (
    "Workflow tags: the person's own labels for their workflow work (e.g. ['draft']), a field of their own beside "
    "clusters. They are never clusters or #tags, and nothing reads them to make or change workflows. Set them only "
    "when the person asks.")


class NewNote(BaseModel):
    text: str | None = Field(default=None, description="The note's Markdown text. Leave out only for a web-page card (url).")
    url: str | None = Field(default=None, description=(
        "Instead of text: makes a web-page card for this address (e.g. 'https://example.com'), as the web app's "
        "'+ Link' does. It has no text and no clusters of its own; a group box around it puts it in a cluster."))
    clusters: list[str] = Field(default_factory=list, description=(
        "Clusters to put the note in, e.g. ['backend', 'open-question']. Do not write them into the text."))
    key: str | None = Field(default=None, description=(
        "A short name of your choosing for this note, e.g. 'ingest', so that `links` in the same call can "
        "refer to it before it has an id."))
    near: str | None = Field(default=None, description=(
        "The id of an existing note this one relates to; it is placed beside it. Without it, the note goes "
        "beside the notes it is linked to, or that share one of its clusters."))
    workflow_tags: list[str] = Field(default_factory=list, description=WORKFLOW_TAGS)


class NoteChange(BaseModel):
    id: str = Field(description="The id of the note (or web-page or file card) to change.")
    text: str | None = Field(default=None, description="Its new Markdown text; leave out to keep the text.")
    url: str | None = Field(default=None, description="A web-page card's new address; leave out to keep it.")
    file: str | None = Field(default=None, description=(
        "A file card's new path in the person's Obsidian vault, e.g. 'notes/idea.md'; leave out to keep it."))
    clusters: list[str] | None = Field(default=None, description=(
        "The full list of clusters the note should be in (an empty list removes it from all); "
        "leave out to keep its clusters."))
    workflow_tags: list[str] | None = Field(default=None, description=(
        f"The card's full list of workflow tags (an empty list removes them all); leave out to keep them. "
        f"{WORKFLOW_TAGS}"))


COLOR = (
    "One of the app's six colours 'red', 'orange', 'yellow', 'green', 'cyan', 'purple', any '#rrggbb' "
    "colour, or 'none' for no colour (the app's default).")
Side = Literal["top", "right", "bottom", "left"]
SIDE = ("Which side of {card} the link attaches to: 'top', 'right', 'bottom' or 'left', as when a link is "
        "drawn from side to side in the web app. Give from_side and to_side together, or leave both out: "
        "the link then attaches to the sides facing each other.")


class LinkLook(BaseModel):
    """How a link is drawn: the web app's Inspector fields for a link. Leave a field out to keep it."""
    color: str | None = Field(default=None, description=f"The link's colour. {COLOR}")
    line: Literal["solid", "dashed", "short-dashed", "dotted"] | None = Field(default=None, description=(
        "The line: 'solid' (the default), 'dashed', 'short-dashed' or 'dotted'."))
    path: Literal["curved", "straight", "right-angles"] | None = Field(default=None, description=(
        "The route the line takes between the cards: 'curved' (the default), 'straight' or 'right-angles'."))
    from_side: Side | None = Field(default=None, description=SIDE.format(card="from_note"))
    to_side: Side | None = Field(default=None, description=SIDE.format(card="to_note"))


class NewLink(LinkLook):
    from_note: str = Field(description="The id of the note the link starts at.")
    to_note: str = Field(description="The id of the note the link points to.")
    label: str | None = Field(default=None, description="A short label shown on the link, e.g. 'depends on'.")
    direction: Literal["forward", "backward", "both", "none"] = Field(default="forward", description=(
        "'forward': an arrow from from_note to to_note. 'backward': an arrow at from_note only. "
        "'both': arrows at both ends. 'none': a plain line."))


class LinkChange(LinkLook):
    id: str = Field(description="The id of the link to change, as read_graph lists it in links.")
    label: str | None = Field(default=None, description="Its new label; '' removes the label. Leave out to keep it.")
    direction: Literal["forward", "backward", "both", "none"] | None = Field(default=None, description=(
        "Its arrows: 'forward' (at to_note), 'backward' (at from_note), 'both', or 'none'. Leave out to keep them."))


class NoteLook(BaseModel):
    id: str = Field(description="The id of the note, other card or group box.")
    color: str | None = Field(default=None, description=f"Its colour. {COLOR}")
    border: Literal["solid", "dashed", "dotted", "none"] | None = Field(default=None, description=(
        "Its border: 'solid' (the default), 'dashed', 'dotted' or 'none'. Not for group boxes."))
    shape: Literal["rectangle", "pill", "circle", "database"] | None = Field(default=None, description=(
        "Its shape: 'rectangle' (the default), 'pill', 'circle' or 'database' (a cylinder). Not for group boxes."))
    text_align: Literal["left", "center", "right"] | None = Field(default=None, description=(
        "How its text is aligned: 'left' (the default), 'center' or 'right'. Not for group boxes."))


class NewGroupBox(BaseModel):
    label: str = Field(description=(
        "The box's label, e.g. 'Training'. It is shown on the box and names the cluster of the notes inside it."))
    notes: list[str] = Field(default_factory=list, description=(
        "The ids of the notes (or other cards) the box holds: exactly these, and no others. A group box's id "
        "(or the key of a box earlier in this call) puts that box, with everything in it, inside this one. "
        "Leave empty for an empty box of the web app's size for a new box, placed clear of everything "
        "(as the web app's '+ Group' makes one); move_notes then puts it where you want."))
    key: str | None = Field(default=None, description=(
        "A short name of your choosing for this box, so that a later box in the same call can hold it (in its notes)."))
    collapsed: bool = Field(default=False, description=(
        "True to have the box start collapsed in the web app: only its label shows, and its notes and their "
        "links are hidden until the reader opens it."))


class GroupBoxChange(BaseModel):
    id: str = Field(description="The id of the group box to change, as read_graph lists it in group_boxes.")
    label: str | None = Field(default=None, description="Its new label (this renames its cluster); leave out to keep it.")
    notes: list[str] | None = Field(default=None, description=(
        "The full set of notes (or other cards, or group boxes, which come with everything in them) it should hold "
        "from now on; the box is refitted around them. Leave out to keep what it holds."))
    collapsed: bool | None = Field(default=None, description=(
        "True to have it start collapsed in the web app, false to have it start open; leave out to keep it."))


class Placement(BaseModel):
    id: str = Field(description="The id of the note, other card or group box.")
    x: int | None = Field(default=None, description="Its new left edge, in canvas pixels (read_graph with layout shows the current ones).")
    y: int | None = Field(default=None, description="Its new top edge, in canvas pixels (y grows downwards).")
    width: int | None = Field(default=None, ge=40, description="Its new width, at least 40 (a new note is 260).")
    height: int | None = Field(default=None, ge=40, description="Its new height, at least 40 (a new note is 120).")


class LinkOfNewNotes(NewLink):
    from_note: str = Field(description="Where the link starts: the `key` of a note in this call, or an existing note's id.")
    to_note: str = Field(description="Where the link points: the `key` of a note in this call, or an existing note's id.")


class Step(BaseModel):
    """One step of a workflow: an existing link, walked along its arrow."""
    link: str | None = Field(default=None, description=(
        "The link's id, as read_graph lists it in links. Or leave out and name the step by from_note and to_note."))
    from_note: str | None = Field(default=None, description=(
        "The id of the card the step goes from. With link, needed only for a link with arrows at both ends or none, "
        "which may be walked either way."))
    to_note: str | None = Field(default=None, description="The id of the card the step goes to.")


class NewWorkflow(BaseModel):
    name: str = Field(description="The workflow's name, e.g. 'W1 · axes'; name it for the question it answers.")
    description: str | None = Field(default=None, description="The question it answers, e.g. 'What does PCA compute?'.")
    start: str = Field(description="The id of the card it starts at.")
    steps: list[Step] = Field(default_factory=list, description=(
        "Its steps, each an existing link walked along its arrow, and each reached from start through the steps "
        "before it (in this list or in any order): make the links first, with add_notes or link_notes."))
    after: list[str] = Field(default_factory=list, description=(
        "The workflows it comes after (prerequisites): ids of this graph's workflows, or keys of others in this call."))
    key: str | None = Field(default=None, description=(
        "A short name of your choosing for this workflow, e.g. 'w1', so that another workflow's `after` in the "
        "same call can refer to it before it has an id."))


class WorkflowChange(BaseModel):
    id: str = Field(description="The id of the workflow to change, as list_workflows shows it.")
    name: str | None = Field(default=None, description="Its new name; leave out to keep it.")
    description: str | None = Field(default=None, description="Its new question; '' removes it. Leave out to keep it.")
    start: str | None = Field(default=None, description="The id of its new start card; leave out to keep it.")
    remove_steps: list[Step] = Field(default_factory=list, description=(
        "Steps to take out: each by its link id, or by from_note and to_note. Done before add_steps."))
    add_steps: list[Step] = Field(default_factory=list, description=(
        "Steps to add, each an existing link walked along its arrow and reached from the start card."))
    after: list[str] | None = Field(default=None, description=(
        "The full new list of workflows it comes after (ids; an empty list: none). Leave out to keep it."))
    move_to: int | None = Field(default=None, ge=1, description=(
        "Its new place in the graph's list of workflows: 1 is first. Leave out to keep its place."))


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
        shown["public_url"] = f"{app_url}/?g={row['slug']}"
    if row.get("deleted_at"):
        shown["trashed_at"] = row["deleted_at"]
    return shown


def contents(doc: dict, layout: bool = False) -> dict:
    """What ``read_graph`` shows of a canvas: its notes, links, clusters and boxes, and (when it has
    any) its workflows, each as list_workflows shows it."""
    listed = flows.listed(doc)
    return {**canvas.plain(doc, layout), **({"workflows": listed} if listed else {})}


def save_new_file(folder: Path, name: str, data: bytes) -> Path:
    """Writes ``data`` to a new file ``name`` in ``folder``, never over an existing file: a
    name already taken becomes ``name (1)``, ``name (2)`` and so on, as a browser names a download."""
    stem, suffix = os.path.splitext(name)
    for n in range(1000):
        path = folder / (name if n == 0 else f"{stem} ({n}){suffix}")
        try:
            out = open(path, "xb")
        except FileExistsError:
            continue
        except OSError as failure:
            raise ToolError(f"could not save the zip in '{folder}': {failure.strerror or failure}") from None
        try:
            with out:
                out.write(data)
        except OSError as failure:
            path.unlink(missing_ok=True)
            raise ToolError(f"could not save the zip in '{folder}': {failure.strerror or failure}") from None
        return path
    raise ToolError(f"'{folder}' already has a thousand files named like {name}; choose another folder")


COPY_BATCH = 15  # the app copies at most this many images in one request


def pasted_text(text: str, paths: dict[str, str], missing: list[tuple[str, str]]) -> str:
    """``text`` as the web app pastes it into another graph: each copied picture's path rewritten
    (``paths``: old path -> new path), each picture that could not be copied left out."""
    text = images.rewrite_images(text, paths)
    for source, file in missing:
        text = images.without_image(text, source, file)
    return text


def paste_report(paths: dict[str, str], missing: list[tuple[str, str]], texts: list[str]) -> dict:
    """What a result says about the pictures a change copied in, or had to leave out."""
    report: dict = {}
    if paths:
        report["images_copied"] = sorted(set(paths.values()))
    if missing:
        names = ", ".join(images.picture_name(texts, source, file) for source, file in missing)
        report["notices"] = [f"Saved without {names}: not available (deleted, or no longer public)."]
    return report


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
        return {**summary(api.app_url, graph), **contents(graph["canvas"])}

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

    def editable(slug: str, version: int) -> dict:
        """My graph ``slug``, as it is at ``version``; else the reason it cannot be changed from there."""
        graph = read(slug)
        if not graph["mine"]:
            raise ToolError(f"'{slug}' is someone else's public graph: you can read it and copy it, but not change it.")
        if graph["version"]["n"] != version:
            raise changed(version, graph)
        return graph

    def edit(slug: str, version: int, change: Callable[[dict], tuple[dict, dict]]) -> dict:
        """Applies ``change`` (canvas -> (canvas, what to report)) to version ``version`` of my graph and saves it,
        labelled in History by the report's ``note`` when it has one."""
        after, report = change(editable(slug, version)["canvas"])
        return {**saved(slug, version, lambda: client.save_graph(api, slug, version, canvas=after,
                                                                 note=report.get("note"))), **report}

    def copy_in(slug: str, wanted: dict[str, list[str]]) -> tuple[dict[str, str], list[tuple[str, str]]]:
        """Copies ``wanted`` (``{source slug: [files]}``) into my graph ``slug``'s library, as the web app's
        paste does: ``(old path -> new path, [(source, file) that could not be copied])``."""
        paths: dict[str, str] = {}
        missing: list[tuple[str, str]] = []
        for source, files in wanted.items():
            for at in range(0, len(files), COPY_BATCH):
                answer = client.copy_images(api, slug, source, files[at:at + COPY_BATCH])
                paths |= {images.image_path(source, file): path for file, path in answer["copied"].items()}
                missing += [(source, file) for file in answer["unavailable"]]
        return paths, missing

    def paste(slug: str, texts: list[str]) -> tuple[Callable[[str], str], dict]:
        """Copies the pictures ``texts`` show from other graphs into my graph ``slug``'s library first:
        ``(how each text is then saved, what the result says about it)``."""
        paths, missing = copy_in(slug, images.foreign_images(texts, slug))
        return (lambda text: pasted_text(text, paths, missing)), paste_report(paths, missing, texts)

    def with_notices(report: dict, notices: list[str]) -> dict:
        both = [*report.get("notices", []), *notices]
        return {**report, **({"notices": both} if both else {})}

    # ── learning what there is ─────────────────────────────────────────────

    @tool(READS)
    def about_graphs() -> str:
        """Explains what a graph is (notes, links, clusters, group boxes, workflows, versions, the trash, public and
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
        layout: Annotated[bool, Field(description=(
            "True to also get where each note, card and group box is on the canvas and its size (x, y, width, "
            "height, in canvas pixels), which you need only to move or resize things with move_notes."))] = False,
    ) -> dict:
        """Returns what a graph contains: its notes (id, text, clusters, and workflow_tags when a note has
        any), the links between them, its clusters, its group boxes (id, label, the notes and boxes each
        holds, whether it starts collapsed), its workflows (each as list_workflows shows it) and its
        current version number, with how each note, link and box looks wherever that is not the
        app's default. Call this before changing a graph."""
        if at_version is None:
            graph = read(slug)
            return {**summary(api.app_url, graph), **contents(graph["canvas"], layout)}
        old = client.read_version(api, slug, at_version)
        return {"slug": old["slug"], "title": old["title"], "version": old["version"]["n"],
                "saved_at": old["version"]["saved_at"], "is_current": False, **contents(old["canvas"], layout)}

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
        person's, or someone else's public graph). The original is not changed; history is not copied.
        Its pictures are copied into the new graph's library, and its notes show the copies: from one of
        the person's own graphs, its whole image library; from someone else's, the pictures its notes show."""
        source = read(slug)
        doc = source["canvas"]
        texts = [n["text"] for n in doc.get("nodes") or [] if n.get("type") == "text" and isinstance(n.get("text"), str)]
        made = client.create_graph(api, new_slug or slug_for(title), title)
        try:
            wanted = images.foreign_images(texts, made["slug"])
            if source["mine"]:  # the whole library, pictures on no card included: the person's own
                wanted[slug] = list(dict.fromkeys([*wanted.get(slug, []), *(e["file"] for e in client.list_images(api, slug))]))
            paths, missing = copy_in(made["slug"], wanted)
            nodes = [{**n, "text": pasted_text(n["text"], paths, missing)}
                     if n.get("type") == "text" and isinstance(n.get("text"), str) else n for n in doc.get("nodes") or []]
            copied = client.save_graph(api, made["slug"], made["version"]["n"], canvas={**doc, "nodes": nodes})
        except ApiError as failure:
            raise ApiError(failure.status, (
                f"'{made['slug']}' was created but copying into it failed, so it is empty: {failure.message}")) from None
        return {**summary(api.app_url, copied), **paste_report(paths, missing, texts)}

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
        """Brings back an older version's content and title as the graph's newest version. The versions in
        between stay in the history, so this can itself be undone. A version holds content and title only:
        restoring one whose content and title equal the current ones saves no new version, and who can see
        the graph is not restored (change that with set_visibility). This is undo and redo for content and
        title changes: to undo one, restore the version before it; to redo, restore the version the undo
        replaced."""
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

    @tool(ADDS)
    def download_for_obsidian(
        folder: Annotated[str | None, Field(description=(
            "The folder on this computer to save the zip in; leave out for the Downloads folder."))] = None,
    ) -> dict:
        """The web app's "Download for Obsidian": saves a zip of every one of the person's graphs (not the
        trash), each as an Obsidian canvas, with each graph's image library under attachments/<slug>/, to a
        new file on this computer, and says where. Unzip it, then in Obsidian: Open folder as vault. It
        never overwrites a file. Only do this when the person asks."""
        place = Path(folder).expanduser() if folder else Path.home() / "Downloads"
        if not place.is_dir():
            raise ToolError(f"'{place}' is not a folder on this computer; name one that exists, or leave folder out")
        data = client.export_zip(api)
        try:
            names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        except zipfile.BadZipFile:
            raise ToolError("the app's download came back cut short or damaged; nothing was saved. Try again.") from None
        pictures = client.export_images(api)
        day = datetime.datetime.now(datetime.timezone.utc).date().isoformat()  # the app's own name for it
        saved = save_new_file(place.resolve(), f"graph-notes-{day}.zip", data)
        try:
            added, missing = add_attachments(saved, pictures)
        except BaseException:
            saved.unlink(missing_ok=True)
            raise
        return {"path": str(saved), "bytes": saved.stat().st_size,
                "graphs": sorted(n.split("/", 1)[1][:-len(".canvas")] for n in names if n.endswith(".canvas")),
                "images": added, **({"images_missing": missing} if missing else {}),
                "next": "Unzip it, then in Obsidian: Open folder as vault. Edits made there are not sent back."}

    def add_attachments(zip_path: Path, pictures: list[dict]) -> tuple[int, list[str]]:
        """Adds each library image to the saved zip at ``graph-notes/attachments/<slug>/<file>``, where the
        notes' paths find it from the vault's root, fetched one at a time so only one is ever in memory.
        Returns how many it added, and the paths it could not fetch (deleted since the list was made)."""
        added, missing = 0, []
        try:
            with zipfile.ZipFile(zip_path, "a") as archive:
                for picture in pictures:
                    found = images.PATH.fullmatch(picture.get("path", ""))
                    if not found:
                        continue
                    data = client.image_bytes(api, found[1], found[2])
                    if data is None:
                        missing.append(picture["path"])
                        continue
                    # Stored as they are: pictures are compressed already.
                    archive.writestr(f"graph-notes/{picture['path']}", data, compress_type=zipfile.ZIP_STORED)
                    added += 1
        except OSError as failure:
            raise ToolError(f"could not save the zip in '{zip_path.parent}': {failure.strerror or failure}") from None
        return added, missing

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
        """Adds notes (and web-page cards: give a url instead of text) to a graph, with the links between
        them, all in one saved version, and returns the new ids. The server chooses where each note goes
        on the canvas: notes linked to each other are laid out left to right along their links, beside
        the notes they relate to. A picture line copied from another graph's note
        (![name](attachments/<other slug>/<file>)) brings that picture into this graph's library first, as
        a paste in the web app does, once the notes and links are known to be right: a change that is refused
        copies nothing."""
        def change(doc: dict) -> tuple[dict, dict]:
            given, edges = [n.model_dump() for n in notes], [l.model_dump() for l in links]
            canvas.add_notes(doc, given, edges)  # an edit that fails copies no picture in
            pasted, report = paste(slug, [n.text for n in notes if n.text])
            given = [{**g, **({"text": pasted(g["text"])} if g["text"] else {})} for g in given]
            after, added, linked = canvas.add_notes(doc, given, edges)
            return after, {"added": added, **({"links_added": linked} if linked else {}), **report}
        return edit(slug, version, change)

    @tool(REPLACES)
    def update_notes(slug: Slug, version: Version, notes: Annotated[list[NoteChange], Field(min_length=1)]) -> dict:
        """Changes the text and/or the clusters of existing notes, the address of web-page cards and the
        vault path of file cards, all in one saved version. Everything else about each card (its place,
        size and look) stays as it is. A picture line copied from another graph's note brings that
        picture into this graph's library first, as add_notes does. Taking a picture's line out of a
        note leaves the picture in the library (list_images); only the person can delete it, in the web app."""
        def change(doc: dict) -> tuple[dict, dict]:
            given = [n.model_dump() for n in notes]
            canvas.update_notes(doc, given)  # an edit that fails copies no picture in
            pasted, report = paste(slug, [n.text for n in notes if n.text])
            given = [{**g, **({"text": pasted(g["text"])} if g["text"] else {})} for g in given]
            after, notices = canvas.update_notes(doc, given)
            return after, with_notices({"updated": [note.id for note in notes], **report}, notices)
        return edit(slug, version, change)

    @tool(REPLACES)
    def remove_notes(slug: Slug, version: Version, note_ids: Annotated[list[str], Field(min_length=1)]) -> dict:
        """Removes notes (or other cards) from a graph, and the links attached to them, in one saved
        version. The workflow steps that used those links go too, and a workflow that started at a removed
        card is left without a start; the result's notices say which. The previous version stays in the history."""
        def change(doc: dict) -> tuple[dict, dict]:
            after, links = canvas.remove_cards(doc, note_ids)
            after, notices = flows.pruned(doc, after, links, note_ids)
            return after, with_notices({"removed": note_ids, "links_removed": links}, notices)
        return edit(slug, version, change)

    @tool(ADDS)
    def link_notes(slug: Slug, version: Version, links: Annotated[list[NewLink], Field(min_length=1)]) -> dict:
        """Links notes that are already in the graph: an arrow from one note to another, with an optional
        label and look. All the links go in one saved version; their new ids are returned. No note moves."""
        def change(doc: dict) -> tuple[dict, dict]:
            after, added = canvas.add_links(doc, [link.model_dump() for link in links])
            return after, {"added": added}
        return edit(slug, version, change)

    @tool(REPLACES)
    def unlink_notes(slug: Slug, version: Version, link_ids: Annotated[list[str], Field(min_length=1)]) -> dict:
        """Removes links (by link id, as read_graph shows them) in one saved version. The notes stay. The
        workflow steps that used those links go too, and the result's notices say which."""
        def change(doc: dict) -> tuple[dict, dict]:
            after, notices = flows.pruned(doc, canvas.remove_links(doc, link_ids), link_ids)
            return after, with_notices({"removed": link_ids}, notices)
        return edit(slug, version, change)

    @tool(REPLACES)
    def update_links(slug: Slug, version: Version, links: Annotated[list[LinkChange], Field(min_length=1)]) -> dict:
        """Changes existing links in place, all in one saved version: their label, arrows (direction),
        colour, line, path and the sides of the cards they attach to, as the web app's link editor does.
        Each link keeps its id and its two notes; what you leave out stays as it is."""
        def change(doc: dict) -> tuple[dict, dict]:
            after, updated = canvas.update_links(doc, [link.model_dump() for link in links])
            return after, {"updated": updated}
        return edit(slug, version, change)

    # ── appearance ─────────────────────────────────────────────────────────

    @tool(REPLACES)
    def style_notes(slug: Slug, version: Version, notes: Annotated[list[NoteLook], Field(min_length=1)]) -> dict:
        """Sets how notes, other cards and group boxes look, all in one saved version, as the web app's
        card editor does: colour (also for group boxes), and for cards the border, shape and text
        alignment. What you leave out stays as it is; nothing moves. Link colours: update_links."""
        def change(doc: dict) -> tuple[dict, dict]:
            after, styled = canvas.style_notes(doc, [note.model_dump() for note in notes])
            return after, {"styled": styled}
        return edit(slug, version, change)

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

    # ── moving and resizing ────────────────────────────────────────────────

    @tool(REPLACES)
    def move_notes(slug: Slug, version: Version, notes: Annotated[list[Placement], Field(min_length=1)]) -> dict:
        """Moves and resizes notes, other cards and group boxes by hand, all in one saved version, as
        dragging and resizing them in the web app does: an entry that changes only a group box's x/y
        drags it, carrying everything wholly inside it; an entry that changes its width or height
        (with or without x/y) resizes it, as the web app's resize handles do, and carries nothing. To
        move a box and resize it, send a move entry then a resize entry. Read the graph with layout=true first for where things
        are. A card moved into or out of a labelled box joins or leaves its cluster; the result says so,
        and says when a card now overlaps another. Prefer add_group_boxes / update_group_boxes to
        arrange notes in boxes: they place everything for you."""
        def change(doc: dict) -> tuple[dict, dict]:
            after, placed, notices = canvas.move_cards(doc, [n.model_dump() for n in notes])
            return after, {"placed": placed, **({"notices": notices} if notices else {})}
        return edit(slug, version, change)

    @tool(REPLACES)
    def remove_group_boxes(slug: Slug, version: Version, box_ids: Annotated[list[str], Field(min_length=1)]) -> dict:
        """Removes group boxes (by id, as read_graph lists them in group_boxes) in one saved version. The
        notes inside stay where they are, with their links; they leave only the boxes' clusters."""
        def change(doc: dict) -> tuple[dict, dict]:
            after, links = canvas.remove_group_boxes(doc, box_ids)
            after, notices = flows.pruned(doc, after, links, box_ids)
            return after, with_notices({"removed": box_ids, **({"links_removed": links} if links else {})}, notices)
        return edit(slug, version, change)

    # ── workflows ──────────────────────────────────────────────────────────

    @tool(READS)
    def list_workflows(slug: Slug) -> dict:
        """Lists a graph's workflows in their order: each one's id, name, question (description), start
        card, how many cards and steps it has and how many steps Follow takes, its end cards, the workflows
        it comes after, and any warnings (a step that can't be reached, a missing start). Also for other
        people's public graphs, read-only."""
        graph = read(slug)
        return {"slug": slug, "version": graph["version"]["n"], "workflows": flows.listed(graph["canvas"])}

    @tool(READS)
    def read_workflow(
        slug: Slug,
        workflow: Annotated[str, Field(description="The workflow's id, as list_workflows shows it.")],
    ) -> dict:
        """Returns a workflow's route as the web app's Follow walks it: one step per depth level of the route
        (a card comes after every card leading into it; at a split, every branch), each with its cards (in
        reading order, top to bottom then left to right), the cards it came from, the cards it leads to (a
        card reached by a longer way too is marked reached_later, with the step it is reached at) and the
        link ids in and out. Also its end cards, the workflows that come after it, its warnings, and all of
        it as plain directions text to quote. Use it to explain or check a workflow."""
        graph = read(slug)
        return {"slug": slug, "version": graph["version"]["n"], **flows.directions(graph["canvas"], workflow)}

    @tool(ADDS)
    def add_workflows(slug: Slug, version: Version, workflows: Annotated[list[NewWorkflow], Field(
            min_length=1, description="The workflows to add, at the end of the graph's list.")]) -> dict:
        """Adds workflows to a graph, all in one saved version: each a named route from a start card along
        existing links (each step walked along its link's arrow), with the workflows it comes after. Make
        the links first. A step names its link by id, or by the two notes it joins. Returns each new
        workflow's id (with your key), its cards, steps and ends. Never encode a workflow in clusters."""
        def change(doc: dict) -> tuple[dict, dict]:
            after, added = flows.add(doc, [w.model_dump() for w in workflows])
            return after, {"added": added, "note": flows.note([w["name"] for w in added], "added")}
        return edit(slug, version, change)

    @tool(REPLACES)
    def update_workflows(slug: Slug, version: Version, workflows: Annotated[list[WorkflowChange], Field(
            min_length=1, description="The changes, each naming its workflow's id.")]) -> dict:
        """Changes workflows in place, all in one saved version: their name, question, start card, the
        workflows they come after (the full new list), their place in the list, and their steps, which are
        added or taken out (never sent whole). What you leave out stays as it is. Taking steps out removes
        content the person made: do it only when they ask."""
        def change(doc: dict) -> tuple[dict, dict]:
            after, updated = flows.update(doc, [w.model_dump() for w in workflows])
            return after, {"updated": updated, "note": flows.note([w["name"] for w in updated], "updated")}
        return edit(slug, version, change)

    @tool(REPLACES)
    def remove_workflows(slug: Slug, version: Version, workflow_ids: Annotated[list[str], Field(min_length=1)]) -> dict:
        """Removes workflows (by id) in one saved version, and takes them out of other workflows' 'comes
        after' lists. Their cards and links stay. Only when the person asks; restore_version undoes it."""
        def change(doc: dict) -> tuple[dict, dict]:
            after, notices = flows.remove(doc, workflow_ids)
            names = [flows.find(doc, i).get("name") for i in dict.fromkeys(workflow_ids)]
            return after, with_notices({"removed": workflow_ids, "note": flows.note(names, "removed")}, notices)
        return edit(slug, version, change)

    # ── images ─────────────────────────────────────────────────────────────

    @tool(READS)
    def list_images(slug: Slug) -> dict:
        """Lists a graph's image library, newest first: each picture's path (what a note's
        ![name](path) line carries), its name, type, size in bytes and pixels, and the notes that
        show it (none: it is in the library only). Only for the person's own graphs."""
        graph = read(slug)
        if not graph["mine"]:
            raise ToolError(f"'{slug}' is someone else's public graph: its image library is theirs. read_graph shows "
                            "the pictures its notes show, and copy_graph or a note's picture line copies them.")
        shows = images.usage(graph["canvas"], slug)
        return {"slug": slug, "version": graph["version"]["n"], "images": [
            {"path": e["path"], "name": e["name"], "content_type": e["content_type"], "bytes": e["bytes"],
             "width": e["width"], "height": e["height"], "added_at": e["created_at"], "notes": shows.get(e["file"], [])}
            for e in client.list_images(api, slug)]}

    @tool(ADDS)
    def attach_image(
        slug: Slug,
        version: Version,
        note_id: Annotated[str, Field(description="The id of the note to show the picture in (a note, not a web-page or file card).")],
        path: Annotated[str, Field(description=(
            "The picture's file on this computer, e.g. '~/Desktop/whiteboard.png': a PNG, JPEG, WebP or GIF. "
            "Never a web address: a picture on the web goes in a note's text as ![name](https://...)."))],
        alt: Annotated[str | None, Field(description=(
            "The picture's name in the note (its alt text); leave out for the file's name."))] = None,
    ) -> dict:
        """Adds a picture from a file on this computer (a screenshot, a downloaded file) to the graph's
        image library, and shows it in a note: its ![name](attachments/<slug>/<file>) line goes under the
        note's first line, in one saved version. A note with no text takes the picture's shape (its height
        fits the picture, up to 600). Location and other metadata are removed from the file first, without
        changing a pixel. A picture the library already has is not stored twice. Only do this with a file
        the person named or asked for."""
        file = Path(path).expanduser()
        if not file.is_file():
            raise ToolError(f"'{file}' is not a file on this computer; name a picture file that exists")
        if file.stat().st_size > images.CLOUDFLARE_MAX_BYTES:
            raise ToolError("Cloudflare doesn't accept uploads over 100 MB on this site's plan; choose a smaller file")
        try:
            data = file.read_bytes()
        except OSError as failure:
            raise ToolError(f"could not read '{file}': {failure.strerror or failure}") from None
        kind = images.sniff(data)
        if kind == "heic":
            raise ToolError("HEIC isn't accepted: export it as JPEG or PNG first")
        if kind == "svg":
            raise ToolError("SVG isn't accepted (it can carry scripts): export it as PNG")
        if kind is None:
            raise ToolError(f"'{file.name}' is not a PNG, JPEG, WebP or GIF image: only those can be added")
        graph = editable(slug, version)
        note = next((n for n in graph["canvas"].get("nodes") or [] if n.get("id") == note_id), None)
        if note is None:
            raise ToolError(f"'{slug}' has no note with id '{note_id}' (read_graph lists them)")
        if note.get("type") != "text":
            card = "a group box" if note.get("type") == "group" else "a web-page or file card"
            raise ToolError(f"'{note_id}' is {card}, not a note: a picture goes in a note's text")
        kept = images.strip_metadata(data, kind)
        entry, added = client.upload_image(api, slug, client.Upload(kind, kept, file.name))
        text = note.get("text") or ""
        shaped = {}
        if images.is_empty_note(text) and entry.get("width") and entry.get("height") and note.get("width"):
            size = images.drawn_size(kept, kind, (entry["width"], entry["height"]))
            shaped = {"height": images.fitted_height(note["width"], size)}
        after = {**note, "text": images.insert_image_line(text, images.image_line(alt or entry["name"], entry["path"])), **shaped}
        doc = {**graph["canvas"], "nodes": [after if n is note else n for n in graph["canvas"]["nodes"]]}
        shown = {"path": entry["path"], "name": entry["name"], "note": note_id,
                 **({} if added else {"already_in_library": True})}
        try:
            return {**summary(api.app_url, client.save_graph(api, slug, version, canvas=doc)), **shown}
        except ApiError as failure:
            if failure.status != 409:
                raise ApiError(failure.status, f"The picture is in the library as {entry['path']}, but the note was "
                                               f"not changed: {failure.message}") from None
        raise ToolError(f"The picture is in the library as {entry['path']}, but the note was not changed. "
                        f"{changed(version, read(slug))}", log_level=logging.WARNING)

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
    build_server(Api(app_url, Session(sign_in_url(app_url)))).run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
