"""Each graph's image library, as this server sees it: pure functions over note text and file bytes.

A card shows an image with a Markdown line in its text, ``![name](attachments/<graph slug>/<file>)``,
the path the web app writes and the Obsidian download uses. The bytes live in the app's private
bucket and are reached only through the app's API (backend/graph_notes/api.py), so all this server
does with a path is read it out of note text, copy what another graph's paths name into this graph's
library, and rewrite them, as the web app's paste does (frontend/app/page.tsx ``pasteFromGraphs``).

``strip_metadata`` is the web app's lossless removal of location and other metadata
(frontend/lib/images.ts ``stripMetadata``), byte for byte: the same picture attached here or
uploaded from the browser is the same file, so the library keeps it once.
"""

from __future__ import annotations

import math
import re

from .canvas import join_tags, split_tags
from .client import IMAGE_FILE, SLUG

# Cloudflare refuses request bodies over 100 MB on this zone's plan (Free and Pro), with a 413.
CLOUDFLARE_MAX_BYTES = 100_000_000
# How tall an empty card grows to take an image's shape (the web app's MAX_FIT_HEIGHT).
MAX_FIT_HEIGHT = 600

# Where a library path can start: a line's start, after a space, `(` or `<` (a link's target), or
# after `./` there; never inside a web address such as https://host/vault/attachments/<slug>/<file>,
# which is a web image (the web app's PATH_START, frontend/lib/images.ts).
PATH_START = r"(?:(?<![^ \t\r\n(<])|(?<=\./)(?<![^ \t\r\n(<]\./))"
PATH = re.compile(rf"{PATH_START}attachments/({SLUG.pattern})/({IMAGE_FILE.pattern})")
_PICTURE = re.compile(rf"!\[([^\]\n]*)\]\(\s*<?attachments/({SLUG.pattern})/({IMAGE_FILE.pattern})")


def image_path(slug: str, file: str) -> str:
    return f"attachments/{slug}/{file}"


def image_line(name: str, path: str) -> str:
    """``![name](path)``, the name made safe as Markdown alt text (the web app's ``imageLine``)."""
    alt = " ".join(re.sub(r"[\[\]\\]", "", name).split())
    return f"![{alt}]({path})"


# ── which images note text names ──────────────────────────────────────────

def foreign_images(texts: list[str], slug: str) -> dict[str, list[str]]:
    """``{source slug: [files]}``: every library path in ``texts`` that names a graph other than ``slug``,
    in order of first mention."""
    out: dict[str, list[str]] = {}
    for text in texts:
        for source, file in PATH.findall(text):
            if source != slug and file not in out.setdefault(source, []):
                out[source].append(file)
    return out


def rewrite_images(text: str, paths: dict[str, str]) -> str:
    """``text`` with each path in ``paths`` (old path -> new path) rewritten."""
    return PATH.sub(lambda m: paths.get(m[0], m[0]), text)


def picture_name(texts: list[str], slug: str, file: str) -> str:
    """What a picture is called where ``texts`` show it: its alt text, else its file name."""
    for text in texts:
        for alt, s, f in _PICTURE.findall(text):
            if (s, f) == (slug, file) and alt:
                return alt
    return file


def usage(canvas: dict, slug: str) -> dict[str, list[str]]:
    """Which text cards mention each of ``slug``'s images: ``{file: [card ids]}`` (what the library's
    "On cards" counts, and the files the public route serves)."""
    out: dict[str, list[str]] = {}
    for node in canvas.get("nodes") or []:
        if isinstance(node, dict) and node.get("type") == "text" and isinstance(node.get("text"), str):
            for s, file in PATH.findall(node["text"]):
                if s == slug and node["id"] not in out.setdefault(file, []):
                    out[file].append(node["id"])
    return out


def _mentions(slug: str, file: str) -> tuple[re.Pattern[str], re.Pattern[str], re.Pattern[str]]:
    """``(image, link, bare)``: each form a card can mention that file in, with the spaces either side."""
    path = re.escape(image_path(slug, file))
    target = rf"""\(\s*<?{path}>?(?:\s+(?:"[^"\n]*"|'[^'\n]*'|\([^)\n]*\)))?\s*\)"""
    lead, trail = r"(?P<lead>[ \t]*)", r"(?P<trail>[ \t]*)"
    return (re.compile(rf"{lead}!\[[^\]\n]*\]{target}{trail}"),
            re.compile(rf"{lead}\[(?P<text>[^\]\n]*)\]{target}{trail}"),
            re.compile(rf"{lead}{PATH_START}{path}{trail}"))


