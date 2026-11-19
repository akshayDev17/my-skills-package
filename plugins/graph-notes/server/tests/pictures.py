"""Small real picture files for the tests, built byte by byte (no image library needed).

Each is as much of its format as the app's checks and the metadata removal read: a PNG that
decodes, and JPEG, WebP and GIF files whose headers say their type and size.
"""

from __future__ import annotations

import struct
import zlib


def png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def png(width: int = 300, height: int = 200, rgb: tuple[int, int, int] = (192, 57, 43), extra: bytes = b"") -> bytes:
    """A real PNG of one colour; ``extra`` is chunks put before its image data (an eXIf, a tEXt)."""
    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + extra
            + png_chunk(b"IDAT", zlib.compress(raw)) + png_chunk(b"IEND", b""))


def tiff(*tags: tuple[int, int], little: bool = False) -> bytes:
    """A TIFF/EXIF block whose first IFD holds ``tags``, each ``(tag, value)`` as a SHORT."""
    o = "<" if little else ">"
    ifd = struct.pack(o + "H", len(tags)) + b"".join(struct.pack(o + "HHIHH", t, 3, 1, v, 0) for t, v in tags) + b"\0\0\0\0"
    return (b"II\x2a\x00" if little else b"MM\x00\x2a") + struct.pack(o + "I", 8) + ifd


ORIENTATION, GPS = 0x0112, 0x8825


def segment(marker: int, payload: bytes) -> bytes:
    return bytes((0xFF, marker)) + struct.pack(">H", len(payload) + 2) + payload


SCAN = segment(0xDA, b"\x03\x01\x00\x02\x11\x03\x11\x00\x3f\x00") + b"\x12\x34" * 40 + b"\xff\xd9"


def jpeg(width: int = 640, height: int = 480, exif: bytes | None = None, xmp: bytes | None = None,
         comment: bytes | None = None, icc: bytes | None = None) -> bytes:
    """A JPEG's structure: SOI, JFIF, optional APP1 Exif / XMP, comment and colour profile, SOF0, the scan."""
    parts = [b"\xff\xd8", segment(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")]
    if exif is not None:
        parts.append(segment(0xE1, b"Exif\x00\x00" + exif))
    if xmp is not None:
        parts.append(segment(0xE1, b"http://ns.adobe.com/xap/1.0/\x00" + xmp))
    if comment is not None:
        parts.append(segment(0xFE, comment))
    if icc is not None:
        parts.append(segment(0xE2, b"ICC_PROFILE\x00" + icc))
    parts.append(segment(0xC0, struct.pack(">BHHB", 8, height, width, 3) + b"\x01\x22\x00\x02\x11\x01\x03\x11\x01"))
    return b"".join(parts) + SCAN


def webp(width: int, height: int, exif: bytes | None = None, xmp: bytes | None = None) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return kind + struct.pack("<I", len(data)) + data + (b"\0" if len(data) & 1 else b"")

    flags = (0x08 if exif else 0) | (0x04 if xmp else 0)
    vp8x = bytes((flags, 0, 0, 0)) + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    body = (b"WEBP" + chunk(b"VP8X", vp8x) + chunk(b"VP8L", b"\x2f" + b"\0" * 20)
            + (chunk(b"EXIF", exif) if exif else b"") + (chunk(b"XMP ", xmp) if xmp else b""))
    return b"RIFF" + struct.pack("<I", len(body)) + body


def gif(width: int = 16, height: int = 12) -> bytes:
    return b"GIF89a" + struct.pack("<HH", width, height) + b"\x00\x00\x00" + b"\x3b"


XMP_GPS = b'<x:xmpmeta><rdf:Description exif:GPSLatitude="51,30.0N" exif:GPSLongitude="0,7.5W"/></x:xmpmeta>'
HEIC = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\0" * 32
SVG = b'<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
