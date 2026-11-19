"""Pictures through the tools, against the real API and its stand-in bucket (support.py): a file on
this computer attached to a note, the library listed, pictures copied the way the web app's paste
copies them, and the Obsidian download carrying each library. No tool deletes a picture."""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError

from graph_notes_mcp import client, images
from pictures import GPS, HEIC, ORIENTATION, SVG, XMP_GPS, jpeg, png, tiff
from support import OTHER, App

PICTURE = re.compile(r"!\[([^\]]*)\]\((attachments/([a-z0-9-]+)/(img_[0-9a-f]{16}\.(?:png|jpg|webp|gif)))\)")


@pytest.fixture
def app() -> App:
    return App()


def note(app: App, slug: str = "plan", text: str = "Whiteboard") -> tuple[int, str]:
    """A graph ``slug`` with one note; ``(version, note id)``."""
    app.call("create_graph", title=slug.title(), slug=slug)
    added = app.call("add_notes", slug=slug, version=1, notes=[{"text": text}])
    return added["version"], added["added"][0]["id"]


def save(tmp_path: Path, name: str, data: bytes) -> str:
    path = tmp_path / name
    path.write_bytes(data)
    return str(path)


def stored(app: App, slug: str, path: str) -> bytes:
    """The bytes the bucket holds for a library path of ``slug``."""
    file = path.rsplit("/", 1)[1]
    (key,) = [k for k in app.edge.bucket.keys() if k.endswith(f"/{slug}/{file}")]
    return app.edge.bucket.bytes_of(key)


def text_of(app: App, slug: str, note_id: str) -> str:
    return next(n["text"] for n in app.edge.canvas(slug)["nodes"] if n["id"] == note_id)


# ── attach_image ───────────────────────────────────────────────────────────

def test_a_file_goes_into_the_library_and_its_line_under_the_notes_title(app, tmp_path):
    version, card = note(app, text="Whiteboard\nSketched on Monday.\n#ml")
    photo = jpeg(1200, 900, exif=tiff((ORIENTATION, 6), (GPS, 1)), xmp=XMP_GPS)
    done = app.call("attach_image", slug="plan", version=version, note_id=card, path=save(tmp_path, "board.jpg", photo))
    assert done["version"] == version + 1 and done["name"] == "board.jpg" and done["note"] == card
    assert "already_in_library" not in done
    assert re.fullmatch(r"attachments/plan/img_[0-9a-f]{16}\.jpg", done["path"])
    assert text_of(app, "plan", card) == f"Whiteboard\n![board.jpg]({done['path']})\nSketched on Monday.\n#ml"
    kept = stored(app, "plan", done["path"])
    assert kept == images.strip_metadata(photo, "image/jpeg") and b"GPSLatitude" not in kept
    assert images.exif_orientation(kept[kept.index(b"Exif\0\0") + 6:]) == 6  # it stays upright
    history = app.call("list_history", slug="plan")["versions"]
    assert len(history) == version + 1  # one saved version for the whole attach


def test_the_upload_is_the_file_itself_with_its_type_hash_and_the_write_headers(app, tmp_path):
    version, card = note(app)
    picture = png(40, 30)
    app.call("attach_image", slug="plan", version=version, note_id=card, path=save(tmp_path, "my shot (1).png", picture))
    ((method, url, headers, body),) = [r for r in app.edge.requests if r[1].endswith("/graphs/plan/images")]
    assert (method, url) == ("POST", "https://graph.test/graph/api/graphs/plan/images") and body == picture
    assert headers["content-type"] == "image/png" and headers["content-length"] == str(len(picture))
    assert headers["x-content-sha256"] == hashlib.sha256(picture).hexdigest()
    assert headers["x-image-name"] == "my%20shot%20%281%29.png"
    assert headers["origin"] == "https://graph.test" and headers["x-graph-notes"] == "1"
    assert app.call("list_images", slug="plan")["images"][0]["name"] == "my shot (1).png"


