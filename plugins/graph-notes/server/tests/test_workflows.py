"""Workflows: routes along a graph's links, as data in the canvas, the five tools, Follow's
step order (one depth level per step) and the edits that keep them tidy.

The graph here has the shape of the owner's W1 in classical-ml (cards, links and reading
order), with made-up text: P13 → P4, P5, L10; P4 → P3, L6; P3 → P2, L5, L12; L12 → L3;
P2 → S1; L6 → L5; L5 → L3; P5 → L3. Follow walks it as [P13] [P4 L10 P5] [P3 L6]
[L12 P2 L5] [S1 L3], and P5 → L3 skips from step 2 to step 5.
"""

from __future__ import annotations

import copy
import json

import pytest
from fastmcp.exceptions import ToolError

from graph_notes_mcp import canvas, workflows
from graph_notes_mcp.canvas import EditError
from support import OTHER, App

PLACES = {"p1": (-400, 0), "p13": (0, 0), "p4": (400, 0), "l10": (400, 300), "p5": (400, 600), "p3": (800, 0),
          "l6": (800, 300), "l12": (1200, -200), "p2": (1200, 0), "l5": (1200, 300), "s1": (1600, 0), "l3": (1600, 300)}
W1 = [("p13", "p4"), ("p13", "p5"), ("p13", "l10"), ("p4", "p3"), ("p4", "l6"), ("p3", "p2"), ("p3", "l5"),
      ("p3", "l12"), ("l12", "l3"), ("p2", "s1"), ("l6", "l5"), ("l5", "l3"), ("p5", "l3")]
LEVELS = [["p13"], ["p4", "l10", "p5"], ["p3", "l6"], ["l12", "p2", "l5"], ["s1", "l3"]]


def link_id(a: str, b: str) -> str:
    return f"e-{a}-{b}"


def graph() -> dict:
    """The W1-shaped graph, without workflows, plus a menu card P1 linked to P13."""
    nodes = [{"id": c, "type": "text", "text": f"## {c.upper()} · card {c}\nbody #not-a-tag here\n#pca",
              "x": x, "y": y, "width": 260, "height": 120} for c, (x, y) in PLACES.items()]
    edges = [{"id": link_id(a, b), "fromNode": a, "toNode": b} for a, b in W1 + [("p1", "p13")]]
    return {"nodes": nodes, "edges": edges, "metadata": {"kept": True}}


def stored_w1(**more) -> dict:
    return {"id": "w1id", "name": "W1 · axes", "description": "What does PCA compute?", "start": "p13",
            "steps": [{"link": link_id(a, b), "from": a} for a, b in W1], **more}


def with_w1(**more) -> dict:
    return {**graph(), "workflows": [stored_w1(**more)]}


def ids(cards: list[dict]) -> list[str]:
    return [c["id"] for c in cards]


# ── Follow's order: one depth level per step ───────────────────────────────

def test_follow_walks_one_depth_level_per_step_in_reading_order():
    found = workflows.route(with_w1(), stored_w1())
    assert found["levels"] == LEVELS
    assert found["ends"] == ["l10", "s1", "l3"]
    assert found["warnings"] == [] and len(found["cards"]) == 11


def test_read_workflow_gives_each_step_its_cards_where_it_came_from_where_it_goes_and_its_links():
    doc = with_w1()
    route = workflows.directions(doc, "w1id")
    assert [ids(step["cards"]) for step in route["route"]] == LEVELS
    second = route["route"][1]
    assert second["step"] == 2 and ids(second["came_from"]) == ["p13"]
    # P5 → L3 skips levels: L3 is next from step 2, but is reached at step 5
    assert second["next"] == [
        {"id": "p3", "title": "P3 · card p3", "step": 3}, {"id": "l6", "title": "L6 · card l6", "step": 3},
        {"id": "l3", "title": "L3 · card l3", "step": 5, "reached_later": True}]
    assert second["links_in"] == [link_id("p13", "p4"), link_id("p13", "p5"), link_id("p13", "l10")]
    assert sorted(second["links_out"]) == sorted([link_id("p4", "p3"), link_id("p4", "l6"), link_id("p5", "l3")])
    assert [c for c in second["cards"] if c.get("end")] == [{"id": "l10", "title": "L10 · card l10", "end": True}]
    last = route["route"][-1]
    assert ids(last["came_from"]) == ["p5", "l12", "p2", "l5"] and last["next"] == [] and last["links_out"] == []
    assert ids(route["ends"]) == ["l10", "s1", "l3"]
    assert route["workflow"]["follow_steps"] == 5 and route["workflow"]["cards"] == 11 and route["workflow"]["steps"] == 13


