"""Group boxes: drawn around the notes named, so that each box holds exactly them and no
two boxes overlap, moving as few cards as it can; renamed, refitted, collapsed and
removed; and written exactly as the web app writes a group node."""

from __future__ import annotations

import copy
import json
import random
import re

import pytest

from graph_notes_mcp import canvas
from graph_notes_mcp.canvas import BOX_CLEAR, BOX_PAD, BOX_TOP, EditError
from support import REPO

FIXTURE = REPO / "mcp-server" / "tests" / "fixtures" / "server-boxes.canvas"


def card(card_id: str, x: int, y: int, text: str = "note", **more) -> dict:
    return {"id": card_id, "type": "text", "text": text, "x": x, "y": y, "width": 260, "height": 120, **more}


def box(box_id: str, x: int, y: int, width: int, height: int, label: str | None = None, **more) -> dict:
    return {"id": box_id, "type": "group", "x": x, "y": y, "width": width, "height": height,
            **({"label": label} if label is not None else {}), **more}


def by_id(doc: dict, item_id: str) -> dict:
    return next(n for n in doc["nodes"] if n["id"] == item_id)


def near(a: dict, b: dict) -> bool:
    """``a`` comes within ``BOX_CLEAR`` of ``b``: what counts as being in a box."""
    return not (a["x"] >= b["x"] + b["width"] + BOX_CLEAR or a["x"] + a["width"] + BOX_CLEAR <= b["x"]
                or a["y"] >= b["y"] + b["height"] + BOX_CLEAR or a["y"] + a["height"] + BOX_CLEAR <= b["y"])


def holds_only(doc: dict, box_id: str, members: set[str]) -> None:
    """The box holds every member wholly, no other card comes near it, and no other box does."""
    g = by_id(doc, box_id)
    for n in doc["nodes"]:
        if n["id"] == box_id:
            continue
        if n["id"] in members:
            assert canvas.is_inside(n, g), f"{n['id']} is not inside {box_id}"
        else:
            assert not near(n, g), f"{n['id']} is in or touching {box_id}"


def chain(count: int, prefix: str = "s") -> dict:
    """``count`` notes linked one after the other, laid out as add_notes lays them out."""
    notes = [{"text": f"Step {i + 1}", "key": f"{prefix}{i + 1}"} for i in range(count)]
    links = [{"from_note": f"{prefix}{i + 1}", "to_note": f"{prefix}{i + 2}"} for i in range(count - 1)]
    doc, added, _ = canvas.add_notes({}, notes, links)
    return doc, [a["id"] for a in added]


# ── drawing a box ──────────────────────────────────────────────────────────

def test_a_box_around_notes_that_stand_apart_fits_them_and_moves_nothing():
    doc, ids = chain(8)
    after, added, moved, notices = canvas.add_group_boxes(doc, [{"label": "Training", "notes": ids[:4]}])
    assert moved == [] and notices == []
    g = after["nodes"][-1]
    cards = [by_id(doc, i) for i in ids[:4]]
    assert g["x"] == min(c["x"] for c in cards) - BOX_PAD and g["y"] == min(c["y"] for c in cards) - BOX_TOP
    assert g["x"] + g["width"] == max(c["x"] + c["width"] for c in cards) + BOX_PAD
    assert g["y"] + g["height"] == max(c["y"] + c["height"] for c in cards) + BOX_PAD
    holds_only(after, g["id"], set(ids[:4]))
    assert added == [{"id": g["id"], "label": "Training", "cluster": "training", "contains": ids[:4], "collapsed": False}]
    assert after["nodes"][:-1] == doc["nodes"] and all(a is b for a, b in zip(after["nodes"], doc["nodes"]))
    assert after["edges"] is doc["edges"]


