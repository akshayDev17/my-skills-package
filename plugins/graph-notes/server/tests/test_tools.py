"""Every tool, called through a real MCP client session, against the real API behind a
stand-in for Cloudflare (support.py)."""

from __future__ import annotations

import asyncio
import json

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from graph_notes_mcp import client
from support import OTHER, App, fixture

TOOLS = {
    "about_graphs", "list_graphs", "read_graph", "list_history", "list_trash",
    "create_graph", "copy_graph", "rename_graph", "set_visibility", "restore_version",
    "trash_graph", "restore_from_trash",
    "add_notes", "update_notes", "remove_notes", "link_notes", "unlink_notes", "update_links", "style_notes",
    "add_group_boxes", "update_group_boxes", "remove_group_boxes", "move_notes", "download_for_obsidian",
    "list_images", "attach_image",
    "list_workflows", "read_workflow", "add_workflows", "update_workflows", "remove_workflows",
}


def properties(schema: object) -> set[str]:
    """Every property name anywhere in a JSON schema."""
    if isinstance(schema, dict):
        names = set(schema.get("properties") or {}) if isinstance(schema.get("properties"), dict) else set()
        return names | {name for value in schema.values() for name in properties(value)}
    if isinstance(schema, list):
        return {name for value in schema for name in properties(value)}
    return set()


@pytest.fixture
def app() -> App:
    return App()


def new_graph(app: App, title: str = "Plan") -> dict:
    return app.call("create_graph", title=title)


def test_the_tools_are_the_graph_features_and_none_deletes_forever_or_deletes_an_image(app):
    async def listed():
        async with Client(app.server) as mcp_client:
            return await mcp_client.list_tools()
    tools = asyncio.run(listed())
    assert {t.name for t in tools} == TOOLS
    assert not any("delete" in t.name for t in tools)
    assert all(t.description and len(t.description) > 40 for t in tools)
    # Nothing a model must send is in canvas terms, and only moving things by hand takes a place or a size.
    schemas = json.dumps([t.input_schema for t in tools]).lower()
    assert not any(word in schemas for word in ("fromnode", "tonode", "styleattributes", "canvas json"))
    geometry = {"x", "y", "width", "height"}
    assert {t.name for t in tools if properties(t.input_schema) & geometry} == {"move_notes"}


def test_the_about_graphs_tool_explains_a_graph(app):
    async def read():
        async with Client(app.server) as mcp_client:
            return mcp_client.instructions
    instructions = asyncio.run(read())
    guide = app.call("about_graphs")
    for word in ("note", "link", "cluster", "group box", "version", "history", "trash", "public", "private", "forever"):
        assert word in guide.lower()
    assert "about_graphs" in instructions


def test_create_then_list_then_read(app):
    made = new_graph(app, "Ünïcode & Spaces!  ")
    assert made == {"slug": "unicode-spaces", "title": "Ünïcode & Spaces!", "version": 1, "saved_at": made["saved_at"],
                    "visibility": "private", "mine": True, "url": "https://graph.test/graph/?g=unicode-spaces"}
    assert app.call("create_graph", title="Second", slug="my-own-slug")["slug"] == "my-own-slug"
    listed = app.call("list_graphs")
    assert sorted(g["slug"] for g in listed["mine"]) == ["my-own-slug", "unicode-spaces"] and listed["public_from_others"] == []
    assert app.call("read_graph", slug="unicode-spaces") == {**made, "notes": [], "links": [], "clusters": []}


def test_a_taken_or_malformed_slug_is_refused_with_the_reason(app):
    new_graph(app, "Plan")
    with pytest.raises(ToolError, match="'plan' is already one of your graphs"):
        new_graph(app, "Plan")
    with pytest.raises(ToolError, match="'Not A Slug' is not a graph's slug"):
        app.call("create_graph", title="x", slug="Not A Slug")
    with pytest.raises(ToolError, match="a title is required"):
        app.call("create_graph", title="   ", slug="blank")
    with pytest.raises(ToolError, match="no graph named 'missing'"):
        app.call("read_graph", slug="missing")


