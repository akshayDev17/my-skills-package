"""How notes, cards, group boxes and links look, and links edited in place: each choice is
written exactly as the web app's Inspector (frontend/components/Inspector.tsx) writes it, as a
graph styled by hand in the web app shows (fixtures/web-app-looks.canvas)."""

from __future__ import annotations

import json

import pytest

from graph_notes_mcp import canvas
from graph_notes_mcp.canvas import EditError
from support import REPO

def card(card_id: str, x: int = 0, y: int = 0, **more) -> dict:
    return {"id": card_id, "type": "text", "text": "note", "x": x, "y": y, "width": 260, "height": 120, **more}


def test_the_colours_are_the_apps_six_presets_and_any_hex():
    assert canvas.color_value("Red") == "1" and canvas.color_value("purple") == "6"
    assert canvas.color_value("#3A4BD8") == "#3a4bd8"  # the colour input's own lowercase form
    assert canvas.color_value("none") is None
    for bad in ("blue", "#fff", "3", "rgb(1,2,3)"):
        with pytest.raises(EditError, match="is not one the app offers"):
            canvas.color_value(bad)


# ── styling cards ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("change, written", [
    ({"color": "red"}, {"color": "1"}),
    ({"color": "orange"}, {"color": "2"}),
    ({"color": "yellow"}, {"color": "3"}),
    ({"color": "green"}, {"color": "4"}),
    ({"color": "cyan"}, {"color": "5"}),
    ({"color": "purple"}, {"color": "6"}),
    ({"color": "#AB12cd"}, {"color": "#ab12cd"}),
    ({"border": "dashed"}, {"styleAttributes": {"border": "dashed"}}),
    ({"border": "dotted"}, {"styleAttributes": {"border": "dotted"}}),
    ({"border": "none"}, {"styleAttributes": {"border": "invisible"}}),
    ({"shape": "pill"}, {"styleAttributes": {"shape": "pill"}}),
    ({"shape": "circle"}, {"styleAttributes": {"shape": "circle"}}),
    ({"shape": "database"}, {"styleAttributes": {"shape": "database"}}),
    ({"text_align": "center"}, {"styleAttributes": {"textAlign": "center"}}),
    ({"text_align": "right"}, {"styleAttributes": {"textAlign": "right"}}),
    ({"color": "green", "border": "dashed", "shape": "pill", "text_align": "center"},
     {"color": "4", "styleAttributes": {"border": "dashed", "shape": "pill", "textAlign": "center"}}),
])
def test_each_look_of_a_card_is_written_as_the_inspector_writes_it(change, written):
    n = card("a", zIndex=2)
    after, styled = canvas.style_notes({"nodes": [n]}, [{"id": "a", **change}])
    assert after["nodes"][0] == {**n, **written}
    assert list(after["nodes"][0]) == [*n, *written]  # new keys last, as the app's patch adds them
    assert styled == [{"id": "a", **canvas.appearance(after["nodes"][0])}]
    shown = canvas.plain(after)["notes"][0]
    assert {k: shown[k] for k in change} == {k: (v.lower() if k == "color" else v) for k, v in change.items()}


@pytest.mark.parametrize("change, key", [
    ({"border": "solid"}, "border"), ({"shape": "rectangle"}, "shape"), ({"text_align": "left"}, "textAlign")])
def test_choosing_the_default_removes_the_attribute_and_an_empty_set_goes_too(change, key):
    n = card("a", styleAttributes={key: "x"})
    after, _ = canvas.style_notes({"nodes": [n]}, [{"id": "a", **change}])
    assert "styleAttributes" not in after["nodes"][0]
    kept = card("a", styleAttributes={key: "x", "other": "keep"})
    after, _ = canvas.style_notes({"nodes": [kept]}, [{"id": "a", **change}])
    assert after["nodes"][0]["styleAttributes"] == {"other": "keep"}


def test_no_colour_removes_the_colour_and_everything_else_stays():
    n = card("a", color="4", styleAttributes={"shape": "pill"}, mystery={"k": 1})
    doc = {"nodes": [n, card("b", 400)], "edges": [{"id": "e", "fromNode": "a", "toNode": "b"}], "metadata": {"v": 1}}
    after, styled = canvas.style_notes(doc, [{"id": "a", "color": "none"}])
    assert after["nodes"][0] == {k: v for k, v in n.items() if k != "color"}
    assert after["nodes"][1] is doc["nodes"][1] and after["edges"] is doc["edges"] and after["metadata"] is doc["metadata"]
    assert styled == [{"id": "a", "shape": "pill"}]


