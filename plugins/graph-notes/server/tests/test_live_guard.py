"""The live test's guard, tested offline: it refuses every slug that is not a test graph's."""

from __future__ import annotations

import pytest

from live_guard import NotATestGraph, guarded
from test_tools import TOOLS


@pytest.mark.parametrize("slug", ["example", "kmeans-in-production", "plan", "mcp-test", "mcp-test-", "xmcp-test-1",
                                  "MCP-TEST-1", "", None, 7, "../mcp-test-1"])
@pytest.mark.parametrize("tool", sorted(TOOLS - {"about_graphs", "list_graphs", "list_trash"}))
def test_a_slug_that_is_not_a_test_graphs_is_refused_for_every_tool(tool, slug):
    with pytest.raises(NotATestGraph, match="may only touch 'mcp-test-\\*' graphs"):
        guarded(tool, {"slug": slug, "new_slug": "mcp-test-ok", "version": 1})


def test_a_test_graphs_slug_passes():
    arguments = {"slug": "mcp-test-20261115-010203", "version": 3, "notes": [{"text": "n"}]}
    assert guarded("add_notes", arguments) is arguments
    assert guarded("list_graphs", {}) == {}


def test_creating_or_copying_must_name_the_new_graph_itself():
    with pytest.raises(NotATestGraph):
        guarded("create_graph", {"title": "mcp-test-looking title"})  # the slug would be derived
    with pytest.raises(NotATestGraph):
        guarded("copy_graph", {"slug": "mcp-test-1", "title": "copy"})
    with pytest.raises(NotATestGraph):
        guarded("copy_graph", {"slug": "example", "title": "copy", "new_slug": "mcp-test-copy"})  # not even to read from
    assert guarded("copy_graph", {"slug": "mcp-test-1", "title": "copy", "new_slug": "mcp-test-1-copy"})


def test_a_tool_the_guard_does_not_know_is_refused_unless_it_names_a_test_graph():
    with pytest.raises(NotATestGraph):
        guarded("some_future_tool", {})
    with pytest.raises(NotATestGraph):
        guarded("list_graphs", {"slug": "example"})


def test_the_obsidian_download_is_refused_whatever_it_is_given():
    for arguments in ({}, {"folder": "/tmp"}, {"slug": "mcp-test-1"}):
        with pytest.raises(NotATestGraph, match="download_for_obsidian refused"):
            guarded("download_for_obsidian", arguments)


def test_a_picture_of_a_graph_that_is_not_a_test_graphs_is_refused_in_any_argument():
    real = "![p](attachments/example/img_0123456789abcdef.png)"
    for tool, arguments in (("add_notes", {"slug": "mcp-test-1", "version": 2, "notes": [{"text": real}]}),
                            ("update_notes", {"slug": "mcp-test-1", "version": 2, "notes": [{"id": "n", "text": f"x {real}"}]})):
        with pytest.raises(NotATestGraph, match="names a picture of 'example'"):
            guarded(tool, arguments)
    ours = {"slug": "mcp-test-1-copy", "version": 2, "notes": [{"text": "![p](attachments/mcp-test-1/img_0123456789abcdef.png)"}]}
    assert guarded("add_notes", ours) is ours


def test_a_picture_on_its_own_line_is_found_and_one_inside_a_web_address_is_not():
    for text in ("Title\nattachments/example/img_0123456789abcdef.png", "x\n![p](<attachments/example/img_0123456789abcdef.png>)"):
        with pytest.raises(NotATestGraph, match="names a picture of 'example'"):
            guarded("add_notes", {"slug": "mcp-test-1", "version": 2, "notes": [{"text": text}]})
    web = {"slug": "mcp-test-1", "version": 2, "notes": [
        {"text": "![p](https://raw.githubusercontent.com/me/vault/main/attachments/example/img_0123456789abcdef.png)"}]}
    assert guarded("add_notes", web) is web
