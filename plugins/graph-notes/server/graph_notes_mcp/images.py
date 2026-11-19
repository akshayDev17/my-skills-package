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
from collections.abc import Callable
from typing import NamedTuple

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


def _be16(n: int) -> bytes:
    return (n & 0xFFFF).to_bytes(2, "big")


def _be32(n: int) -> bytes:
    return (n & 0xFFFFFFFF).to_bytes(4, "big")


def _segment(marker: int, payload: bytes) -> bytes:
    """A JPEG segment: its marker, its length, its payload."""
    return bytes((0xFF, marker)) + _be16(len(payload) + 2) + payload


def _orientation_segment(orientation: int) -> bytes:
    """An APP1 Exif segment holding nothing but the orientation."""
    tiff = bytes([0x4D, 0x4D, 0x00, 0x2A, 0, 0, 0, 8, 0, 1, 0x01, 0x12, 0, 3, 0, 0, 0, 1, 0, orientation, 0, 0, 0, 0, 0, 0])
    return _segment(0xE1, b"Exif\x00\x00" + tiff)


def _exif_segment(orientation: int | None, headroom: list[tuple[int, bytes]] | None) -> bytes | None:
    """The primary image's Exif: its orientation alone, or, beside an Apple gain map, that and the HDR
    headroom Apple's maker note gives (its tags 33 and 48, ``[(tag, value)]``), which HDR decoders draw
    that gain map with, in a maker note of nothing else. None when there is nothing to say (the web app's
    ``exifSegment``)."""
    turn = orientation if orientation and orientation != 1 else 0
    if headroom is None:
        return _orientation_segment(turn) if turn else None
    # Apple's maker note: its signature, then an IFD whose values sit after it, at offsets from its start.
    note = b"Apple iOS\x00\x00\x01MM" + _be16(len(headroom))
    note += b"".join(_be16(tag) + b"\x00\x0a\x00\x00\x00\x01" + _be32(14 + 2 + 12 * len(headroom) + 4 + 8 * i)
                     for i, (tag, _) in enumerate(headroom))
    note += b"\x00\x00\x00\x00" + b"".join(value for _, value in headroom)
    count = 2 if turn else 1
    exif_ifd = 8 + 2 + 12 * count + 4
    tiff = b"MM\x00\x2a\x00\x00\x00\x08" + _be16(count)
    if turn:
        tiff += bytes([0x01, 0x12, 0, 3, 0, 0, 0, 1, 0, turn, 0, 0])
    tiff += b"\x87\x69\x00\x04\x00\x00\x00\x01" + _be32(exif_ifd) + b"\x00\x00\x00\x00"  # the Exif IFD, holding the maker note
    tiff += b"\x00\x01\x92\x7c\x00\x07" + _be32(len(note)) + _be32(exif_ifd + 2 + 12 + 4) + b"\x00\x00\x00\x00" + note
    return _segment(0xE1, b"Exif\x00\x00" + tiff)


def _apple_headroom(tiff: bytes) -> list[tuple[int, bytes]] | None:
    """Apple's HDR headroom in a TIFF block (an Exif's): its maker note's tags 33 and 48
    (``[(tag, value)]``, 33 first), signed rationals; None unless it has 33."""
    if len(tiff) < 8:
        return None
    little = tiff[:2] == b"II"
    if not little and tiff[:2] != b"MM":
        return None

    def entry(ifd: int, tag: int) -> int:
        if ifd + 2 > len(tiff):
            return -1
        for i in range(_u16(tiff, ifd, little)):
            at = ifd + 2 + 12 * i
            if at + 12 > len(tiff):
                return -1
            if _u16(tiff, at, little) == tag:
                return at
        return -1

    exif = entry(_u32(tiff, 4, little), 0x8769)
    at = -1 if exif < 0 else entry(_u32(tiff, exif + 8, little), 0x927C)
    if at < 0 or _u16(tiff, at + 2, little) != 7:
        return None
    size, start = _u32(tiff, at + 4, little), _u32(tiff, at + 8, little)
    note = tiff[start:start + size]
    if size < 16 or start + size > len(tiff) or not note.startswith(b"Apple iOS\x00\x00\x01MM"):
        return None
    found: dict[int, bytes] = {}
    count, i = _u16(note, 14, False), 0
    while i < count and 16 + 12 * i + 12 <= len(note):
        e = 16 + 12 * i
        tag, value = _u16(note, e, False), _u32(note, e + 8, False)
        rational = _u16(note, e + 2, False) == 10 and _u32(note, e + 4, False) == 1 and value + 8 <= len(note)
        if tag in (33, 48) and rational and tag not in found:
            found[tag] = note[value:value + 8]
        i += 1
    if 33 not in found:
        return None
    return [(tag, found[tag]) for tag in (33, 48) if tag in found]


