"""The real thing, end to end: a real MCP client session, the server over stdio as a
harness runs it, the saved cloudflared sign-in, and the live app.

Opt-in, never in CI:  GRAPH_NOTES_LIVE=1 uv run pytest tests/test_live.py -s

It touches only graphs it creates, named ``mcp-test-<time>...`` (``live_guard`` refuses
any other slug before a call is made), leaves them in the trash, and checks at the end
that every other graph of the account is exactly as it was. It needs a saved sign-in
and will not start one: run ``cloudflared access login <app>/signin`` first.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from graph_notes_mcp.server import APP_URL, sign_in_url
from live_guard import PREFIX, guarded

pytestmark = pytest.mark.skipif(os.environ.get("GRAPH_NOTES_LIVE") != "1", reason="live run: set GRAPH_NOTES_LIVE=1")

PROJECT = Path(__file__).resolve().parents[1]


def others(listed: dict, trash: dict) -> list:
    """Every graph that is not this test's: what must not change."""
    rows = [(g["slug"], g["version"], g["title"], g["visibility"], g.get("trashed_at")) for g in listed["mine"] + trash["trash"]]
    return sorted(row for row in rows if not row[0].startswith(PREFIX))


async def live_run(report) -> None:
    transport = StdioTransport(command=sys.executable, args=["-m", "graph_notes_mcp"], cwd=str(PROJECT),
                               env={"PATH": os.environ["PATH"], "HOME": os.environ["HOME"]})
    stamp = time.strftime("%Y%m%d-%H%M%S")
    slug, copy = f"{PREFIX}{stamp}", f"{PREFIX}{stamp}-copy"

    async with Client(transport) as mcp:
        async def call(tool: str, **arguments):
            result = await mcp.call_tool(tool, guarded(tool, arguments), raise_on_error=False)
            return result

        async def ok(tool: str, **arguments):
            result = await call(tool, **arguments)
            assert not result.is_error, f"{tool}: {result.content[0].text}"
            return result.data

        tools = {t.name for t in await mcp.list_tools()}
        assert "trash_graph" in tools and not any("forever" in name for name in tools)
        report(f"connected over stdio: {len(tools)} tools, protocol {mcp.protocol_version}")
        assert "what a graph is" in (await ok("about_graphs")).lower()

        before = others(await ok("list_graphs"), await ok("list_trash"))
        report(f"{len(before)} other graphs on the account (untouched by this run)")
        try:
            made = await ok("create_graph", title="MCP live test (safe to delete)", slug=slug)
            assert (made["slug"], made["version"], made["visibility"], made["mine"]) == (slug, 1, "private", True)
            report(f"create_graph: {slug} at version 1")

            added = await ok("add_notes", slug=slug, version=1, notes=[
                {"text": "## First\nMade by the MCP live test.", "clusters": ["Live Test"], "key": "first"},
                {"text": "Second", "clusters": ["live-test", "extra"], "key": "second"},
                {"text": "Third"},
            ], links=[{"from_note": "first", "to_note": "second", "label": "leads to"}])
            first, second, third = (n["id"] for n in added["added"])
            leads = added["links_added"][0]["id"]
            linked = await ok("link_notes", slug=slug, version=added["version"],
                              links=[{"from_note": second, "to_note": third, "direction": "both"}])
            both = linked["added"][0]["id"]
            graph = await ok("read_graph", slug=slug)
            assert graph["version"] == 3 and [n["id"] for n in graph["notes"]] == [first, second, third]
            assert graph["notes"][0] == {"id": first, "text": "## First\nMade by the MCP live test.", "clusters": ["live-test"]}
            assert graph["links"] == [
                {"id": leads, "from_note": first, "to_note": second, "direction": "forward", "label": "leads to"},
                {"id": both, "from_note": second, "to_note": third, "direction": "both"}]
            assert [c["name"] for c in graph["clusters"]] == ["extra", "live-test"]
            report("add_notes, link_notes, read_graph: 3 notes, 2 links, 2 clusters read back")

            updated = await ok("update_notes", slug=slug, version=3, notes=[{"id": third, "text": "Third, edited", "clusters": ["extra"]}])
            assert updated["version"] == 4
            graph = await ok("read_graph", slug=slug)
            assert graph["notes"][2] == {"id": third, "text": "Third, edited", "clusters": ["extra"]}
            assert graph["notes"][:2] == [{"id": first, "text": "## First\nMade by the MCP live test.", "clusters": ["live-test"]},
                                          {"id": second, "text": "Second", "clusters": ["extra", "live-test"]}]
            report("update_notes: one note changed, the others as they were")

            stale = await call("add_notes", slug=slug, version=2, notes=[{"text": "from an old version"}])
            assert stale.is_error and "Nothing was saved" in stale.content[0].text and "now at version 4" in stale.content[0].text
            assert (await ok("read_graph", slug=slug))["version"] == 4
            report("outdated change: refused, nothing saved, current graph returned")

            await ok("unlink_notes", slug=slug, version=4, link_ids=[both])
            removed = await ok("remove_notes", slug=slug, version=5, note_ids=[first])
            assert removed["version"] == 6 and removed["links_removed"] == [leads]
            graph = await ok("read_graph", slug=slug)
            assert [n["id"] for n in graph["notes"]] == [second, third] and graph["links"] == []
            report("unlink_notes, remove_notes: gone, with the attached link")

            renamed = await ok("rename_graph", slug=slug, version=6, title="MCP live test, renamed (safe to delete)")
            assert renamed["title"].startswith("MCP live test, renamed") and renamed["slug"] == slug and renamed["version"] == 7

            history = await ok("list_history", slug=slug)
            assert [(v["version"], v["change"]) for v in history["versions"]] == [
                (7, "rename"), (6, "save"), (5, "save"), (4, "save"), (3, "save"), (2, "save"), (1, "create")]
            old = await ok("read_graph", slug=slug, at_version=3)
            assert len(old["notes"]) == 3 and len(old["links"]) == 2 and old["is_current"] is False
            restored = await ok("restore_version", slug=slug, version=7, restore_to=3)
            assert restored["version"] == 8 and restored["title"] == "MCP live test (safe to delete)"
            graph = await ok("read_graph", slug=slug)
            assert [n["id"] for n in graph["notes"]] == [first, second, third] and len(graph["links"]) == 2
            report("rename_graph, list_history, read at a version, restore_version: version 3 is back as version 8")

            public = await ok("set_visibility", slug=slug, version=8, visibility="public")
            assert public["visibility"] == "public" and public["public_url"].endswith(f"/public?g={slug}")
            private = await ok("set_visibility", slug=slug, version=9, visibility="private")
            assert private["visibility"] == "private" and private["version"] == 10
            report("set_visibility: public, then private again")

            copied = await ok("copy_graph", slug=slug, title="MCP live test copy (safe to delete)", new_slug=copy)
            assert copied["slug"] == copy and copied["visibility"] == "private"
            copy_graph = await ok("read_graph", slug=copy)
            assert [(n["text"], n["clusters"]) for n in copy_graph["notes"]] == [(n["text"], n["clusters"]) for n in graph["notes"]]
            assert len(copy_graph["links"]) == 2
            report(f"copy_graph: {copy} holds the same notes and links")

            boxed = await ok("add_group_boxes", slug=slug, version=10, boxes=[
                {"label": "Start", "notes": [first, second]}, {"label": "End", "notes": [third], "collapsed": True}])
            start, end = boxed["added"]
            assert boxed["version"] == 11 and start["contains"] == [first, second] and end["contains"] == [third]
            assert end["collapsed"] is True and start["collapsed"] is False
            graph = await ok("read_graph", slug=slug)
            assert graph["group_boxes"] == boxed["added"]
            assert {c["name"]: c["members"] for c in graph["clusters"] if c["made_by"] != "tags"} == {
                "start": [first, second], "end": [third]}
            report(f"add_group_boxes: two boxes, one collapsed, each holding only its notes (moved: {boxed['moved']})")

            changed = await ok("update_group_boxes", slug=slug, version=11, boxes=[
                {"id": start["id"], "label": "Beginning", "notes": [first]}, {"id": end["id"], "collapsed": False}])
            assert changed["version"] == 12 and changed["updated"][0]["contains"] == [first]
            assert changed["updated"][0]["cluster"] == "beginning" and changed["updated"][1]["collapsed"] is False
            removed = await ok("remove_group_boxes", slug=slug, version=12, box_ids=[end["id"]])
            graph = await ok("read_graph", slug=slug)
            assert removed["version"] == 13 and [b["id"] for b in graph["group_boxes"]] == [start["id"]]
            assert [n["id"] for n in graph["notes"]] == [first, second, third]
            report("update_group_boxes, remove_group_boxes: renamed and refitted, opened, removed with its note kept")

            styled = await ok("style_notes", slug=slug, version=13, notes=[
                {"id": first, "color": "purple", "border": "dashed", "shape": "pill", "text_align": "center"},
                {"id": start["id"], "color": "#30a46c"}])
            assert styled["version"] == 14 and styled["styled"][1] == {"id": start["id"], "color": "#30a46c"}
            linked = await ok("link_notes", slug=slug, version=14, links=[
                {"from_note": first, "to_note": third, "direction": "backward", "from_side": "bottom", "to_side": "top"}])
            back = linked["added"][0]["id"]
            changed = await ok("update_links", slug=slug, version=15, links=[
                {"id": back, "label": "comes from", "color": "orange", "line": "dotted", "path": "right-angles"}])
            graph = await ok("read_graph", slug=slug)
            assert graph["version"] == 16 and next(n for n in graph["notes"] if n["id"] == first) == {
                "id": first, "text": "## First\nMade by the MCP live test.", "clusters": ["beginning", "live-test"],
                "color": "purple", "border": "dashed", "shape": "pill", "text_align": "center"}
            assert next(link for link in graph["links"] if link["id"] == back) == changed["updated"][0] == {
                "id": back, "from_note": first, "to_note": third, "direction": "backward", "label": "comes from",
                "color": "orange", "line": "dotted", "path": "right-angles", "from_side": "bottom", "to_side": "top"}
            report("style_notes, link_notes with sides, update_links: looks and a backward link read back as set")

            nested = await ok("add_group_boxes", slug=slug, version=16, boxes=[{"label": "Everything", "notes": [start["id"], second, third]}])
            outer = nested["added"][0]
            assert outer["boxes"] == [start["id"]] and set(outer["contains"]) == {first, second, third}
            laid = await ok("read_graph", slug=slug, layout=True)
            box = next(b for b in laid["group_boxes"] if b["id"] == outer["id"])
            placed = await ok("move_notes", slug=slug, version=17, notes=[
                {"id": outer["id"], "x": box["x"] + 200, "y": box["y"]}, {"id": third, "width": 280}])
            assert {p["id"] for p in placed["placed"]} == {outer["id"], start["id"], first, second, third}
            laid = await ok("read_graph", slug=slug, layout=True)
            assert next(b for b in laid["group_boxes"] if b["id"] == outer["id"])["x"] == box["x"] + 200
            assert next(n for n in laid["notes"] if n["id"] == third)["width"] == 280
            report("add_group_boxes around a box, read_graph with layout, move_notes: a box nests, moves with its contents, a note resizes")

            page = await ok("add_notes", slug=slug, version=18, notes=[{"url": "https://example.com/start", "key": "page"}],
                            links=[{"from_note": "page", "to_note": first, "label": "explains"}])
            card = page["added"][0]
            assert card["kind"] == "web page" and card["url"] == "https://example.com/start"
            await ok("update_notes", slug=slug, version=19, notes=[{"id": card["id"], "url": "https://example.org/start"}])
            graph = await ok("read_graph", slug=slug)
            assert [c for c in graph["other_cards"] if c["id"] == card["id"]] == [
                {"id": card["id"], "kind": "web page", "url": "https://example.org/start", "clusters": []}]
            report("add_notes with a url, update_notes url: a web-page card made and its address changed")

            trashed = await ok("trash_graph", slug=slug)
            assert trashed["trashed_at"]
            assert slug in [g["slug"] for g in (await ok("list_trash"))["trash"]]
            assert slug not in [g["slug"] for g in (await ok("list_graphs"))["mine"]]
            back = await ok("restore_from_trash", slug=slug)
            assert "trashed_at" not in back and slug in [g["slug"] for g in (await ok("list_graphs"))["mine"]]
            report("trash_graph, list_trash, restore_from_trash: there and back")
        finally:
            for test_graph in (slug, copy):  # leave the test graphs in the trash, whatever happened above
                await call("trash_graph", slug=test_graph)

        trash = [g["slug"] for g in (await ok("list_trash"))["trash"]]
        assert slug in trash and copy in trash
        after = others(await ok("list_graphs"), await ok("list_trash"))
        assert after == before, "a graph that is not this test's changed"
        report(f"clean up: {slug} and {copy} are in the trash; the {len(after)} other graphs are exactly as before")


def test_every_tool_against_the_live_app():
    saved = subprocess.run(["cloudflared", "access", "token", f"-app={sign_in_url(APP_URL)}"], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
    if saved.returncode != 0:
        pytest.fail(f"no saved sign-in: run `cloudflared access login {sign_in_url(APP_URL)}` once, then run this again", pytrace=False)
    # Printed, not captured through a fixture: the stdio client hands the real stderr to the server (run with -s).
    asyncio.run(live_run(lambda line: print(f"PASS | {line}", flush=True)))
