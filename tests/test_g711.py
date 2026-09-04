"""G.711 mu-law / A-law expansion, checked against the reference constants.

The values here are the fixed points of Sun's g711.c: silence and the positive
and negative peaks. If the table drifts, speech decoded from a .au turns to
noise, and these three points per law are enough to catch it.
"""

import struct

from acidcat.core.codecs import g711


def test_ulaw_fixed_points():
    assert g711._ulaw2lin(0xFF) == 0            # mu-law silence
    assert g711._ulaw2lin(0x80) == 32124        # positive peak
    assert g711._ulaw2lin(0x00) == -32124       # negative peak


def test_alaw_fixed_points():
    assert g711._alaw2lin(0xD5) == 8            # A-law silence (sign inverted)
    assert g711._alaw2lin(0x55) == -8
    assert g711._alaw2lin(0xAA) == 32256        # positive peak
    assert g711._alaw2lin(0x2A) == -32256       # negative peak


def test_every_code_fits_signed_16bit():
    for i in range(256):
        assert -32768 <= g711._ulaw2lin(i) <= 32767
        assert -32768 <= g711._alaw2lin(i) <= 32767


def test_decode_ulaw_is_interleaved_16bit_le():
    pcm = g711.decode_ulaw(bytes([0xFF, 0x80, 0x00]))
    assert pcm == struct.pack("<3h", 0, 32124, -32124)


def test_decode_alaw_is_interleaved_16bit_le():
    pcm = g711.decode_alaw(bytes([0xD5, 0x55, 0xAA]))
    assert pcm == struct.pack("<3h", 8, -8, 32256)


def test_decode_length_is_two_bytes_per_sample():
    assert len(g711.decode_ulaw(bytes(100))) == 200
    assert len(g711.decode_alaw(bytes(100))) == 200
