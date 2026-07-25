"""Tests for core.vag (PS1 SPU-ADPCM + the .VAG container).

Synthetic blocks: header byte 0x00 selects filter 0 (memoryless) and shift 0, so
each 4-bit nibble decodes to exactly nibble << 12 -- an exact, checkable decode.
"""
import array
import io
import struct
import wave

import pytest

from acidcat.core import vag


def _vag_file(rate, name, body):
    h = (b"VAGp" + struct.pack(">I", 0x20) + b"\x00" * 4
         + struct.pack(">I", len(body)) + struct.pack(">I", rate)
         + b"\x00" * 12 + name.encode()[:16].ljust(16, b"\x00"))
    return h + body


def _block(shift_filter, flag, data14):
    return bytes([shift_filter, flag]) + bytes(data14)


def test_parse_vag():
    body = _block(0x00, 0x00, [0] * 14)
    info = vag.parse_vag(_vag_file(22050, "hit", body))
    assert info["rate"] == 22050 and info["name"] == "hit"
    assert info["data"] == body

    with pytest.raises(vag.VagError):
        vag.parse_vag(b"NOTVAG.." + bytes(64))


def test_decode_exact_nibbles():
    data = [0] * 14
    data[0] = 0x21                              # low nibble 1, high nibble 2
    pcm = vag.decode_spu(_block(0x00, 0x00, data))
    s = array.array("h")
    s.frombytes(pcm)
    assert len(s) == 28                         # one block = 28 samples
    assert s[0] == 1 << 12                      # data[0] low nibble
    assert s[1] == 2 << 12                      # data[0] high nibble
    assert s[2] == 0


def test_decode_stops_on_end_flag():
    loud = _block(0x00, vag.FLAG_LOOP_END, [0x11] * 14)   # end block (no loop bit)
    after = _block(0x00, 0x00, [0x22] * 14)
    pcm = vag.decode_spu(loud + after, stop_on_end=True)
    assert len(pcm) // 2 == 28                  # stopped after the end block
    pcm2 = vag.decode_spu(loud + after, stop_on_end=False)
    assert len(pcm2) // 2 == 56                 # both blocks


def test_loop_points():
    blocks = (_block(0x00, vag.FLAG_LOOP_START, [0x11] * 14)
              + _block(0x00, 0x00, [0x11] * 14)
              + _block(0x00, vag.FLAG_LOOP_END | vag.FLAG_LOOP, [0x11] * 14))
    assert vag.loop_points(blocks) == (0, 3 * 28)
    assert vag.loop_points(_block(0x00, 0x00, [0] * 14)) is None


def test_extract_wires_vag(tmp_path):
    from acidcat.core import sniff as sniffmod
    from acidcat.core import samples as smod

    body = _block(0x00, 0x00, [0x11] * 14) * 4
    f = tmp_path / "sfx.vag"
    f.write_bytes(_vag_file(44100, "boom", body))

    assert sniffmod.sniff(str(f)) == "vag"
    recs = list(smod.iter_samples(str(f)))
    assert len(recs) == 1
    assert recs[0]["name"] == "boom" and "SPU-ADPCM" in recs[0]["note"]
    w = wave.open(io.BytesIO(recs[0]["wav"]))
    assert w.getnchannels() == 1 and w.getframerate() == 44100