def _close_gap(m: re.Match[str]) -> str:
    """What stays where a mention was taken out of a line: its indentation at the line's start,
    nothing at its end, one space between words."""
    if m.start() == 0:
        return m.group("lead")
    if m.end() == len(m.string):
        return ""
    return " " if m.group("lead") or m.group("trail") else ""


def _link_text(m: re.Match[str]) -> str:
    return m.group("lead") + m.group("text") + m.group("trail") if m.group("text") else _close_gap(m)


def without_image(text: str, slug: str, file: str) -> str:
    """``text`` with no mention of that image left, as the web app's paste leaves out a picture it could
    not copy (and as the app's Delete image takes one out): an image goes, a link keeps its text, the
    bare path goes; a line left empty goes whole, and the rest of each line stays as it was."""
    image, link, bare = _mentions(slug, file)
    lines = []
    for line in text.split("\n"):
        rest = bare.sub(_close_gap, link.sub(_link_text, image.sub(_close_gap, line)))
        if rest == line or rest.strip():
            lines.append(rest)
    return "\n".join(lines)


# ── putting an image into a note ───────────────────────────────────────────

def insert_image_line(text: str, line: str) -> str:
    """``text`` with ``line`` right below its first line (its title); in an empty note, as the whole
    text. A trailing ``#tag`` line stays last (the web app's ``insertImageLine`` with no caret)."""
    body, tags = split_tags(text)
    if not body.strip():
        return join_tags(line, tags)
    lines = body.split("\n")
    first = next(i for i, l in enumerate(lines) if l.strip())
    lines.insert(first + 1, line)
    return join_tags("\n".join(lines), tags)


def is_empty_note(text: str) -> bool:
    return not split_tags(text)[0].strip()


def fitted_height(width: int, size: tuple[int, int]) -> int:
    """The height an empty card of ``width`` takes to show an image of ``size`` whole: the image at the
    card's inner width, with the card's padding and border above and below it alike (the space its line
    keeps below it falls past the card's bottom, which clips it); at most MAX_FIT_HEIGHT, at least the
    editor's smallest card (the web app's ``fittedHeight``)."""
    inner = max(1, width - 2 * 12 - 2 * 2)
    drawn = min(size[0], inner) * (size[1] / size[0])
    return min(MAX_FIT_HEIGHT, max(40, math.ceil(drawn + 2 * (8 + 2))))


def drawn_size(data: bytes, content_type: str, size: tuple[int, int]) -> tuple[int, int]:
    """A picture's ``size`` as its header gives it, turned the way a browser draws the file: a JPEG whose
    EXIF orientation is a quarter turn (5 to 8) has its width and height swapped (the size the web app
    shapes an empty card from)."""
    if content_type == "image/jpeg" and (jpeg_orientation(data) or 1) >= 5:
        return size[1], size[0]
    return size


# ── what a file is, from its first bytes ───────────────────────────────────

def sniff(data: bytes) -> str | None:
    """The content type a file's signature says: one of the four the library stores, ``"heic"``,
    ``"svg"``, or None (the web app's ``sniffImage``)."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:8] == b"ftyp" and data[8:12] in (b"heic", b"heix", b"hevc", b"heim", b"heis", b"mif1", b"msf1", b"heif"):
        return "heic"
    head = data[:512].decode("utf-8", "replace").lstrip().lower()
    if head.startswith("<svg") or (head.startswith("<?xml") and "<svg" in head):
        return "svg"
    return None


# ── lossless removal of location and other metadata ───────────────────────

def _u16(b: bytes, i: int, little: bool) -> int:
    return int.from_bytes(b[i:i + 2], "little" if little else "big")


def _u32(b: bytes, i: int, little: bool) -> int:
    return int.from_bytes(b[i:i + 4], "little" if little else "big")


def exif_orientation(tiff: bytes) -> int | None:
    """The EXIF orientation (1..8) in a TIFF block, or None."""
    if len(tiff) < 8:
        return None
    little = tiff[:2] == b"II"
    if not little and tiff[:2] != b"MM":
        return None
    ifd = _u32(tiff, 4, little)
    if ifd + 2 > len(tiff):
        return None
    for i in range(_u16(tiff, ifd, little)):
        at = ifd + 2 + 12 * i
        if at + 12 > len(tiff):
            return None
        if _u16(tiff, at, little) == 0x0112:
            value = _u16(tiff, at + 8, little)
            return value if 1 <= value <= 8 else None
    return None


def _orientation_segment(orientation: int) -> bytes:
    """An APP1 Exif segment holding nothing but the orientation."""
    tiff = bytes([0x4D, 0x4D, 0x00, 0x2A, 0, 0, 0, 8, 0, 1, 0x01, 0x12, 0, 3, 0, 0, 0, 1, 0, orientation, 0, 0, 0, 0, 0, 0])
    payload = b"Exif\x00\x00" + tiff
    length = len(payload) + 2
    return bytes([0xFF, 0xE1, length >> 8, length & 0xFF]) + payload


def _jpeg_segments(b: bytes) -> list[tuple[int, bytes]]:
    """``[(marker, segment)]``: a JPEG's segments before its first scan."""
    segments: list[tuple[int, bytes]] = []
    at = 2
    while at + 4 <= len(b):
        if b[at] != 0xFF or b[at + 1] == 0x00:
            at = b.find(b"\xff", at + 1)  # not a marker: skipped, as _strip_jpeg skips it
            if at < 0:
                break
            continue
        marker = b[at + 1]
        if marker == 0xFF:
            at += 1
            continue
        if marker in (0xDA, 0xD9):  # start of scan (or the end): the rest is image data
            break
        if marker == 0x01 or 0xD0 <= marker <= 0xD8:  # markers without a length
            at += 2
            continue
        end = at + 2 + ((b[at + 2] << 8) | b[at + 3])
        segments.append((marker, b[at:end]))
        at = end
    return segments