def test_notes_and_links_are_added_changed_and_removed_and_each_call_is_one_version(app):
    new_graph(app)
    added = app.call("add_notes", slug="plan", version=1, notes=[
        {"text": "Collect data", "clusters": ["Pipeline"]},
        {"text": "Train", "clusters": ["pipeline", "ml"]},
        {"text": "Ship"},
    ])
    assert added["version"] == 2
    collect, train, ship = (note["id"] for note in added["added"])
    assert added["added"][0] == {"id": collect, "text": "Collect data", "clusters": ["pipeline"]}

    linked = app.call("link_notes", slug="plan", version=2, links=[
        {"from_note": collect, "to_note": train, "label": "feeds"},
        {"from_note": train, "to_note": ship, "direction": "both"},
    ])
    assert linked["version"] == 3
    feeds, ships = (link["id"] for link in linked["added"])

    updated = app.call("update_notes", slug="plan", version=3, notes=[
        {"id": ship, "text": "Ship it", "clusters": ["release"]},
        {"id": collect, "clusters": []},
    ])
    assert updated["version"] == 4 and updated["updated"] == [ship, collect] and "notices" not in updated

    graph = app.call("read_graph", slug="plan")
    assert graph["version"] == 4
    assert graph["notes"] == [
        {"id": collect, "text": "Collect data", "clusters": []},
        {"id": train, "text": "Train", "clusters": ["ml", "pipeline"]},
        {"id": ship, "text": "Ship it", "clusters": ["release"]},
    ]
    assert graph["links"] == [
        {"id": feeds, "from_note": collect, "to_note": train, "direction": "forward", "label": "feeds"},
        {"id": ships, "from_note": train, "to_note": ship, "direction": "both"},
    ]
    assert [c["name"] for c in graph["clusters"]] == ["ml", "pipeline", "release"]

    assert app.call("unlink_notes", slug="plan", version=4, link_ids=[ships])["removed"] == [ships]
    removed = app.call("remove_notes", slug="plan", version=5, note_ids=[collect])
    assert removed["version"] == 6 and removed["removed"] == [collect] and removed["links_removed"] == [feeds]
    graph = app.call("read_graph", slug="plan")
    assert [n["id"] for n in graph["notes"]] == [train, ship] and graph["links"] == []


def test_notes_arrive_with_their_links_by_key_in_one_version(app):
    new_graph(app)
    added = app.call("add_notes", slug="plan", version=1,
                     notes=[{"text": "Collect", "key": "collect"}, {"text": "Train", "key": "train", "clusters": ["ml"]},
                            {"text": "Ship", "key": "ship"}],
                     links=[{"from_note": "collect", "to_note": "train", "label": "feeds"},
                            {"from_note": "train", "to_note": "ship"}])
    assert added["version"] == 2
    ids = {note["key"]: note["id"] for note in added["added"]}
    assert [(l["from_note"], l["to_note"], l.get("label")) for l in added["links_added"]] == [
        (ids["collect"], ids["train"], "feeds"), (ids["train"], ids["ship"], None)]
    graph = app.call("read_graph", slug="plan")
    assert graph["version"] == 2 and len(graph["notes"]) == 3 and graph["links"] == added["links_added"]
    stored = {n["id"]: n for n in app.edge.canvas("plan")["nodes"]}
    xs = [stored[ids[key]]["x"] for key in ("collect", "train", "ship")]
    assert xs == sorted(xs) and len(set(xs)) == 3 and len({stored[i]["y"] for i in ids.values()}) == 1  # a row, in order

    more = app.call("add_notes", slug="plan", version=2, notes=[{"text": "Monitor", "key": "m"}],
                    links=[{"from_note": ids["ship"], "to_note": "m", "direction": "both"}])
    assert more["links_added"][0]["from_note"] == ids["ship"] and more["links_added"][0]["direction"] == "both"
    with pytest.raises(ToolError, match="to_note 'nobody' names no note"):
        app.call("add_notes", slug="plan", version=3, notes=[{"text": "x", "key": "x"}], links=[{"from_note": "x", "to_note": "nobody"}])
    assert app.call("read_graph", slug="plan")["version"] == 3


def test_what_the_server_stores_is_a_canvas_the_app_accepts_with_cards_that_do_not_overlap(app):
    new_graph(app)
    app.call("add_notes", slug="plan", version=1, notes=[{"text": f"Note {i}", "clusters": ["a"]} for i in range(6)])
    stored = app.edge.canvas("plan")
    assert stored["metadata"] == {"version": "1.0-1.0", "frontmatter": {}}  # the new graph's marker, kept
    cards = stored["nodes"]
    assert all(n["type"] == "text" and n["text"].endswith("\n#a") and len(n["id"]) == 16 for n in cards)
    for a in cards:
        for b in cards:
            assert a is b or (a["x"] >= b["x"] + b["width"] or b["x"] >= a["x"] + a["width"]
                              or a["y"] >= b["y"] + b["height"] or b["y"] >= a["y"] + a["height"])


