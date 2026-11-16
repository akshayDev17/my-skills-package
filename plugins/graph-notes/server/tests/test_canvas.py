"""canvas.py: the plain form, the cluster rules (the app's, frontend/lib/canvas.test.ts),
placement, and that an edit changes nothing but what it was asked to."""

from __future__ import annotations

import copy

import pytest

from graph_notes_mcp import canvas
from graph_notes_mcp.canvas import COLUMN_GAP, GAP, ROW_GAP, EditError
from support import fixture


def card(card_id: str, x: int, y: int, text: str = "note", **more) -> dict:
    return {"id": card_id, "type": "text", "text": text, "x": x, "y": y, "width": 260, "height": 120, **more}


def box(box_id: str, x: int, y: int, width: int, height: int, label: str | None = None) -> dict:
    return {"id": box_id, "type": "group", "x": x, "y": y, "width": width, "height": height,
            **({"label": label} if label is not None else {})}


def touching(a: dict, b: dict, gap: int = 0) -> bool:
    return not (a["x"] >= b["x"] + b["width"] + gap or a["x"] + a["width"] + gap <= b["x"]
                or a["y"] >= b["y"] + b["height"] + gap or a["y"] + a["height"] + gap <= b["y"])


def by_id(doc: dict, item_id: str) -> dict:
    return next(i for key in ("nodes", "edges") for i in doc.get(key, []) if i["id"] == item_id)


# ── clusters: the same answers as the app ──────────────────────────────────

@pytest.mark.parametrize("text, body, tags", [
    ("note\n#a #b", "note", ["a", "b"]),
    ("## Read API\nServes it.\n#auth", "## Read API\nServes it.", ["auth"]),
    ("no tags here", "no tags here", []),
    ("a #tag in a sentence", "a #tag in a sentence", []),
    ("#only", "", ["only"]),
    ("note\n\n#A  #a #B/c\n", "note", ["a", "b/c"]),
    ("note\n#ünï_code-1", "note", ["ünï_code-1"]),
    ("# A heading is not a tag", "# A heading is not a tag", []),
])
def test_a_trailing_line_of_tags_is_the_notes_clusters(text, body, tags):
    assert canvas.split_tags(text) == (body, tags)


def test_cluster_names_are_normalized_and_made_safe_for_a_tag():
    assert canvas.cluster_key("  Open Question ") == "open-question"
    assert canvas.cluster_key("#backend") == "backend"
    assert canvas.clean_tags(["Back End", "back-end", "c++", "", "!!!"]) == ["back-end", "c"]
    assert canvas.join_tags("note", ["a", "b"]) == "note\n#a #b"
    assert canvas.join_tags("", ["a"]) == "#a"
    assert canvas.join_tags("note", []) == "note"


def test_the_plain_form_has_notes_links_clusters_and_looks_and_no_geometry():
    plain = canvas.plain(fixture("example"))
    assert plain["notes"][0] == {"id": "api", "text": "## Read API\nServes `/graph/api/*` from D1.", "clusters": ["auth", "backend"],
                                 "color": "green"}
    assert {"id": "e4", "from_note": "access", "to_note": "api", "direction": "both", "label": "guards", "line": "dotted",
            "from_side": "right", "to_side": "left"} in plain["links"]
    assert {"name": "backend", "made_by": "group box", "members": ["api", "ingest", "d1"]} in plain["clusters"]
    assert plain["group_boxes"][0] == {"id": "grp-backend", "label": "Backend", "cluster": "backend", "contains": ["api", "ingest", "d1"],
                                       "collapsed": False, "color": "cyan"}
    text = repr(plain)
    assert not any(key in text for key in ("'x'", "'width'", "fromSide", "styleAttributes", "pathfindingMethod", "textAlign"))


def test_a_tag_and_a_group_box_of_the_same_name_are_one_cluster():
    doc = {"nodes": [box("g", 0, 0, 400, 300, "Back End"), card("in", 20, 20), card("out", 900, 0, "x\n#back-end")]}
    assert canvas.plain(doc)["clusters"] == [{"name": "back-end", "made_by": "both", "members": ["in", "out"]}]


