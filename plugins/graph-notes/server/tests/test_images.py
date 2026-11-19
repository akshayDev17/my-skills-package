"""The image library's pure rules, as this server applies them: what note text names, how a paste
rewrites it, where a picture's line goes, and the lossless removal of location and other metadata,
which must give the very bytes the web app's removal gives (frontend/lib/images.ts)."""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess

import pytest

from graph_notes_mcp import images
from pictures import (APP2_MPF, APP11_C2PA, C2PA, GIF_FRAME, GIF_LOOP, GPS, HEIC, MP4_WITH_LOCATION, ORIENTATION, SCAN, SCANS, SVG,
                      XMP_GPS, XMP_MOTION_PHOTO, gif, gif_sub_blocks, jpeg, png, png_chunk, segment, tiff, webp, webp_chunk)
from support import REPO

A = "img_7f3a91c0e1b2d4f5.jpg"
B = "img_0123456789abcdef.png"


# ── note text ──────────────────────────────────────────────────────────────

def test_the_line_names_the_picture_safely():
    assert images.image_line("white[board]\\ \n2.jpg", "attachments/rag/" + A) == f"![whiteboard 2.jpg](attachments/rag/{A})"


def test_a_line_goes_under_the_title_or_is_the_whole_note_and_tags_stay_last():
    line = f"![w](attachments/rag/{A})"
    assert images.insert_image_line("", line) == line
    assert images.insert_image_line("#ml #ops", line) == f"{line}\n#ml #ops"
    assert images.insert_image_line("Title\nBody", line) == f"Title\n{line}\nBody"
    assert images.insert_image_line("\n\nTitle\n#ml", line) == f"\n\nTitle\n{line}\n#ml"
    assert images.is_empty_note("  \n#ml") and not images.is_empty_note("x")


def test_an_empty_card_takes_the_pictures_shape_up_to_600():
    assert images.fitted_height(260, (2000, 1000)) == 136  # 232 wide inner: 116 drawn, + 10 of padding and border each side
    assert images.fitted_height(260, (100, 50)) == 70      # never drawn larger than the picture
    assert images.fitted_height(260, (1000, 9000)) == 600
    assert images.fitted_height(260, (1000, 10)) == 40


def test_paths_naming_other_graphs_are_found_once_each_in_order():
    texts = [f"![a](attachments/rag/{A}) and attachments/rag/{A}", f"[b](attachments/ml/{B}) ![](attachments/here/{B})"]
    assert images.foreign_images(texts, "here") == {"rag": [A], "ml": [B]}
    assert images.foreign_images(["![x](https://example.com/img_0123456789abcdef.png)"], "here") == {}


def test_a_paste_rewrites_the_copied_paths_and_leaves_out_the_rest():
    from graph_notes_mcp.server import pasted_text
    text = f"Title\n![a](attachments/rag/{A})\nsee ![b](attachments/ml/{B}) here\n[link](attachments/ml/{B})"
    out = pasted_text(text, {f"attachments/rag/{A}": f"attachments/here/{A}"}, [("ml", B)])
    assert out == f"Title\n![a](attachments/here/{A})\nsee here\nlink"


def test_which_notes_show_each_picture():
    doc = {"nodes": [{"id": "n1", "type": "text", "text": f"![a](attachments/rag/{A})\n![a](attachments/rag/{A})"},
                     {"id": "n2", "type": "text", "text": f"attachments/rag/{A} attachments/ml/{B}"},
                     {"id": "w", "type": "link", "url": "https://x.test"}]}
    assert images.usage(doc, "rag") == {A: ["n1", "n2"]}


def test_a_picture_is_called_by_its_alt_text_else_its_file():
    assert images.picture_name([f"![Whiteboard](attachments/rag/{A})"], "rag", A) == "Whiteboard"
    assert images.picture_name([f"[x](attachments/rag/{A})"], "rag", A) == A


# ── what a file is ─────────────────────────────────────────────────────────

def test_the_type_comes_from_the_first_bytes():
    assert [images.sniff(b) for b in (png(2, 2), jpeg(), webp(4, 4), gif(), HEIC, SVG, b"<svg/>", b"hello", b"")] == \
        ["image/png", "image/jpeg", "image/webp", "image/gif", "heic", "svg", "svg", None, None]


