"""Small real picture files for the tests, built byte by byte (no image library needed).

Each is as much of its format as the app's checks and the metadata removal read: a PNG that
decodes, and JPEG, WebP and GIF files whose headers say their type and size. The rest are the places
cameras and editors put location besides Exif and XMP: a multi-picture JPEG's later pictures, a motion
photo's video after the JPEG, C2PA content credentials (JUMBF boxes), a GIF's comment and XMP.
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
# A progressive picture's scans: entropy data with stuffed 0xff bytes and restart markers, a table
# between the scans, then the end.
SCANS = (segment(0xDA, b"\x01\x01\x00\x00\x00\x00") + b"\x12\xff\x00\x34\xff\xd0\x56\xff\xff\xd1\x78"
         + segment(0xC4, b"\x10" + b"\x01" * 16 + b"\x05") + segment(0xDA, b"\x01\x01\x00\x01\x3f\x00") + b"\x9a\xff\x00"
         + b"\xff\xd9")


def jpeg(width: int = 640, height: int = 480, exif: bytes | None = None, xmp: bytes | None = None,
         comment: bytes | None = None, icc: bytes | None = None, extra: bytes = b"", scan: bytes = SCAN) -> bytes:
    """A JPEG's structure: SOI, JFIF, optional APP1 Exif / XMP, comment and colour profile, ``extra``
    segments, SOF0, the scan."""
    parts = [b"\xff\xd8", segment(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")]
    if exif is not None:
        parts.append(segment(0xE1, b"Exif\x00\x00" + exif))
    if xmp is not None:
        parts.append(segment(0xE1, b"http://ns.adobe.com/xap/1.0/\x00" + xmp))
    if comment is not None:
        parts.append(segment(0xFE, comment))
    if icc is not None:
        parts.append(segment(0xE2, b"ICC_PROFILE\x00" + icc))
    parts.append(extra)
    parts.append(segment(0xC0, struct.pack(">BHHB", 8, height, width, 3) + b"\x01\x22\x00\x02\x11\x01\x03\x11\x01"))
    return b"".join(parts) + scan


def webp_chunk(kind: bytes, data: bytes) -> bytes:
    return kind + struct.pack("<I", len(data)) + data + (b"\0" if len(data) & 1 else b"")


def webp(width: int, height: int, exif: bytes | None = None, xmp: bytes | None = None, extra: bytes = b"") -> bytes:
    """A WebP with a VP8X header and lossless image data; ``extra`` is chunks put after them."""
    flags = (0x08 if exif else 0) | (0x04 if xmp else 0)
    vp8x = bytes((flags, 0, 0, 0)) + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    body = (b"WEBP" + webp_chunk(b"VP8X", vp8x) + webp_chunk(b"VP8L", b"\x2f" + b"\0" * 20)
            + (webp_chunk(b"EXIF", exif) if exif else b"") + (webp_chunk(b"XMP ", xmp) if xmp else b"") + extra)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def gif_sub_blocks(data: bytes) -> bytes:
    return b"".join(bytes([len(data[i:i + 255])]) + data[i:i + 255] for i in range(0, len(data), 255)) + b"\0"


# A frame: its graphic control (100 ms), descriptor with a two-colour local table, and image data.
GIF_FRAME = (b"\x21\xf9\x04\x00\x0a\x00\x00\x00" + b"\x2c" + struct.pack("<HHHH", 0, 0, 4, 3) + b"\x80"
             + b"\xff\x00\x00\x00\x00\xff" + b"\x02" + gif_sub_blocks(b"\x84\x8f\xa9\xcb\x0d\x00"))
GIF_LOOP = b"\x21\xff\x0bNETSCAPE2.0\x03\x01\x00\x00\x00"


def gif(width: int = 16, height: int = 12, blocks: bytes = b"") -> bytes:
    """A GIF89a with a global colour table of two, ``blocks`` (frames, extensions), then the trailer."""
    return b"GIF89a" + struct.pack("<HH", width, height) + b"\x80\x00\x00" + b"\x00\x00\x00\xff\xff\xff" + blocks + b"\x3b"


XMP_GPS = b'<x:xmpmeta><rdf:Description exif:GPSLatitude="51,30.0N" exif:GPSLongitude="0,7.5W"/></x:xmpmeta>'


def box(kind: bytes, data: bytes) -> bytes:
    """An ISO base-media box, as JUMBF (C2PA) and MP4 lay them out."""
    return struct.pack(">I", len(data) + 8) + kind + data


# A C2PA manifest store (a JUMBF superbox labelled c2pa) whose Exif assertion holds the GPS fields.
C2PA = box(b"jumb", box(b"jumd", b"c2pa\x00\x11\x00\x10\x80\x00\x00\xaa\x00\x38\x9b\x71\x03c2pa\x00")
                 + box(b"jumb", box(b"jumd", b"json" + b"\0" * 12 + b"\x03stds.exif\x00")
                             + box(b"json", b'{"exif:GPSLatitude":"51,30.0N","exif:GPSLongitude":"0,7.5W"}')))
# JPEG XT's APP11: common identifier 'JP', box instance, packet sequence, then the box.
APP11_C2PA = segment(0xEB, b"JP\x00\x01\x00\x00\x00\x01" + C2PA)
# The multi-picture index (CIPA DC-007) in APP2, naming a second picture after the first.
APP2_MPF = segment(0xE2, b"MPF\x00MM\x00\x2a\x00\x00\x00\x08\x00\x01\xb0\x00\x00\x07\x00\x00\x00\x04" + b"0100" + b"\0" * 4)
# A motion photo's video: an MP4 whose moov carries the location (the \xa9xyz atom), after the still.
MP4_WITH_LOCATION = (box(b"ftyp", b"mp42\x00\x00\x00\x00isommp42")
                     + box(b"moov", box(b"udta", box(b"\xa9xyz", b"\x00\x12\x15\xc7+51.5073-000.1277/"))))
XMP_MOTION_PHOTO = (b"<x:xmpmeta><rdf:Description GCamera:MotionPhoto=\"1\" "
                    b"GCamera:MicroVideoOffset=\"84\"/></x:xmpmeta>")
HEIC = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\0" * 32
SVG = b'<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