def test_an_empty_note_takes_the_pictures_shape_and_a_note_with_text_keeps_its_size(app, tmp_path):
    app.call("create_graph", title="Plan")
    added = app.call("add_notes", slug="plan", version=1, notes=[{"text": "", "clusters": ["ml"]}, {"text": "Words"}])
    empty, words = (n["id"] for n in added["added"])
    before = {n["id"]: n for n in app.edge.canvas("plan")["nodes"]}
    shot = save(tmp_path, "wide.png", png(2000, 1000))
    done = app.call("attach_image", slug="plan", version=2, note_id=empty, path=shot)
    after = {n["id"]: n for n in app.edge.canvas("plan")["nodes"]}
    assert after[empty]["width"] == before[empty]["width"]
    assert after[empty]["height"] == images.fitted_height(before[empty]["width"], (2000, 1000))
    assert after[empty]["text"] == f"![wide.png]({done['path']})\n#ml"
    app.call("attach_image", slug="plan", version=3, note_id=words, path=shot, alt="Wide view")
    assert {k: after[words][k] for k in ("width", "height")} == \
        {k: v for k, v in app.edge.canvas("plan")["nodes"][1].items() if k in ("width", "height")}


@pytest.mark.parametrize("orientation, drawn", [(6, (3024, 4032)), (8, (3024, 4032)), (3, (4032, 3024))])
def test_an_empty_note_takes_the_shape_a_turned_photo_is_drawn_in(app, tmp_path, orientation, drawn):
    app.call("create_graph", title="Plan")
    added = app.call("add_notes", slug="plan", version=1, notes=[{"text": ""}, {"text": ""}])
    first, second = (n["id"] for n in added["added"])
    width = app.edge.canvas("plan")["nodes"][0]["width"]
    photo = save(tmp_path, "phone.jpg", jpeg(4032, 3024, exif=tiff((ORIENTATION, orientation), (GPS, 1))))
    done = app.call("attach_image", slug="plan", version=2, note_id=first, path=photo)
    again = app.call("attach_image", slug="plan", version=done["version"], note_id=second, path=photo)
    assert again["already_in_library"] is True
    heights = {n["id"]: n["height"] for n in app.edge.canvas("plan")["nodes"]}
    assert heights[first] == heights[second] == images.fitted_height(width, drawn)


def test_the_same_picture_twice_is_one_library_entry(app, tmp_path):
    version, card = note(app)
    shot = save(tmp_path, "a.png", png(10, 10))
    first = app.call("attach_image", slug="plan", version=version, note_id=card, path=shot)
    again = app.call("attach_image", slug="plan", version=first["version"], note_id=card, path=save(tmp_path, "b.png", png(10, 10)))
    assert again["path"] == first["path"] and again["already_in_library"] is True and again["name"] == "a.png"
    assert len(app.call("list_images", slug="plan")["images"]) == 1


@pytest.mark.parametrize("data, refusal", [
    (HEIC, "HEIC isn't accepted: export it as JPEG or PNG first"),
    (SVG, r"SVG isn't accepted \(it can carry scripts\): export it as PNG"),
    (b"just some text", "is not a PNG, JPEG, WebP or GIF image"),
    (b"", "is not a PNG, JPEG, WebP or GIF image"),
])
def test_a_file_the_library_cannot_take_is_refused_before_anything_is_sent(app, tmp_path, data, refusal):
    version, card = note(app)
    sent = len(app.edge.requests)
    with pytest.raises(ToolError, match=refusal):
        app.call("attach_image", slug="plan", version=version, note_id=card, path=save(tmp_path, "x.bin", data))
    assert len(app.edge.requests) == sent


def test_a_path_that_is_not_a_file_is_refused(app, tmp_path):
    version, card = note(app)
    for path in (str(tmp_path / "missing.png"), str(tmp_path), "https://example.com/cat.png"):
        with pytest.raises(ToolError, match="is not a file on this computer"):
            app.call("attach_image", slug="plan", version=version, note_id=card, path=path)


def test_a_file_over_cloudflares_cap_is_refused_before_it_is_read(app, tmp_path, monkeypatch):
    version, card = note(app)
    monkeypatch.setattr(images, "CLOUDFLARE_MAX_BYTES", 20)
    with pytest.raises(ToolError, match="Cloudflare doesn't accept uploads over 100 MB"):
        app.call("attach_image", slug="plan", version=version, note_id=card, path=save(tmp_path, "big.png", png(30, 30)))