def test_the_directions_text_reads_step_by_step():
    text = workflows.directions(with_w1(), "w1id")["directions"]
    lines = text.split("\n")
    assert lines[0] == "W1 · axes: What does PCA compute?"
    assert "Step 1 of 5 (start): P13 · card p13." in lines
    assert "Step 2 of 5: P4 · card p4; L10 · card l10 (an end); P5 · card p5." in lines
    assert "  Next: P3 · card p3, L6 · card l6; and later L3 · card l3 (step 5)." in lines
    assert "  From: P5 · card p5, L12 · card l12, P2 · card p2, L5 · card l5." in lines
    assert lines[-1] == "Ends: L10 · card l10, S1 · card s1, L3 · card l3."


def test_a_route_names_the_workflows_its_cards_start_and_the_ones_that_come_after_it():
    doc = with_w1()
    doc["workflows"].append({"id": "w2id", "name": "W2 · scores", "start": "p4", "steps": [], "after": ["w1id"]})
    route = workflows.directions(doc, "w1id")
    assert route["route"][1]["cards"][0]["starts"] == [{"id": "w2id", "name": "W2 · scores"}]
    assert route["followed_by"] == [{"id": "w2id", "name": "W2 · scores"}]
    assert "P4 · card p4 (starts W2 · scores)" in route["directions"] and "After it: W2 · scores." in route["directions"]
    second = workflows.summary(doc, doc["workflows"][1])
    assert second["after"] == [{"id": "w1id", "name": "W1 · axes"}] and second["follow_steps"] == 1
    assert ids(second["ends"]) == ["p4"]


# ── lenient on references: warnings, never a refusal ───────────────────────

def test_a_dangling_step_start_or_prerequisite_is_a_warning_and_is_skipped():
    doc = graph()
    doc["edges"] = [e for e in doc["edges"] if e["id"] != link_id("p2", "s1")]
    flow = stored_w1(after=["gone"])
    flow["steps"].append({"link": link_id("p13", "p4"), "from": "p13"})  # twice
    flow["steps"].append({"link": link_id("l6", "l5"), "from": "s1"})    # from neither end
    found = workflows.route({**doc, "workflows": [flow]}, flow)
    assert found["levels"] == [["p13"], ["p4", "l10", "p5"], ["p3", "l6"], ["l12", "p2", "l5"], ["l3"]]
    assert any(f"'{link_id('p2', 's1')}' is no longer in the graph" in w for w in found["warnings"])
    assert any("is listed twice" in w for w in found["warnings"])
    assert any("which is not one of the link's two cards" in w for w in found["warnings"])
    assert any("comes after workflow 'gone'" in w for w in found["warnings"])

    no_start = {**stored_w1(), "start": None}
    assert workflows.route({**doc, "workflows": [no_start]}, no_start)["levels"] is None
    assert "it has no start card" in workflows.route(graph(), no_start)["warnings"][0]
    gone = {**stored_w1(), "start": "deleted"}
    summary = workflows.summary(graph(), gone)
    assert summary["start"] is None and summary["cards"] == 0 and summary["follow_steps"] == 0
    assert "'deleted' is no longer in the graph" in summary["warnings"][0]
    assert "There is no route to follow" in workflows.directions({**graph(), "workflows": [gone]}, "w1id")["directions"]


def test_steps_cut_off_from_the_start_stay_and_are_flagged():
    flow = stored_w1()
    flow["steps"] = [s for s in flow["steps"] if s["link"] != link_id("p13", "p4")]
    found = workflows.route({**graph(), "workflows": [flow]}, flow)
    assert "p4" not in found["cards"]
    assert any(w.startswith("9 steps can't be reached from the start card P13 · card p13: P4 · card p4 → P3 · card p3")
               for w in found["warnings"])


def test_a_step_whose_arrow_was_flipped_is_kept_and_warned_about():
    doc = with_w1()
    doc["edges"] = [{**e, "toEnd": "none", "fromEnd": "arrow"} if e["id"] == link_id("p2", "s1") else e for e in doc["edges"]]
    found = workflows.route(doc, doc["workflows"][0])
    assert found["levels"] == LEVELS
    assert found["warnings"] == ["the step P2 · card p2 → S1 · card s1 now goes against its link's arrow"]