def test_an_edit_that_cannot_be_made_saves_nothing_and_says_why(app):
    new_graph(app)
    with pytest.raises(ToolError, match="'nope' names no note"):
        app.call("update_notes", slug="plan", version=1, notes=[{"id": "nope", "text": "x"}])
    with pytest.raises(ToolError, match="from_note 'a' names no note"):
        app.call("link_notes", slug="plan", version=1, links=[{"from_note": "a", "to_note": "b"}])
    with pytest.raises(ToolError):
        app.call("add_notes", slug="plan", version=1, notes=[])
    assert app.call("read_graph", slug="plan")["version"] == 1


def test_rename_keeps_the_slug(app):
    new_graph(app)
    renamed = app.call("rename_graph", slug="plan", version=1, title="The plan, renamed")
    assert (renamed["slug"], renamed["title"], renamed["version"]) == ("plan", "The plan, renamed", 2)


def test_history_lists_versions_reads_an_old_one_and_restores_it(app):
    new_graph(app)
    app.call("add_notes", slug="plan", version=1, notes=[{"text": "first"}])
    app.call("add_notes", slug="plan", version=2, notes=[{"text": "second"}])
    app.call("rename_graph", slug="plan", version=3, title="Renamed")
    history = app.call("list_history", slug="plan")
    assert [(v["version"], v["change"]) for v in history["versions"]] == [(4, "rename"), (3, "save"), (2, "save"), (1, "create")]
    assert "saved_by" not in json.dumps(history) and "@" not in json.dumps(history)
    assert [v["version"] for v in app.call("list_history", slug="plan", before=3)["versions"]] == [2, 1]

    old = app.call("read_graph", slug="plan", at_version=2)
    assert (old["version"], old["title"], old["is_current"]) == (2, "Plan", False)
    assert [n["text"] for n in old["notes"]] == ["first"]

    restored = app.call("restore_version", slug="plan", version=4, restore_to=2)
    assert (restored["version"], restored["title"]) == (5, "Plan")
    assert [n["text"] for n in app.call("read_graph", slug="plan")["notes"]] == ["first"]
    assert app.call("list_history", slug="plan")["versions"][0]["note"] == "restored version 2"
    with pytest.raises(ToolError, match="'plan' has no version 99"):
        app.call("restore_version", slug="plan", version=5, restore_to=99)


def test_public_and_private(app):
    new_graph(app)
    public = app.call("set_visibility", slug="plan", version=1, visibility="public")
    assert (public["visibility"], public["version"]) == ("public", 2)
    assert public["public_url"] == "https://graph.test/graph/?g=plan"
    private = app.call("set_visibility", slug="plan", version=2, visibility="private")
    assert private["visibility"] == "private" and "public_url" not in private


def test_trash_and_restore_from_trash(app):
    new_graph(app)
    trashed = app.call("trash_graph", slug="plan")
    assert trashed["trashed_at"] and trashed["version"] == 2
    assert app.call("list_graphs")["mine"] == []
    assert [g["slug"] for g in app.call("list_trash")["trash"]] == ["plan"]
    with pytest.raises(ToolError, match="'plan' is in the trash"):
        app.call("read_graph", slug="plan")
    with pytest.raises(ToolError, match="already in the trash"):
        app.call("trash_graph", slug="plan")
    back = app.call("restore_from_trash", slug="plan")
    assert "trashed_at" not in back and app.call("list_trash") == {"trash": []}
    assert [g["slug"] for g in app.call("list_graphs")["mine"]] == ["plan"]


def test_copy_makes_a_new_graph_with_the_same_canvas_and_leaves_the_original(app):
    app.edge.seed("example", fixture("example"))
    copied = app.call("copy_graph", slug="example", title="Example copy")
    assert (copied["slug"], copied["title"], copied["visibility"], copied["version"]) == ("example-copy", "Example copy", "private", 2)
    assert app.edge.canvas("example-copy") == app.edge.canvas("example") == fixture("example")
    assert app.call("read_graph", slug="example")["version"] == 1
    assert app.call("copy_graph", slug="example", title="Another", new_slug="chosen")["slug"] == "chosen"