def test_two_boxes_in_one_call_split_a_pipeline_without_overlapping():
    doc, ids = chain(8)
    after, added, moved, _ = canvas.add_group_boxes(doc, [{"label": "Training", "notes": ids[:4]},
                                                          {"label": "Inference", "notes": ids[4:]}])
    assert moved == []
    first, second = (a["id"] for a in added)
    holds_only(after, first, set(ids[:4]))
    holds_only(after, second, set(ids[4:]))
    plain = canvas.plain(after)
    assert {c["name"]: c["members"] for c in plain["clusters"]} == {"training": ids[:4], "inference": ids[4:]}


def test_a_new_box_is_the_group_node_the_web_app_writes():
    doc, ids = chain(2)
    after, _, _, _ = canvas.add_group_boxes(doc, [{"label": "  Open questions ", "notes": ids}])
    g = after["nodes"][-1]
    # frontend/lib/canvas.ts newCard('group', id, at); frontend/lib/canvas.test.ts checks the shared fixture against it
    assert list(g) == ["type", "label", "width", "height", "id", "x", "y"]
    assert g["type"] == "group" and g["label"] == "Open questions" and re.fullmatch(r"[0-9a-f]{16}", g["id"])
    assert all(isinstance(g[k], int) for k in ("x", "y", "width", "height"))
    assert "color" not in g and "collapsed" not in g


def test_a_box_that_starts_collapsed_carries_collapsed_true_last_as_the_app_writes_it():
    doc, ids = chain(2)
    after, added, _, _ = canvas.add_group_boxes(doc, [{"label": "Later", "notes": ids, "collapsed": True}])
    g = after["nodes"][-1]
    assert list(g)[-1] == "collapsed" and g["collapsed"] is True and added[0]["collapsed"] is True
    assert canvas.plain(after)["group_boxes"][0]["collapsed"] is True


def test_a_long_label_widens_the_box_so_the_label_shows():
    doc = {"nodes": [card("a", 0, 0)]}
    label = "A label much longer than one card is wide"
    after, _, _, _ = canvas.add_group_boxes(doc, [{"label": label, "notes": ["a"]}])
    assert after["nodes"][-1]["width"] >= 40 + 9 * len(label)


# ── moving what is in the way ──────────────────────────────────────────────

def test_a_card_between_the_members_moves_out_and_the_members_stay():
    doc = {"nodes": [card("a", 0, 0), card("b", 600, 0), card("c", 300, 40)], "edges": []}
    after, added, moved, notices = canvas.add_group_boxes(doc, [{"label": "Pair", "notes": ["a", "b"]}])
    assert moved == ["c"] and notices == []
    assert by_id(after, "a") is doc["nodes"][0] and by_id(after, "b") is doc["nodes"][1]
    holds_only(after, added[0]["id"], {"a", "b"})
    c = by_id(after, "c")
    assert {k: v for k, v in c.items() if k not in ("x", "y")} == {k: v for k, v in doc["nodes"][2].items() if k not in ("x", "y")}


def test_a_note_in_another_box_moves_out_of_it_with_the_others_and_says_so():
    doc = {"nodes": [box("old", -40, -80, 340, 260, "Old"), card("a", 0, 0), card("b", 400, 0), card("x", 0, 600)], "edges": []}
    after, added, moved, notices = canvas.add_group_boxes(doc, [{"label": "New", "notes": ["a", "b"]}])
    assert sorted(moved) == ["a", "b"]
    assert notices == ["'a' was moved out of group box 'old', so it is no longer in cluster 'old'"]
    holds_only(after, added[0]["id"], {"a", "b"})
    a, b = by_id(after, "a"), by_id(after, "b")
    assert (b["x"] - a["x"], b["y"] - a["y"]) == (400, 0)  # moved together, as they were arranged
    assert by_id(after, "old") is doc["nodes"][0] and by_id(after, "x") is doc["nodes"][3]
    assert not near(by_id(after, "old"), by_id(after, added[0]["id"]))