@pytest.mark.parametrize("loop", [("l3", "p4"), ("l3", "l3")])
def test_a_route_that_loops_is_reported_and_given_no_order_never_walked_forever(loop):
    doc = with_w1()
    doc["edges"].append({"id": "back", "fromNode": loop[0], "toNode": loop[1]})
    doc["workflows"][0]["steps"].append({"link": "back", "from": loop[0]})
    found = workflows.route(doc, doc["workflows"][0])
    assert found["levels"] is None
    assert any("loops back" in w and workflows.LOOPS_ISSUE in w and "not supported yet" in w for w in found["warnings"])
    route = workflows.directions(doc, "w1id")
    assert route["route"] == [] and "There is no route to follow" in route["directions"]
    assert workflows.summary(doc, doc["workflows"][0])["follow_steps"] == 0


# ── making, changing and removing them ─────────────────────────────────────

def test_add_makes_the_stored_shape_from_steps_named_by_notes_or_by_link():
    before = graph()
    pristine = copy.deepcopy(before)
    steps = [{"from_note": a, "to_note": b} for a, b in W1[:-1]] + [{"link": link_id("p5", "l3")}]
    after, added = workflows.add(before, [
        {"key": "w2", "name": " W2 · scores ", "start": "p4", "steps": [{"from_note": "p4", "to_note": "p3"}],
         "after": ["w1"]},
        {"key": "w1", "name": "W1 · axes", "description": "What does PCA compute?", "start": "p13", "steps": steps},
    ])
    assert before == pristine and after["nodes"] is before["nodes"] and after["edges"] is before["edges"]
    assert after["metadata"] == {"kept": True}
    second, first = after["workflows"]
    assert list(first) == ["id", "name", "description", "start", "steps"]
    assert first == {"id": first["id"], "name": "W1 · axes", "description": "What does PCA compute?", "start": "p13",
                     "steps": [{"link": link_id(a, b), "from": a} for a, b in W1]}
    assert second == {"id": second["id"], "name": "W2 · scores", "start": "p4",
                      "steps": [{"link": link_id("p4", "p3"), "from": "p4"}], "after": [first["id"]]}
    assert len(first["id"]) == 16 and int(first["id"], 16) >= 0 and first["id"] != second["id"]
    assert [a["key"] for a in added] == ["w2", "w1"] and added[1]["cards"] == 11 and added[1]["steps"] == 13
    assert ids(added[1]["ends"]) == ["l10", "s1", "l3"]


def test_steps_may_come_in_any_order_and_two_way_links_need_from_note():
    doc = graph()
    doc["edges"].append({"id": "both", "fromNode": "s1", "toNode": "l3", "fromEnd": "arrow"})
    steps = [{"from_note": a, "to_note": b} for a, b in reversed(W1)] + [{"link": "both", "from_note": "l3"}]
    after, _ = workflows.add(doc, [{"name": "W", "start": "p13", "steps": steps}])
    assert after["workflows"][0]["steps"][-1] == {"link": "both", "from": "l3"}
    with pytest.raises(EditError, match="can be walked either way: say which way with from_note"):
        workflows.add(doc, [{"name": "W", "start": "s1", "steps": [{"link": "both"}]}])
    walked_back, _ = workflows.add(doc, [{"name": "W", "start": "l3", "steps": [{"from_note": "l3", "to_note": "s1"}]}])
    assert walked_back["workflows"][0]["steps"] == [{"link": "both", "from": "l3"}]