def test_someone_elses_public_graph_can_be_read_and_copied_but_not_changed(app):
    app.edge.seed("theirs", fixture("example"), owner=OTHER, visibility="public")
    app.edge.seed("hidden", fixture("example"), owner=OTHER)
    listed = app.call("list_graphs")
    assert listed["mine"] == [] and [g["slug"] for g in listed["public_from_others"]] == ["theirs"]
    assert "@" not in json.dumps(listed)
    theirs = app.call("read_graph", slug="theirs")
    assert theirs["mine"] is False and len(theirs["notes"]) == 7
    with pytest.raises(ToolError, match="someone else's public graph"):
        app.call("add_notes", slug="theirs", version=theirs["version"], notes=[{"text": "mine now"}])
    for tool, more in (("rename_graph", {"title": "x"}), ("set_visibility", {"visibility": "private"}),
                       ("restore_version", {"restore_to": 1})):
        with pytest.raises(ToolError, match="no graph named 'theirs'"):
            app.call(tool, slug="theirs", version=theirs["version"], **more)
    for tool in ("trash_graph", "list_history"):
        with pytest.raises(ToolError, match="no graph named 'theirs'"):
            app.call(tool, slug="theirs")
    with pytest.raises(ToolError, match="no graph named 'hidden'"):
        app.call("read_graph", slug="hidden")
    assert app.call("copy_graph", slug="theirs", title="My copy")["mine"] is True
    assert app.edge.as_user("GET", "/graphs/theirs", who=OTHER)["version"]["n"] == 2  # only its own publish


def test_the_client_can_send_nothing_but_its_routes_and_no_slug_can_escape_its_path(app):
    new_graph(app)
    app.call("trash_graph", slug="plan")
    sent = len(app.edge.requests)
    for slug in ("../trash/plan", "plan/../../trash/plan", "trash/plan", "plan?x=1", "Plan", "", "plan/versions"):
        for tool, more in (("trash_graph", {}), ("restore_from_trash", {}), ("read_graph", {}), ("list_history", {}),
                           ("rename_graph", {"version": 1, "title": "t"}), ("copy_graph", {"title": "t"})):
            with pytest.raises(ToolError, match="is not a graph's slug"):
                app.call(tool, slug=slug, **more)
    assert len(app.edge.requests) == sent  # refused before anything was sent
    for method, path in (("DELETE", "/trash/plan"), ("DELETE", "/graphs/plan/versions/1"), ("DELETE", "/graphs"),
                         ("POST", "/export"), ("GET", "/export?all=1"), ("POST", "/trash/plan"), ("PATCH", "/graphs/plan")):
        with pytest.raises(client.ApiError, match="not something this server does"):
            client.call(app.api, method, path)
    assert len(app.edge.requests) == sent
    assert [g["slug"] for g in app.call("list_trash")["trash"]] == ["plan"]  # still there to restore


def test_group_boxes_are_drawn_changed_and_removed_and_each_call_is_one_version(app):
    new_graph(app)
    steps = app.call("add_notes", slug="plan", version=1, notes=[{"text": f"Step {i}", "key": f"s{i}"} for i in range(1, 9)],
                     links=[{"from_note": f"s{i}", "to_note": f"s{i + 1}"} for i in range(1, 8)])
    ids = [n["id"] for n in steps["added"]]
    made = app.call("add_group_boxes", slug="plan", version=2, boxes=[
        {"label": "Training", "notes": ids[:4]}, {"label": "Inference", "notes": ids[4:], "collapsed": True}])
    assert made["version"] == 3 and made["moved"] == [] and "notices" not in made
    training, inference = made["added"]
    assert training == {"id": training["id"], "label": "Training", "cluster": "training", "contains": ids[:4], "collapsed": False}
    assert inference["contains"] == ids[4:] and inference["collapsed"] is True

    graph = app.call("read_graph", slug="plan")
    assert graph["group_boxes"] == made["added"]
    assert {c["name"]: (c["made_by"], c["members"]) for c in graph["clusters"]} == {
        "training": ("group box", ids[:4]), "inference": ("group box", ids[4:])}
    assert all(n["clusters"] == (["training"] if n["id"] in ids[:4] else ["inference"]) for n in graph["notes"])

    changed = app.call("update_group_boxes", slug="plan", version=3, boxes=[
        {"id": inference["id"], "label": "Serving", "collapsed": False}, {"id": training["id"], "notes": ids[:3]}])
    assert changed["version"] == 4 and [b["id"] for b in changed["updated"]] == [inference["id"], training["id"]]
    assert changed["updated"][0] == {**inference, "label": "Serving", "cluster": "serving", "collapsed": False}
    assert changed["updated"][1]["contains"] == ids[:3]
    graph = app.call("read_graph", slug="plan")
    assert next(n for n in graph["notes"] if n["id"] == ids[3])["clusters"] == []

    removed = app.call("remove_group_boxes", slug="plan", version=4, box_ids=[training["id"]])
    assert removed["version"] == 5 and removed["removed"] == [training["id"]]
    graph = app.call("read_graph", slug="plan")
    assert [b["id"] for b in graph["group_boxes"]] == [inference["id"]] and len(graph["notes"]) == 8


