"""An edit made through the tools changes one thing in the stored canvas and nothing
else: positions, sizes, colours, group boxes, Advanced Canvas attributes and keys this
server does not know all come back from the app exactly as they went in.

"Exactly" is checked on the app's own stored form (``store.dumps``: compact JSON, key
order kept), item by item, in order.
"""

from __future__ import annotations

import copy

import pytest

from support import App, fixture  # first: it puts backend/ on the path

from graph_notes import store  # noqa: E402

# What no editor here knows about: it must survive untouched at every level.
ODD = {
    "nodes": [
        {"id": "n1", "type": "text", "text": "One\n#alpha", "x": -12.5, "y": 7, "width": 260, "height": 120,
         "color": "#ff8800", "styleAttributes": {"shape": "pill", "border": "dashed", "textAlign": "center"},
         "zIndex": 3, "futureKey": {"nested": [1, 2.0, None, "é"]}},
        {"id": "n2", "type": "text", "text": "Two", "x": 400, "y": 0, "width": 300, "height": 90, "isStartNode": True},
        {"id": "g1", "type": "group", "x": -100, "y": -100, "width": 900, "height": 400, "label": "Box",
         "collapsed": True, "background": "bg.png", "backgroundStyle": "cover"},
        {"id": "w1", "type": "link", "url": "https://example.com", "x": 1200, "y": 0, "width": 400, "height": 240},
        {"id": "f1", "type": "file", "file": "notes/a.md", "subpath": "#heading", "x": 1200, "y": 400, "width": 300, "height": 200},
    ],
    "edges": [
        {"id": "e1", "fromNode": "n1", "fromSide": "right", "toNode": "n2", "toSide": "left", "label": "to",
         "color": "4", "styleAttributes": {"path": "long-dashed", "arrow": "triangle", "pathfindingMethod": "a-star"},
         "fromEnd": "arrow", "toEnd": "none", "futureEdgeKey": 1},
        {"id": "e2", "fromNode": "n2", "toNode": "g1"},
    ],
    "metadata": {"version": "1.0-1.0", "frontmatter": {"tags": ["x"]}, "startNode": "n2"},
    "futureTopLevel": {"kept": True},
}

CANVASES = {"example": fixture("example"), "kmeans-in-production": fixture("kmeans-in-production"), "odd": ODD}


def stored(canvas: dict, *without: str) -> dict:
    """The canvas as rows of the app's stored JSON, minus the items named."""
    return {
        "top": store.dumps({k: v for k, v in canvas.items() if k not in ("nodes", "edges")}),
        "top_keys": list(canvas),
        "nodes": [store.dumps(n) for n in canvas.get("nodes", []) if n["id"] not in without],
        "edges": [store.dumps(e) for e in canvas.get("edges", []) if e["id"] not in without],
    }


def first_two_notes(canvas: dict) -> tuple[dict, dict]:
    notes = [n for n in canvas["nodes"] if n["type"] == "text"]
    return notes[0], notes[1]


@pytest.fixture(params=sorted(CANVASES))
def seeded(request) -> tuple[App, str, dict]:
    app = App()
    app.edge.seed(request.param, copy.deepcopy(CANVASES[request.param]))
    assert app.edge.canvas(request.param) == CANVASES[request.param]
    return app, request.param, CANVASES[request.param]


def test_reading_changes_nothing(seeded):
    app, slug, before = seeded
    app.call("read_graph", slug=slug)
    app.call("list_history", slug=slug)
    assert stored(app.edge.canvas(slug)) == stored(before)
    assert app.call("read_graph", slug=slug)["version"] == 1


def test_changing_one_notes_text_changes_only_that_text(seeded):
    app, slug, before = seeded
    note, _ = first_two_notes(before)
    app.call("update_notes", slug=slug, version=1, notes=[{"id": note["id"], "text": "Rewritten by a model"}])
    after = app.edge.canvas(slug)
    assert stored(after, note["id"]) == stored(before, note["id"])
    changed = next(n for n in after["nodes"] if n["id"] == note["id"])
    assert [n["id"] for n in after["nodes"]] == [n["id"] for n in before["nodes"]]  # still in its place
    assert list(changed) == list(note)
    assert store.dumps({k: v for k, v in changed.items() if k != "text"}) == store.dumps({k: v for k, v in note.items() if k != "text"})
    assert changed["text"].split("\n")[0] == "Rewritten by a model"


def test_changing_one_notes_clusters_changes_only_its_last_line(seeded):
    app, slug, before = seeded
    note, _ = first_two_notes(before)
    app.call("update_notes", slug=slug, version=1, notes=[{"id": note["id"], "clusters": ["Round Trip"]}])
    after = app.edge.canvas(slug)
    assert stored(after, note["id"]) == stored(before, note["id"])
    changed = next(n for n in after["nodes"] if n["id"] == note["id"])
    body = note["text"].split("\n#")[0] if "\n#" in note["text"] else note["text"]
    assert changed == {**note, "text": f"{body}\n#round-trip"}