def test_members_spread_among_other_cards_are_laid_out_together_along_their_links():
    nodes = [card(f"n{i}", (i % 4) * 300, (i // 4) * 160) for i in range(12)]
    members = ["n0", "n5", "n10"]
    doc = {"nodes": nodes, "edges": [{"id": "e1", "fromNode": "n0", "toNode": "n5"}, {"id": "e2", "fromNode": "n5", "toNode": "n10"}]}
    after, added, moved, _ = canvas.add_group_boxes(doc, [{"label": "Path", "notes": members}])
    assert set(moved) == set(members)
    holds_only(after, added[0]["id"], set(members))
    a, b, c = (by_id(after, m) for m in members)
    assert a["x"] < b["x"] < c["x"] and a["y"] == b["y"] == c["y"]  # a chain reads left to right
    assert all(by_id(after, n["id"]) is n for n in nodes if n["id"] not in members)


def test_layout_is_the_same_every_time():
    doc = {"nodes": [card(f"n{i}", (i % 4) * 300, (i // 4) * 160) for i in range(12)], "edges": []}
    runs = [canvas.add_group_boxes(copy.deepcopy(doc), [{"label": "A", "notes": ["n0", "n5"]}, {"label": "B", "notes": ["n6", "n11"]}])
            for _ in range(2)]
    strip = lambda d: [{k: v for k, v in n.items() if k != "id"} for n in d["nodes"]]  # noqa: E731 (box ids are random)
    assert strip(runs[0][0]) == strip(runs[1][0]) and runs[0][2] == runs[1][2]


@pytest.mark.parametrize("seed", range(40))
def test_whatever_the_layout_every_box_holds_only_its_notes_and_no_boxes_overlap(seed):
    rng = random.Random(seed)
    nodes = [card(f"n{i}", rng.randrange(-900, 900, 20), rng.randrange(-700, 700, 20), width=rng.choice([260, 360]))
             for i in range(rng.randrange(6, 18))]
    for i in range(rng.randrange(0, 3)):
        nodes.append(box(f"g{i}", rng.randrange(-900, 900, 20), rng.randrange(-700, 700, 20),
                         rng.randrange(300, 700, 20), rng.randrange(200, 500, 20), rng.choice([None, f"Box {i}"])))
    ids = [n["id"] for n in nodes if n["type"] == "text"]
    edges = [{"id": f"e{i}", "fromNode": rng.choice(ids), "toNode": rng.choice(ids)} for i in range(rng.randrange(0, 8))]
    doc = {"nodes": nodes, "edges": edges}
    rng.shuffle(ids)
    specs = [{"label": "First", "notes": ids[:3]}, {"label": "Second", "notes": ids[3:5]}]
    before = copy.deepcopy(doc)
    after, added, moved, _ = canvas.add_group_boxes(doc, specs)
    assert doc == before
    for spec, made in zip(specs, added):
        holds_only(after, made["id"], set(spec["notes"]))
        assert made["contains"] == [n["id"] for n in after["nodes"] if n["id"] in spec["notes"]]
    for n in after["nodes"]:  # what did not move is the very same card
        if n["id"] not in moved and n["id"] not in {a["id"] for a in added}:
            assert n is by_id(doc, n["id"])
    for n in nodes:  # a card that moved changed place and nothing else
        if n["id"] in moved:
            assert {k: v for k, v in by_id(after, n["id"]).items() if k not in ("x", "y")} == {k: v for k, v in n.items() if k not in ("x", "y")}
    try:
        renamed, _, _, _ = canvas.update_group_boxes(after, [{"id": added[1]["id"], "label": "A label much longer than any box here is wide"}])
    except EditError:
        return
    for spec, made in zip(specs, added):
        holds_only(renamed, made["id"], set(spec["notes"]))


# ── changing and removing a box ────────────────────────────────────────────

def test_renaming_a_box_changes_its_label_and_cluster_and_nothing_else():
    g = box("g", -20, -56, 300, 196, "Old name", color="4", background="bg.png")
    doc = {"nodes": [g, card("a", 0, 0)]}
    after, updated, moved, _ = canvas.update_group_boxes(doc, [{"id": "g", "label": "New name"}])
    assert by_id(after, "g") == {**g, "label": "New name"} and list(by_id(after, "g")) == list(g)
    assert updated == [{"id": "g", "label": "New name", "cluster": "new-name", "contains": ["a"], "collapsed": False}]
    assert moved == [] and by_id(after, "a") is doc["nodes"][1]


def test_changing_what_a_box_holds_refits_it():
    doc, ids = chain(6)
    made, added, _, _ = canvas.add_group_boxes(doc, [{"label": "Part", "notes": ids[:2]}])
    box_id = added[0]["id"]
    after, updated, moved, _ = canvas.update_group_boxes(made, [{"id": box_id, "notes": ids[:3]}])
    holds_only(after, box_id, set(ids[:3]))
    assert updated[0]["contains"] == ids[:3] and moved == []
    smaller, updated, _, _ = canvas.update_group_boxes(after, [{"id": box_id, "notes": ids[1:2]}])
    holds_only(smaller, box_id, {ids[1]})
    assert list(by_id(smaller, box_id)) == list(by_id(made, box_id))  # same fields, same order: only geometry changed


def test_collapsed_is_set_true_and_removed_when_false_as_the_app_does():
    g = box("g", -20, -56, 300, 196, "Box")
    doc = {"nodes": [g, card("a", 0, 0)]}
    closed, _, _, _ = canvas.update_group_boxes(doc, [{"id": "g", "collapsed": True}])
    assert by_id(closed, "g") == {**g, "collapsed": True} and list(by_id(closed, "g"))[-1] == "collapsed"
    opened, _, _, _ = canvas.update_group_boxes(closed, [{"id": "g", "collapsed": False}])
    assert by_id(opened, "g") == g and "collapsed" not in by_id(opened, "g")
    same, _, _, _ = canvas.update_group_boxes(doc, [{"id": "g"}])
    assert same is doc


def test_removing_a_box_keeps_its_notes_exactly_where_they_are():
    doc = {"nodes": [box("g", -20, -56, 300, 196, "Box"), card("a", 0, 0)],
           "edges": [{"id": "e", "fromNode": "a", "toNode": "g"}]}
    after, links = canvas.remove_group_boxes(doc, ["g"])
    assert after["nodes"] == [doc["nodes"][1]] and after["nodes"][0] is doc["nodes"][1]
    assert links == ["e"] and canvas.plain(after)["notes"][0]["clusters"] == []


@pytest.mark.parametrize("edit, message", [
    (lambda d: canvas.add_group_boxes(d, [{"label": " ", "notes": ["a"]}]), "needs a label"),
    (lambda d: canvas.add_group_boxes(d, [{"label": "X", "notes": []}]), "at least one note"),
    (lambda d: canvas.add_group_boxes(d, [{"label": "X", "notes": ["nope"]}]), "'nope' names no note"),
    (lambda d: canvas.add_group_boxes(d, [{"label": "X", "notes": ["g"]}]), "is a group box"),
    (lambda d: canvas.add_group_boxes(d, [{"label": "X", "notes": ["a"]}, {"label": "Y", "notes": ["a"]}]), "two boxes"),
    (lambda d: canvas.update_group_boxes(d, [{"id": "a", "label": "X"}]), "is not a group box"),
    (lambda d: canvas.update_group_boxes(d, [{"id": "g", "label": ""}]), "needs a label"),
    (lambda d: canvas.update_group_boxes(d, [{"id": "g", "notes": []}]), "remove_group_boxes"),
    (lambda d: canvas.remove_group_boxes(d, ["a"]), "is not a group box"),
])
def test_a_box_edit_that_cannot_be_made_says_why(edit, message):
    doc = {"nodes": [box("g", 500, 0, 300, 200, "G"), card("a", 0, 0)]}
    with pytest.raises(EditError, match=message):
        edit(doc)


# ── the web app reads them as the server meant ─────────────────────────────

def fixture_canvas(monkeypatch) -> dict:
    """The canvas in ``fixtures/server-boxes.canvas``: an eight-step pipeline made by
    add_notes, in two boxes made by add_group_boxes, the second starting collapsed.
    frontend/lib/canvas.test.ts reads the same file with the web app's own code."""
    counter = iter(range(1, 1000))
    monkeypatch.setattr(canvas.secrets, "token_hex", lambda n: f"{next(counter):016x}")
    doc, ids = chain(8)
    after, _, _, _ = canvas.add_group_boxes(doc, [{"label": "Training", "notes": ids[:4]},
                                                  {"label": "Inference", "notes": ids[4:], "collapsed": True}])
    return after


def test_the_shared_fixture_is_what_the_server_writes(monkeypatch):
    assert json.loads(FIXTURE.read_text(encoding="utf-8")) == fixture_canvas(monkeypatch)


LONG = "A considerably longer label here"


def test_a_box_renamed_to_a_label_too_long_for_it_widens_to_the_right_and_nothing_moves():
    doc = {"nodes": [box("g", -10, -10, 280, 140, "X"), card("a", 0, 0), card("above", 0, -200)]}
    after, updated, moved, _ = canvas.update_group_boxes(doc, [{"id": "g", "label": LONG}])
    assert by_id(after, "g") == {**by_id(doc, "g"), "label": LONG, "width": canvas._label_width(LONG)}
    assert canvas._label_width(LONG) > 280 and moved == [] and updated[0]["contains"] == ["a"]
    assert by_id(after, "a") is by_id(doc, "a") and by_id(after, "above") is by_id(doc, "above")


@pytest.mark.parametrize("label", ["Y", "A label of some length that this big box still shows"])
def test_a_box_drawn_larger_than_its_notes_keeps_its_size_when_renamed(label):
    g = box("g", -100, -100, 800, 500, "Room to grow")
    doc = {"nodes": [g, card("a", 0, 0)]}
    after, _, moved, _ = canvas.update_group_boxes(doc, [{"id": "g", "label": label}])
    assert by_id(after, "g") == {**g, "label": label} and moved == []


def test_a_card_the_widened_box_would_cover_moves_out_and_joins_no_cluster():
    label = "Questions still open for review, all of them"
    doc = {"nodes": [box("g", 0, 0, 120, 200, "X"), card("a", 160, 40)]}
    after, updated, moved, notices = canvas.update_group_boxes(doc, [{"id": "g", "label": label}])
    assert by_id(after, "g") == {**by_id(doc, "g"), "label": label, "width": canvas._label_width(label)}
    assert moved == ["a"] and notices == [] and updated[0]["contains"] == []
    holds_only(after, "g", set())
    assert canvas.plain(after)["notes"][0]["clusters"] == []


def test_a_box_with_another_box_on_its_right_widens_to_the_left_and_refuses_when_both_sides_are_taken():
    doc = {"nodes": [box("g", 0, 0, 120, 200, "X"), box("r", 200, 0, 300, 200, "Right")]}
    after, _, moved, _ = canvas.update_group_boxes(doc, [{"id": "g", "label": LONG}])
    g = by_id(after, "g")
    assert (g["x"] + g["width"], g["y"], g["width"], g["height"]) == (120, 0, canvas._label_width(LONG), 200)
    assert moved == [] and by_id(after, "r") is by_id(doc, "r")
    boxed_in = {"nodes": doc["nodes"] + [box("l", -400, 0, 200, 200, "Left")]}
    before = copy.deepcopy(boxed_in)
    with pytest.raises(EditError, match="in the way on both sides"):
        canvas.update_group_boxes(boxed_in, [{"id": "g", "label": LONG}])
    assert boxed_in == before


def test_the_padding_is_even_on_every_side_with_the_label_bar_above():
    doc = {"nodes": [card("a", 0, 0), card("b", 400, 200)]}
    after, added, _, _ = canvas.add_group_boxes(doc, [{"label": "Pair", "notes": ["a", "b"]}])
    g = by_id(after, added[0]["id"])
    left, top, right, bottom = 0 - g["x"], 0 - g["y"], g["x"] + g["width"] - 660, g["y"] + g["height"] - 320
    assert left == right == bottom == BOX_PAD >= 40 and top == canvas.LABEL_BAR + BOX_PAD