def test_cards_that_are_not_notes_and_link_directions_are_reported():
    doc = {"nodes": [card("a", 0, 0), {"id": "w", "type": "link", "url": "https://example.com", "x": 400, "y": 0, "width": 400, "height": 240},
                     {"id": "f", "type": "file", "file": "notes/a.md", "x": 900, "y": 0, "width": 300, "height": 200}],
           "edges": [{"id": "1", "fromNode": "a", "toNode": "w"}, {"id": "2", "fromNode": "a", "toNode": "f", "toEnd": "none"},
                     {"id": "3", "fromNode": "w", "toNode": "a", "fromEnd": "arrow", "toEnd": "none"}]}
    plain = canvas.plain(doc)
    assert plain["other_cards"] == [{"id": "w", "kind": "web page", "url": "https://example.com", "clusters": []},
                                    {"id": "f", "kind": "file", "file": "notes/a.md", "clusters": []}]
    assert [link["direction"] for link in plain["links"]] == ["forward", "none", "backward"]
    assert "group_boxes" not in plain


def test_an_empty_canvas_reads_as_an_empty_graph():
    assert canvas.plain({}) == {"notes": [], "links": [], "clusters": []}
    assert canvas.plain({"nodes": [], "edges": [], "metadata": {"version": "1.0-1.0"}}) == {"notes": [], "links": [], "clusters": []}


# ── placement ──────────────────────────────────────────────────────────────

def test_the_first_note_of_an_empty_graph_is_centred_on_the_origin():
    after, added, _ = canvas.add_notes({"nodes": [], "edges": []}, [{"text": "first"}])
    node = after["nodes"][0]
    assert (node["x"], node["y"], node["width"], node["height"]) == (-130, -60, 260, 120)
    assert added == [{"id": node["id"], "text": "first", "clusters": []}]


def test_new_notes_never_touch_a_card_or_each_other():
    after, _, _ = canvas.add_notes(fixture("kmeans-in-production"), [{"text": f"note {i}"} for i in range(12)])
    cards = [n for n in after["nodes"] if n["type"] != "group"]
    new = cards[-12:]
    for n in new:
        assert all(not touching(n, other, GAP - 1) for other in cards if other is not n)


def test_a_note_is_placed_beside_the_note_it_is_near():
    doc = {"nodes": [card("far", 0, 0), card("anchor", 5000, 5000)]}
    after, _, _ = canvas.add_notes(doc, [{"text": "new", "near": "anchor"}])
    new = after["nodes"][-1]
    assert abs(new["x"] - 5000) <= 300 and abs(new["y"] - 5000) <= 300
    assert not touching(new, by_id(doc, "anchor"), GAP - 1)


def test_a_note_goes_beside_the_notes_that_share_its_cluster():
    doc = {"nodes": [card("a", 0, 0), card("b", 4000, 0, "b\n#ops"), card("c", 4300, 0, "c\n#ops")]}
    after, _, _ = canvas.add_notes(doc, [{"text": "new", "clusters": ["Ops"]}])
    new = after["nodes"][-1]
    assert new["text"] == "new\n#ops"
    assert 3400 <= new["x"] <= 4900


def test_near_must_name_a_card_of_the_graph():
    with pytest.raises(EditError, match="near 'nope' names no note"):
        canvas.add_notes({"nodes": [card("a", 0, 0)]}, [{"text": "new", "near": "nope"}])


def test_a_new_note_never_joins_a_group_boxs_cluster_it_was_not_given():
    # The only card sits in a roomy labelled box: the nearest free spot would be inside it.
    doc = {"nodes": [box("g", 0, 0, 1600, 1200, "Backend"), card("in", 700, 500)]}
    after, _, _ = canvas.add_notes(doc, [{"text": "elsewhere", "near": "in"}])
    new = after["nodes"][-1]
    assert not touching(new, by_id(doc, "g"), GAP - 1)
    assert canvas.plain(after)["notes"][-1]["clusters"] == []


def test_a_note_given_a_group_boxs_cluster_is_placed_inside_the_box_when_there_is_room():
    doc = {"nodes": [box("g", 0, 0, 1600, 1200, "Backend"), card("in", 700, 500)]}
    after, _, _ = canvas.add_notes(doc, [{"text": "inside", "clusters": ["backend"]}])
    new = after["nodes"][-1]
    assert canvas.is_inside(new, by_id(doc, "g")) and not touching(new, by_id(doc, "in"), GAP - 1)
    assert {"name": "backend", "made_by": "both", "members": ["in", new["id"]]} in canvas.plain(after)["clusters"]