def _jpeg_segments(b: bytes, start: int = 0) -> list[tuple[int, int, int]]:
    """``[(marker, start, end)]``: the segments of the picture whose SOI is at ``start``, before its first
    scan."""
    segments: list[tuple[int, int, int]] = []
    at = start + 2
    while at + 4 <= len(b):
        if b[at] != 0xFF or b[at + 1] == 0x00:
            at = b.find(b"\xff", at + 1)  # not a marker: skipped, as _walk_picture skips it
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
        segments.append((marker, at, end))
        at = end
    return segments


def jpeg_orientation(b: bytes) -> int | None:
    """The EXIF orientation (1..8) of a JPEG, from its first Exif segment that has one, or None."""
    for marker, start, end in _jpeg_segments(b):
        if marker == 0xE1 and b[start + 4:start + 8] == b"Exif":
            orientation = exif_orientation(b[start + 10:end])
            if orientation:
                return orientation
    return None


# The application segments a JPEG decoder reads, by marker and signature: JFIF, the colour profile, and
# Adobe's colour transform. Every other one (APP1 Exif and XMP, APP2 multi-picture index, APP11 content
# credentials, APP13 IPTC, makers' own) is metadata, but for what an HDR gain map is drawn with
# (_find_gain_map).
_JPEG_KEPT_APPS = {0xE0: b"JFIF\x00", 0xE2: b"ICC_PROFILE\x00", 0xEE: b"Adobe"}

_XMP = b"http://ns.adobe.com/xap/1.0/\x00"
_ISO_21496 = b"urn:iso:std:iso:ts:21496:-1\x00"
# The namespaces a gain map's XMP is written with, by the prefix it is written with.
_GAIN_MAP_NS = {
    "hdrgm": "http://ns.adobe.com/hdr-gain-map/1.0/",
    "apdi": "http://ns.apple.com/pixeldatainfo/1.0/",
    "HDRGainMap": "http://ns.apple.com/HDRGainMap/1.0/",
}
_NUMBER = r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?"
_INTEGER = r"[0-9]+"
_VERSION = r"[0-9]+(?:\.[0-9]+)*"
# Adobe's gain map fields (as Ultra HDR writes them): name, what a value may be, and whether it may be
# three (one per colour channel).
_HDRGM_FIELDS = (
    ("Version", _VERSION, False), ("BaseRenditionIsHDR", r"True|False", False), ("GainMapMin", _NUMBER, True),
    ("GainMapMax", _NUMBER, True), ("Gamma", _NUMBER, True), ("OffsetSDR", _NUMBER, True), ("OffsetHDR", _NUMBER, True),
    ("HDRCapacityMin", _NUMBER, False), ("HDRCapacityMax", _NUMBER, False),
)
# Apple's gain map fields: prefix, name, what the value may be.
_APPLE_FIELDS = (
    ("apdi", "AuxiliaryImageType", r"urn:com:apple:photo:2020:aux:hdrgainmap"), ("apdi", "StoredFormat", _INTEGER),
    ("apdi", "NativeFormat", _INTEGER), ("HDRGainMap", "HDRGainMapVersion", _INTEGER),
    ("HDRGainMap", "HDRGainMapHeadroom", _NUMBER),
)
_XML_SPACE = r"[ \t\r\n]"


def _trim_xml(s: str) -> str:
    return s.strip(" \t\r\n")