@pytest.mark.parametrize("spec, message", [
    ({"name": "W", "start": "p13", "steps": [{"from_note": "p13", "to_note": "s1"}]}, "is not a link in this graph"),
    ({"name": "W", "start": "p4", "steps": [{"from_note": "p4", "to_note": "p13"}]}, "points the other way"),
    ({"name": "W", "start": "p4", "steps": [{"link": link_id("p13", "p4"), "from_note": "p4"}]}, "points the other way"),
    ({"name": "W", "start": "p13", "steps": [{"from_note": "p3", "to_note": "p2"}]}, "can't be reached from the start card P13"),
    ({"name": "W", "start": "p13", "steps": [{"link": link_id("p13", "p4")}, {"link": link_id("p13", "p4")}]}, "is already in W"),
    ({"name": "W", "start": "p13", "steps": [{"link": "nope"}]}, "'nope' names no link"),
    ({"name": "W", "start": "p13", "steps": [{"from_note": "p13"}]}, "a step names its link"),
    ({"name": "W", "start": "p13", "steps": [{"from_note": "nope", "to_note": "p4"}]}, "from_note 'nope' names no note"),
    ({"name": "W", "start": "nope"}, "start 'nope' names no note"),
    ({"name": "W", "start": "box"}, "is a group box"),
    ({"name": "  ", "start": "p13"}, "a workflow needs a name"),
    ({"name": "W", "start": "p13", "after": ["nope"]}, "after names 'nope', which is no workflow"),
    ({"name": "W", "start": "p13", "after": ["me"], "key": "me"}, "cannot come after itself"),
    ({"name": "W", "start": "p13", "key": "p13"}, "key 'p13' must be unique"),
])
def test_add_refuses_what_is_not_a_route_with_what_to_fix(spec, message):
    doc = graph()
    doc["nodes"].append({"id": "box", "type": "group", "label": "Box", "x": 0, "y": 900, "width": 400, "height": 300})
    with pytest.raises(EditError, match=message):
        workflows.add(doc, [spec])


def test_two_links_between_the_same_notes_need_the_link_id():
    doc = graph()
    doc["edges"].append({"id": "again", "fromNode": "p13", "toNode": "p4"})
    with pytest.raises(EditError, match="could be any of the links"):
        workflows.add(doc, [{"name": "W", "start": "p13", "steps": [{"from_note": "p13", "to_note": "p4"}]}])


def test_new_ids_are_never_a_workflows_id():
    doc = {**graph(), "workflows": [{"id": "taken"}]}
    offered = iter(["p13", link_id("p13", "p4"), "taken", "fresh"])
    assert canvas.new_id(doc, lambda: next(offered)) == "fresh"


def test_update_changes_a_workflow_in_place_and_keeps_what_is_left_out():
    doc = {**with_w1(extra={"kept": 1}), "workflows": [
        stored_w1(extra={"kept": 1}), {"id": "w2id", "name": "W2", "start": "p4", "steps": []},
        {"id": "w3id", "name": "W3", "start": "p5", "steps": []}]}
    after, updated = workflows.update(doc, [
        {"id": "w1id", "name": "W1 · the axes", "description": "",
         "remove_steps": [{"link": link_id("p2", "s1")}, {"from_note": "p5", "to_note": "l3"}]},
        {"id": "w2id", "start": "p3", "add_steps": [{"from_note": "p3", "to_note": "p2"}, {"from_note": "p2", "to_note": "s1"}],
         "after": ["w1id", "w1id"]},
        {"id": "w3id", "move_to": 1},
    ])
    w3, w1, w2 = after["workflows"]
    assert w3["id"] == "w3id"
    assert list(w1) == ["id", "name", "start", "steps", "extra"] and w1["extra"] == {"kept": 1} and w1["name"] == "W1 · the axes"
    assert len(w1["steps"]) == 11 and {"link": link_id("p2", "s1"), "from": "p2"} not in w1["steps"]
    assert w2 == {"id": "w2id", "name": "W2", "start": "p3", "after": ["w1id"],
                  "steps": [{"link": link_id("p3", "p2"), "from": "p3"}, {"link": link_id("p2", "s1"), "from": "p2"}]}
    assert ids(updated) == ["w1id", "w2id", "w3id"] and updated[1]["follow_steps"] == 3
    cleared, _ = workflows.update(after, [{"id": "w2id", "after": []}])
    assert "after" not in workflows.find(cleared, "w2id")
    same, _ = workflows.update(doc, [{"id": "w2id", "move_to": 3}])
    assert [w["id"] for w in same["workflows"]] == ["w1id", "w3id", "w2id"]


@pytest.mark.parametrize("change, message", [
    ({"id": "nope"}, "'nope' names no workflow in this graph; its workflows are: 'w1id' \\(W1 · axes\\)"),
    ({"id": "w1id", "remove_steps": [{"link": link_id("p1", "p13")}]}, "is not a step of W1 · axes"),
    ({"id": "w1id", "add_steps": [{"link": link_id("p13", "p4")}]}, "is already in W1 · axes"),
    ({"id": "w1id", "add_steps": [{"link": link_id("p1", "p13")}]}, "can't be reached from the start card P13"),
    ({"id": "w1id", "after": ["w1id"]}, "cannot come after itself"),
    ({"id": "w1id", "move_to": 2}, "move_to must be from 1 \\(first\\) to 1 \\(last\\)"),
    ({"id": "w1id", "name": ""}, "a workflow needs a name"),
])
def test_update_refuses_what_it_cannot_do_with_the_reason(change, message):
    with pytest.raises(EditError, match=message):
        workflows.update(with_w1(), [change])