# ── lossless metadata removal ──────────────────────────────────────────────

def test_a_jpeg_keeps_only_its_orientation_and_colour_profile():
    original = jpeg(exif=tiff((ORIENTATION, 6), (GPS, 1)), xmp=XMP_GPS, comment=b"a comment", icc=b"profile-bytes")
    out = images.strip_metadata(original, "image/jpeg")
    assert b"GPSLatitude" not in out and b"a comment" not in out and b"\x88\x25" not in out
    assert b"ICC_PROFILE\x00profile-bytes" in out
    assert out.endswith(SCAN)
    app0 = segment(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")
    assert out[2:2 + len(app0)] == app0  # JFIF stays first
    at = out.index(b"Exif\x00\x00")
    assert at > 2 + len(app0) - 1 and images.exif_orientation(out[at + 6:]) == 6


def test_an_upright_jpeg_keeps_no_exif_at_all():
    out = images.strip_metadata(jpeg(exif=tiff((ORIENTATION, 1), (GPS, 1), little=True)), "image/jpeg")
    assert b"Exif" not in out and out == jpeg()


def test_a_png_loses_exif_and_text_chunks_and_nothing_else():
    clean = png(3, 2)
    dirty = png(3, 2, extra=png_chunk(b"eXIf", tiff((GPS, 1))) + png_chunk(b"iTXt", XMP_GPS) + png_chunk(b"tIME", b"\0" * 7))
    assert images.strip_metadata(dirty, "image/png") == clean


def test_a_webp_loses_exif_and_xmp_and_their_flags():
    assert images.strip_metadata(webp(9, 9, exif=tiff((GPS, 1)), xmp=XMP_GPS), "image/webp") == webp(9, 9)


def test_a_multi_picture_jpeg_keeps_only_its_first_picture():
    """The later pictures of a multi-picture file carry their own Exif (GPS among it), and its index goes too."""
    first = jpeg(exif=tiff((ORIENTATION, 6), (GPS, 1)), extra=APP2_MPF, scan=SCANS)
    second = jpeg(exif=tiff((GPS, 1)), xmp=XMP_GPS)
    out = images.strip_metadata(first + second, "image/jpeg")
    assert out == images.strip_metadata(first, "image/jpeg")
    assert out.count(b"\xff\xd8") == 1 and b"MPF\x00" not in out and b"\x88\x25" not in out and b"GPS" not in out
    assert out.endswith(SCANS)  # every scan, stuffed bytes and restarts included, as it was
    assert images.jpeg_orientation(out) == 6


def test_a_motion_photos_video_is_left_out():
    out = images.strip_metadata(jpeg(xmp=XMP_MOTION_PHOTO) + MP4_WITH_LOCATION, "image/jpeg")
    assert out == jpeg() and b"51.5073" not in out


def test_a_jpeg_loses_its_c2pa_content_credentials_and_every_other_application_segment():
    makers = segment(0xEC, b"Ducky\x00\x01") + segment(0xE2, b"FPXR\x00stuff") + segment(0xE0, b"JFXX\x00\x10thumb")
    adobe = segment(0xEE, b"Adobe\x00\x64\x00\x00\x00\x00\x01")
    out = images.strip_metadata(jpeg(extra=APP11_C2PA + makers + adobe, icc=b"p"), "image/jpeg")
    assert out == jpeg(extra=adobe, icc=b"p")


def with_stray_bytes(data: bytes) -> bytes:
    """A jpeg() with bytes that are no marker before its first APP1 and before its frame header (a stuffed
    0xff 0x00 among them), as some cameras leave after a segment: decoders skip them."""
    exif_at, frame_at = data.index(b"\xff\xe1"), data.index(b"\xff\xc0")
    return data[:exif_at] + b"\x00\x00" + data[exif_at:frame_at] + b"\x12\xff\x00\x34" + data[frame_at:]


def test_bytes_between_a_jpegs_segments_that_are_no_marker_go_and_the_metadata_after_them_too():
    original = jpeg(exif=tiff((ORIENTATION, 6), (GPS, 1)), xmp=XMP_GPS, extra=APP11_C2PA)
    out = images.strip_metadata(with_stray_bytes(original), "image/jpeg")
    assert out == images.strip_metadata(original, "image/jpeg")
    assert b"\x88\x25" not in out and b"GPS" not in out and b"c2pa" not in out
    assert images.jpeg_orientation(out) == 6


def test_a_png_loses_its_c2pa_chunk_and_any_other_it_does_not_draw_with():
    kept = png_chunk(b"gAMA", b"\0\0\xb1\x8f") + png_chunk(b"pHYs", b"\0\0\x0b\x13\0\0\x0b\x13\x01")
    dirty = png(3, 2, extra=kept + png_chunk(b"caBX", C2PA) + png_chunk(b"iDOT", b"\0" * 28) + png_chunk(b"zxIf", b"x"))
    assert images.strip_metadata(dirty + b"after the end", "image/png") == png(3, 2, extra=kept)


def test_a_webp_loses_its_c2pa_chunk_and_anything_after_its_end():
    dirty = webp(9, 9, exif=tiff((GPS, 1)), extra=webp_chunk(b"C2PA", C2PA)) + b"GPS 51.5073,-0.1277" + C2PA
    assert images.strip_metadata(dirty, "image/webp") == webp(9, 9)


def test_a_gif_loses_comments_xmp_and_anything_after_its_trailer_and_keeps_its_frames_and_looping():
    comment = b"\x21\xfe" + gif_sub_blocks(b"Taken at 51.5073 N, 0.1277 W")
    xmp = b"\x21\xff\x0bXMP DataXMP" + XMP_GPS + b"\x01" + bytes(range(255, -1, -1)) + b"\x00"
    dirty = gif(blocks=GIF_LOOP + comment + GIF_FRAME + xmp + GIF_FRAME) + b"GPS 51.5073"
    assert images.strip_metadata(dirty, "image/gif") == gif(blocks=GIF_LOOP + GIF_FRAME + GIF_FRAME)


def test_a_clean_file_comes_back_the_same():
    for data, kind in ((jpeg(), "image/jpeg"), (jpeg(scan=SCANS), "image/jpeg"), (png(2, 2), "image/png"),
                       (webp(3, 3), "image/webp"), (gif(), "image/gif"), (gif(blocks=GIF_LOOP + GIF_FRAME), "image/gif")):
        assert images.strip_metadata(data, kind) == data


def _node_strips_types() -> bool:
    node = shutil.which("node")
    if not node:
        return False
    version = subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip().lstrip("v")
    major, minor, *_ = (int(p) for p in version.split("."))
    return (major, minor) >= (22, 6)


def run_web_app(tmp_path, body: str, given: object) -> object:
    """``body`` (JavaScript that reads ``given`` and sets ``out``) run against the web app's own
    frontend/lib/images.ts; returns ``out``. In CI it must run: node is set up for it there."""
    if not _node_strips_types():
        if os.environ.get("CI"):
            pytest.fail("node 22.6+ is needed to check the MCP server against the web app's TypeScript")
        pytest.skip("needs node 22.6+ to run the web app's TypeScript")
    script = tmp_path / "check.mjs"
    script.write_text(f"import * as web from {json.dumps((REPO / 'frontend/lib/images.ts').as_uri())};\n"
                      "const given = JSON.parse(process.argv[2]);\nlet out;\n" + body + "\nconsole.log(JSON.stringify(out));\n")
    ran = subprocess.run(["node", "--experimental-strip-types", "--no-warnings", str(script), json.dumps(given)],
                         capture_output=True, text=True, timeout=60)
    assert ran.returncode == 0, ran.stderr
    return json.loads(ran.stdout)


def test_the_same_bytes_as_the_web_apps_removal(tmp_path):
    """So a picture attached here and the same one uploaded from the browser are one library entry."""
    samples = {
        "jpeg-orientation-gps": (jpeg(exif=tiff((ORIENTATION, 8), (GPS, 1)), xmp=XMP_GPS, comment=b"c", icc=b"p"), "image/jpeg"),
        "jpeg-little-endian": (jpeg(exif=tiff((GPS, 1), (ORIENTATION, 3), little=True)), "image/jpeg"),
        "jpeg-clean": (jpeg(), "image/jpeg"),
        "png-text": (png(4, 3, extra=png_chunk(b"tEXt", b"Comment\0hi") + png_chunk(b"eXIf", tiff((GPS, 1)))), "image/png"),
        "webp-exif-xmp": (webp(5, 7, exif=tiff((GPS, 1)), xmp=XMP_GPS), "image/webp"),
        "gif": (gif(), "image/gif"),
        "jpeg-multi-picture": (jpeg(exif=tiff((ORIENTATION, 6)), extra=APP2_MPF, scan=SCANS) + jpeg(exif=tiff((GPS, 1))), "image/jpeg"),
        "jpeg-motion-photo": (jpeg(xmp=XMP_MOTION_PHOTO) + MP4_WITH_LOCATION, "image/jpeg"),
        "jpeg-app11-c2pa": (jpeg(extra=APP11_C2PA + segment(0xEE, b"Adobe\x00\x64"), icc=b"p"), "image/jpeg"),
        "jpeg-truncated": (jpeg(exif=tiff((ORIENTATION, 3)))[:-30], "image/jpeg"),
        "jpeg-stray-bytes": (with_stray_bytes(jpeg(exif=tiff((ORIENTATION, 6), (GPS, 1)), xmp=XMP_GPS, extra=APP11_C2PA)), "image/jpeg"),
        "jpeg-stray-to-the-end": (jpeg()[:20] + b"\x00\x12\x34", "image/jpeg"),
        "png-cabx": (png(4, 3, extra=png_chunk(b"caBX", C2PA) + png_chunk(b"pHYs", b"\0" * 9)) + b"tail", "image/png"),
        "webp-c2pa-trailer": (webp(5, 7, extra=webp_chunk(b"C2PA", C2PA)) + b"tail", "image/webp"),
        "gif-comment-xmp": (gif(blocks=GIF_LOOP + b"\x21\xfe\x02hi\x00" + GIF_FRAME + b"\x21\xff\x0bXMP DataXMP\x03abc\x00") + b"x", "image/gif"),
        "gif-truncated": (gif(blocks=GIF_LOOP + GIF_FRAME)[:-9], "image/gif"),
    }
    theirs = run_web_app(tmp_path, (
        "out = {};\nfor (const [name, [b64, type]] of Object.entries(given)) "
        "out[name] = Buffer.from(web.stripMetadata(new Uint8Array(Buffer.from(b64, 'base64')), type)).toString('base64');"),
        {name: [base64.b64encode(data).decode(), kind] for name, (data, kind) in samples.items()})
    for name, (data, kind) in samples.items():
        assert base64.b64decode(theirs[name]) == images.strip_metadata(data, kind), name


def test_an_empty_card_takes_the_same_shape_as_in_the_web_app(tmp_path):
    sizes = [[260, 4032, 3024], [260, 3024, 4032], [260, 800, 600], [260, 100, 50], [260, 1000, 4000],
             [260, 2000, 10], [360, 1920, 1080], [41, 7, 9]]
    theirs = run_web_app(tmp_path, "out = given.map(([w, iw, ih]) => web.fittedHeight(w, { width: iw, height: ih }));", sizes)
    assert theirs == [images.fitted_height(w, (iw, ih)) for w, iw, ih in sizes]


def test_a_line_goes_where_the_web_app_puts_it(tmp_path):
    texts = ["", "#ml", "Title", "Title\nBody\n#a #b", "\n\n  Title  \n\nmore", "## Heading\n- list"]
    line = f"![w](attachments/rag/{A})"
    theirs = run_web_app(tmp_path, f"out = given.map((t) => web.insertImageLine(t, {json.dumps(line)}).text);", texts)
    assert theirs == [images.insert_image_line(t, line) for t in texts]


def test_a_picture_is_left_out_of_a_paste_as_the_web_app_leaves_it_out(tmp_path):
    p = f"attachments/rag/{A}"
    texts = [f"![a]({p})", f"Title\n  ![a]({p})  \nend", f"see ![a]({p}) here", f"[link]({p}) and [](<{p}> \"t\")",
             f"bare {p} path", f"- ![a]({p})\n- keep", f"![a]({p})![b]({p})", f"![other](attachments/rag/{B}) {p}"]
    theirs = run_web_app(tmp_path, "out = given.map((t) => web.withoutImage(t, 'rag', " + json.dumps(A) + "));", texts)
    assert theirs == [images.without_image(t, "rag", A) for t in texts]


# A picture on the web whose address happens to hold a library-like path: a web image, not a library one.
WEB = "https://raw.githubusercontent.com/me/vault/main/attachments/{slug}/{file}"
IN_URLS = [f"![board]({WEB.format(slug='other', file=B)})", f"![board]({WEB.format(slug='rag', file=A)})",
           f"see {WEB.format(slug='rag', file=A)} here", f"[link](<{WEB.format(slug='other', file=A)}>)",
           f"<{WEB.format(slug='rag', file=A)}>", f"x/attachments/rag/{A} and ../attachments/other/{B}"]


def test_a_path_inside_a_web_address_is_no_library_path():
    assert images.foreign_images(IN_URLS, "here") == {}
    paths = {f"attachments/other/{B}": f"attachments/here/{B}", f"attachments/rag/{A}": f"attachments/here/{A}"}
    for text in IN_URLS:
        assert images.rewrite_images(text, paths) == text
        assert images.without_image(text, "rag", A) == text
        assert images.without_image(text, "other", B) == text
    doc = {"nodes": [{"id": f"n{i}", "type": "text", "text": text} for i, text in enumerate(IN_URLS)]}
    assert images.usage(doc, "rag") == {} and images.usage(doc, "other") == {}


def test_a_path_is_a_library_path_as_a_link_target_or_on_its_own():
    texts = [f"![a](attachments/rag/{A})", f"![a]( <attachments/rag/{A}> )", f"[a](attachments/rag/{A})",
             f"attachments/rag/{A}", f"Path:\tattachments/rag/{A}", f"x\nattachments/rag/{A}", f"![a](./attachments/rag/{A})",
             f"./attachments/rag/{A}"]
    doc = {"nodes": [{"id": f"n{i}", "type": "text", "text": text} for i, text in enumerate(texts)]}
    assert images.usage(doc, "rag") == {A: [f"n{i}" for i in range(len(texts))]}
    assert images.foreign_images(texts, "here") == {"rag": [A]}
    assert [images.rewrite_images(t, {f"attachments/rag/{A}": f"attachments/here/{A}"}) for t in texts] == \
        [t.replace("attachments/rag/", "attachments/here/") for t in texts]


def test_a_web_address_is_treated_as_the_web_app_treats_it(tmp_path):
    p = f"attachments/rag/{A}"
    texts = [*IN_URLS, f"![a](attachments/rag/{A}) {WEB.format(slug='rag', file=A)}", f"./attachments/other/{B}",
             f"bare attachments/other/{B} and https://x.test/attachments/other/{B}",
             f"{p} {p}", f"{p} ./{p},", f"x/{p}  {p},", f"{p}{p} {p}", f"({p}) <{p}>", f"see {WEB.format(slug='rag', file=A)} {p} end"]
    paths = {f"attachments/other/{B}": f"attachments/here/{B}", f"attachments/rag/{A}": f"attachments/here/{A}"}
    theirs = run_web_app(tmp_path, (
        "const doc = { nodes: given.texts.map((text, i) => ({ id: `n${i}`, type: 'text', text, x: 0, y: 0, width: 1, height: 1 })) };\n"
        "out = { foreign: web.foreignImages(given.texts.join('\\n'), 'here'),\n"
        "  rewritten: given.texts.map((t) => web.rewriteImages(t, given.paths)),\n"
        "  without: given.texts.map((t) => [web.withoutImage(t, 'rag', given.A), web.withoutImage(t, 'other', given.B)]),\n"
        "  usage: Object.fromEntries([...web.usage(doc, 'rag')]), usageOther: Object.fromEntries([...web.usage(doc, 'other')]) };"),
        {"texts": texts, "paths": paths, "A": A, "B": B})
    doc = {"nodes": [{"id": f"n{i}", "type": "text", "text": text} for i, text in enumerate(texts)]}
    assert theirs == {
        "foreign": images.foreign_images(["\n".join(texts)], "here"),
        "rewritten": [images.rewrite_images(t, paths) for t in texts],
        "without": [[images.without_image(t, "rag", A), images.without_image(t, "other", B)] for t in texts],
        "usage": images.usage(doc, "rag"), "usageOther": images.usage(doc, "other"),
    }
    assert theirs["foreign"] == {"rag": [A], "other": [B]} and set(theirs["usage"]) == {A}
