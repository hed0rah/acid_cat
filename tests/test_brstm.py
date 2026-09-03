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
    from acidcat.core.infra import sniff as sniffmod
    from acidcat.core.extract import samples as smod
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


# ── the predictor's starting state ──────────────────────────────────

def _brstm_with_history(y1, y2):
    """The same minimal stream, with a non-zero initial history written where
    the format keeps it: behind the 16 coefficients, past the gain and the
    initial predictor/scale."""
    buf = bytearray(_brstm_file())
    coef_at = (0x40 + 8) + 0x68          # channel info -> coefs, as built above
    struct.pack_into(">HHhh", buf, coef_at + 32, 0, 0, y1, y2)
    return bytes(buf)


def test_the_initial_history_is_read_from_the_file():
    h = brstm.parse_header(_brstm_with_history(1234, -567))
    assert h["hist"][0] == (1234, -567), (
        "the per-channel initial history was read past; a stream that does not "
        "begin at silence decodes its first frames against the wrong state")


def test_a_seeded_predictor_changes_the_opening_samples():
    """The reason it matters, rather than the fact that the field exists.

    Coefficients are zero in this fixture, so a decoder that ignores history
    produces the same audio either way and the field can look decorative. Give
    the stream a real predictor and the two readings separate immediately.
    """
    plain = bytearray(_brstm_with_history(0, 0))
    seeded = bytearray(_brstm_with_history(4000, 4000))
    coef_at = (0x40 + 8) + 0x68
    for buf in (plain, seeded):                  # predictor 1 reads coefs[2:4]
        struct.pack_into(">h", buf, coef_at + 4, 2048)   # c0 = 2048, i.e. 1.0
        buf[0x120] = 0x11                        # coefficient index 1, scale 1
    a = array.array("h"); a.frombytes(brstm.decode(bytes(plain))[0])
    b = array.array("h"); b.frombytes(brstm.decode(bytes(seeded))[0])
    assert a[0] != b[0], (
        "seeding the predictor changed nothing; the history is being dropped "
        "between the header and the decoder")
    assert b[0] == a[0] + 4000, (b[0], a[0])     # (2048 * 4000) >> 11 = 4000


def test_history_defaults_to_silence_when_the_field_is_absent():
    """A truncated channel context must not crash the walk. Every shipped
    specimen measured starts at silence anyway, so zero is also the right
    answer when the bytes are not there to say otherwise."""
    h = brstm.parse_header(_brstm_file())
    assert h["hist"] == [(0, 0)]


def _with_header_u32(data, h1_rel, value):
    """The fixture with one HEAD1 field overwritten."""
    buf = bytearray(data)
    struct.pack_into(">I", buf, 0x48 + 0x18 + h1_rel, value)
    return bytes(buf)


def test_forged_block_count_is_bounded_by_the_file():
    """Found by an adversarial audit: `blocks` is a header u32 nothing checked
    against the file, and 0xFFFFFFFF spun ~4.3 billion iterations over empty
    slices -- an unbounded hang reachable from `acidcat extract`. The decode
    must be bounded by the bytes that exist, and identical for any declared
    count past that bound."""
    good = _brstm_file()
    honest, _ = brstm.decode(good)
    forged, _ = brstm.decode(_with_header_u32(good, 0x14, 0xFFFFFFFF))
    assert forged == honest


def test_zero_block_size_raises():
    with pytest.raises(brstm.BrstmError):
        brstm.decode(_with_header_u32(_brstm_file(), 0x18, 0))


def test_hostile_channel_count_raises():
    buf = bytearray(_brstm_file())
    buf[0x48 + 0x18 + 2] = 0
    with pytest.raises(brstm.BrstmError):
        brstm.decode(bytes(buf))