def test_a_new_start_can_take_the_steps_that_lead_on_from_it():
    after, _ = workflows.update(with_w1(), [{"id": "w1id", "start": "p1", "add_steps": [{"link": link_id("p1", "p13")}]}])
    assert workflows.route(after, after["workflows"][0])["levels"][0:2] == [["p1"], ["p13"]]


def test_remove_takes_a_workflow_out_of_the_others_after_lists():
    doc = {**graph(), "workflows": [stored_w1(), {"id": "w2id", "name": "W2", "start": "p4", "steps": [], "after": ["w1id"]},
                                    {"id": "w3id", "name": "W3", "start": "p5", "steps": [], "after": ["w2id", "w1id"]}]}
    after, notices = workflows.remove(doc, ["w1id"])
    assert after["workflows"] == [{"id": "w2id", "name": "W2", "start": "p4", "steps": []},
                                  {"id": "w3id", "name": "W3", "start": "p5", "steps": [], "after": ["w2id"]}]
    assert notices == ["W2 no longer comes after W1 · axes, which was removed",
                       "W3 no longer comes after W1 · axes, which was removed"]
    assert after["nodes"] is doc["nodes"] and after["edges"] is doc["edges"]
    with pytest.raises(EditError, match="names no workflow"):
        workflows.remove(doc, ["nope"])


def test_removing_links_or_cards_takes_out_their_steps_and_a_removed_start_in_the_same_edit():
    doc = with_w1()
    after, notices = workflows.pruned(doc, canvas.remove_links(doc, [link_id("p3", "p2")]), [link_id("p3", "p2")])
    assert {"link": link_id("p3", "p2"), "from": "p3"} not in after["workflows"][0]["steps"]
    assert notices == ["W1 · axes lost the step P3 · card p3 → P2 · card p2"]
    gone, links = canvas.remove_cards(doc, ["p13"])
    after, notices = workflows.pruned(doc, gone, links, ["p13"])
    flow = after["workflows"][0]
    assert flow["start"] is None and "start" in flow and len(flow["steps"]) == 10
    assert notices[-1] == ("W1 · axes lost its start card P13 · card p13: it has no start until one is given "
                           "(update_workflows with start)")
    assert len(notices) == 4  # and the three steps from P13
    untouched = canvas.remove_links(doc, [link_id("p1", "p13")])
    assert workflows.pruned(doc, untouched, [link_id("p1", "p13")]) == (untouched, [])


# ── workflow tags: a field of their own, never clusters ────────────────────

def test_workflow_tags_are_their_own_field_never_clusters_or_text():
    doc = graph()
    after, added, _ = canvas.add_notes(doc, [{"text": "new", "clusters": ["a"], "workflow_tags": ["Check Proof", "draft", "draft"]},
                                             {"url": "https://example.com", "workflow_tags": ["draft"]}])
    note, page = after["nodes"][-2:]
    assert note["workflowTags"] == ["check-proof", "draft"] and list(note)[-1] == "workflowTags"
    assert note["text"] == "new\n#a" and page["workflowTags"] == ["draft"]
    assert added[0]["workflow_tags"] == ["check-proof", "draft"] and added[1]["workflow_tags"] == ["draft"]
    plain = canvas.plain(after)
    assert plain["notes"][-1]["workflow_tags"] == ["check-proof", "draft"] and plain["notes"][-1]["clusters"] == ["a"]
    assert plain["other_cards"][0]["workflow_tags"] == ["draft"]
    assert all("workflow_tags" not in n for n in plain["notes"][:-1])
    assert not any("draft" in c["name"] or "check" in c["name"] for c in plain["clusters"])

    changed, _ = canvas.update_notes(after, [{"id": "p13", "workflow_tags": ["draft"]}, {"id": page["id"], "workflow_tags": []}])
    p13 = next(n for n in changed["nodes"] if n["id"] == "p13")
    assert p13 == {**next(n for n in doc["nodes"] if n["id"] == "p13"), "workflowTags": ["draft"]}
    assert "workflowTags" not in changed["nodes"][-1]
    same, _ = canvas.update_notes(changed, [{"id": "p13", "workflow_tags": ["Draft"]}, {"id": "p4", "workflow_tags": []}])
    assert same is changed
    both, _ = canvas.update_notes(changed, [{"id": "p13", "text": "rewritten", "workflow_tags": ["x"]}])
    assert next(n for n in both["nodes"] if n["id"] == "p13") == {**p13, "text": "rewritten\n#pca", "workflowTags": ["x"]}
    box = {"id": "box", "type": "group", "label": "Box", "x": 0, "y": 900, "width": 400, "height": 300}
    with pytest.raises(EditError, match="only notes and other cards have workflow tags"):
        canvas.update_notes({**doc, "nodes": [*doc["nodes"], box]}, [{"id": "box", "workflow_tags": ["x"]}])