def jpeg_orientation(b: bytes) -> int | None:
    """The EXIF orientation (1..8) of a JPEG, from its first Exif segment that has one, or None."""
    for marker, segment in _jpeg_segments(b):
        if marker == 0xE1 and segment[4:8] == b"Exif":
            orientation = exif_orientation(segment[10:])
            if orientation:
                return orientation
    return None


# The application segments a JPEG decoder reads, by marker and signature: JFIF, the colour profile, and
# Adobe's colour transform. Every other one (APP1 Exif and XMP, APP2 multi-picture index, APP11 content
# credentials, APP13 IPTC, makers' own) is metadata.
_JPEG_KEPT_APPS = {0xE0: b"JFIF\x00", 0xE2: b"ICC_PROFILE\x00", 0xEE: b"Adobe"}


def _strip_jpeg(b: bytes) -> bytes:
    """The primary image only, from SOI to its EOI, so later pictures (a multi-picture file's) and
    trailers (a motion photo's video) go. Of its segments, drops comments and every application segment
    but JFIF, the colour profile and Adobe's, adding back an orientation-only Exif so photos stay upright;
    the tables, frame and scans (the image data) stay as they are."""
    kept = [b[:2]]
    placed = False
    at = 2
    while at + 2 <= len(b):
        if b[at] != 0xFF or b[at + 1] == 0x00:
            at = b.find(b"\xff", at + 1)  # not a marker: skipped up to the next 0xff, as decoders skip it
            if at < 0:
                break
            continue
        marker = b[at + 1]
        if marker == 0xFF:
            at += 1  # fill
            continue
        if marker == 0xD9:
            kept.append(b[at:at + 2])  # the primary image's end: what follows is not shown
            break
        if marker == 0x01 or 0xD0 <= marker <= 0xD8:
            kept.append(b[at:at + 2])  # markers without a length
            at += 2
            continue
        if at + 4 > len(b):
            kept.append(b[at:])
            break
        end = at + 2 + ((b[at + 2] << 8) | b[at + 3])
        is_app = 0xE0 <= marker <= 0xEF or marker == 0xFE
        if not is_app or (marker in _JPEG_KEPT_APPS and b[at + 4:end].startswith(_JPEG_KEPT_APPS[marker])):
            if len(kept) == 1 and marker == 0xE0:
                placed = True  # the Exif goes after JFIF's APP0
            kept.append(b[at:end])
        at = end
        if marker == 0xDA:
            # The scan's entropy-coded data, up to the next marker that is not a restart (0xff 0x00 is a
            # data byte; fill bytes, more 0xff, may come before a marker).
            start = at
            while True:
                at = b.find(b"\xff", at)
                after = at + 1
                while 0 <= at and after < len(b) and b[after] == 0xFF:
                    after += 1
                if at < 0 or after >= len(b):
                    at = len(b)
                    break
                if b[after] != 0x00 and not 0xD0 <= b[after] <= 0xD7:
                    break
                at = after + 1
            kept.append(b[start:at])
    orientation = jpeg_orientation(b)
    if orientation and orientation != 1:
        kept.insert(2 if placed else 1, _orientation_segment(orientation))
    return b"".join(kept)