def test_a_note_never_lands_inside_an_unlabelled_box_or_across_a_box_edge():
    doc = {"nodes": [box("plain", -400, -400, 800, 800), box("inner", 2000, 0, 300, 200, "Inner")]}
    after, _, _ = canvas.add_notes(doc, [{"text": "n"}, {"text": "m", "clusters": ["inner"]}])
    for new in after["nodes"][-2:]:
        for group in doc["nodes"]:
            assert not touching(new, group, GAP - 1)  # "inner" is too small to hold a card: outside it, with the tag
    assert canvas.plain(after)["notes"][-1]["clusters"] == ["inner"]


def test_with_no_room_nearby_a_note_goes_to_the_right_of_everything():
    wall = {"nodes": [card("wall", -20000, -20000, width=40000, height=40000)]}
    after, _, _ = canvas.add_notes(wall, [{"text": "n", "near": "wall"}])
    assert after["nodes"][-1]["x"] == 20000 + 2 * GAP


def test_notes_that_relate_to_nothing_go_below_the_graph_in_a_grid_not_into_its_gaps():
    doc = fixture("kmeans-in-production")
    left = min(n["x"] for n in doc["nodes"])
    bottom = max(n["y"] + n["height"] for n in doc["nodes"])
    after, _, _ = canvas.add_notes(doc, [{"text": f"loose {i}"} for i in range(5)])
    new = after["nodes"][-5:]
    assert [(n["x"] - left, n["y"] - bottom - 2 * GAP) for n in new] == [
        (0, 0), (260 + GAP, 0), (2 * (260 + GAP), 0), (0, 120 + GAP), (260 + GAP, 120 + GAP)]


def test_several_notes_into_an_empty_graph_form_a_grid_around_the_origin():
    after, _, _ = canvas.add_notes({}, [{"text": f"n{i}"} for i in range(4)])
    assert [(n["x"], n["y"]) for n in after["nodes"]] == [(-280, -140), (20, -140), (-280, 20), (20, 20)]


def test_a_card_is_sized_for_its_text_as_the_web_app_draws_it():
    assert canvas.note_size("short") == (260, 120)  # the app's own new note
    assert canvas.note_size("## Ingest\nPull events from the queue and validate them.") == (260, 120)
    # A heading, a line and three bullets need 125px in the app.
    assert canvas.note_size("## Feature store\nOne row per user per day.\n- recency\n- frequency\n- monetary value") == (260, 140)
    assert canvas.note_size("\n".join(["a line of text"] * 8)) == (260, 180)
    # Long text gets a wider card before a very tall one (about 190px of text at this width in the app).
    assert canvas.note_size("A long note. " + "This sentence pads the note so it needs a taller card than the default one. " * 6) == (360, 220)
    assert canvas.note_size("word " * 400) == (360, 600)


# ── notes that arrive with their links are laid out along them ─────────────

def flow(edges: list[tuple[str, str]], names: str = "abcd", size: tuple[int, int] = (260, 120)) -> dict:
    return canvas.flow_layout({name: size for name in names}, edges)


def test_a_chain_runs_left_to_right_on_one_level_with_room_for_labels():
    assert flow([("a", "b"), ("b", "c")], "abc") == {"a": (0, 0), "b": (260 + COLUMN_GAP, 0), "c": (2 * (260 + COLUMN_GAP), 0)}


def test_a_note_sits_level_with_the_middle_of_what_it_links_to():
    fan_out = flow([("a", "b"), ("a", "c"), ("a", "d")])
    assert fan_out == {"a": (0, 160), "b": (420, 0), "c": (420, 160), "d": (420, 320)}
    fan_in = flow([("a", "d"), ("b", "d"), ("c", "d")])
    assert fan_in == {"a": (0, 0), "b": (0, 160), "c": (0, 320), "d": (420, 160)}
    diamond = flow([("a", "b"), ("a", "c"), ("b", "d"), ("c", "d")])
    assert diamond == {"a": (0, 80), "b": (420, 0), "c": (420, 160), "d": (840, 80)}