# ── the tools, through a real MCP session and the real API ─────────────────

@pytest.fixture
def app() -> App:
    made = App()
    made.edge.seed("pca", graph())
    return made


def w1_steps() -> list[dict]:
    return [{"from_note": a, "to_note": b} for a, b in W1]


def test_a_workflow_made_followed_changed_pruned_and_removed_through_the_tools(app):
    made = app.call("add_workflows", slug="pca", version=1, workflows=[
        {"key": "w1", "name": "W1 · axes", "description": "What does PCA compute?", "start": "p13", "steps": w1_steps()},
        {"key": "w2", "name": "W2 · scores", "start": "p4", "steps": [{"link": link_id("p4", "p3")}], "after": ["w1"]}])
    assert made["version"] == 2 and [a["key"] for a in made["added"]] == ["w1", "w2"]
    w1, w2 = (a["id"] for a in made["added"])
    stored = app.edge.canvas("pca")
    assert stored["workflows"][0] == {"id": w1, "name": "W1 · axes", "description": "What does PCA compute?",
                                      "start": "p13", "steps": [{"link": link_id(a, b), "from": a} for a, b in W1]}
    assert stored["workflows"][1]["after"] == [w1] and stored["metadata"] == {"kept": True}

    listed = app.call("list_workflows", slug="pca")
    assert listed["version"] == 2 and [w["name"] for w in listed["workflows"]] == ["W1 · axes", "W2 · scores"]
    assert listed["workflows"][0]["start"] == {"id": "p13", "title": "P13 · card p13"}
    assert listed["workflows"][0]["follow_steps"] == 5 and "warnings" not in listed["workflows"][0]
    assert app.call("read_graph", slug="pca")["workflows"] == listed["workflows"]

    route = app.call("read_workflow", slug="pca", workflow=w1)
    assert [ids(s["cards"]) for s in route["route"]] == LEVELS
    assert route["followed_by"] == [{"id": w2, "name": "W2 · scores"}] and "Step 5 of 5" in route["directions"]

    updated = app.call("update_workflows", slug="pca", version=2, workflows=[
        {"id": w1, "name": "W1 · the axes", "remove_steps": [{"from_note": "p5", "to_note": "l3"}]}, {"id": w2, "move_to": 1}])
    assert updated["version"] == 3 and updated["updated"][0]["steps"] == 12
    assert [w["id"] for w in app.edge.canvas("pca")["workflows"]] == [w2, w1]

    unlinked = app.call("unlink_notes", slug="pca", version=3, link_ids=[link_id("p3", "p2")])
    assert unlinked["notices"] == ["W1 · the axes lost the step P3 · card p3 → P2 · card p2"]
    assert len(app.edge.canvas("pca")["workflows"][1]["steps"]) == 11
    removed = app.call("remove_notes", slug="pca", version=4, note_ids=["p4"])
    assert "W2 · scores lost its start card P4 · card p4: it has no start until one is given (update_workflows with start)" \
        in removed["notices"]
    assert "W1 · the axes lost the step P13 · card p13 → P4 · card p4" in removed["notices"]
    after_removal = app.call("list_workflows", slug="pca")["workflows"]
    assert after_removal[0]["start"] is None and "it has no start card" in after_removal[0]["warnings"][0]

    gone = app.call("remove_workflows", slug="pca", version=5, workflow_ids=[w1])
    assert gone["notices"] == ["W2 · scores no longer comes after W1 · the axes, which was removed"]
    assert app.edge.canvas("pca")["workflows"] == [{"id": w2, "name": "W2 · scores", "start": None, "steps": []}]
    old = app.call("read_graph", slug="pca", at_version=2)
    assert [w["name"] for w in old["workflows"]] == ["W1 · axes", "W2 · scores"]
    app.call("restore_version", slug="pca", version=6, restore_to=2)
    assert len(app.edge.canvas("pca")["workflows"]) == 2