# The ancillary PNG chunks a decoder reads: transparency, colour, pixel size, animation. The rest (eXIf,
# text chunks where XMP lives, tIME, caBX content credentials, makers' own) are metadata.
_PNG_KEPT = {b"tRNS", b"gAMA", b"cHRM", b"sRGB", b"iCCP", b"cICP", b"mDCV", b"cLLI", b"sBIT", b"bKGD", b"pHYs",
             b"acTL", b"fcTL", b"fdAT"}


def _strip_png(b: bytes) -> bytes:
    """Keeps the critical chunks (header, palette, image data, end) and the ancillary chunks above, up
    to IEND."""
    kept = [b[:8]]
    at = 8
    while at + 12 <= len(b):
        size, kind = _u32(b, at, False), b[at + 4:at + 8]
        end = at + 12 + size
        critical = not b[at + 4] & 0x20
        if critical or kind in _PNG_KEPT:
            kept.append(b[at:end])
        at = end
        if kind == b"IEND":
            break
    return b"".join(kept)


# The WebP chunks a decoder reads; EXIF, XMP, C2PA and any others are metadata.
_WEBP_KEPT = {b"VP8X", b"VP8 ", b"VP8L", b"ALPH", b"ANIM", b"ANMF", b"ICCP"}


def _strip_webp(b: bytes) -> bytes:
    """Keeps the chunks above, within the RIFF's own size (so nothing after it), clearing the EXIF and
    XMP flags in VP8X and fixing the RIFF size."""
    kept = []
    limit = min(len(b), 8 + _u32(b, 4, True))
    at = 12
    while at + 8 <= limit:
        kind, size = b[at:at + 4], _u32(b, at + 4, True)
        end = min(limit, at + 8 + size + (size & 1))
        if kind == b"VP8X":
            chunk = bytearray(b[at:end])
            if len(chunk) > 8:
                chunk[8] &= ~(0x08 | 0x04) & 0xFF  # EXIF and XMP present
            kept.append(bytes(chunk))
        elif kind in _WEBP_KEPT:
            kept.append(b[at:end])
        at = end
    body = b"".join(kept)
    return b"RIFF" + (len(body) + 4).to_bytes(4, "little") + b[8:12] + body


# The GIF application extensions a decoder reads: looping (two spellings) and the colour profile.
_GIF_KEPT_APPS = (b"NETSCAPE2.0", b"ANIMEXTS1.0", b"ICCRGBG1012")


def _after_sub_blocks(b: bytes, at: int) -> int:
    """Where a run of GIF data sub-blocks starting at ``at`` ends (after its zero-length terminator)."""
    while at < len(b):
        size = b[at]
        at += 1 + size
        if size == 0:
            break
    return min(at, len(b))


def _byte(b: bytes, i: int) -> int:
    return b[i] if i < len(b) else 0


def _strip_gif(b: bytes) -> bytes:
    """Keeps the header, colour tables, frames, their graphic controls and plain text, and the extensions
    above, up to the trailer; drops comments, other application extensions (XMP among them) and anything
    after the trailer."""
    at = 13
    if _byte(b, 10) & 0x80:
        at += 3 << ((b[10] & 0x07) + 1)  # global colour table
    kept = [b[:at]]
    while at < len(b):
        start = at
        if b[at] == 0x3B:
            kept.append(b[at:at + 1])
            break
        if b[at] == 0x2C:
            flags = _byte(b, at + 9)
            at += 10
            if flags & 0x80:
                at += 3 << ((flags & 0x07) + 1)  # local colour table
            at = _after_sub_blocks(b, at + 1)  # after the LZW code size, the image data
            kept.append(b[start:at])
        elif b[at] == 0x21:
            label = _byte(b, at + 1)
            at = _after_sub_blocks(b, at + 2)
            app = label == 0xFF and _byte(b, start + 2) == 11 and b[start + 3:start + 14] in _GIF_KEPT_APPS
            if label in (0xF9, 0x01) or app:
                kept.append(b[start:at])
        else:
            break  # not a block a decoder reads: the rest goes
    return b"".join(kept)


def strip_metadata(data: bytes, content_type: str) -> bytes:
    """The same picture without location and other metadata, losslessly: no pixel is decoded or changed.
    Each format keeps only what a decoder reads to draw it (and JPEG its orientation, as an Exif of that
    alone): JPEG its primary image, PNG, WebP and GIF their image data, colour and animation; everything
    else, and anything after the picture's end, goes."""
    if content_type == "image/jpeg":
        return _strip_jpeg(data)
    if content_type == "image/png":
        return _strip_png(data)
    if content_type == "image/webp":
        return _strip_webp(data)
    if content_type == "image/gif":
        return _strip_gif(data)
    return data