def test_notes_boxes_and_links_are_styled_and_links_changed_in_place_each_call_one_version(app):
    new_graph(app)
    added = app.call("add_notes", slug="plan", version=1, notes=[{"text": "A", "key": "a"}, {"text": "B", "key": "b"}],
                     links=[{"from_note": "a", "to_note": "b", "label": "to", "direction": "backward", "color": "red",
                             "line": "dashed", "path": "right-angles", "from_side": "bottom", "to_side": "top"}])
    a, b = (n["id"] for n in added["added"])
    link = added["links_added"][0]
    assert link == {"id": link["id"], "from_note": a, "to_note": b, "direction": "backward", "label": "to", "color": "red",
                    "line": "dashed", "path": "right-angles", "from_side": "bottom", "to_side": "top"}
    boxed = app.call("add_group_boxes", slug="plan", version=2, boxes=[{"label": "Pair", "notes": [a, b]}])
    box = boxed["added"][0]["id"]

    styled = app.call("style_notes", slug="plan", version=3, notes=[
        {"id": a, "color": "#3A4BD8", "border": "dashed", "shape": "database", "text_align": "center"}, {"id": box, "color": "yellow"}])
    assert styled["version"] == 4
    assert styled["styled"] == [{"id": a, "color": "#3a4bd8", "border": "dashed", "shape": "database", "text_align": "center"},
                                {"id": box, "color": "yellow"}]
    stored = {n["id"]: n for n in app.edge.canvas("plan")["nodes"]}
    assert stored[a]["color"] == "#3a4bd8" and stored[a]["styleAttributes"] == {"border": "dashed", "shape": "database", "textAlign": "center"}
    assert stored[box]["color"] == "3" and "styleAttributes" not in stored[box]

    changed = app.call("update_links", slug="plan", version=4, links=[
        {"id": link["id"], "label": "", "direction": "both", "color": "none", "line": "solid", "path": "straight",
         "from_side": "right", "to_side": "left"}])
    assert changed["version"] == 5
    assert changed["updated"] == [{"id": link["id"], "from_note": a, "to_note": b, "direction": "both", "path": "straight",
                                   "from_side": "right", "to_side": "left"}]
    assert app.edge.canvas("plan")["edges"] == [{"id": link["id"], "fromNode": a, "fromSide": "right", "toNode": b, "toSide": "left",
                                                 "styleAttributes": {"pathfindingMethod": "direct"}, "fromEnd": "arrow"}]
    graph = app.call("read_graph", slug="plan")
    assert graph["links"] == changed["updated"] and graph["group_boxes"][0]["color"] == "yellow"
    assert graph["notes"][0] == {"id": a, "text": "A", "clusters": ["pair"], **styled["styled"][0]}

    with pytest.raises(ToolError, match="is a group box: it takes a colour"):
        app.call("style_notes", slug="plan", version=5, notes=[{"id": box, "shape": "pill"}])
    with pytest.raises(ToolError, match="'nope' names no link"):
        app.call("update_links", slug="plan", version=5, links=[{"id": "nope", "label": "x"}])
    with pytest.raises(ToolError, match="from_side and to_side go together"):
        app.call("update_links", slug="plan", version=5, links=[{"id": link["id"], "to_side": "top"}])
    with pytest.raises(ToolError):
        app.call("update_links", slug="plan", version=5, links=[{"id": link["id"], "from_side": "auto", "to_side": "auto"}])
    with pytest.raises(ToolError):
        app.call("link_notes", slug="plan", version=5, links=[{"from_note": a, "to_note": b, "from_side": "auto", "to_side": "top"}])
    assert app.call("read_graph", slug="plan")["version"] == 5