def test_a_value_the_controls_do_not_offer_is_kept_until_that_control_changes():
    n = card("a", styleAttributes={"shape": "diamond", "border": "dashed"})
    after, _ = canvas.style_notes({"nodes": [n]}, [{"id": "a", "border": "dotted"}])
    assert after["nodes"][0]["styleAttributes"] == {"shape": "diamond", "border": "dotted"}
    assert canvas.plain(after)["notes"][0]["shape"] == "diamond"


def test_a_group_box_takes_a_colour_and_nothing_else():
    g = {"id": "g", "type": "group", "label": "Box", "x": 0, "y": 0, "width": 400, "height": 300}
    after, styled = canvas.style_notes({"nodes": [g]}, [{"id": "g", "color": "cyan"}])
    assert after["nodes"][0] == {**g, "color": "5"} and styled == [{"id": "g", "color": "cyan"}]
    assert canvas.plain(after)["group_boxes"][0]["color"] == "cyan"
    for look in ({"border": "dashed"}, {"shape": "pill"}, {"text_align": "center"}):
        with pytest.raises(EditError, match="is a group box: it takes a colour"):
            canvas.style_notes({"nodes": [g]}, [{"id": "g", **look}])


def test_web_page_and_file_cards_take_every_look():
    w = {"id": "w", "type": "link", "url": "https://example.com", "x": 0, "y": 0, "width": 400, "height": 240}
    after, _ = canvas.style_notes({"nodes": [w]}, [{"id": "w", "color": "#112233", "shape": "pill"}])
    assert after["nodes"][0] == {**w, "color": "#112233", "styleAttributes": {"shape": "pill"}}
    assert canvas.plain(after)["other_cards"][0] == {"id": "w", "kind": "web page", "url": "https://example.com",
                                                     "clusters": [], "color": "#112233", "shape": "pill"}


def test_styling_the_same_card_twice_in_one_call_applies_both_in_order():
    after, styled = canvas.style_notes({"nodes": [card("a")]}, [{"id": "a", "color": "red"}, {"id": "a", "color": "green", "shape": "pill"}])
    assert after["nodes"][0]["color"] == "4" and styled == [{"id": "a", "color": "green", "shape": "pill"}]


@pytest.mark.parametrize("change, message", [
    ({"id": "nope", "color": "red"}, "'nope' names no note"),
    ({"id": "a", "color": "blue"}, "is not one the app offers"),
    ({"id": "a", "shape": "hexagon"}, "shape must be one of"),
])
def test_a_look_that_cannot_be_set_is_refused_and_nothing_changes(change, message):
    doc = {"nodes": [card("a")]}
    with pytest.raises(EditError, match=message):
        canvas.style_notes(doc, [change])
    assert doc == {"nodes": [card("a")]}


# ── links: drawn, then edited in place ─────────────────────────────────────

def two_cards(**edge) -> dict:
    return {"nodes": [card("a"), card("b", 600)], "edges": [{"id": "e", "fromNode": "a", "toNode": "b", **edge}]}


@pytest.mark.parametrize("change, written", [
    ({"label": "feeds"}, {"label": "feeds"}),
    ({"color": "orange"}, {"color": "2"}),
    ({"color": "#00FF00"}, {"color": "#00ff00"}),
    ({"line": "dashed"}, {"styleAttributes": {"path": "long-dashed"}}),
    ({"line": "short-dashed"}, {"styleAttributes": {"path": "short-dashed"}}),
    ({"line": "dotted"}, {"styleAttributes": {"path": "dotted"}}),
    ({"direction": "backward"}, {"toEnd": "none", "fromEnd": "arrow"}),
    ({"direction": "both"}, {"fromEnd": "arrow"}),
    ({"direction": "none"}, {"toEnd": "none"}),
    ({"path": "straight"}, {"styleAttributes": {"pathfindingMethod": "direct"}}),
    ({"path": "right-angles"}, {"styleAttributes": {"pathfindingMethod": "square"}}),
    ({"from_side": "bottom", "to_side": "top"}, {"fromSide": "bottom", "toSide": "top"}),
    # the Inspector's order, top to bottom: label, colour, line, arrows, path
    ({"label": "x", "color": "red", "line": "dotted", "direction": "backward", "path": "straight"},
     {"label": "x", "color": "1", "styleAttributes": {"path": "dotted", "pathfindingMethod": "direct"}, "toEnd": "none", "fromEnd": "arrow"}),
])
def test_each_change_to_a_link_is_written_as_the_inspector_writes_it(change, written):
    doc = two_cards(futureKey=1)
    after, updated = canvas.update_links(doc, [{"id": "e", **change}])
    edge = after["edges"][0]
    assert edge == {**doc["edges"][0], **written}
    assert list(edge) == [*doc["edges"][0], *written]
    assert after["nodes"] is doc["nodes"]
    assert updated == [canvas._plain_link(edge)]