def _xmp_text(b: bytes, start: int, end: int) -> str:
    """The text of an XMP segment (from where it starts to where it ends), a character per byte."""
    return b[start + 4 + len(_XMP):end].decode("latin-1")


def _xmp_prefix(xmp: str, uri: str) -> str | None:
    """The prefix XMP text declares for namespace ``uri``, or None."""
    m = re.search(rf"""xmlns:([A-Za-z_][A-Za-z0-9_.-]*){_XML_SPACE}*={_XML_SPACE}*(["']){re.escape(uri)}\2""", xmp)
    return m[1] if m else None


def _xmp_property(xmp: str, name: str) -> list[str] | None:
    """What XMP text gives property ``name`` (prefix:name): an attribute's value, an element's text, or
    the items of the rdf:Seq the element holds, each trimmed; anything else the element holds comes back
    whole, which no field allows. None if the text does not have it (the web app's ``xmpProperty``)."""
    n, s = re.escape(name), _XML_SPACE
    attribute = re.search(rf"""{s}{n}{s}*={s}*(?:"([^"]*)"|'([^']*)')""", xmp)
    if attribute:
        return [_trim_xml(attribute[1] if attribute[1] is not None else attribute[2])]
    element = re.search(rf"<{n}(?:{s}[^>]*)?>([^<]*(?:<(?!/{n}{s}*>)[^<]*)*)</{n}{s}*>", xmp)
    if not element:
        return None
    inner = element[1]
    if "<" not in inner:
        return [_trim_xml(inner)]
    seq = re.fullmatch(rf"{s}*<rdf:Seq{s}*>((?:{s}*<rdf:li{s}*>[^<]*</rdf:li{s}*>)*){s}*</rdf:Seq{s}*>{s}*", inner)
    if not seq:
        return [inner]
    return [_trim_xml(item) for item in re.findall(rf"<rdf:li{s}*>([^<]*)</rdf:li{s}*>", seq[1])]


def _hdrgm_version(xmp: str) -> str | None:
    """The hdrgm:Version a primary image's XMP gives (that its gain map is Adobe's, as Ultra HDR writes
    it), or None."""
    prefix = _xmp_prefix(xmp, _GAIN_MAP_NS["hdrgm"])
    value = None if prefix is None else _xmp_property(xmp, f"{prefix}:Version")
    return value[0] if value and len(value) == 1 and re.fullmatch(_VERSION, value[0]) else None


def _hdrgm_fields(xmp: str) -> list[tuple[str, list[str]]] | None:
    """A gain map image's Adobe fields, ``[(name, values)]`` (written with the hdrgm prefix), each value
    what the format allows; None unless it gives the version, or if any field it gives is not what the
    format allows."""
    prefix = _xmp_prefix(xmp, _GAIN_MAP_NS["hdrgm"])
    if prefix is None:
        return None
    out = []
    for name, pattern, channels in _HDRGM_FIELDS:
        values = _xmp_property(xmp, f"{prefix}:{name}")
        if values is None:
            continue
        if not (len(values) == 1 or (channels and len(values) == 3)) or not all(re.fullmatch(pattern, v) for v in values):
            return None
        out.append((f"hdrgm:{name}", values))
    return out if out and out[0][0] == "hdrgm:Version" else None


def _apple_fields(xmp: str) -> list[tuple[str, list[str]]] | None:
    """A gain map image's Apple fields, as _hdrgm_fields gives Adobe's; None unless it says it is Apple's
    HDR gain map and gives its version."""
    out = []
    for ns, name, pattern in _APPLE_FIELDS:
        prefix = _xmp_prefix(xmp, _GAIN_MAP_NS[ns])
        values = None if prefix is None else _xmp_property(xmp, f"{prefix}:{name}")
        if values is None:
            continue
        if len(values) != 1 or not re.fullmatch(pattern, values[0]):
            return None
        out.append((f"{ns}:{name}", values))
    names = {name for name, _ in out}
    return out if {"apdi:AuxiliaryImageType", "HDRGainMap:HDRGainMapVersion"} <= names else None


_XMP_START = ('<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
              '<rdf:Description rdf:about=""')
