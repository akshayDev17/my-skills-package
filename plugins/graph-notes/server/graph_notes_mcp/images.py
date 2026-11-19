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

PATH = re.compile(rf"attachments/({SLUG.pattern})/({IMAGE_FILE.pattern})")
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
            re.compile(rf"{lead}{path}{trail}"))


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


def _jpeg_segments(b: bytes) -> tuple[list[tuple[int, bytes]], int]:
    """``([(marker, segment)], start)``: a JPEG's segments before its image data, and where that data
    starts."""
    segments: list[tuple[int, bytes]] = []
    at = 2
    while at + 4 <= len(b) and b[at] == 0xFF:
        marker = b[at + 1]
        if marker == 0xFF:
            at += 1
            continue
        if marker in (0xDA, 0xD9):  # start of scan (or the end): the rest is image data
            break
        end = at + 2 + ((b[at + 2] << 8) | b[at + 3])
        segments.append((marker, b[at:end]))
        at = end
    return segments, at


def jpeg_orientation(b: bytes) -> int | None:
    """The EXIF orientation (1..8) of a JPEG, from its first Exif segment that has one, or None."""
    for marker, segment in _jpeg_segments(b)[0]:
        if marker == 0xE1 and segment[4:8] == b"Exif":
            orientation = exif_orientation(segment[10:])
            if orientation:
                return orientation
    return None


def _strip_jpeg(b: bytes) -> bytes:
    """Drops APP1 (Exif, XMP), APP13 (IPTC) and comments, keeping an orientation-only Exif so photos stay
    upright, and every other segment (the colour profile among them) and the image data as they are."""
    segments, start = _jpeg_segments(b)
    kept = [b[:2], *(segment for marker, segment in segments if marker not in (0xE1, 0xED, 0xFE))]
    orientation = jpeg_orientation(b)
    if orientation and orientation != 1:
        placed = any(marker == 0xE0 for marker, _ in segments)  # the Exif goes after JFIF's APP0
        kept.insert(2 if placed else 1, _orientation_segment(orientation))
    return b"".join(kept) + b[start:]


_PNG_DROPPED = {b"eXIf", b"tEXt", b"zTXt", b"iTXt", b"tIME"}


def _strip_png(b: bytes) -> bytes:
    """Drops the eXIf chunk and the text chunks (where XMP and other notes live)."""
    kept = [b[:8]]
    at = 8
    while at + 12 <= len(b):
        size, kind = _u32(b, at, False), b[at + 4:at + 8]
        end = at + 12 + size
        if kind not in _PNG_DROPPED:
            kept.append(b[at:end])
        at = end
        if kind == b"IEND":
            break
    return b"".join(kept)


def _strip_webp(b: bytes) -> bytes:
    """Drops the EXIF and XMP chunks, clearing their flags in VP8X and fixing the RIFF size."""
    kept = []
    at = 12
    while at + 8 <= len(b):
        kind, size = b[at:at + 4], _u32(b, at + 4, True)
        end = min(len(b), at + 8 + size + (size & 1))
        if kind == b"VP8X":
            chunk = bytearray(b[at:end])
            if len(chunk) > 8:
                chunk[8] &= ~(0x08 | 0x04) & 0xFF  # EXIF and XMP present
            kept.append(bytes(chunk))
        elif kind not in (b"EXIF", b"XMP "):
            kept.append(b[at:end])
        at = end
    body = b"".join(kept)
    return b"RIFF" + (len(body) + 4).to_bytes(4, "little") + b[8:12] + body


def strip_metadata(data: bytes, content_type: str) -> bytes:
    """The same picture without location and other metadata, losslessly: no pixel is decoded or changed.
    JPEG keeps only its orientation and colour profile; PNG loses eXIf and text chunks; WebP its EXIF and
    XMP chunks; GIF is returned as it is."""
    if content_type == "image/jpeg":
        return _strip_jpeg(data)
    if content_type == "image/png":
        return _strip_png(data)
    if content_type == "image/webp":
        return _strip_webp(data)
    return data