def test_cloudflares_own_413_is_said_plainly():
    with pytest.raises(client.ApiError, match="Cloudflare doesn't accept uploads over 100 MB"):
        client.answer_of(client.Response(413, b"<html>413 Request Entity Too Large</html>"))
    with pytest.raises(client.ApiError, match="the app's own"):
        client.answer_of(client.Response(413, b'{"error": "the app\'s own"}'))


def test_a_stale_version_saves_nothing_but_says_where_the_picture_went(app, tmp_path):
    version, card = note(app)
    app.edge.before_put.append(lambda: app.edge.as_user("PUT", "/graphs/plan", {"base": version, "title": "Renamed meanwhile"}))
    with pytest.raises(ToolError) as failure:
        app.call("attach_image", slug="plan", version=version, note_id=card, path=save(tmp_path, "a.png", png(5, 5)))
    path = app.call("list_images", slug="plan")["images"][0]["path"]
    assert f"The picture is in the library as {path}, but the note was not changed" in str(failure.value)
    assert "Nothing was saved" in str(failure.value) and text_of(app, "plan", card) == "Whiteboard"


def test_an_outdated_version_or_a_wrong_card_uploads_nothing(app, tmp_path):
    version, card = note(app)
    app.call("create_graph", title="Theirs", slug="theirs")
    shot = save(tmp_path, "a.png", png(5, 5))
    with pytest.raises(ToolError, match="Nothing was saved"):
        app.call("attach_image", slug="plan", version=version - 1, note_id=card, path=shot)
    with pytest.raises(ToolError, match="has no note with id 'nope'"):
        app.call("attach_image", slug="plan", version=version, note_id="nope", path=shot)
    web = app.call("add_notes", slug="plan", version=version, notes=[{"url": "https://example.com"}])
    with pytest.raises(ToolError, match="is a web-page or file card, not a note"):
        app.call("attach_image", slug="plan", version=web["version"], note_id=web["added"][0]["id"], path=shot)
    assert app.edge.bucket.keys() == []


def test_someone_elses_graph_is_not_attached_to_and_its_library_is_not_listed(app, tmp_path):
    app.edge.seed("theirs", {"nodes": [{"id": "t1", "type": "text", "text": "T", "x": 0, "y": 0, "width": 260, "height": 120}],
                             "edges": []}, owner=OTHER, visibility="public")
    with pytest.raises(ToolError, match="someone else's public graph"):
        app.call("attach_image", slug="theirs", version=2, note_id="t1", path=save(tmp_path, "a.png", png(5, 5)))
    with pytest.raises(ToolError, match="its image library is theirs"):
        app.call("list_images", slug="theirs")
    assert app.edge.bucket.keys() == []


# ── list_images ────────────────────────────────────────────────────────────

def test_the_library_lists_each_picture_with_the_notes_that_show_it(app, tmp_path):
    version, card = note(app)
    first = app.call("attach_image", slug="plan", version=version, note_id=card, path=save(tmp_path, "a.png", png(30, 20)))
    second = app.call("attach_image", slug="plan", version=first["version"], note_id=card, path=save(tmp_path, "b.jpg", jpeg(64, 48)))
    app.call("update_notes", slug="plan", version=second["version"], notes=[{"id": card, "text": "Whiteboard\n" + f"![b]({second['path']})"}])
    listed = app.call("list_images", slug="plan")
    assert listed["slug"] == "plan" and listed["version"] == second["version"] + 1
    by_path = {e["path"]: e for e in listed["images"]}
    assert by_path[first["path"]]["notes"] == []  # off every note, still in the library
    assert by_path[second["path"]]["notes"] == [card]
    assert {k: by_path[first["path"]][k] for k in ("name", "content_type", "width", "height")} == \
        {"name": "a.png", "content_type": "image/png", "width": 30, "height": 20}
    assert by_path[first["path"]]["bytes"] == len(png(30, 20)) and by_path[first["path"]]["added_at"]


