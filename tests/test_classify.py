"""Tests for audioscan.classify -- ranked codec-vs-PCM audio classification, and
its wiring into the TUI region browser."""
import math
import struct

import pytest

from acidcat.core.forensics import audioscan


def _varied_spu(n=300):
    """Realistic SPU-ADPCM: valid block headers (shift 0..12, filter 0..4, flag
    0..7) with jagged data, so it reads as a codec, not linear PCM."""
    out = bytearray()
    for i in range(n):
        out.append(((i % 5) << 4) | (i % 13))       # filter 0..4, shift 0..12
        out.append(i % 8)                            # flag 0..7
        out += bytes((i * 7 + j * 3) & 0xFF for j in range(14))
    return bytes(out)


def _sine_pcm(n=8000):
    return b"".join(struct.pack("<h", int(20000 * math.sin(2 * math.pi * i / 50)))
                    for i in range(n))


def test_classify_spu_is_codec():
    c = audioscan.classify(_varied_spu())
    assert c["is_codec"] is True
    assert c["candidates"][0]["label"] == "spu-adpcm"
    assert not c["uncertain"]


def test_classify_pcm_is_not_codec():
    c = audioscan.classify(_sine_pcm())
    assert c["is_codec"] is False
    assert c["candidates"][0]["label"].startswith("raw-pcm")
    assert not c["uncertain"]


def test_classify_empty():
    c = audioscan.classify(b"")
    assert c["is_codec"] is False and c["uncertain"]


def test_tui_region_classification(tmp_path):
    """_classify_regions tags a blob region with its ranked interpretation."""
    pytest.importorskip("textual")
    from acidcat.tui_app import AcidcatTUI

    spu = _varied_spu()
    blob = tmp_path / "blob.bin"
    blob.write_bytes(bytes(1000) + spu)               # SPU data at offset 1000

    app = AcidcatTUI.__new__(AcidcatTUI)
    app._blob_src = str(blob)
    regions = [{"kind": "blob", "format": None, "offset": 1000, "end": 1000 + len(spu)}]
    app._classify_regions(regions)
    assert regions[0]["probe"]["is_codec"] is True
    assert regions[0]["probe"]["top"] == "spu-adpcm"