def test_links_that_form_a_cycle_still_lay_out():
    assert flow([("a", "b"), ("b", "a")], "ab") == {"a": (0, 0), "b": (420, 0)}
    loop = flow([("a", "b"), ("b", "c"), ("c", "a"), ("c", "d")])
    assert [loop[n][0] for n in "abcd"] == [0, 420, 840, 1260]


def test_a_note_nothing_points_to_sits_just_left_of_what_it_points_to():
    # e only feeds d, three columns in: it goes beside d, not in the first column with a long link.
    at = flow([("a", "b"), ("b", "c"), ("c", "d"), ("e", "d")], "abcde")
    assert at["e"][0] == at["c"][0] and at["e"][1] != at["c"][1]


def test_columns_never_overlap_whatever_the_card_sizes():
    sizes = {"a": (260, 120), "b": (360, 600), "c": (260, 140), "d": (360, 220), "e": (260, 120), "f": (260, 300)}
    at = canvas.flow_layout(sizes, [("a", "b"), ("a", "c"), ("a", "d"), ("b", "e"), ("c", "e"), ("d", "f"), ("c", "f")])
    cards = [{"id": n, "x": at[n][0], "y": at[n][1], "width": sizes[n][0], "height": sizes[n][1]} for n in sizes]
    assert min(c["x"] for c in cards) == 0 and min(c["y"] for c in cards) == 0
    for a in cards:
        for b in cards:
            assert a is b or not touching(a, b, ROW_GAP - 1)


def test_notes_added_with_their_links_are_placed_as_one_block_clear_of_everything():
    doc = fixture("kmeans-in-production")
    notes = [{"text": name.upper(), "key": name} for name in "abcd"]
    links = [{"from_note": "a", "to_note": "b", "label": "then"}, {"from_note": "b", "to_note": "c"},
             {"from_note": "b", "to_note": "d", "direction": "both"}]
    after, added, linked = canvas.add_notes(doc, notes, links)
    ids = {note["key"]: note["id"] for note in added}
    a, b, c, d = (by_id(after, ids[name]) for name in "abcd")
    assert a["y"] + 60 == b["y"] + 60 and b["x"] - a["x"] == 260 + COLUMN_GAP  # a -> b level, a column apart
    assert c["x"] == d["x"] == b["x"] + 260 + COLUMN_GAP and abs(c["y"] - d["y"]) == 120 + ROW_GAP
    for new in (a, b, c, d):
        assert all(not touching(new, other, GAP - 1) for other in after["nodes"] if other is not new and other["type"] != "group")
        assert all(not touching(new, group) for group in after["nodes"] if group["type"] == "group")
    assert [(link["from_note"], link["to_note"], link["direction"]) for link in linked] == [
        (ids["a"], ids["b"], "forward"), (ids["b"], ids["c"], "forward"), (ids["b"], ids["d"], "both")]
    assert after["edges"][-3] == {"id": linked[0]["id"], "fromNode": ids["a"], "toNode": ids["b"], "label": "then"}
    assert after["edges"][:-3] == doc["edges"] and after["nodes"][:-4] == doc["nodes"]


def test_a_new_note_linked_to_an_existing_one_goes_beside_it():
    doc = {"nodes": [card("far", 0, 0), card("anchor", 5000, 5000)], "edges": []}
    after, added, linked = canvas.add_notes(doc, [{"text": "new", "key": "n"}], [{"from_note": "anchor", "to_note": "n"}])
    new = after["nodes"][-1]
    assert abs(new["x"] - 5000) <= 360 and abs(new["y"] - 5000) <= 360
    assert linked == [{"id": linked[0]["id"], "from_note": "anchor", "to_note": new["id"], "direction": "forward"}]


def test_a_block_goes_beside_the_note_one_of_its_notes_is_near():
    doc = {"nodes": [card("far", 0, 0), card("anchor", 5000, 5000)]}
    after, _, _ = canvas.add_notes(doc, [{"text": "one", "key": "1", "near": "anchor"}, {"text": "two", "key": "2"}],
                                   [{"from_note": "1", "to_note": "2"}])
    one, two = after["nodes"][-2:]
    assert abs(one["x"] - 5000) <= 900 and abs(one["y"] - 5000) <= 400 and two["x"] - one["x"] == 260 + COLUMN_GAP