def test_notes_and_boxes_are_moved_and_resized_by_hand_and_boxes_nest(app):
    new_graph(app)
    steps = app.call("add_notes", slug="plan", version=1, notes=[{"text": f"Step {i}", "key": f"s{i}"} for i in range(1, 5)],
                     links=[{"from_note": f"s{i}", "to_note": f"s{i + 1}"} for i in range(1, 4)])
    ids = [n["id"] for n in steps["added"]]
    boxed = app.call("add_group_boxes", slug="plan", version=2, boxes=[
        {"label": "Index", "notes": ids[:2], "key": "index"}, {"label": "Query", "notes": ids[2:], "key": "query"},
        {"label": "RAG", "notes": ["index", "query"]}])
    index, query, rag = boxed["added"]
    assert rag["boxes"] == [index["id"], query["id"]] and rag["contains"] == ids and boxed["moved"] == []
    laid = app.call("read_graph", slug="plan", layout=True)
    where = {n["id"]: n for n in laid["notes"] + laid["group_boxes"]}
    assert {"x", "y", "width", "height"} <= set(where[ids[0]]) and "x" not in app.call("read_graph", slug="plan")["notes"][0]

    moved = app.call("move_notes", slug="plan", version=3, notes=[
        {"id": rag["id"], "x": where[rag["id"]]["x"] + 100, "y": where[rag["id"]]["y"] + 50},
        {"id": ids[3], "width": 300, "height": 160}])
    assert moved["version"] == 4 and "notices" not in moved
    placed = {p["id"]: p for p in moved["placed"]}
    assert set(placed) == {rag["id"], index["id"], query["id"], *ids}  # the box carried both boxes and every note
    assert placed[ids[0]]["x"] == where[ids[0]]["x"] + 100 and (placed[ids[3]]["width"], placed[ids[3]]["height"]) == (300, 160)
    out = app.call("move_notes", slug="plan", version=4, notes=[{"id": ids[0], "x": placed[ids[0]]["x"], "y": placed[rag["id"]]["y"] - 400}])
    assert out["notices"] == [f"'{ids[0]}' left a group box, so it is no longer in cluster '{c}'" for c in ("rag", "index")]  # in canvas order: RAG is written before the boxes it holds
    with pytest.raises(ToolError, match="greater than or equal to 40"):
        app.call("move_notes", slug="plan", version=5, notes=[{"id": ids[0], "width": 10}])
    assert app.call("read_graph", slug="plan")["version"] == 5


@pytest.mark.parametrize("tool, arguments", [
    ("move_notes", {"notes": [{"id": "n", "x": 5}]}),
    ("style_notes", {"notes": [{"id": "n", "color": "red"}]}),
    ("update_links", {"links": [{"id": "e", "label": "late"}]}),
    ("add_group_boxes", {"boxes": [{"label": "Late", "notes": ["n"]}]}),
    ("update_group_boxes", {"boxes": [{"id": "g", "label": "Late"}]}),
    ("remove_group_boxes", {"box_ids": ["g"]}),
])
def test_a_change_from_an_outdated_version_saves_nothing(app, tool, arguments):
    app.edge.seed("plan", {"nodes": [{"id": "g", "type": "group", "label": "G", "x": -20, "y": -56, "width": 300, "height": 196},
                                     {"id": "n", "type": "text", "text": "N", "x": 0, "y": 0, "width": 260, "height": 120}],
                           "edges": [{"id": "e", "fromNode": "n", "toNode": "g"}]})
    app.call("rename_graph", slug="plan", version=1, title="Moved on")
    before = app.edge.canvas("plan")
    with pytest.raises(ToolError, match="Nothing was saved: 'plan' has changed since version 1 and is now at version 2"):
        app.call(tool, slug="plan", version=1, **arguments)
    assert app.edge.canvas("plan") == before and app.call("read_graph", slug="plan")["version"] == 2


def test_every_link_to_a_graph_is_the_apps_page_with_its_slash(app):
    """``/graph/?g=<slug>`` is the app's page; the bare ``/graph?g=<slug>`` is answered with a 308
    redirect to it, which a link to the page itself saves."""
    made = new_graph(app)
    results = [made, app.call("read_graph", slug="plan"), *app.call("list_graphs")["mine"],
               app.call("add_notes", slug="plan", version=1, notes=[{"text": "x"}]), app.call("trash_graph", slug="plan")]
    results += app.call("list_trash")["trash"]
    assert {r["url"] for r in results} == {"https://graph.test/graph/?g=plan"}
    assert "graph?g=" not in json.dumps(results)
