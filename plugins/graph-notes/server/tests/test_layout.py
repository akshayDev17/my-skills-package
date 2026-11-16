"""Moving and resizing by hand (as dragging and resizing in the web app writes it), and
boxes inside boxes: a box drawn around boxes, and a box drawn inside another."""

from __future__ import annotations

import json
import random
from typing import Callable
import pytest

from graph_notes_mcp import canvas
from graph_notes_mcp.canvas import BOX_CLEAR, BOX_PAD, BOX_TOP, EditError
from support import REPO


def card(card_id: str, x: int, y: int, text: str = "note", **more) -> dict:
    return {"id": card_id, "type": "text", "text": text, "x": x, "y": y, "width": 260, "height": 120, **more}


def box(box_id: str, x: int, y: int, width: int, height: int, label: str | None = None, **more) -> dict:
    return {"id": box_id, "type": "group", "x": x, "y": y, "width": width, "height": height,
            **({"label": label} if label is not None else {}), **more}


def by_id(doc: dict, item_id: str) -> dict:
    return next(n for n in doc["nodes"] if n["id"] == item_id)


def near(a: dict, b: dict) -> bool:
    return not canvas._apart(a, b, BOX_CLEAR)


def roomy(inner: dict, outer: dict) -> bool:
    """``outer`` holds ``inner`` with a box's padding around it and its label bar above."""
    return (inner["x"] - outer["x"] >= BOX_PAD and inner["y"] - outer["y"] >= BOX_TOP
            and outer["x"] + outer["width"] - inner["x"] - inner["width"] >= BOX_PAD
            and outer["y"] + outer["height"] - inner["y"] - inner["height"] >= BOX_PAD)


def boxes_nest_or_stay_apart(doc: dict) -> None:
    groups = [n for n in doc["nodes"] if n["type"] == "group"]
    for a in groups:
        for b in groups:
            if a is not b and near(a, b):
                assert canvas.is_inside(a, b) or canvas.is_inside(b, a), f"{a['id']} and {b['id']} overlap without nesting"


# ── moving and resizing ────────────────────────────────────────────────────

def test_a_moved_card_takes_its_new_place_and_keeps_every_key_where_it_was():
    n = card("a", 0, 0, color="4", mystery={"k": 1})
    doc = {"nodes": [n, card("b", 600, 0)], "edges": [{"id": "e", "fromNode": "a", "toNode": "b"}]}
    after, placed, notices = canvas.move_cards(doc, [{"id": "a", "x": 100, "y": -40}])
    assert after["nodes"][0] == {**n, "x": 100, "y": -40} and list(after["nodes"][0]) == list(n)
    assert after["nodes"][1] is doc["nodes"][1] and after["edges"] is doc["edges"]
    assert placed == [{"id": "a", "x": 100, "y": -40, "width": 260, "height": 120}] and notices == []


def test_a_resize_writes_the_new_size_and_whole_pixel_positions_as_the_app_does():
    n = card("a", -12.5, 7.5)
    after, placed, _ = canvas.move_cards({"nodes": [n]}, [{"id": "a", "width": 400, "height": 200}])
    # placeNodes rounds every number it writes, as Math.round does: halves up
    assert after["nodes"][0] == {**n, "x": -12, "y": 8, "width": 400, "height": 200}
    assert canvas._js_round(2.5) == 3 and canvas._js_round(-2.5) == -2


def test_moving_a_box_carries_everything_inside_it_nested_boxes_too_and_resizing_carries_nothing():
    doc = {"nodes": [box("outer", 0, 0, 1000, 600, "Outer"), box("inner", 40, 84, 400, 300, "Inner"),
                     card("a", 80, 168), card("b", 600, 100), card("away", 2000, 0)]}
    after, placed, notices = canvas.move_cards(doc, [{"id": "outer", "x": 100, "y": 50}])
    assert [(by_id(after, i)["x"], by_id(after, i)["y"]) for i in ("outer", "inner", "a", "b")] == [
        (100, 50), (140, 134), (180, 218), (700, 150)]
    assert by_id(after, "away") is doc["nodes"][4]
    assert [p["id"] for p in placed] == ["outer", "inner", "a", "b"] and notices == []
    resized, placed, _ = canvas.move_cards(doc, [{"id": "outer", "width": 500, "height": 500}])
    assert [p["id"] for p in placed] == ["outer"] and by_id(resized, "b") is doc["nodes"][3]


