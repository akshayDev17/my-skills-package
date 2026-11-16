"""The web app's "Download for Obsidian" as a tool: the same zip the app downloads, saved
to a new file on this computer, never over another file."""

from __future__ import annotations

import dataclasses
import errno
import io
import os
import re
import zipfile
from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError

from graph_notes_mcp import client, server
from support import OTHER, App, fixture


def entries(data: bytes) -> dict[str, bytes]:
    """Each file in the zip and its contents, with the README's "made <time>" left out."""
    archive = zipfile.ZipFile(io.BytesIO(data))
    files = {name: archive.read(name) for name in archive.namelist()}
    files["graph-notes/README.md"] = re.sub(rb"made \S+\.", b"made <time>.", files["graph-notes/README.md"])
    return files


@pytest.fixture
def app() -> App:
    app = App()
    app.edge.seed("example", fixture("example"))
    app.edge.seed("kmeans-in-production", fixture("kmeans-in-production"))
    app.edge.seed("theirs", fixture("example"), owner=OTHER, visibility="public")
    return app


def test_the_zip_is_the_one_the_app_downloads_saved_under_the_apps_name(app, tmp_path):
    saved = app.call("download_for_obsidian", folder=str(tmp_path))
    path = Path(saved["path"])
    assert path.parent == tmp_path.resolve() and path.name.startswith("graph-notes-") and path.suffix == ".zip"
    assert saved["graphs"] == ["example", "kmeans-in-production"]  # the person's own, never someone else's
    assert saved["bytes"] == path.stat().st_size and "Open folder as vault" in saved["next"]
    served = app.edge.as_user("GET", "/export")
    assert entries(path.read_bytes()) == entries(served)  # what the app's route answers, but for when it was made
    assert "graph-notes/README.md" in entries(served) and "graph-notes/example.canvas" in entries(served)
    assert [r[:2] for r in app.edge.requests[-1:]] == [("GET", "https://graph.test/graph/api/export")]


def test_a_second_download_never_overwrites_the_first(app, tmp_path):
    first = Path(app.call("download_for_obsidian", folder=str(tmp_path))["path"])
    first.write_bytes(b"the person's own file")
    second = Path(app.call("download_for_obsidian", folder=str(tmp_path))["path"])
    third = Path(app.call("download_for_obsidian", folder=str(tmp_path))["path"])
    assert first.read_bytes() == b"the person's own file"
    assert (second.name, third.name) == (first.stem + " (1).zip", first.stem + " (2).zip")


def test_a_folder_that_is_not_there_is_refused_before_anything_is_fetched(app, tmp_path):
    sent = len(app.edge.requests)
    with pytest.raises(ToolError, match="is not a folder on this computer"):
        app.call("download_for_obsidian", folder=str(tmp_path / "missing"))
    (tmp_path / "a-file").write_text("x")
    with pytest.raises(ToolError, match="is not a folder"):
        app.call("download_for_obsidian", folder=str(tmp_path / "a-file"))
    assert len(app.edge.requests) == sent


def test_trashed_graphs_are_not_in_it(app, tmp_path):
    app.call("trash_graph", slug="example")
    assert app.call("download_for_obsidian", folder=str(tmp_path))["graphs"] == ["kmeans-in-production"]


def test_without_a_folder_it_goes_to_the_downloads_folder(app, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "Downloads").mkdir()
    saved = Path(app.call("download_for_obsidian")["path"])
    assert saved.parent == (tmp_path / "Downloads").resolve()


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write anywhere")
def test_a_folder_it_may_not_write_in_is_a_tool_error_that_names_it(app, tmp_path):
    shut = tmp_path / "shut"
    shut.mkdir()
    shut.chmod(0o500)
    try:
        with pytest.raises(ToolError, match=rf"could not save the zip in '{re.escape(str(shut.resolve()))}': Permission denied"):
            app.call("download_for_obsidian", folder=str(shut))
    finally:
        shut.chmod(0o700)
    assert list(shut.iterdir()) == []


def test_a_write_that_fails_partway_leaves_no_file_behind(app, tmp_path, monkeypatch):
    class FullDisk(io.FileIO):
        def write(self, data):
            super().write(data[:10])
            raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(server, "open", lambda path, mode: FullDisk(path, "x"), raising=False)
    with pytest.raises(ToolError, match="could not save the zip in .*: No space left on device"):
        app.call("download_for_obsidian", folder=str(tmp_path))
    assert list(tmp_path.iterdir()) == []


def test_a_zip_cut_short_on_the_way_is_refused_and_nothing_is_saved(app, tmp_path):
    send = app.api.send

    def cut_short(method, url, headers, body):
        answer = send(method, url, headers, body)
        return client.Response(answer.status, answer.body[:len(answer.body) // 2])

    app.server = server.build_server(dataclasses.replace(app.api, send=cut_short))
    with pytest.raises(ToolError, match="cut short or damaged; nothing was saved"):
        app.call("download_for_obsidian", folder=str(tmp_path))
    assert list(tmp_path.iterdir()) == []