_XMP_END = "</rdf:Description></rdf:RDF></x:xmpmeta>"


def _xmp_segment(xml: str) -> bytes:
    """An APP1 XMP segment holding ``xml``."""
    return _segment(0xE1, _XMP + xml.encode("ascii"))


def _gain_map_xmp(fields: list[tuple[str, list[str]]]) -> str:
    """A gain map image's XMP: ``fields`` alone, as their writers lay them out: Adobe's one value as an
    attribute (as Ultra HDR writes it), Apple's as an element (decoders read it only so), three in an
    rdf:Seq."""
    used = {name.split(":")[0] for name, _ in fields}
    xmlns = "".join(f' xmlns:{prefix}="{uri}"' for prefix, uri in _GAIN_MAP_NS.items() if prefix in used)

    def is_attribute(name: str, values: list[str]) -> bool:
        return name.startswith("hdrgm:") and len(values) == 1

    def element(name: str, values: list[str]) -> str:
        items = "".join(f"<rdf:li>{v}</rdf:li>" for v in values)
        return f"<{name}>{values[0] if len(values) == 1 else f'<rdf:Seq>{items}</rdf:Seq>'}</{name}>"

    attributes = "".join(f' {name}="{values[0]}"' for name, values in fields if is_attribute(name, values))
    elements = "".join(element(name, values) for name, values in fields if not is_attribute(name, values))
    return f"{_XMP_START}{xmlns}{attributes}>{elements}{_XMP_END}"


def _primary_xmp(version: str, length: int) -> str:
    """The primary image's XMP beside an Adobe gain map: its version, and where the gain map is (the
    container directory Ultra HDR writes: the primary, then a gain map of ``length`` bytes)."""
    def item(attributes: str) -> str:
        return f'<rdf:li rdf:parseType="Resource"><Container:Item {attributes}/></rdf:li>'
    return (f'{_XMP_START} xmlns:hdrgm="{_GAIN_MAP_NS["hdrgm"]}" xmlns:Container="http://ns.google.com/photos/1.0/container/"'
            f' xmlns:Item="http://ns.google.com/photos/1.0/container/item/" hdrgm:Version="{version}"><Container:Directory><rdf:Seq>'
            + item('Item:Semantic="Primary" Item:Mime="image/jpeg"')
            + item(f'Item:Semantic="GainMap" Item:Mime="image/jpeg" Item:Length="{length}"')
            + f"</rdf:Seq></Container:Directory>{_XMP_END}")


def _mpf_segment(size: int, map_offset: int, map_size: int) -> bytes:
    """A multi-picture index (APP2 MPF, big-endian, as Ultra HDR writes it) of two pictures: the primary of
    ``size`` bytes, then the gain map of ``map_size`` bytes at ``map_offset`` from the index's TIFF header."""
    return _segment(0xE2, b"MPF\x00MM\x00\x2a\x00\x00\x00\x08\x00\x03"
                    + b"\xb0\x00\x00\x07\x00\x00\x00\x04" + b"0100"  # version
                    + b"\xb0\x01\x00\x04\x00\x00\x00\x01\x00\x00\x00\x02"  # number of pictures
                    + b"\xb0\x02\x00\x07\x00\x00\x00\x20\x00\x00\x00\x32"  # their entries, at 50
                    + b"\x00\x00\x00\x00"
                    + b"\x00\x03\x00\x00" + _be32(size) + b"\x00" * 8  # the primary
                    + b"\x00" * 4 + _be32(map_size) + _be32(map_offset) + b"\x00" * 4)  # the gain map


def _mpf_pictures(tiff: bytes, base: int) -> list[tuple[int, int]] | None:
    """The pictures after the first that a multi-picture index lists (``tiff``: its TIFF header onward, at
    ``base`` in the file), each one's ``(start in the file, size)``; None if it lists none."""
    if len(tiff) < 8:
        return None
    little = tiff[:2] == b"II"
    if not little and tiff[:2] != b"MM":
        return None
    ifd = _u32(tiff, 4, little)
    if ifd + 2 > len(tiff):
        return None
    count, entries = 0, -1
    for i in range(_u16(tiff, ifd, little)):
        at = ifd + 2 + 12 * i
        if at + 12 > len(tiff):
            return None
        tag = _u16(tiff, at, little)
        if tag == 0xB001:
            count = _u32(tiff, at + 8, little)
        if tag == 0xB002:
            entries = _u32(tiff, at + 8, little)
    if count < 2 or entries < 0 or entries + 16 * count > len(tiff):
        return None
    return [(base + _u32(tiff, entries + 16 * i + 8, little), _u32(tiff, entries + 16 * i + 4, little))
            for i in range(1, count)]


