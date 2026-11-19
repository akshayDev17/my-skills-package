# HDR gain map pictures

Real JPEGs with an HDR gain map, for the metadata removal's tests (`tests/test_images.py`), which check
that the gain map stays, with only what draws it, and that the web app's removal gives the same bytes.

| File | What it is | Where it comes from | Licence |
| --- | --- | --- | --- |
| `gainmap-ultrahdr.jpg` | Ultra HDR as libultrahdr writes it (the library Android cameras use): ISO 21496-1 metadata in an APP2 of each picture, a multi-picture index; its gain map also carries an Exif and an IPTC block | Encoded with libultrahdr 2.0.2's `ultrahdr_app -m 0` from its test pictures `tests/data/minnie-320x240-yuv.jpg` (the base) and `tests/data/minnie-320x240-y.jpg` scaled to 80×60 by macOS `sips` (the gain map), with its `examples/metadata.cfg` | libultrahdr's MIT / Apache-2.0, `gainmap-LICENSE-libultrahdr` |
| `gainmap-iphone.jpg` | An iPhone photo's JPEG with Apple's gain map: the HDR headroom in the primary's maker note, Apple XMP in the gain map; GPS fields in its Exif | libultrahdr's `tests/data/apple_gainmap_new.jpg`, commit c6fd15cdb2d05d3aaed84b817e4c4a274a4ce600 | libultrahdr's MIT / Apache-2.0, `gainmap-LICENSE-libultrahdr` |
| `gainmap-adobe-iso.jpg` | Adobe's gain map XMP (hdrgm, per-channel values in rdf:Seq) and the GContainer directory, with ISO 21496-1 alongside | Skia's `resources/images/gainmap_iso21496_1_adobe_gcontainer.jpg`, commit ed933b2a6f9e44d4ff9f923bc1bf744137cb7373 | Skia's BSD 3-clause, `gainmap-LICENSE-skia` |