def test_each_workflow_change_sends_history_a_short_note_saying_what_changed(app):
    def sent_note() -> str | None:
        body = [body for method, _, _, body in app.edge.requests if method == "PUT"][-1]
        return json.loads(body).get("note")

    two = app.call("add_workflows", slug="pca", version=1, workflows=[
        {"name": "W1 · axes", "start": "p13", "steps": w1_steps()[:1]}, {"name": "W2 · scores", "start": "p4"}])
    assert sent_note() == two["note"] == "2 workflows added"
    w1, w2 = (a["id"] for a in two["added"])
    app.call("update_workflows", slug="pca", version=2, workflows=[{"id": w1, "description": "What does PCA compute?"}])
    assert sent_note() == "W1 · axes updated"
    long = "W3 · " + "why " * 40
    app.call("add_workflows", slug="pca", version=3, workflows=[{"name": long, "start": "p13"}])
    assert sent_note().endswith("… added") and len(sent_note()) <= workflows.TITLE_LENGTH + len(" added")
    app.call("remove_workflows", slug="pca", version=4, workflow_ids=[w2, w2])
    assert sent_note() == "W2 · scores removed"
    app.call("unlink_notes", slug="pca", version=5, link_ids=[link_id("p1", "p13")])
    assert sent_note() is None
    assert app.call("read_graph", slug="pca")["version"] == 6


def test_a_workflow_change_from_an_old_version_saves_nothing_and_shows_the_workflows(app):
    app.call("add_workflows", slug="pca", version=1, workflows=[{"name": "W", "start": "p13", "steps": w1_steps()[:1]}])
    with pytest.raises(ToolError, match="Nothing was saved") as refused:
        app.call("remove_workflows", slug="pca", version=1, workflow_ids=["anything"])
    assert '"workflows": [{' in str(refused.value)
    with pytest.raises(ToolError, match="is not a link in this graph"):
        app.call("add_workflows", slug="pca", version=2, workflows=[
            {"name": "W", "start": "p13", "steps": [{"from_note": "p13", "to_note": "s1"}]}])
    assert app.edge.canvas("pca")["workflows"][0]["name"] == "W"


def test_someone_elses_public_workflows_can_be_followed_but_not_changed(app):
    app.edge.seed("theirs", with_w1(), owner=OTHER, visibility="public")
    assert [w["name"] for w in app.call("list_workflows", slug="theirs")["workflows"]] == ["W1 · axes"]
    assert [ids(s["cards"]) for s in app.call("read_workflow", slug="theirs", workflow="w1id")["route"]] == LEVELS
    with pytest.raises(ToolError, match="someone else's public graph"):
        app.call("remove_workflows", slug="theirs", version=2, workflow_ids=["w1id"])


def test_workflow_tags_through_the_tools(app):
    added = app.call("add_notes", slug="pca", version=1, notes=[{"text": "x", "workflow_tags": ["Draft"]}])
    assert added["added"][0]["workflow_tags"] == ["draft"]
    app.call("update_notes", slug="pca", version=2, notes=[{"id": "p13", "workflow_tags": ["check-proof"]}])
    read = app.call("read_graph", slug="pca")
    assert next(n for n in read["notes"] if n["id"] == "p13")["workflow_tags"] == ["check-proof"]
    assert "check-proof" not in json.dumps(read["clusters"])
    assert "#check-proof" not in next(n for n in app.edge.canvas("pca")["nodes"] if n["id"] == "p13")["text"]


def test_a_graph_without_workflows_reads_as_before(app):
    assert "workflows" not in app.call("read_graph", slug="pca")
    assert app.call("list_workflows", slug="pca")["workflows"] == []
    with pytest.raises(ToolError, match="its workflows are: none"):
        app.call("read_workflow", slug="pca", workflow="nope")


def test_the_guide_explains_workflows(app):
    guide = app.call("about_graphs")
    for words in ("## Workflows", "Never encode a workflow in clusters", "read_workflow", "Workflow tags", "ask first"):
        assert words in guide
