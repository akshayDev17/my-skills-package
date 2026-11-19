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


# ── HDR gain maps ──────────────────────────────────────────────────────────
# What Ultra HDR (Pixel, Samsung, libultrahdr) and iPhones write: a multi-picture JPEG whose second picture
# is the gain map, described in ISO 21496-1 APP2s, Adobe's hdrgm XMP or Apple's XMP and maker note.

ISO_21496 = b"urn:iso:std:iso:ts:21496:-1\x00"
# ISO 21496-1 in a primary: the version alone; in a gain map: the version and its parameters (as
# libultrahdr writes them).
ISO_VERSION = segment(0xE2, ISO_21496 + b"\x00\x00\x00\x00")
ISO_PARAMETERS = segment(0xE2, ISO_21496 + bytes.fromhex(
    "0000000040000000000000000100b3ea8300200000000000000000000100a5700700400000000000010000000100000000000000010000000000000001"))


def xmp_segment(xml: bytes) -> bytes:
    return segment(0xE1, b"http://ns.adobe.com/xap/1.0/\x00" + xml)


# A Pixel's primary XMP: the gain map's version and the container directory (with a motion photo's
# video after the gain map), beside other fields and location.
ULTRA_HDR_PRIMARY_XMP = b"""<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="Adobe XMP Core 5.1.0-jc003">
  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
    <rdf:Description rdf:about=""
        xmlns:hdrgm="http://ns.adobe.com/hdr-gain-map/1.0/"
        xmlns:xmpNote="http://ns.adobe.com/xmp/note/"
        xmlns:Container="http://ns.google.com/photos/1.0/container/"
        xmlns:Item="http://ns.google.com/photos/1.0/container/item/"
        xmlns:exif="http://ns.adobe.com/exif/1.0/"
      hdrgm:Version="1.0"
      xmpNote:HasExtendedXMP="BA3F34D72C675C9BB1B76C15723D23E5"
      exif:GPSLatitude="51,30.4N" exif:GPSLongitude="0,7.7W">
      <Container:Directory>
        <rdf:Seq>
          <rdf:li rdf:parseType="Resource">
            <Container:Item Item:Mime="image/jpeg" Item:Semantic="Primary"/>
          </rdf:li>
          <rdf:li rdf:parseType="Resource">
            <Container:Item Item:Mime="image/jpeg" Item:Length="62570" Item:Semantic="GainMap"/>
          </rdf:li>
          <rdf:li rdf:parseType="Resource">
            <Container:Item Item:Mime="video/mp4" Item:Length="4021" Item:Semantic="MotionPhoto"/>
          </rdf:li>
        </rdf:Seq>
      </Container:Directory>
    </rdf:Description>
  </rdf:RDF>
</x:xmpmeta>"""
# Extended XMP (more of the primary's XMP, in its own APP1), carrying location.
EXTENDED_XMP = segment(0xE1, b"http://ns.adobe.com/xmp/extension/\x00BA3F34D72C675C9BB1B76C15723D23E5"
                       + b"\x00\x00\x00\x50\x00\x00\x00\x00" + b'<x:xmpmeta><rdf:Description exif:GPSLatitude="51,30.4N"/></x:xmpmeta>')
# A Pixel's gain map XMP: the gain map's fields, here beside location.
ULTRA_HDR_GAIN_MAP_XMP = b"""<?xpacket begin="\xef\xbb\xbf" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="XMP Core 5.5.0">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:hdrgm="http://ns.adobe.com/hdr-gain-map/1.0/"
    xmlns:exif="http://ns.adobe.com/exif/1.0/"
   hdrgm:Version="1.0"
   hdrgm:GainMapMin="0.000000"
   hdrgm:GainMapMax="2.656715"
   hdrgm:HDRCapacityMin="0.000000"
   hdrgm:HDRCapacityMax="2.656715"
   hdrgm:OffsetHDR="0.000000"
   hdrgm:OffsetSDR="0.000000"
   exif:GPSLatitude="51,30.4N"/>
 </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>"""