# ── copying, as a paste does ───────────────────────────────────────────────

def attached(app: App, tmp_path: Path, slug: str, name: str, data: bytes, text: str = "Board") -> tuple[str, str]:
    """A graph ``slug`` with a note showing a picture; ``(note id, its path)``."""
    version, card = note(app, slug, text)
    return card, app.call("attach_image", slug=slug, version=version, note_id=card, path=save(tmp_path, name, data))["path"]


def test_a_note_naming_another_graphs_picture_copies_it_in_first(app, tmp_path):
    _, path = attached(app, tmp_path, "rag", "board.png", png(12, 8))
    version, _ = note(app, "plan")
    added = app.call("add_notes", slug="plan", version=version, notes=[{"text": f"From RAG\n![board.png]({path})"}])
    file = path.rsplit("/", 1)[1]
    assert added["images_copied"] == [f"attachments/plan/{file}"]
    assert text_of(app, "plan", added["added"][0]["id"]) == f"From RAG\n![board.png](attachments/plan/{file})"
    assert stored(app, "plan", f"attachments/plan/{file}") == stored(app, "rag", path)
    # the same paste again reuses the copy rather than storing it twice
    again = app.call("add_notes", slug="plan", version=added["version"], notes=[{"text": f"![x]({path})"}])
    assert again["images_copied"] == [f"attachments/plan/{file}"]
    assert len(app.call("list_images", slug="plan")["images"]) == 1


def test_update_notes_copies_too_and_a_picture_it_cannot_copy_is_left_out_with_a_notice(app, tmp_path):
    _, mine = attached(app, tmp_path, "rag", "board.png", png(12, 8))
    app.edge.seed("hidden", {"nodes": [], "edges": []}, owner=OTHER)
    version, card = note(app, "plan")
    gone = "attachments/hidden/img_0123456789abcdef.png"
    done = app.call("update_notes", slug="plan", version=version, notes=[
        {"id": card, "text": f"Two pictures\n![board.png]({mine})\n![Secret plan]({gone})\nend"}])
    file = mine.rsplit("/", 1)[1]
    assert text_of(app, "plan", card) == f"Two pictures\n![board.png](attachments/plan/{file})\nend"
    assert done["images_copied"] == [f"attachments/plan/{file}"]
    assert done["notices"] == ["Saved without Secret plan: not available (deleted, or no longer public)."]


def test_an_edit_that_fails_copies_no_picture_in(app, tmp_path):
    _, path = attached(app, tmp_path, "rag", "board.png", png(12, 8))
    version, card = note(app, "plan")
    line = f"![board.png]({path})"
    with pytest.raises(ToolError, match="'nope' names no note"):
        app.call("update_notes", slug="plan", version=version, notes=[{"id": "nope", "text": line}])
    with pytest.raises(ToolError, match="names no note"):
        app.call("add_notes", slug="plan", version=version, notes=[{"text": line, "key": "new"}],
                 links=[{"from_note": "new", "to_note": "nope"}])
    with pytest.raises(ToolError, match="a web-page card has a url and nothing else"):
        app.call("add_notes", slug="plan", version=version, notes=[{"text": line}, {"url": "https://example.com", "text": "x"}])
    assert app.call("list_images", slug="plan")["images"] == []
    assert text_of(app, "plan", card) == "Whiteboard"


def test_a_picture_on_the_web_is_saved_as_it_is_and_copies_nothing(app, tmp_path):
    _, path = attached(app, tmp_path, "rag", "board.png", png(12, 8))
    version, _ = note(app, "plan")
    line = f"![board](https://raw.githubusercontent.com/me/vault/main/{path})"
    added = app.call("add_notes", slug="plan", version=version, notes=[{"text": line}])
    assert "images_copied" not in added and "notices" not in added
    assert text_of(app, "plan", added["added"][0]["id"]) == line
    assert app.call("list_images", slug="plan")["images"] == []