def test_a_box_resized_from_its_top_left_carries_nothing_as_the_app_s_handle_writes_it():
    # live: the web app's top-left handle turned Core idea (-170,-144 340x244) into
    # -210,-174 380x274, and Alpha stayed at -130,-60
    alpha = card("alpha", -130, -60, "Alpha")
    doc = {"nodes": [box("core", -170, -144, 340, 244, "Core idea"), alpha]}
    after, placed, notices = canvas.move_cards(doc, [{"id": "core", "x": -210, "y": -174, "width": 380, "height": 274}])
    assert after["nodes"] == [box("core", -210, -174, 380, 274, "Core idea"), alpha] and after["nodes"][1] is alpha
    assert placed == [{"id": "core", "x": -210, "y": -174, "width": 380, "height": 274}] and notices == []
    # a move then a resize, as two entries: the move carries Alpha, the resize does not
    after, _, _ = canvas.move_cards(doc, [{"id": "core", "x": -130, "y": -104}, {"id": "core", "width": 400}])
    assert by_id(after, "core") == box("core", -130, -104, 400, 244, "Core idea")
    assert (by_id(after, "alpha")["x"], by_id(after, "alpha")["y"]) == (-90, -20)


def test_a_card_moved_into_or_out_of_a_box_joins_or_leaves_its_cluster_and_overlaps_are_said():
    doc = {"nodes": [box("g", 0, 0, 600, 400, "Training"), card("in", 40, 84), card("out", 1000, 0)]}
    after, _, notices = canvas.move_cards(doc, [{"id": "out", "x": 300, "y": 200}, {"id": "in", "x": 1000, "y": 400}])
    assert notices == ["'in' left a group box, so it is no longer in cluster 'training'",
                       "'out' is now inside a group box, so it is in cluster 'training'"]
    assert canvas.plain(after)["clusters"] == [{"name": "training", "made_by": "group box", "members": ["out"]}]
    _, _, notices = canvas.move_cards(doc, [{"id": "out", "x": 100, "y": 100}])
    assert "'out' now overlaps 'in'" in notices


@pytest.mark.parametrize("change, expected", [
    ({"id": "g", "width": 200}, ["'a' left a group box, so it is no longer in cluster 'outer'"]),
    ({"id": "g", "width": 1000}, ["'z' is now inside a group box, so it is in cluster 'outer'"]),
    ({"id": "g", "x": 600}, ["'z' is now inside a group box, so it is in cluster 'outer'"]),
])
def test_a_box_resized_or_moved_says_which_cards_it_took_in_or_let_go(change, expected):
    doc = {"nodes": [box("g", 0, 0, 500, 400, "Outer"), card("a", 220, 84), card("z", 700, 84)]}
    _, _, notices = canvas.move_cards(doc, [change])
    assert [n for n in notices if "overlaps" not in n] == expected


@pytest.mark.parametrize("change, message", [
    ({"id": "nope", "x": 1}, "'nope' names no note"),
    ({"id": "a", "width": 39}, "width of 'a' must be at least 40"),
    ({"id": "a", "height": 0}, "height of 'a' must be at least 40"),
])
def test_a_move_that_cannot_be_made_is_refused(change, message):
    with pytest.raises(EditError, match=message):
        canvas.move_cards({"nodes": [card("a", 0, 0)]}, [change])


def test_a_move_that_moves_nothing_returns_the_same_canvas():
    doc = {"nodes": [card("a", 0, 0)]}
    assert canvas.move_cards(doc, [{"id": "a"}, {"id": "a", "x": 0}]) == (doc, [], [])


def test_a_layout_read_gives_each_card_and_box_its_place_and_size():
    doc = {"nodes": [box("g", 0, 0, 600, 400, "G"), card("a", 40, 84), {"id": "w", "type": "link", "url": "u", "x": 900, "y": 0,
                                                                        "width": 400, "height": 240}]}
    shown = canvas.plain(doc, layout=True)
    assert shown["notes"][0] == {"id": "a", "text": "note", "clusters": ["g"], "x": 40, "y": 84, "width": 260, "height": 120}
    assert (shown["group_boxes"][0]["x"], shown["other_cards"][0]["width"]) == (0, 400)
    assert "x" not in canvas.plain(doc)["notes"][0]