# Adobe's per-channel form: three values in an rdf:Seq.
ADOBE_GAIN_MAP_XMP = b"""<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
<rdf:Description rdf:about="" xmlns:hdrgm="http://ns.adobe.com/hdr-gain-map/1.0/" xmlns:xmp="http://ns.adobe.com/xap/1.0/"
 hdrgm:Version="1.0" hdrgm:BaseRenditionIsHDR="False" xmp:CreatorTool="Lightroom at 51.5073N">
 <hdrgm:GainMapMax> <rdf:Seq> <rdf:li>1.5</rdf:li> <rdf:li>2</rdf:li> <rdf:li>2.5e0</rdf:li> </rdf:Seq> </hdrgm:GainMapMax>
 <hdrgm:Gamma>1</hdrgm:Gamma>
</rdf:Description></rdf:RDF></x:xmpmeta>"""
# An iPhone's gain map XMP, here beside location.
APPLE_GAIN_MAP_XMP = b"""<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="XMP Core 6.0.0">
   <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
      <rdf:Description rdf:about=""
            xmlns:apdi="http://ns.apple.com/pixeldatainfo/1.0/"
            xmlns:HDRGainMap="http://ns.apple.com/HDRGainMap/1.0/"
            xmlns:exif="http://ns.adobe.com/exif/1.0/">
         <apdi:NativeFormat>1278226488</apdi:NativeFormat>
         <apdi:AuxiliaryImageType>urn:com:apple:photo:2020:aux:hdrgainmap</apdi:AuxiliaryImageType>
         <apdi:StoredFormat>1278226488</apdi:StoredFormat>
         <HDRGainMap:HDRGainMapVersion>131072</HDRGainMap:HDRGainMapVersion>
         <HDRGainMap:HDRGainMapHeadroom>3.755272</HDRGainMap:HDRGainMapHeadroom>
         <exif:GPSLatitude>51,30.4N</exif:GPSLatitude>
      </rdf:Description>
   </rdf:RDF>
</x:xmpmeta>"""
# An iPhone's depth map, a third picture.
APPLE_DISPARITY_XMP = (b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
                       b'<rdf:Description xmlns:apdi="http://ns.apple.com/pixeldatainfo/1.0/">'
                       b"<apdi:AuxiliaryImageType>disparity</apdi:AuxiliaryImageType></rdf:Description></rdf:RDF></x:xmpmeta>")


def ifd(entries: list[tuple[int, int, int, bytes]], at: int) -> bytes:
    """A big-endian TIFF IFD placed at ``at`` (where its offsets count from), with its values that do not
    fit an entry after it: ``entries`` as ``(tag, type, count, value)``."""
    data_at = at + 2 + 12 * len(entries) + 4
    head, data = struct.pack(">H", len(entries)), b""
    for tag, kind, count, value in entries:
        if len(value) <= 4:
            head += struct.pack(">HHI", tag, kind, count) + value.ljust(4, b"\0")
        else:
            head += struct.pack(">HHII", tag, kind, count, data_at + len(data))
            data += value
    return head + b"\0\0\0\0" + data


# Apple's maker-note HDR headroom (tags 33 and 48): signed rationals, as an iPhone 15 wrote them.
APPLE_HEADROOM = (bytes.fromhex("001028aa000fffb5"), bytes.fromhex("000427f700033213"))


def apple_exif(orientation: int, headroom: tuple[bytes, bytes] | None = APPLE_HEADROOM) -> bytes:
    """An iPhone's Exif (a TIFF block): IFD0 with the orientation, the Exif IFD and a GPS IFD; the Exif IFD
    holds Apple's maker note, with its version, a photo identifier and the HDR headroom (if given)."""
    fields = [(0x0001, 9, 1, struct.pack(">i", 14)), (0x0011, 2, 37, b"57DB622E-3825-4064-BD4D-51B4F4D516C6\x00")]
    if headroom:
        fields += [(0x0021, 10, 1, headroom[0]), (0x0030, 10, 1, headroom[1])]
    note = b"Apple iOS\x00\x00\x01MM" + ifd(fields, 14)
    exif_ifd = ifd([(0x927C, 7, len(note), note)], 50)
    gps_ifd = ifd([(0x0001, 2, 2, b"N\x00"), (0x0002, 5, 3, struct.pack(">6I", 51, 1, 30, 1, 2628, 100))], 50 + len(exif_ifd))
    ifd0 = ifd([(0x0112, 3, 1, struct.pack(">H", orientation)), (0x8769, 4, 1, struct.pack(">I", 50)),
                (0x8825, 4, 1, struct.pack(">I", 50 + len(exif_ifd)))], 8)
    return b"MM\x00\x2a\x00\x00\x00\x08" + ifd0 + exif_ifd + gps_ifd