def test_a_block_stays_out_of_group_boxes_and_its_notes_keep_their_clusters_as_tags():
    doc = {"nodes": [box("g", 0, 0, 3000, 2000, "Backend"), card("in", 1400, 900)]}
    after, added, _ = canvas.add_notes(doc, [{"text": "one", "key": "1", "clusters": ["backend"]}, {"text": "two", "key": "2"}],
                                       [{"from_note": "1", "to_note": "2"}])
    for new in after["nodes"][-2:]:
        assert not touching(new, doc["nodes"][0])
    plain = canvas.plain(after)
    assert plain["notes"][-2]["clusters"] == ["backend"] and plain["notes"][-1]["clusters"] == []


@pytest.mark.parametrize("notes, links, message", [
    ([{"text": "a", "key": "k"}, {"text": "b", "key": "k"}], [], "key 'k' must be unique: another new note has it"),
    ([{"text": "a", "key": "api"}], [], "key 'api' must be unique: a card of this graph already has that id"),
    ([{"text": "a", "key": "k"}], [{"from_note": "k", "to_note": "nope"}], "to_note 'nope' names no note"),
    ([{"text": "a", "key": "k"}], [{"from_note": "k", "to_note": "k"}], "two different notes"),
])
def test_keys_and_link_ends_of_new_notes_are_checked(notes, links, message):
    with pytest.raises(EditError, match=message):
        canvas.add_notes(fixture("example"), notes, links)


# ── edits change only what they were asked to ──────────────────────────────

def untouched(before: dict, after: dict, *changed_ids: str) -> None:
    """Every item but ``changed_ids`` is the same object in the same order; so are the other top-level keys."""
    for key in ("nodes", "edges"):
        kept_before = [i for i in before.get(key, []) if i["id"] not in changed_ids]
        kept_after = [i for i in after.get(key, []) if i["id"] not in changed_ids]
        assert len(kept_after) >= len(kept_before)
        assert all(a is b for a, b in zip(kept_before, kept_after))
    assert {k: v for k, v in after.items() if k not in ("nodes", "edges")} == {k: v for k, v in before.items() if k not in ("nodes", "edges")}


@pytest.mark.parametrize("slug", ["example", "kmeans-in-production"])
def test_every_edit_leaves_the_rest_of_the_canvas_as_it_was(slug):
    before = fixture(slug)
    pristine = copy.deepcopy(before)
    first = next(n for n in before["nodes"] if n["type"] == "text")
    second = [n for n in before["nodes"] if n["type"] == "text"][1]
    edge = before["edges"][0]

    added, _, _ = canvas.add_notes(before, [{"text": "new", "clusters": ["x"]}])
    untouched(before, added)
    assert len(added["nodes"]) == len(before["nodes"]) + 1 and added["edges"] is before["edges"]

    updated, _ = canvas.update_notes(before, [{"id": first["id"], "text": "rewritten"}])
    untouched(before, updated, first["id"])
    changed = by_id(updated, first["id"])
    assert {k: v for k, v in changed.items() if k != "text"} == {k: v for k, v in first.items() if k != "text"}
    assert list(changed) == list(first)  # key order too

    linked, links = canvas.add_links(before, [{"from_note": first["id"], "to_note": second["id"], "label": "l"}])
    untouched(before, linked)
    assert linked["nodes"] is before["nodes"] and len(linked["edges"]) == len(before["edges"]) + 1

    unlinked = canvas.remove_links(before, [edge["id"]])
    untouched(before, unlinked, edge["id"])
    assert unlinked["nodes"] is before["nodes"]

    removed, dropped = canvas.remove_cards(before, [first["id"]])
    untouched(before, removed, first["id"], *dropped)
    assert all(first["id"] in (by_id(before, e)["fromNode"], by_id(before, e)["toNode"]) for e in dropped)

    assert before == pristine  # and no edit changed its input


def test_updating_text_keeps_the_notes_clusters_and_updating_clusters_keeps_its_text():
    doc = {"nodes": [card("a", 0, 0, "old\n#x #y", color="3", styleAttributes={"shape": "pill"}, unknownKey=[1, {"deep": True}])]}
    text, _ = canvas.update_notes(doc, [{"id": "a", "text": "new"}])
    assert text["nodes"][0] == {**doc["nodes"][0], "text": "new\n#x #y"}
    tags, _ = canvas.update_notes(doc, [{"id": "a", "clusters": ["Z"]}])
    assert tags["nodes"][0] == {**doc["nodes"][0], "text": "old\n#z"}
    none, _ = canvas.update_notes(doc, [{"id": "a", "clusters": []}])
    assert none["nodes"][0]["text"] == "old"


