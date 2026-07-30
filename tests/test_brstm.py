"""Tests for core.brstm (Nintendo RSTM / BRSTM) and the Wii-disc detection in
core.wiidisc. A minimal 1-channel DSP-ADPCM stream with zero coefficients
decodes to the raw nibble * scale (predictor seeded to zero), which pins the
reference-chain header walk and the block gather."""
import array
import struct

import pytest

from acidcat.core.containers import wiidisc
from acidcat.core.codecs import brstm


def _brstm_file():
    """A minimal 1-channel BRSTM: one 8-byte DSP frame, zero coefficients, so the
    first nibble (1) at scale exponent 1 (scale 2) decodes to (1*2<<11 + 1024)>>11 = 2."""
    head_off = 0x40
    base = head_off + 8
    buf = bytearray(0x140)
    buf[0:4] = b"RSTM"
    struct.pack_into(">I", buf, 0x10, head_off)          # HEAD chunk offset
    struct.pack_into(">I", buf, 0x20, 0x100)             # DATA chunk offset

    def ref(at, off):
        struct.pack_into(">I", buf, at, 0x01000000)
        struct.pack_into(">i", buf, at + 4, off)

    buf[head_off:head_off + 4] = b"HEAD"
    ref(base + 0x00, 0x18)                               # -> HEAD1 (stream info)
    ref(base + 0x08, 0x18)                               # -> HEAD2 (unused here)
    ref(base + 0x10, 0x50)                               # -> HEAD3 (channel table)
    h1 = base + 0x18
    buf[h1], buf[h1 + 1], buf[h1 + 2] = 2, 0, 1          # codec DSP, no loop, 1 channel
    struct.pack_into(">H", buf, h1 + 4, 8000)            # sample rate
    struct.pack_into(">I", buf, h1 + 0x0C, 14)           # total samples
    struct.pack_into(">I", buf, h1 + 0x10, 0x120)        # audio data offset (absolute)
    struct.pack_into(">I", buf, h1 + 0x14, 1)            # blocks
    struct.pack_into(">I", buf, h1 + 0x18, 8)            # block size
    struct.pack_into(">I", buf, h1 + 0x1C, 14)           # samples per block
    struct.pack_into(">I", buf, h1 + 0x20, 8)            # final block size
    struct.pack_into(">I", buf, h1 + 0x24, 14)           # final block samples
    struct.pack_into(">I", buf, h1 + 0x28, 8)            # final block padded size
    h3 = base + 0x50
    buf[h3] = 1                                          # channel count
    ref(h3 + 0x04, 0x60)                                 # -> channel info 0
    ref(base + 0x60, 0x68)                               # channel info -> coefs (rel 0x68, zeros)
    buf[0x100:0x104] = b"DATA"
    buf[0x120] = 0x01                                    # predictor 0, scale exponent 1
    buf[0x121] = 0x10                                    # nibbles: 1, then 0
    return bytes(buf)


def test_parse_header():
    h = brstm.parse_header(_brstm_file())
    assert h["codec"] == 2 and h["channels"] == 1 and h["rate"] == 8000
    assert h["samples"] == 14 and h["blocks"] == 1 and len(h["coefs"][0]) == 16
    with pytest.raises(brstm.BrstmError):
        brstm.parse_header(b"nope" + bytes(0x40))


def test_unsupported_codec_raises():
    data = bytearray(_brstm_file())
    base = 0x48
    data[base + 0x18] = 1                                # codec 1 = PCM16, unsupported
    with pytest.raises(brstm.BrstmError):
        brstm.parse_header(bytes(data))


def test_decode_first_nibble():
    pcm, info = brstm.decode(_brstm_file())
    s = array.array("h"); s.frombytes(pcm)
    assert info == {"channels": 1, "rate": 8000, "frames": 14}
    assert len(s) == 14 and s[0] == 2                    # nibble 1 * scale 2, zero predictor


def test_extract_wires_brstm(tmp_path):
    from acidcat.core import sniff as sniffmod
    from acidcat.core import samples as smod
    import wave, io

    f = tmp_path / "bgm.brstm"
    f.write_bytes(_brstm_file())
    assert sniffmod.sniff(str(f)) == "brstm"
    recs = list(smod.iter_samples(str(f)))
    assert len(recs) == 1 and "DSP-ADPCM" in recs[0]["note"]
    w = wave.open(io.BytesIO(recs[0]["wav"]))
    assert w.getnchannels() == 1 and w.getframerate() == 8000


def test_wii_detection(tmp_path):
    disc = bytearray(0x20)
    struct.pack_into(">I", disc, 0x18, wiidisc.MAGIC)
    f = tmp_path / "game.iso"
    f.write_bytes(bytes(disc))
    assert wiidisc.is_wii(str(f))
    plain = tmp_path / "not.iso"
    plain.write_bytes(bytes(0x20))
    assert not wiidisc.is_wii(str(plain))
    with pytest.raises(wiidisc.WiiError):
        wiidisc.WiiDisc(str(plain))                      # not a Wii disc