def mpf(entries: list[tuple[int, int, int]], little: bool = False) -> bytes:
    """A multi-picture index (APP2 MPF), ``entries`` as ``(attribute, size, offset)``."""
    o = "<" if little else ">"
    index = (struct.pack(o + "H", 3) + struct.pack(o + "HHI", 0xB000, 7, 4) + b"0100"
             + struct.pack(o + "HHII", 0xB001, 4, 1, len(entries))
             + struct.pack(o + "HHII", 0xB002, 7, 16 * len(entries), 50) + struct.pack(o + "I", 0))
    tiff_head = (b"II\x2a\x00" if little else b"MM\x00\x2a") + struct.pack(o + "I", 8)
    return segment(0xE2, b"MPF\x00" + tiff_head + index + b"".join(struct.pack(o + "IIIHH", a, s, off, 0, 0) for a, s, off in entries))


FRAME = segment(0xC0, struct.pack(">BHHB", 8, 480, 640, 3) + b"\x01\x22\x00\x02\x11\x01\x03\x11\x01")


def multi_picture(head: bytes, pictures: list[bytes], trailer: bytes = b"", little: bool = False, scan: bytes = SCANS) -> bytes:
    """A multi-picture JPEG: a primary of ``head`` (its segments after SOI), a multi-picture index listing
    ``pictures``, its frame and ``scan``; then the pictures, then ``trailer`` (a motion photo's video)."""
    index_at = 2 + len(head)
    primary = 2 + len(head) + len(mpf([(0, 0, 0)] * (1 + len(pictures)))) + len(FRAME) + len(scan)
    entries, at = [(0x030000, primary, 0)], primary
    for picture in pictures:
        entries.append((0, len(picture), at - (index_at + 8)))
        at += len(picture)
    return b"\xff\xd8" + head + mpf(entries, little) + FRAME + scan + b"".join(pictures) + trailer


def split_pictures(data: bytes) -> tuple[list[tuple[list[bytes], bytes]], bytes]:
    """``([(image data, picture)], what follows)``: each SOI..EOI picture of a JPEG, one after another, with
    its image data (every segment that is not APPn or COM, and each scan's entropy-coded bytes)."""
    pictures, at = [], 0
    while data[at:at + 3] == b"\xff\xd8\xff":
        start, parts, at = at, [], at + 2
        while at + 2 <= len(data) and data[at + 1] != 0xD9:
            marker = data[at + 1]
            end = at + 2 + struct.unpack(">H", data[at + 2:at + 4])[0]
            if not (0xE0 <= marker <= 0xEF or marker == 0xFE):
                parts.append(data[at:end])
            at = end
            if marker == 0xDA:  # the entropy-coded data, up to a marker that is not a stuffed byte or restart
                scan = at
                while data[at] != 0xFF or data[at + 1] == 0x00 or 0xD0 <= data[at + 1] <= 0xD7 or data[at + 1] == 0xFF:
                    at += 1
                parts.append(data[scan:at])
        at += 2
        pictures.append((parts, data[start:at]))
    return pictures, data[at:]


def mpf_entries(picture: bytes) -> tuple[int, list[tuple[int, int, int]]]:
    """``(where its TIFF header is in the picture, [(attribute, size, offset)])``: a primary's multi-picture index."""
    at = picture.index(b"MPF\x00") + 4
    o = "<" if picture[at:at + 2] == b"II" else ">"
    ifd_at = at + struct.unpack(o + "I", picture[at + 4:at + 8])[0]
    for i in range(struct.unpack(o + "H", picture[ifd_at:ifd_at + 2])[0]):
        tag, _, count, value = struct.unpack(o + "HHII", picture[ifd_at + 2 + 12 * i:ifd_at + 14 + 12 * i])
        if tag == 0xB002:
            return at, [struct.unpack(o + "III", picture[at + value + 16 * k:at + value + 16 * k + 12]) for k in range(count // 16)]
    raise AssertionError("no MP entries")