def test_someone_elses_public_picture_copies_only_while_their_note_shows_it(app, tmp_path):
    shown = png(6, 6, rgb=(1, 2, 3))
    app.edge.seed("theirs", {"nodes": [], "edges": []}, owner=OTHER)
    their_file = their_upload(app, "theirs", shown)
    app.edge.as_user("PUT", "/graphs/theirs", {"base": 1, "canvas": {"nodes": [
        {"id": "t1", "type": "text", "text": f"![p](attachments/theirs/{their_file})", "x": 0, "y": 0, "width": 260, "height": 120}],
        "edges": []}}, who=OTHER)
    off_card = their_upload(app, "theirs", png(7, 7))
    app.edge.as_user("POST", "/graphs/theirs/visibility", {"base": 2, "visibility": "public"}, who=OTHER)
    version, _ = note(app, "plan")
    added = app.call("add_notes", slug="plan", version=version, notes=[
        {"text": f"![p](attachments/theirs/{their_file})"}, {"text": f"x\n![q](attachments/theirs/{off_card})"}])
    assert added["images_copied"] == [f"attachments/plan/{their_file}"]
    assert added["notices"] == ["Saved without q: not available (deleted, or no longer public)."]
    assert text_of(app, "plan", added["added"][1]["id"]) == "x"


def their_upload(app: App, slug: str, data: bytes) -> str:
    """Someone else's upload into their own graph, straight to the API."""
    from graph_notes.api import ApiRequest, serve
    import asyncio
    import time
    from dev_access import claims_for, mint, shared_key
    from support import AUD, TEAM
    headers = {"cf-access-jwt-assertion": mint(shared_key(), claims_for(OTHER, TEAM, AUD, time.time())),
               "origin": app.edge.origin, "x-graph-notes": "1", "content-type": "image/png",
               "content-length": str(len(data)), "x-content-sha256": hashlib.sha256(data).hexdigest(), "x-image-name": "p.png"}
    reply = asyncio.run(serve(ApiRequest("POST", f"/graph/api/graphs/{slug}/images", {}, headers, "", data),
                              app.edge.db, app.edge.bucket, app.edge.access, app.edge.keys, time.time()))
    assert reply.status == 201, reply.body
    return reply.body["file"]


def test_copying_my_graph_copies_its_whole_library_and_rewrites_every_note(app, tmp_path):
    card, on_card = attached(app, tmp_path, "rag", "board.png", png(12, 8))
    second = app.call("attach_image", slug="rag", version=3, note_id=card, path=save(tmp_path, "later.png", png(9, 9)))
    app.call("update_notes", slug="rag", version=second["version"], notes=[{"id": card, "text": f"Board\n![board.png]({on_card})"}])
    copy = app.call("copy_graph", slug="rag", title="RAG copy")
    assert copy["slug"] == "rag-copy" and sorted(copy["images_copied"]) == sorted(
        [on_card.replace("/rag/", "/rag-copy/"), second["path"].replace("/rag/", "/rag-copy/")])
    (text,) = [n["text"] for n in app.edge.canvas("rag-copy")["nodes"]]
    assert text == f"Board\n![board.png]({on_card.replace('/rag/', '/rag-copy/')})"
    listed = app.call("list_images", slug="rag-copy")["images"]
    assert {e["path"]: e["notes"] for e in listed} == {on_card.replace("/rag/", "/rag-copy/"): [card],
                                                        second["path"].replace("/rag/", "/rag-copy/"): []}
    assert app.call("read_graph", slug="rag")["notes"][0]["text"] == f"Board\n![board.png]({on_card})"  # the original stays


def test_copying_someone_elses_public_graph_copies_the_pictures_its_notes_show(app):
    app.edge.seed("theirs", {"nodes": [], "edges": []}, owner=OTHER)
    shown = their_upload(app, "theirs", png(4, 4))
    their_upload(app, "theirs", png(5, 5))  # in their library, on no card: not theirs to give
    app.edge.as_user("PUT", "/graphs/theirs", {"base": 1, "canvas": {"nodes": [
        {"id": "t1", "type": "text", "text": f"![p](attachments/theirs/{shown})", "x": 0, "y": 0, "width": 260, "height": 120}],
        "edges": []}}, who=OTHER)
    app.edge.as_user("POST", "/graphs/theirs/visibility", {"base": 2, "visibility": "public"}, who=OTHER)
    copy = app.call("copy_graph", slug="theirs", title="Mine now")
    assert copy["images_copied"] == [f"attachments/mine-now/{shown}"]
    assert [e["path"] for e in app.call("list_images", slug="mine-now")["images"]] == [f"attachments/mine-now/{shown}"]


