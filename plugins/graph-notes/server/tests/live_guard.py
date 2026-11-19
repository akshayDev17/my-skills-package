"""What the live test may touch: graphs it made itself, named ``mcp-test-...``.

Every tool call of the live run goes through ``guarded`` first, so no slug of a real
graph (``example``, ``kmeans-in-production``, anything else) can reach the live app
from the test, whatever the test's own code says. That includes a picture path in note
text, ``attachments/<slug>/...``, which makes a change copy that graph's picture in.
"""

from __future__ import annotations

import json
import re

PREFIX = "mcp-test-"
NO_GRAPH_NAMED = {"about_graphs", "list_graphs", "list_trash"}  # they read lists, and change nothing
NAMES = {"create_graph": ("slug",), "copy_graph": ("slug", "new_slug")}  # every slug each tool must be given


_PICTURE_SLUG = re.compile(r"attachments/([a-z0-9][a-z0-9-]*)/")


class NotATestGraph(AssertionError):
    pass


def guarded(tool: str, arguments: dict) -> dict:
    """``arguments``, or ``NotATestGraph`` unless every graph the call names is a test graph.

    A tool that derives a slug when none is given (create, copy) must be given one. The
    Obsidian download holds every graph of the account, so the live run never makes one.
    """
    if tool == "download_for_obsidian":
        raise NotATestGraph(f"the live test may only touch '{PREFIX}*' graphs, and the Obsidian download holds every "
                            "graph of the account: download_for_obsidian refused")
    if tool in NO_GRAPH_NAMED and not arguments:
        return arguments
    for name in NAMES.get(tool, ("slug",)):
        slug = arguments.get(name)
        if not isinstance(slug, str) or not slug.startswith(PREFIX) or len(slug) <= len(PREFIX):
            raise NotATestGraph(f"the live test may only touch '{PREFIX}*' graphs: {tool}({name}={slug!r}) refused")
    for slug in _PICTURE_SLUG.findall(json.dumps(arguments)):
        if not slug.startswith(PREFIX) or len(slug) <= len(PREFIX):
            raise NotATestGraph(f"the live test may only touch '{PREFIX}*' graphs: {tool} names a picture of '{slug}', refused")
    return arguments