def test_adding_a_note_and_a_link_appends_them_and_touches_nothing_else(seeded):
    app, slug, before = seeded
    one, two = first_two_notes(before)
    added = app.call("add_notes", slug=slug, version=1, notes=[{"text": "A new note", "near": one["id"]}])
    new_note = added["added"][0]["id"]
    linked = app.call("link_notes", slug=slug, version=2, links=[{"from_note": new_note, "to_note": two["id"], "label": "new"}])
    new_link = linked["added"][0]["id"]
    after = app.edge.canvas(slug)
    assert stored(after, new_note, new_link) == stored(before)
    assert after["nodes"][-1]["id"] == new_note and after["edges"][-1]["id"] == new_link
    assert after["edges"][-1] == {"id": new_link, "fromNode": new_note, "toNode": two["id"], "label": "new"}


def test_removing_a_note_takes_only_it_and_its_links(seeded):
    app, slug, before = seeded
    note, _ = first_two_notes(before)
    removed = app.call("remove_notes", slug=slug, version=1, note_ids=[note["id"]])
    touching = [e["id"] for e in before["edges"] if note["id"] in (e["fromNode"], e["toNode"])]
    assert sorted(removed["links_removed"]) == sorted(touching)
    assert stored(app.edge.canvas(slug)) == stored(before, note["id"], *touching)


def test_removing_a_link_takes_only_that_link(seeded):
    app, slug, before = seeded
    link = before["edges"][0]
    app.call("unlink_notes", slug=slug, version=1, link_ids=[link["id"]])
    assert stored(app.edge.canvas(slug)) == stored(before, link["id"])


def test_renaming_publishing_trashing_and_restoring_leave_the_canvas_alone(seeded):
    app, slug, before = seeded
    app.call("rename_graph", slug=slug, version=1, title="Renamed")
    app.call("set_visibility", slug=slug, version=2, visibility="public")
    app.call("set_visibility", slug=slug, version=3, visibility="private")
    app.call("trash_graph", slug=slug)
    app.call("restore_from_trash", slug=slug)
    assert stored(app.edge.canvas(slug)) == stored(before)


def test_an_edit_then_restoring_the_version_before_it_gives_the_original_back(seeded):
    app, slug, before = seeded
    note, _ = first_two_notes(before)
    app.call("update_notes", slug=slug, version=1, notes=[{"id": note["id"], "text": "changed"}])
    app.call("add_notes", slug=slug, version=2, notes=[{"text": "extra"}])
    app.call("restore_version", slug=slug, version=3, restore_to=1)
    assert stored(app.edge.canvas(slug)) == stored(before)


def test_a_copy_holds_the_same_canvas(seeded):
    app, slug, before = seeded
    app.call("copy_graph", slug=slug, title="The copy", new_slug="the-copy")
    assert stored(app.edge.canvas("the-copy")) == stored(before)


def test_drawing_a_box_appends_it_and_changes_only_the_places_of_the_cards_it_says_moved(seeded):
    app, slug, before = seeded
    one, two = first_two_notes(before)
    added = app.call("add_group_boxes", slug=slug, version=1, boxes=[{"label": "Round trip", "notes": [one["id"], two["id"]]}])
    box, moved = added["added"][0]["id"], added["moved"]
    after = app.edge.canvas(slug)
    assert after["nodes"][-1]["id"] == box and added["added"][0]["contains"] == [one["id"], two["id"]]
    was = {n["id"]: n for n in before["nodes"]}
    # a box the new one is drawn inside may grow to keep room around it: only its geometry changes
    grown = [n["id"] for n in after["nodes"] if n["type"] == "group" and n["id"] in was and n != was[n["id"]]]
    assert all(f"group box '{g}' grew" in " ".join(added.get("notices", [])) for g in grown)
    assert stored(after, box, *moved, *grown) == stored(before, *moved, *grown)
    for n in after["nodes"]:
        if n["id"] in moved or n["id"] in grown:
            assert list(n) == list(was[n["id"]])
            assert store.dumps({k: v for k, v in n.items() if k not in ("x", "y", "width", "height")}) == store.dumps(
                {k: v for k, v in was[n["id"]].items() if k not in ("x", "y", "width", "height")})


@pytest.mark.parametrize("slug", ["example", "odd"])  # the canvases with a group box the person drew
def test_renaming_collapsing_and_removing_a_drawn_box_change_only_that_box(slug):
    app = App()
    before = copy.deepcopy(CANVASES[slug])
    app.edge.seed(slug, copy.deepcopy(before))
    box = next(n for n in before["nodes"] if n["type"] == "group")
    app.call("update_group_boxes", slug=slug, version=1, boxes=[{"id": box["id"], "label": "Renamed box"}])
    after = app.edge.canvas(slug)
    assert stored(after, box["id"]) == stored(before, box["id"])
    assert store.dumps(next(n for n in after["nodes"] if n["id"] == box["id"])) == store.dumps({**box, "label": "Renamed box"})

    flipped = not box.get("collapsed")
    app.call("update_group_boxes", slug=slug, version=2, boxes=[{"id": box["id"], "collapsed": flipped}])
    changed = next(n for n in app.edge.canvas(slug)["nodes"] if n["id"] == box["id"])
    expected = {k: v for k, v in {**box, "label": "Renamed box"}.items() if k != "collapsed"} | ({"collapsed": True} if flipped else {})
    assert store.dumps(changed) == store.dumps(expected)

    removed = app.call("remove_group_boxes", slug=slug, version=3, box_ids=[box["id"]])
    attached = [e["id"] for e in before["edges"] if box["id"] in (e["fromNode"], e["toNode"])]
    assert removed.get("links_removed", []) == attached
    assert stored(app.edge.canvas(slug)) == stored(before, box["id"], *attached)