class _GainMap(NamedTuple):
    """An HDR gain map, found: the gain map image as it is kept (``data``, from ``start`` in the file), the
    primary's multi-picture index (where it starts), to be written anew, the other segments the primary
    keeps for it (by where they start), and Apple's headroom for the primary's Exif."""
    start: int
    data: bytes
    mpf: int
    primary: dict[int, bytes]
    headroom: list[tuple[int, bytes]] | None


def _find_gain_map(b: bytes) -> _GainMap | None:
    """The HDR gain map a JPEG carries, as HDR decoders (Chrome's, Android's) find one: the first picture
    its multi-picture index lists after the primary that the primary and it both say is the gain map, in
    ISO 21496-1's terms (an APP2 in each: the version in the primary's, the parameters in the gain map's),
    Adobe's (hdrgm XMP in each, as Ultra HDR writes it) or Apple's (the HDR headroom in the primary's maker
    note, XMP in the gain map). The gain map keeps its image data, JFIF, colour profile and Adobe segments,
    its ISO 21496-1 APP2 and an XMP of only its gain map fields; the primary its ISO 21496-1 version, an XMP
    of only hdrgm:Version and where the gain map is, and the Apple headroom. Nothing else of either (Exif,
    GPS, other XMP, C2PA) stays. None if there is no such picture, whole (the web app's ``findGainMap``)."""
    head = _jpeg_segments(b)

    def find(segments: list[tuple[int, int, int]], marker: int, signature: bytes) -> tuple[int, int, int] | None:
        return next(((m, at, end) for m, at, end in segments if m == marker and b[at + 4:end].startswith(signature)), None)

    mpf, pictures = -1, None
    for marker, at, end in head:
        if marker != 0xE2 or not b[at + 4:end].startswith(b"MPF\x00"):
            continue
        pictures = _mpf_pictures(b[at + 8:end], at + 8)
        if pictures:
            mpf = at
            break
    if not pictures:
        return None
    iso = find(head, 0xE2, _ISO_21496)
    xmp = find(head, 0xE1, _XMP)
    version = _hdrgm_version(_xmp_text(b, xmp[1], xmp[2])) if xmp else None
    exif = find(head, 0xE1, b"Exif\x00")
    headroom = _apple_headroom(b[exif[1] + 10:exif[2]]) if exif else None
    if not iso and not version and not headroom:
        return None
    for start, size in pictures:
        if start + size > len(b) or b[start:start + 2] != b"\xff\xd8":
            continue
        segments = _jpeg_segments(b, start)
        map_iso = find(segments, 0xE2, _ISO_21496) if iso else None
        map_xmp = find(segments, 0xE1, _XMP)
        text = _xmp_text(b, map_xmp[1], map_xmp[2]) if map_xmp else None
        adobe = _hdrgm_fields(text) if version and text else None
        apple = _apple_fields(text) if headroom and text else None
        if not map_iso and not adobe and not apple:
            continue
        in_map: dict[int, bytes] = {}
        if map_iso:
            in_map[map_iso[1]] = b[map_iso[1]:map_iso[2]]
        if map_xmp and (adobe or apple):
            in_map[map_xmp[1]] = _xmp_segment(_gain_map_xmp((adobe or []) + (apple or [])))
        parts, end, _ = _walk_picture(b, start, in_map.get)
        if end is None or end > start + size:
            continue
        data = b"".join(parts)
        primary: dict[int, bytes] = {}
        if iso and map_iso:
            primary[iso[1]] = b[iso[1]:iso[2]]
        if xmp and version and adobe:
            primary[xmp[1]] = _xmp_segment(_primary_xmp(version, len(data)))
        return _GainMap(start, data, mpf, primary, headroom if apple else None)
    return None