# ── boxes inside boxes ─────────────────────────────────────────────────────

def chain(count: int) -> tuple[dict, list[str]]:
    notes = [{"text": f"Step {i + 1}", "key": f"s{i + 1}"} for i in range(count)]
    links = [{"from_note": f"s{i + 1}", "to_note": f"s{i + 2}"} for i in range(count - 1)]
    doc, added, _ = canvas.add_notes({}, notes, links)
    return doc, [a["id"] for a in added]


def test_a_box_drawn_around_boxes_holds_them_with_their_notes_and_nothing_moves():
    doc, ids = chain(8)
    after, added, moved, notices = canvas.add_group_boxes(doc, [
        {"label": "Index time", "notes": ids[:4], "key": "index"},
        {"label": "Query time", "notes": ids[4:], "key": "query"},
        {"label": "RAG", "notes": ["index", "query"]}])
    index, query, rag = added
    assert moved == [] and notices == []
    assert (index["key"], query["key"]) == ("index", "query") and "key" not in rag
    assert rag["boxes"] == [index["id"], query["id"]] and rag["contains"] == ids
    for inner in (index, query):
        assert roomy(by_id(after, inner["id"]), by_id(after, rag["id"]))
    boxes_nest_or_stay_apart(after)
    clusters = {c["name"]: c["members"] for c in canvas.plain(after)["clusters"]}
    assert clusters == {"index-time": ids[:4], "query-time": ids[4:], "rag": ids}


def test_a_box_drawn_inside_another_stays_inside_it_and_the_outer_box_grows_to_keep_room():
    doc, ids = chain(4)
    outer, added, _, _ = canvas.add_group_boxes(doc, [{"label": "Pipeline", "notes": ids}])
    big = added[0]["id"]
    after, added, moved, notices = canvas.add_group_boxes(outer, [{"label": "First half", "notes": ids[:2]}])
    inner = by_id(after, added[0]["id"])
    assert moved == [] and notices == [f"group box '{big}' grew to keep room around the box inside it"]
    assert canvas.is_inside(inner, by_id(after, big)) and roomy(inner, by_id(after, big))
    assert list(by_id(after, big)) == list(by_id(outer, big))  # only its geometry changed
    for n in ids:
        assert canvas.is_inside(by_id(after, n), by_id(after, big))
    assert {c["name"]: c["members"] for c in canvas.plain(after)["clusters"]} == {"first-half": ids[:2], "pipeline": ids}
    boxes_nest_or_stay_apart(after)


@pytest.mark.parametrize("named", [["inner"], ["inner", "a"]])
def test_a_box_drawn_around_a_single_box_wraps_it_and_that_box_stays_as_it_was(named):
    doc = {"nodes": [box("inner", 0, 0, 340, 244, "Inner"), card("a", 40, 84)]}
    after, added, moved, notices = canvas.add_group_boxes(doc, [{"label": "Outer", "notes": named}])
    outer = by_id(after, added[0]["id"])
    assert moved == [] and notices == [] and by_id(after, "inner") == doc["nodes"][0]
    assert added[0]["boxes"] == ["inner"] and roomy(by_id(after, "inner"), outer)
    assert {c["name"]: c["members"] for c in canvas.plain(after)["clusters"]} == {"inner": ["a"], "outer": ["a"]}


def test_a_box_drawn_around_a_single_box_inside_another_nests_between_them():
    doc = {"nodes": [box("big", -200, -200, 1200, 1000, "Big"), box("inner", 0, 0, 340, 244, "Inner"), card("a", 40, 84)]}
    after, added, _, _ = canvas.add_group_boxes(doc, [{"label": "Middle", "notes": ["inner"]}])
    middle = by_id(after, added[0]["id"])
    assert by_id(after, "inner") == doc["nodes"][1]
    assert roomy(by_id(after, "inner"), middle) and roomy(middle, by_id(after, "big"))
    assert added[0]["boxes"] == ["inner"] and set(canvas._box_report(after["nodes"], by_id(after, "big"))["boxes"]) == {
        "inner", middle["id"]}


