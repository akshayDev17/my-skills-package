"""Web-page cards made, and web-page and file cards edited, exactly as the web app's
"+ Link" button and its URL and File fields write them."""

from __future__ import annotations

import json

import pytest
from fastmcp.exceptions import ToolError

from graph_notes_mcp import canvas
from graph_notes_mcp.canvas import EditError
from support import REPO, App



def card(card_id: str, x: int = 0, y: int = 0, **more) -> dict:
    return {"id": card_id, "type": "text", "text": "note", "x": x, "y": y, "width": 260, "height": 120, **more}


PAGE = {"id": "w", "type": "link", "url": "https://example.com", "x": 600, "y": 0, "width": 400, "height": 240, "color": "5"}
FILE = {"id": "f", "type": "file", "file": "notes/a.md", "subpath": "#heading", "x": 0, "y": 400, "width": 300, "height": 200}


def test_a_new_web_page_card_is_the_node_the_apps_link_button_makes():
    # what "+ Link" then the URL field saved by hand: fixtures/web-app-hand-made.canvas (replayed in full below)
    after, added, _ = canvas.add_notes({"nodes": [card("a")]}, [{"url": " https://example.com/page ", "near": "a", "key": "page"}])
    page = after["nodes"][-1]
    assert list(page) == ["type", "url", "width", "height", "id", "x", "y"]
    assert page == {"type": "link", "url": "https://example.com/page", "width": 400, "height": 240, "id": page["id"],
                    "x": page["x"], "y": page["y"]}
    assert added == [{"id": page["id"], "key": "page", "kind": "web page", "url": "https://example.com/page"}]
    assert canvas.plain(after)["other_cards"] == [{"id": page["id"], "kind": "web page", "url": "https://example.com/page", "clusters": []}]
    assert canvas._apart(page, after["nodes"][0], canvas.GAP)


def test_web_page_cards_arrive_with_their_links_and_are_laid_out_with_the_notes():
    after, added, linked = canvas.add_notes({}, [{"text": "Read this", "key": "n"}, {"url": "https://example.com", "key": "w"}],
                                            [{"from_note": "n", "to_note": "w", "label": "source"}])
    note, page = after["nodes"]
    assert note["x"] + note["width"] < page["x"] and linked[0]["to_note"] == page["id"]  # left to right, along the link
    assert [a.get("kind", "note") for a in added] == ["note", "web page"]


@pytest.mark.parametrize("note, message", [
    ({"url": "https://example.com", "text": "both"}, "a url and nothing else"),
    ({"url": "https://example.com", "clusters": ["x"]}, "no clusters"),
    ({"url": "   "}, "needs its url"),
    ({}, "needs its text"),
])
def test_a_card_that_cannot_be_made_is_refused(note, message):
    with pytest.raises(EditError, match=message):
        canvas.add_notes({}, [note])


def test_a_web_pages_url_and_a_file_cards_path_change_in_place_and_nothing_else_does():
    doc = {"nodes": [card("a"), PAGE, FILE], "edges": [{"id": "e", "fromNode": "a", "toNode": "w"}]}
    after, notices = canvas.update_notes(doc, [{"id": "w", "url": "https://example.org"}, {"id": "f", "file": "ideas/b.md"}])
    assert after["nodes"][1] == {**PAGE, "url": "https://example.org"} and list(after["nodes"][1]) == list(PAGE)
    assert after["nodes"][2] == {**FILE, "file": "ideas/b.md"} and list(after["nodes"][2]) == list(FILE)
    assert after["nodes"][0] is doc["nodes"][0] and after["edges"] is doc["edges"] and notices == []
    same, _ = canvas.update_notes(doc, [{"id": "w", "url": "https://example.com"}])
    assert same is doc


@pytest.mark.parametrize("change, message", [
    ({"id": "w", "text": "x"}, "'w' is not a note but a web-page card .* no text"),
    ({"id": "w", "file": "a.md"}, "no file"),
    ({"id": "f", "url": "https://x.org"}, "'f' is not a note but a file card .* no url"),
    ({"id": "f", "clusters": ["x"]}, "no clusters"),
    ({"id": "a", "url": "https://x.org"}, "'a' is a note .* no url"),
    ({"id": "w", "url": ""}, "needs its url"),
    ({"id": "f", "file": "  "}, "needs its path"),
])
def test_a_card_change_that_does_not_fit_the_card_is_refused(change, message):
    with pytest.raises(EditError, match=message):
        canvas.update_notes({"nodes": [card("a"), PAGE, FILE]}, [change])


def test_through_the_tools_a_page_is_made_and_both_kinds_of_card_edited_one_version_each():
    app = App()
    app.edge.seed("plan", {"nodes": [card("a"), FILE]})
    made = app.call("add_notes", slug="plan", version=1, notes=[{"url": "https://example.com", "key": "w"}],
                    links=[{"from_note": "a", "to_note": "w"}])
    page = made["added"][0]
    assert made["version"] == 2 and page["kind"] == "web page" and made["links_added"][0]["to_note"] == page["id"]
    changed = app.call("update_notes", slug="plan", version=2, notes=[{"id": page["id"], "url": "https://example.org"},
                                                                       {"id": "f", "file": "ideas/b.md"}])
    assert changed["version"] == 3
    stored = {n["id"]: n for n in app.edge.canvas("plan")["nodes"]}
    assert stored[page["id"]]["url"] == "https://example.org" and stored["f"] == {**FILE, "file": "ideas/b.md"}
    assert {c["id"]: c.get("url") or c.get("file") for c in app.call("read_graph", slug="plan")["other_cards"]} == {
        "f": "ideas/b.md", page["id"]: "https://example.org"}
    with pytest.raises(ToolError, match="is not a note but a file card"):
        app.call("update_notes", slug="plan", version=3, notes=[{"id": "f", "text": "x"}])


def test_the_whole_graph_made_by_hand_in_the_web_app_is_what_the_server_writes_for_the_same_edits(monkeypatch):
    """test_layout.py replays the boxes, moves and resizes made by hand; here the web-page
    card made by hand among them ("+ Link", then its URL) comes too, so the whole canvas the
    web app saved comes out of the server key for key."""
    from test_layout import as_stored, hand_made_boxes_and_moves

    def page(doc: dict, ids: list) -> dict:
        hand = next(n for n in json.loads(HAND.read_text(encoding="utf-8"))["nodes"] if n["type"] == "link")
        ids.insert(0, hand["id"])
        doc, _, _ = canvas.add_notes(doc, [{"url": hand["url"]}])
        doc, _, _ = canvas.move_cards(doc, [{"id": hand["id"], "x": hand["x"], "y": hand["y"]}])
        return doc

    doc, hand = hand_made_boxes_and_moves(monkeypatch, page)
    assert as_stored(doc) == as_stored(hand)


HAND = REPO / "mcp-server" / "tests" / "fixtures" / "web-app-hand-made.canvas"