def _walk_picture(b: bytes, start: int, keep: Callable[[int], bytes | None]) -> tuple[list[bytes], int | None, bool]:
    """The picture whose SOI is at ``start``, up to its EOI: its tables, frame and scans (the image data) as
    they are, of its application segments and comments JFIF, the colour profile and Adobe's, and in place of
    any other what ``keep`` gives for where it starts (nothing, mostly). Bytes that are not a marker, which
    decoders skip, go. Returns ``(parts, end, placed)``: where its EOI ends (None if the file ends first),
    and whether the first part it keeps after SOI is an APP0 (an Exif goes after it)."""
    kept = [b[start:start + 2]]
    placed = False
    at = start + 2
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
            kept.append(b[at:at + 2])  # the picture's end
            return kept, at + 2, placed
        if marker == 0x01 or 0xD0 <= marker <= 0xD8:
            kept.append(b[at:at + 2])  # markers without a length
            at += 2
            continue
        if at + 4 > len(b):
            kept.append(b[at:])
            break
        end = at + 2 + ((b[at + 2] << 8) | b[at + 3])
        is_app = 0xE0 <= marker <= 0xEF or marker == 0xFE
        read = not is_app or (marker in _JPEG_KEPT_APPS and b[at + 4:end].startswith(_JPEG_KEPT_APPS[marker]))
        part = b[at:end] if read else keep(at)
        if part is not None:
            if len(kept) == 1 and marker == 0xE0:
                placed = True  # the Exif goes after JFIF's APP0
            kept.append(part)
        at = end
        if marker == 0xDA:
            # The scan's entropy-coded data, up to the next marker that is not a restart (0xff 0x00 is a
            # data byte; fill bytes, more 0xff, may come before a marker).
            data = at
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
            kept.append(b[data:at])
    return kept, None, placed


def _with_exif(parts: list[bytes], placed: bool, orientation: int | None,
               headroom: list[tuple[int, bytes]] | None) -> list[bytes]:
    """A walked primary image's parts with its Exif (_exif_segment) in: after JFIF's APP0 if that comes
    first, else right after SOI."""
    exif = _exif_segment(orientation, headroom)
    if exif is None:
        return parts
    at = 2 if placed else 1
    return parts[:at] + [exif] + parts[at:]


def _strip_jpeg(b: bytes) -> bytes:
    """The primary image, from SOI to its EOI, and after it its HDR gain map if it has one
    (_find_gain_map); every other picture (a multi-picture file's thumbnails and depth maps) and trailer (a
    motion photo's video) goes. Of the primary's segments, drops comments and every application segment
    but JFIF, the colour profile, Adobe's and what its gain map is drawn with, adding back an Exif of its
    orientation alone so photos stay upright (and beside an Apple gain map its HDR headroom,
    _exif_segment), and a multi-picture index of the two pictures; the tables, frames and scans (the image
    data) stay as they are."""
    orientation = jpeg_orientation(b)
    found = _find_gain_map(b)
    if found:
        index = _mpf_segment(0, 0, 0)  # its size is all its place needs: the numbers come once the primary is laid out
        keep = {**found.primary, found.mpf: index}
        walked, end, placed = _walk_picture(b, 0, keep.get)
        parts = _with_exif(walked, placed, orientation, found.headroom)
        i = next((k for k, part in enumerate(parts) if part is index), -1)
        if end is not None and end <= found.start and i >= 0:
            size = sum(map(len, parts))
            parts[i] = _mpf_segment(size, size - (sum(map(len, parts[:i])) + 8), len(found.data))
            return b"".join(parts) + found.data
    walked, _, placed = _walk_picture(b, 0, lambda at: None)
    return b"".join(_with_exif(walked, placed, orientation, None))


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
    alone but an Apple gain map's HDR headroom): JPEG its primary image and its HDR gain map, with only
    what the gain map is drawn with, PNG, WebP and GIF their image data, colour and animation; everything
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