def test_a_note_given_a_nested_box_cluster_goes_inside_that_box_when_there_is_room():
    doc = {"nodes": [box("outer", 0, 0, 1500, 1100, "Outer"), box("inner", 100, 100, 1200, 800, "Inner"), card("a", 140, 184)]}
    after, added, _ = canvas.add_notes(doc, [{"text": "New", "clusters": ["inner"]}], [])
    note = by_id(after, added[0]["id"])
    assert canvas.is_inside(note, by_id(after, "inner"))
    assert {c["name"]: c["members"] for c in canvas.plain(after)["clusters"]} == {
        "inner": ["a", note["id"]], "outer": ["a", note["id"]]}


def test_a_note_given_a_cluster_never_lands_in_a_box_around_it_alone():
    doc = {"nodes": [box("outer", 0, 0, 1500, 1100, "Outer"), box("inner", 100, 100, 300, 244, "Inner"), card("a", 140, 184)]}
    after, added, _ = canvas.add_notes(doc, [{"text": "New", "clusters": ["inner"]}], [])
    note = by_id(after, added[0]["id"])
    assert not canvas.is_inside(note, by_id(after, "outer")) or canvas.is_inside(note, by_id(after, "inner"))


def test_a_box_that_grows_moves_a_card_it_would_take_in_out_of_its_way():
    doc = {"nodes": [box("outer", 0, 0, 340, 244, "Outer"), card("a", 40, 84), card("close", 0, 290)]}
    after, added, moved, notices = canvas.add_group_boxes(doc, [{"label": "Inner", "notes": ["a"]}])
    assert moved == ["close"] and roomy(by_id(after, added[0]["id"]), by_id(after, "outer"))
    assert not near(by_id(after, "close"), by_id(after, "outer"))


def test_a_box_in_the_way_of_one_that_grows_moves_with_everything_inside_it():
    doc = {"nodes": [box("outer", 0, 0, 340, 244, "Outer"), card("a", 40, 84), box("below", 0, 270, 340, 244, "Below"),
                     card("b", 40, 354)]}
    after, added, moved, _ = canvas.add_group_boxes(doc, [{"label": "Inner", "notes": ["a"]}])
    below, b = by_id(after, "below"), by_id(after, "b")
    assert moved == ["b"] and (b["x"] - below["x"], b["y"] - below["y"]) == (40, 84)  # carried with its box
    assert roomy(by_id(after, added[0]["id"]), by_id(after, "outer"))
    boxes_nest_or_stay_apart(after)
    assert canvas.plain(after)["group_boxes"][1]["contains"] == ["b"]


def test_a_box_given_a_box_among_its_notes_on_update_wraps_it():
    doc, ids = chain(4)
    made, added, _, _ = canvas.add_group_boxes(doc, [{"label": "A", "notes": ids[:2]}, {"label": "B", "notes": ids[2:]}])
    a, b = (x["id"] for x in added)
    after, updated, moved, _ = canvas.update_group_boxes(made, [{"id": b, "notes": [ids[2], ids[3], a]}])
    assert updated[0]["boxes"] == [a] and set(updated[0]["contains"]) == set(ids)
    boxes_nest_or_stay_apart(after)


@pytest.mark.parametrize("edit, message", [
    (lambda d: canvas.add_group_boxes(d, [{"label": "X", "notes": ["a"], "key": "k"}, {"label": "Y", "notes": ["b"], "key": "k"}]),
     "key 'k' must be unique"),
    (lambda d: canvas.add_group_boxes(d, [{"label": "X", "notes": ["a"], "key": "a"}]), "key 'a' must be unique"),
    (lambda d: canvas.add_group_boxes(d, [{"label": "X", "notes": ["later"]}, {"label": "Y", "notes": ["b"], "key": "later"}]),
     "'later' names no note"),
    (lambda d: canvas.add_group_boxes(d, [{"label": "X", "notes": ["a"]}, {"label": "Y", "notes": ["a", "b"]}]),
     "given to two boxes"),
])
def test_box_keys_are_checked(edit, message):
    with pytest.raises(EditError, match=message):
        edit({"nodes": [card("a", 0, 0), card("b", 600, 0)]})


def test_a_box_renamed_inside_another_widens_and_the_outer_box_grows_with_it():
    doc = {"nodes": [box("outer", 0, 0, 420, 328, "Outer"), box("in", 40, 84, 340, 204, "X"), card("a", 80, 128)]}
    long = "A label far longer than this little box is wide"
    after, _, moved, notices = canvas.update_group_boxes(doc, [{"id": "in", "label": long}])
    assert by_id(after, "in")["width"] == canvas._label_width(long) and moved == []
    assert roomy(by_id(after, "in"), by_id(after, "outer")) and notices == ["group box 'outer' grew to keep room around the box inside it"]