@pytest.mark.parametrize("stored, change, left", [
    ({"label": "old"}, {"label": ""}, {}),
    ({"color": "4"}, {"color": "none"}, {}),
    ({"styleAttributes": {"path": "dotted"}}, {"line": "solid"}, {}),
    ({"styleAttributes": {"pathfindingMethod": "square", "arrow": "triangle"}}, {"path": "curved"}, {"styleAttributes": {"arrow": "triangle"}}),
    ({"toEnd": "none", "fromEnd": "arrow"}, {"direction": "forward"}, {}),
])
def test_a_link_change_back_to_the_default_removes_what_the_app_removes(stored, change, left):
    doc = two_cards(**stored)
    after, _ = canvas.update_links(doc, [{"id": "e", **change}])
    assert after["edges"][0] == {"id": "e", "fromNode": "a", "toNode": "b", **left}


def test_a_link_reads_back_with_its_look_in_the_models_words():
    doc = two_cards(fromSide="right", toSide="left", color="6", label="to", styleAttributes={"path": "short-dashed", "pathfindingMethod": "square"},
                    fromEnd="arrow", toEnd="none")
    assert canvas.plain(doc)["links"] == [{"id": "e", "from_note": "a", "to_note": "b", "direction": "backward", "label": "to",
                                           "color": "purple", "line": "short-dashed", "path": "right-angles",
                                           "from_side": "right", "to_side": "left"}]
    v1 = two_cards(styleAttributes={"path": "dashed", "pathfindingMethod": "a-star"})  # written by v1 / Obsidian
    assert canvas.plain(v1)["links"][0] == {"id": "e", "from_note": "a", "to_note": "b", "direction": "forward",
                                                "line": "dashed", "path": "a-star"}


def test_a_new_link_takes_its_sides_where_the_app_writes_them_and_its_look():
    doc = {"nodes": [card("a"), card("b", 600)], "edges": []}
    after, added = canvas.add_links(doc, [{"from_note": "a", "to_note": "b", "from_side": "right", "to_side": "left",
                                           "direction": "backward", "label": "from", "color": "red", "line": "dotted",
                                           "path": "straight"}])
    edge = after["edges"][0]
    # where the app's "connect side to side" puts the sides, then what the Inspector adds,
    # as a link drawn and then edited in the web app is stored (see web-app-looks.canvas)
    assert list(edge) == ["id", "fromNode", "fromSide", "toNode", "toSide", "label", "color", "styleAttributes", "toEnd", "fromEnd"]
    assert edge == {"id": edge["id"], "fromNode": "a", "fromSide": "right", "toNode": "b", "toSide": "left",
                    "label": "from", "color": "1", "styleAttributes": {"path": "dotted", "pathfindingMethod": "direct"},
                    "toEnd": "none", "fromEnd": "arrow"}
    assert added[0]["direction"] == "backward" and added[0]["from_side"] == "right"
    plain_only, _ = canvas.add_links(doc, [{"from_note": "a", "to_note": "b"}])
    assert list(plain_only["edges"][0]) == ["id", "fromNode", "toNode"]


@pytest.mark.parametrize("sides, message", [
    ({"from_side": "right"}, "from_side and to_side go together"),
    ({"to_side": "left"}, "from_side and to_side go together"),
    ({"from_side": "auto", "to_side": "auto"}, "from_side must be one of top, right, bottom, left, not 'auto'"),
    ({"from_side": "right", "to_side": "auto"}, "to_side must be one of top, right, bottom, left, not 'auto'"),
])
def test_a_new_link_takes_both_sides_or_neither(sides, message):
    doc = {"nodes": [card("a"), card("b", 600)], "edges": []}
    with pytest.raises(EditError, match=message):
        canvas.add_links(doc, [{"from_note": "a", "to_note": "b", **sides}])
    with pytest.raises(EditError, match=message):
        canvas.add_notes(doc, [{"text": "c", "key": "c"}], [{"from_note": "a", "to_note": "c", **sides}])


