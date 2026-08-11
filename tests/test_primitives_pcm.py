"""The shared ADPCM/PCM primitives (Phase-0 dedup across the console codecs)."""
import array

from acidcat.core.primitives.pcm import (PS_ADPCM_FILTER, clip16,
                                          interleave_stereo, signed_nibble)


def test_clip16():
    assert clip16(0) == 0
    assert clip16(40000) == 32767
    assert clip16(-40000) == -32768
    assert clip16(32767) == 32767 and clip16(-32768) == -32768


def test_signed_nibble():
    assert [signed_nibble(n) for n in range(16)] == list(range(8)) + list(range(-8, 0))
    assert signed_nibble(0xF7) == 7          # masks to the low nibble


def test_ps_adpcm_filter():
    # the canonical PS/CD-XA table; DTK uses the first four (0x3C=60, 0x73=115...)
    assert PS_ADPCM_FILTER == ((0, 0), (60, 0), (115, -52), (98, -55), (122, -60))
    assert PS_ADPCM_FILTER[:4] == ((0, 0), (0x3C, 0), (0x73, -0x34), (0x62, -0x37))


def test_interleave_stereo():
    left = array.array("h", [1, 3, 5])
    right = array.array("h", [2, 4, 6])
    out = interleave_stereo(left, right)
    assert array.array("h", out).tolist() == [1, 2, 3, 4, 5, 6]
    # accepts plain int lists and truncates to the shorter channel
    assert array.array("h", interleave_stereo([10, 20], [30, 40, 50])).tolist() == [10, 30, 20, 40]