def groups_in_order(doc: dict) -> list[str]:
    return [n["id"] for n in doc["nodes"] if n["type"] == "group"]


def test_a_box_drawn_around_boxes_is_written_before_them_so_the_app_draws_them_on_top():
    # the app draws every box at the same depth, so a box later in the canvas covers one before it
    doc = {"nodes": [card("x", 900, 0), box("inner", 0, 0, 340, 244, "Inner"), card("a", 40, 84)]}
    after, added, _, _ = canvas.add_group_boxes(doc, [{"label": "Outer", "notes": ["inner"]}])
    assert groups_in_order(after) == [added[0]["id"], "inner"]
    chained, ids = chain(8)
    after, added, _, _ = canvas.add_group_boxes(chained, [
        {"label": "Index time", "notes": ids[:4], "key": "index"}, {"label": "Query time", "notes": ids[4:], "key": "query"},
        {"label": "RAG", "notes": ["index", "query"]}, {"label": "First two", "notes": ids[:2]}])
    index, query, rag, first = (a["id"] for a in added)
    assert groups_in_order(after) == [rag, index, query, first]
    doc = {"nodes": [box("inner", 0, 0, 340, 244, "Inner"), card("a", 40, 84), box("other", 860, -84, 340, 244, "Other"),
                     card("x", 900, 0)]}
    refit, updated, _, _ = canvas.update_group_boxes(doc, [{"id": "other", "notes": ["inner", "x"]}])
    assert updated[0]["boxes"] == ["inner"] and groups_in_order(refit) == ["other", "inner"]


def test_a_box_renamed_inside_nested_boxes_keeps_a_card_beside_it_in_its_boxes_and_they_grow():
    doc = {"nodes": [box("outer", 0, 0, 840, 368, "Outer"), box("inner", 40, 84, 760, 244, "Inner"),
                     box("core", 80, 168, 340, 120, "Core idea"), card("beta", 460, 168)]}
    long = "A label far longer than this little box is wide, really"
    after, _, moved, notices = canvas.update_group_boxes(doc, [{"id": "core", "label": long}])
    core, inner, outer, beta = (by_id(after, i) for i in ("core", "inner", "outer", "beta"))
    assert core["width"] == canvas._label_width(long) and moved == ["beta"]
    assert canvas.is_inside(beta, inner) and canvas.is_inside(beta, outer) and not near(beta, core)
    assert roomy(core, inner) and roomy(inner, outer)
    assert {c["name"]: c["members"] for c in canvas.plain(after)["clusters"] if c["members"]} == {
        "inner": ["beta"], "outer": ["beta"]}
    assert notices == ["group box 'inner' grew to keep room around the box inside it",
                       "group box 'outer' grew to keep room around the box inside it"]
    boxes_nest_or_stay_apart(after)


def test_a_box_moved_out_of_the_way_is_reported():
    doc = {"nodes": [box("outer", 0, 0, 340, 244, "Outer"), card("a", 40, 84), box("empty", 0, 270, 480, 320, "Group")]}
    after, added, moved, notices = canvas.add_group_boxes(doc, [{"label": "Inner", "notes": ["a"]}])
    assert by_id(after, "empty") != by_id(doc, "empty") and moved == []
    assert "group box 'empty' was moved out of the way, with everything inside it" in notices
    boxes_nest_or_stay_apart(after)


@pytest.mark.parametrize("seed", range(30))
def test_whatever_the_layout_boxes_nest_or_stay_apart_and_hold_what_they_were_given(seed):
    rng = random.Random(seed)
    nodes = [card(f"n{i}", rng.randrange(-900, 900, 20), rng.randrange(-700, 700, 20)) for i in range(rng.randrange(8, 16))]
    doc = {"nodes": nodes, "edges": []}
    ids = [n["id"] for n in nodes]
    rng.shuffle(ids)
    specs = [{"label": "One", "notes": ids[:2], "key": "one"}, {"label": "Two", "notes": ids[2:4], "key": "two"},
             {"label": "Both", "notes": ["one", "two", ids[4]]}, {"label": "Inside one", "notes": [ids[0]]}]
    after, added, _, _ = canvas.add_group_boxes(doc, specs)
    boxes_nest_or_stay_apart(after)
    one, two, both, inside = (by_id(after, a["id"]) for a in added)
    assert set(added[2]["contains"]) == set(ids[:5]) and added[2]["boxes"] == [one["id"], two["id"], inside["id"]]
    assert set(added[0]["contains"]) == set(ids[:2]) and canvas.is_inside(inside, one)
    for n in after["nodes"]:  # every other card stays out of every box
        if n["type"] == "text" and n["id"] not in ids[:5]:
            assert not any(near(n, g) for g in (one, two, both, inside)), n["id"]


