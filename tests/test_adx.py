"""Tests for core.adx (CRI ADX). A scale-0 frame decodes to exact silence
regardless of the derived coefficients, and the first sample of a scale-1 frame
is the raw nibble (predictor seeded to zero)."""
import array
import struct

import pytest

from acidcat.core.codecs import adx


def _adx_file(frame_data, ch=1, rate=44100, samples=32, hp=500, enc=2, blk=18):
    co = 0x1C
    h = bytearray(co + 4)
    struct.pack_into(">H", h, 0, 0x8000)
    struct.pack_into(">H", h, 2, co)
    h[4], h[5], h[6], h[7] = enc, blk, 4, ch
    struct.pack_into(">I", h, 8, rate)
    struct.pack_into(">I", h, 0x0C, samples)
    struct.pack_into(">H", h, 0x10, hp)
    h[0x12] = 3
    h[co - 2:co + 4] = b"(c)CRI"
    return bytes(h) + frame_data


def test_parse_header():
    data = _adx_file(bytes(18), ch=2, rate=22050, samples=64)
    h = adx.parse_header(data)
    assert h["channels"] == 2 and h["rate"] == 22050 and h["samples"] == 64
    assert h["data_offset"] == 0x20
    with pytest.raises(adx.AdxError):
        adx.parse_header(b"nope" + bytes(0x20))


def test_decode_silence():
    frame = bytes(2) + bytes(16)                      # scale 0 -> exact silence
    pcm, info = adx.decode(_adx_file(frame, ch=1, samples=32))
    s = array.array("h"); s.frombytes(pcm)
    assert len(s) == 32 and not any(s)


def test_decode_first_nibble():
    frame = struct.pack(">H", 1) + bytes([0x30] + [0] * 15)   # scale 1, first nibble 3
    pcm, info = adx.decode(_adx_file(frame, ch=1, samples=32))
    s = array.array("h"); s.frombytes(pcm)
    assert s[0] == 3                                  # nibble 3 * scale 1 + zero predictor


def test_unsupported_type_raises():
    data = bytearray(_adx_file(bytes(18)))
    data[4] = 4                                       # enc type 4 = AHX
    with pytest.raises(adx.AdxError):
        adx.decode(bytes(data))


def test_extract_wires_adx(tmp_path):
    from acidcat.core.infra import sniff as sniffmod
    from acidcat.core.extract import samples as smod
    import wave, io

    frame = struct.pack(">H", 5) + bytes([0x12] * 16)
    f = tmp_path / "bgm.adx"
    f.write_bytes(_adx_file(frame, ch=1, samples=32))
    assert sniffmod.sniff(str(f)) == "adx"
    recs = list(smod.iter_samples(str(f)))
    assert len(recs) == 1 and "ADX" in recs[0]["note"]
    w = wave.open(io.BytesIO(recs[0]["wav"]))
    assert w.getnchannels() == 1


def test_hostile_header_values_raise_not_hang():
    """Found by an adversarial audit: block_size 0 never advanced the decode
    loop (an infinite hang reachable from `acidcat extract`), rate 0 divided
    by zero in _coefs, and channels 0 indexed an empty channel list. Each must
    be an AdxError -- the recognized malformed-input type -- before any loop."""
    frame = bytes(18)
    with pytest.raises(adx.AdxError):
        adx.decode(_adx_file(frame, blk=0))
    with pytest.raises(adx.AdxError):
        adx.decode(_adx_file(frame, blk=1))       # spf would be negative
    with pytest.raises(adx.AdxError):
        adx.decode(_adx_file(frame, rate=0))
    with pytest.raises(adx.AdxError):
        adx.decode(_adx_file(frame, ch=0))
    with pytest.raises(adx.AdxError):
        adx.decode(_adx_file(frame, ch=3))