def test_no_tool_and_no_route_of_the_client_deletes_a_picture(app, tmp_path):
    _, path = attached(app, tmp_path, "rag", "board.png", png(12, 8))
    with pytest.raises(client.ApiError, match="is not something this server does"):
        client.call(app.api, "DELETE", f"/graphs/rag/images/{path.rsplit('/', 1)[1]}?base=3")
    app.call("remove_notes", slug="rag", version=3, note_ids=[app.call("read_graph", slug="rag")["notes"][0]["id"]])
    assert [e["path"] for e in app.call("list_images", slug="rag")["images"]] == [path]  # still in the library
    assert not any(method == "DELETE" and "/images" in url for method, url, *_ in app.edge.requests)


@pytest.mark.parametrize("file", ["../x", "img_0123.png", "img_0123456789abcdef.svg", "img_0123456789abcdef.png/../../trash"])
def test_an_image_file_is_checked_before_it_goes_into_a_path(app, file):
    with pytest.raises(client.ApiError, match="is not an image file"):
        client.image_bytes(app.api, "rag", file)
    assert app.edge.requests == []


# ── the Obsidian download ──────────────────────────────────────────────────

def test_the_download_carries_each_graphs_library_where_its_notes_find_it(app, tmp_path):
    card, on_card = attached(app, tmp_path, "rag", "board.png", png(12, 8))
    later = app.call("attach_image", slug="rag", version=3, note_id=card, path=save(tmp_path, "b.jpg", jpeg(30, 20)))
    app.call("create_graph", title="Gone", slug="gone")
    app.call("trash_graph", slug="gone")
    folder = tmp_path / "out"
    folder.mkdir()
    saved = app.call("download_for_obsidian", folder=str(folder))
    archive = zipfile.ZipFile(io.BytesIO(Path(saved["path"]).read_bytes()))
    assert saved["images"] == 2 and saved["bytes"] == Path(saved["path"]).stat().st_size
    for path in (on_card, later["path"]):
        assert archive.read(f"graph-notes/{path}") == stored(app, "rag", path)
        assert archive.getinfo(f"graph-notes/{path}").compress_type == zipfile.ZIP_STORED
    canvas = archive.read("graph-notes/rag.canvas").decode()
    assert on_card in canvas and later["path"] in canvas  # the notes' own paths, resolving from the vault root
    assert "graph-notes/README.md" in archive.namelist()


def test_a_picture_deleted_while_the_download_runs_is_named_and_the_rest_is_saved(app, tmp_path):
    _, path = attached(app, tmp_path, "rag", "board.png", png(12, 8))
    send = app.api.send

    def deleted_meanwhile(method, url, headers, body):
        if url.endswith(path.rsplit("/", 1)[1]):
            return client.Response(404, b'{"error": "no image"}')
        return send(method, url, headers, body)

    object.__setattr__(app.api, "send", deleted_meanwhile)
    folder = tmp_path / "out"
    folder.mkdir()
    saved = app.call("download_for_obsidian", folder=str(folder))
    assert saved["images"] == 0 and saved["images_missing"] == [path]
    assert "graph-notes/rag.canvas" in zipfile.ZipFile(saved["path"]).namelist()


def test_a_download_that_fails_partway_leaves_no_file_behind(app, tmp_path):
    attached(app, tmp_path, "rag", "board.png", png(12, 8))
    send = app.api.send

    def broken(method, url, headers, body):
        if "/images/img_" in url:
            raise client.ApiError(0, "could not reach graph.test: connection reset")
        return send(method, url, headers, body)

    object.__setattr__(app.api, "send", broken)
    folder = tmp_path / "out"
    folder.mkdir()
    with pytest.raises(ToolError, match="connection reset"):
        app.call("download_for_obsidian", folder=str(folder))
    assert list(folder.iterdir()) == []