# ── against boxes, moves and resizes made by hand in the web app ───────────

HAND_MADE = REPO / "mcp-server" / "tests" / "fixtures" / "web-app-hand-made.canvas"
LOOKS = REPO / "mcp-server" / "tests" / "fixtures" / "web-app-looks.canvas"


def hand_made_boxes_and_moves(monkeypatch, page: Callable[[dict, list], dict] = lambda doc, ids: doc) -> tuple[dict, dict]:
    """The rest of what was made by hand in the web app on the graph of web-app-looks.canvas,
    through the server, in the same order: "+ Group", labelled Pair and coloured cyan (and
    "Starts collapsed" ticked and unticked); Delta dragged into it; the box dragged, carrying
    Delta; Delta resized; (``page``: the web-page card added by hand, its id put first in
    ``ids``; test_cards.py replays it); the box resized; another
    "+ Group", labelled Inner, which landed inside Pair, and resized there. Returns
    (what the server made, what the web app saved)."""
    hand = json.loads(HAND_MADE.read_text(encoding="utf-8"))
    pair, inner = (n for n in hand["nodes"] if n["type"] == "group")
    ids = [pair["id"], inner["id"]]  # the ids the app drew, in the order the server draws them
    monkeypatch.setattr(canvas.secrets, "token_hex", lambda n: ids.pop(0))
    doc = json.loads(LOOKS.read_text(encoding="utf-8"))
    doc, _, _, _ = canvas.add_group_boxes(doc, [{"label": "Pair", "notes": []}])
    doc, _ = canvas.style_notes(doc, [{"id": pair["id"], "color": "cyan"}])
    doc, _, _, _ = canvas.update_group_boxes(doc, [{"id": pair["id"], "collapsed": True}])
    doc, _, _, _ = canvas.update_group_boxes(doc, [{"id": pair["id"], "collapsed": False}])
    doc, _, _ = canvas.move_cards(doc, [{"id": pair["id"], "x": 100, "y": -370}])  # where the app's "+ Group" put it
    doc, _, notices = canvas.move_cards(doc, [{"id": "d4", "x": 172, "y": -273}])
    assert notices == ["'d4' is now inside a group box, so it is in cluster 'pair'"]
    doc, placed, _ = canvas.move_cards(doc, [{"id": pair["id"], "x": 12, "y": -869}])
    assert [p["id"] for p in placed] == ["d4", pair["id"]]  # the box carried Delta
    doc, _, _ = canvas.move_cards(doc, [{"id": "d4", "width": 360, "height": 180}])
    doc = page(doc, ids)
    doc, _, _ = canvas.move_cards(doc, [{"id": pair["id"], "width": pair["width"], "height": pair["height"]}])
    doc, added, _, _ = canvas.add_group_boxes(doc, [{"label": "Inner", "notes": []}])
    doc, _, _ = canvas.move_cards(doc, [{"id": inner["id"], "x": inner["x"], "y": inner["y"]}])
    doc, _, _ = canvas.move_cards(doc, [{"id": inner["id"], "width": inner["width"], "height": inner["height"]}])
    return doc, hand


def as_stored(doc: dict, skip: str = "") -> list[str]:
    return [json.dumps(i, ensure_ascii=False) for i in doc["nodes"] + doc["edges"] if i.get("type") != skip]  # key order counts


def test_boxes_moves_and_resizes_are_written_as_the_web_app_wrote_them_by_hand(monkeypatch):
    doc, hand = hand_made_boxes_and_moves(monkeypatch)
    assert as_stored(doc) == as_stored(hand, skip="link")  # the web-page card added by hand is not drawn here
    pair, inner = (n for n in doc["nodes"] if n["type"] == "group")
    assert canvas.is_inside(inner, pair) and canvas._box_report(doc["nodes"], pair)["boxes"] == [inner["id"]]
