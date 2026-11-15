"""A change made from an outdated version saves nothing: the tool says the graph changed
and returns its current content. Newer work is never overwritten, whether the graph
moved on before the tool looked (the version it was given is old) or between its read
and its save (the app's own check, HTTP 409)."""

from __future__ import annotations

import json

import pytest
from fastmcp.exceptions import ToolError

from support import App

THEIR_NOTE = {"id": "theirs", "type": "text", "text": "Typed in the web app", "x": 0, "y": 0, "width": 260, "height": 120}


@pytest.fixture
def app() -> App:
    app = App()
    app.call("create_graph", title="Plan")
    app.call("add_notes", slug="plan", version=1, notes=[{"text": "Mine"}])
    return app


def their_save(app: App, base: int) -> None:
    """Another tab saves a note of its own on top of version ``base``."""
    canvas = app.edge.canvas("plan")
    app.edge.as_user("PUT", "/graphs/plan", {"base": base, "canvas": {**canvas, "nodes": [*canvas["nodes"], THEIR_NOTE]}})


def current_graph_in(message: str) -> dict:
    return json.loads(message.split("\n", 1)[1])


EDITS = [
    ("add_notes", lambda mine: {"notes": [{"text": "From the model"}]}),
    ("update_notes", lambda mine: {"notes": [{"id": mine, "text": "Overwritten?"}]}),
    ("remove_notes", lambda mine: {"note_ids": [mine]}),
    ("link_notes", lambda mine: {"links": [{"from_note": mine, "to_note": mine}]}),
    ("unlink_notes", lambda mine: {"link_ids": ["whatever"]}),
    ("rename_graph", lambda mine: {"title": "Renamed by the model"}),
    ("set_visibility", lambda mine: {"visibility": "public"}),
    ("restore_version", lambda mine: {"restore_to": 1}),
]


def names(edits: list) -> list[str]:
    return [name for name, _ in edits]


CANVAS_EDITS, GRAPH_EDITS = EDITS[:5], EDITS[5:]


@pytest.mark.parametrize("tool, arguments", EDITS, ids=names(EDITS))
def test_a_change_from_an_old_version_saves_nothing_and_returns_the_current_graph(app, tool, arguments):
    mine = app.call("read_graph", slug="plan")["notes"][0]["id"]
    their_save(app, base=2)  # the person edits in the web app after the model's read
    before = app.edge.as_user("GET", "/graphs/plan")

    with pytest.raises(ToolError) as refused:
        app.call(tool, slug="plan", version=2, **arguments(mine))

    message = str(refused.value)
    assert message.startswith("Nothing was saved: 'plan' has changed since version 2 and is now at version 3.")
    now = current_graph_in(message)
    assert now["version"] == 3 and [n["text"] for n in now["notes"]] == ["Mine", "Typed in the web app"]
    assert app.edge.as_user("GET", "/graphs/plan") == before  # their work, untouched; no new version


@pytest.mark.parametrize("tool, arguments", CANVAS_EDITS, ids=names(CANVAS_EDITS))
def test_an_edit_to_notes_or_links_from_an_old_version_is_never_even_sent(app, tool, arguments):
    mine = app.call("read_graph", slug="plan")["notes"][0]["id"]
    their_save(app, base=2)
    sent = len(app.edge.requests)
    with pytest.raises(ToolError, match="Nothing was saved"):
        app.call(tool, slug="plan", version=2, **arguments(mine))
    assert {method for method, *_ in app.edge.requests[sent:]} == {"GET"}


@pytest.mark.parametrize("tool, arguments", GRAPH_EDITS, ids=names(GRAPH_EDITS))
def test_a_rename_publish_or_restore_from_an_old_version_is_refused_by_the_apps_own_check(app, tool, arguments):
    their_save(app, base=2)
    sent = len(app.edge.requests)
    with pytest.raises(ToolError, match="Nothing was saved"):
        app.call(tool, slug="plan", version=2, **arguments(None))
    write, reread = app.edge.requests[sent:]
    assert write[0] in ("PUT", "POST") and json.loads(write[3])["base"] == 2 and reread[0] == "GET"


RACED = CANVAS_EDITS[:3] + GRAPH_EDITS[:1]  # the tools that save with a PUT


@pytest.mark.parametrize("tool, arguments", RACED, ids=names(RACED))
def test_a_save_that_loses_the_race_is_refused_by_the_app_and_reported_the_same_way(app, tool, arguments):
    mine = app.call("read_graph", slug="plan")["notes"][0]["id"]
    # The graph is at version 2 when the tool reads it; the other tab saves just before the tool's own write.
    app.edge.before_put.append(lambda: their_save(app, base=2))

    with pytest.raises(ToolError) as refused:
        app.call(tool, slug="plan", version=2, **arguments(mine))

    now = current_graph_in(str(refused.value))
    assert now["version"] == 3 and [n["text"] for n in now["notes"]] == ["Mine", "Typed in the web app"]
    after = app.edge.as_user("GET", "/graphs/plan")
    assert after["version"]["n"] == 3 and after["title"] == "Plan"
    assert [n["text"] for n in after["canvas"]["nodes"]] == ["Mine", "Typed in the web app"]


def test_the_app_itself_answered_409_to_the_racing_save(app):
    app.edge.before_put.append(lambda: their_save(app, base=2))
    with pytest.raises(ToolError, match="Nothing was saved"):
        app.call("add_notes", slug="plan", version=2, notes=[{"text": "From the model"}])
    puts = [(method, url) for method, url, *_ in app.edge.requests if method == "PUT"]
    assert puts[-1] == ("PUT", "https://graph.test/graph/api/graphs/plan")
    assert json.loads(app.edge.requests[-2][3])["base"] == 2  # the save named the version it was made from
    assert app.edge.requests[-1][0] == "GET"                  # then the tool read the graph again


def test_making_the_change_again_from_the_current_version_works(app):
    their_save(app, base=2)
    with pytest.raises(ToolError) as refused:
        app.call("add_notes", slug="plan", version=2, notes=[{"text": "From the model"}])
    now = current_graph_in(str(refused.value))
    again = app.call("add_notes", slug="plan", version=now["version"], notes=[{"text": "From the model"}])
    assert again["version"] == 4
    assert [n["text"] for n in app.call("read_graph", slug="plan")["notes"]] == ["Mine", "Typed in the web app", "From the model"]


def test_a_version_from_the_future_is_refused_too(app):
    with pytest.raises(ToolError, match="has changed since version 9 and is now at version 2"):
        app.call("add_notes", slug="plan", version=9, notes=[{"text": "x"}])