@pytest.mark.parametrize("change, message", [
    ({"id": "nope", "label": "x"}, "'nope' names no link"),
    ({"id": "e", "line": "wavy"}, "line must be one of"),
    ({"id": "e", "from_side": "middle", "to_side": "top"}, "from_side must be one of"),
    ({"id": "e", "from_side": "bottom"}, "from_side and to_side go together"),
    ({"id": "e", "to_side": "top"}, "from_side and to_side go together"),
    ({"id": "e", "from_side": "auto", "to_side": "auto"}, "from_side must be one of top, right, bottom, left, not 'auto'"),
    ({"id": "e", "direction": "sideways"}, "direction must be one of"),
    ({"id": "e", "color": "teal"}, "is not one the app offers"),
])
def test_a_link_change_that_cannot_be_made_is_refused(change, message):
    with pytest.raises(EditError, match=message):
        canvas.update_links(two_cards(), [change])


def test_updating_a_link_twice_in_one_call_and_leaving_others_alone():
    doc = {"nodes": [card("a"), card("b", 600)], "edges": [{"id": "e", "fromNode": "a", "toNode": "b"}, {"id": "f", "fromNode": "b", "toNode": "a"}]}
    after, updated = canvas.update_links(doc, [{"id": "e", "label": "one"}, {"id": "e", "direction": "none"}])
    assert after["edges"][0] == {"id": "e", "fromNode": "a", "toNode": "b", "label": "one", "toEnd": "none"}
    assert after["edges"][1] is doc["edges"][1] and len(updated) == 1


# ── against a graph styled by hand in the web app ──────────────────────────

HAND_MADE = REPO / "mcp-server" / "tests" / "fixtures" / "web-app-looks.canvas"


def test_the_server_writes_what_the_web_app_wrote_for_the_same_edits_made_by_hand(monkeypatch):
    """``fixtures/web-app-looks.canvas`` was made in the web app's editor, by hand, from four
    plain notes: each note styled from the Inspector, two links drawn from side to side and
    then edited (label, colour, line, arrows, path). The same edits through the server, in
    the same order, give the same canvas, key for key and in the same order."""
    hand = json.loads(HAND_MADE.read_text(encoding="utf-8"))
    base = {"nodes": [{"id": i, "type": "text", "text": t, "x": x, "y": y, "width": 260, "height": 120}
                      for i, t, x, y in (("a1", "Alpha", 0, 0), ("b2", "Beta", 420, 0), ("c3", "Gamma", 0, 300), ("d4", "Delta", 420, 300))],
            "edges": []}
    ids = iter(e["id"] for e in hand["edges"])
    monkeypatch.setattr(canvas.secrets, "token_hex", lambda n: next(ids))
    doc, _ = canvas.style_notes(base, [
        {"id": "a1", "color": "green", "border": "dashed", "shape": "pill", "text_align": "center"},
        {"id": "b2", "border": "none", "shape": "database", "text_align": "right"}, {"id": "b2", "color": "#AB12CD"},
        {"id": "c3", "color": "red", "border": "dotted", "shape": "circle"},
        {"id": "d4", "color": "purple"}, {"id": "d4", "color": "none"}, {"id": "d4", "color": "yellow"}])
    doc, (feeds,) = canvas.add_links(doc, [{"from_note": "a1", "to_note": "b2", "from_side": "right", "to_side": "left"}])
    doc, _ = canvas.update_links(doc, [{"id": feeds["id"], "label": "feeds", "color": "orange", "line": "dotted",
                                        "direction": "backward", "path": "right-angles"}])
    doc, (back,) = canvas.add_links(doc, [{"from_note": "c3", "to_note": "a1", "from_side": "top", "to_side": "bottom"}])
    doc, _ = canvas.update_links(doc, [{"id": back["id"], "direction": "both"}, {"id": back["id"], "line": "dashed"},
                                       {"id": back["id"], "path": "straight"}, {"id": back["id"], "color": "#30A46C"}])
    as_stored = lambda d: [json.dumps(i, ensure_ascii=False) for i in d["nodes"] + d["edges"]]  # noqa: E731 (key order counts)
    assert as_stored(doc) == as_stored(hand)