def test_an_update_that_changes_nothing_returns_the_same_canvas():
    doc = fixture("example")
    same, _ = canvas.update_notes(doc, [{"id": "api", "text": "## Read API\nServes `/graph/api/*` from D1.", "clusters": ["auth"]}])
    assert same is doc


def test_tags_written_at_the_end_of_the_text_become_clusters_so_a_note_reads_back_as_written():
    after, added, _ = canvas.add_notes({}, [{"text": "body\n#a\n#b #c", "clusters": ["d"]}])
    assert added[0]["text"] == "body" and added[0]["clusters"] == ["d", "a", "b", "c"]
    assert canvas.plain(after)["notes"] == [{"id": added[0]["id"], "text": "body", "clusters": ["a", "b", "c", "d"]}]


def test_a_cluster_that_comes_from_a_group_box_cannot_be_removed_and_says_so():
    doc = fixture("example")
    after, notices = canvas.update_notes(doc, [{"id": "api", "clusters": []}])
    assert by_id(after, "api")["text"] == "## Read API\nServes `/graph/api/*` from D1."
    assert len(notices) == 1 and "'api' is still in cluster 'backend'" in notices[0]


def test_removing_a_note_removes_its_links_and_nothing_else():
    doc = fixture("example")
    after, dropped = canvas.remove_cards(doc, ["api"])
    assert sorted(dropped) == ["e3", "e4", "e5"]
    assert [e["id"] for e in after["edges"]] == ["e1", "e2"]
    assert "api" not in [n["id"] for n in after["nodes"]]


def test_links_carry_a_label_and_a_direction_and_get_no_sides():
    doc = {"nodes": [card("a", 0, 0), card("b", 400, 0)], "edges": []}
    after, added = canvas.add_links(doc, [
        {"from_note": "a", "to_note": "b", "label": "leads to"},
        {"from_note": "b", "to_note": "a", "direction": "both"},
        {"from_note": "a", "to_note": "b", "direction": "none"},
    ])
    one, two, three = after["edges"]
    assert one == {"id": one["id"], "fromNode": "a", "toNode": "b", "label": "leads to"}
    assert two == {"id": two["id"], "fromNode": "b", "toNode": "a", "fromEnd": "arrow"}
    assert three == {"id": three["id"], "fromNode": "a", "toNode": "b", "toEnd": "none"}
    assert [link["direction"] for link in canvas.plain(after)["links"]] == ["forward", "both", "none"]
    assert added[0] == {"id": one["id"], "from_note": "a", "to_note": "b", "direction": "forward", "label": "leads to"}


def test_new_ids_are_sixteen_hex_digits_and_never_repeat_one_in_use():
    doc = {"nodes": [card("a", 0, 0)], "edges": [{"id": "taken", "fromNode": "a", "toNode": "a"}]}
    offered = iter(["a", "taken", "fresh"])
    assert canvas.new_id(doc, lambda: next(offered)) == "fresh"
    after, _, _ = canvas.add_notes(doc, [{"text": "n"}, {"text": "m"}])
    ids = [n["id"] for n in after["nodes"][1:]]
    assert all(len(i) == 16 and int(i, 16) >= 0 for i in ids) and len(set(ids)) == 2


@pytest.mark.parametrize("edit, message", [
    (lambda d: canvas.update_notes(d, [{"id": "nope", "text": "x"}]), "'nope' names no note"),
    (lambda d: canvas.update_notes(d, [{"id": "grp-backend", "text": "x"}]), "'grp-backend' is not a note"),
    (lambda d: canvas.remove_cards(d, ["api", "nope"]), "'nope' names no note"),
    (lambda d: canvas.add_links(d, [{"from_note": "api", "to_note": "nope"}]), "to_note 'nope' names no note"),
    (lambda d: canvas.add_links(d, [{"from_note": "api", "to_note": "api"}]), "two different notes"),
    (lambda d: canvas.remove_links(d, ["nope"]), "'nope' names no link"),
])
def test_an_edit_that_names_something_missing_is_refused_with_the_reason(edit, message):
    with pytest.raises(EditError, match=message):
        edit(fixture("example"))
