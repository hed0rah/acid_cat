"""HAL PCM Stream (.hps), HAL Laboratory's streamed GameCube music container.

The module had no tests at all, which is how it kept a defect its own
dependency documents against: `dsp.decode` takes the predictor history and
`dsp.py`'s docstring passes it, while this caller did not.

Every HPS block header carries a decoder state per channel -- an initial
predictor/scale and two history samples. Only the first block's history is
needed, because the blocks run contiguously and a decode that carries its state
across them reproduces every later block's recorded state exactly. That is not
an assumption here: it was measured over 211 block boundaries in three shipped
HAL streams, all exact. What that measurement licenses is reading past the
later states; it does not license reading past the first.
"""
import array
import struct

import pytest

from acidcat.core.codecs import hps


def hps_file(blocks, rate=32000, channels=1, coefs=None, states=None):
    """A HALPST stream. `blocks` is a list of per-channel payloads; `states` is
    the per-block, per-channel (predictor_scale, hist1, hist2) written into each
    block header."""
    coefs = coefs or [[0] * 16 for _ in range(channels)]
    head = bytearray(0x10 + channels * 0x38)
    head[0:8] = b" HALPST\x00"
    struct.pack_into(">II", head, 8, rate, channels)
    for c in range(channels):
        at = 0x10 + c * 0x38 + 0x10
        struct.pack_into(">16h", head, at, *coefs[c])

    out = bytearray(head)
    offs = []
    for bi, payloads in enumerate(blocks):
        offs.append(len(out))
        per = len(payloads[0])
        hdr = bytearray(0x20)
        struct.pack_into(">I", hdr, 0, per * channels)      # dsp size
        struct.pack_into(">I", hdr, 4, per * 2 * 14 // 8)   # sample count
        struct.pack_into(">I", hdr, 8, 0xFFFFFFFF)          # patched below
        st = (states or [[(0, 0, 0)] * channels])[bi] if states else \
             [(0, 0, 0)] * channels
        for c in range(channels):
            struct.pack_into(">Hhh", hdr, 0x0C + c * 8, *st[c])
        out += hdr
        for pl in payloads:
            out += pl
    for i, o in enumerate(offs[:-1]):
        struct.pack_into(">I", out, o + 8, offs[i + 1])
    return bytes(out)


def pcm_of(data):
    a = array.array("h")
    a.frombytes(hps.decode(data)[0])
    return a


FRAME = bytes([0x01, 0x10] + [0] * 6)       # predictor 0, scale 1; nibbles 1, 0...


# ── the header ──────────────────────────────────────────────────────

class TestHeader:
    def test_rate_and_channels(self):
        _pcm, info = hps.decode(hps_file([[FRAME]], rate=44100))
        assert info["rate"] == 44100 and info["channels"] == 1
        assert info["frames"] == 14

    def test_a_foreign_file_is_refused(self):
        with pytest.raises(hps.HpsError):
            hps.decode(b"RIFF" + bytes(64))

    def test_an_impossible_channel_count_is_refused(self):
        bad = bytearray(hps_file([[FRAME]]))
        struct.pack_into(">I", bad, 12, 9)
        with pytest.raises(hps.HpsError):
            hps.decode(bytes(bad))


# ── the predictor's starting state ──────────────────────────────────

class TestInitialHistory:
    def test_the_first_block_state_is_read(self):
        """Zero coefficients would hide this, so the stream is given a real
        predictor: index 1, coefficient 1.0. Then the seeded and unseeded
        readings separate on the very first sample."""
        coefs = [[0, 0, 2048, 0] + [0] * 12]
        frame = bytes([0x11, 0x10] + [0] * 6)       # index 1, scale 1
        plain = hps_file([[frame]], coefs=coefs, states=[[(0, 0, 0)]])
        seeded = hps_file([[frame]], coefs=coefs, states=[[(0, 4000, 4000)]])
        a, b = pcm_of(plain), pcm_of(seeded)
        assert a[0] != b[0], (
            "seeding the predictor changed nothing; the block header's decoder "
            "state is being read past")
        assert b[0] == a[0] + 4000, (b[0], a[0])    # (2048 * 4000) >> 11

    def test_a_stream_starting_at_silence_is_unaffected(self):
        """Which is why the bug survived: every specimen measured starts at
        silence, so the correct and the incorrect reading agree on all of
        them."""
        coefs = [[0, 0, 2048, 0] + [0] * 12]
        frame = bytes([0x11, 0x10] + [0] * 6)
        got = pcm_of(hps_file([[frame]], coefs=coefs, states=[[(0, 0, 0)]]))
        assert got[0] == 2                          # ((1*2) << 11 + 1024) >> 11

    def test_only_the_first_block_seeds_the_decode(self):
        """Blocks are contiguous, so the later states are a record of where the
        decode should already be, not an instruction to jump there. Writing a
        wild state into block two must not move the audio."""
        # a real predictor, or the planted history has nothing to act on and
        # this passes whatever the decoder does with it
        coefs = [[0, 0, 2048, 0] + [0] * 12]
        frame = bytes([0x11, 0x10] + [0] * 6)       # index 1, scale 1
        two = [[frame * 4], [frame * 4]]
        calm = hps_file(two, coefs=coefs, states=[[(0, 0, 0)], [(0, 0, 0)]])
        wild = hps_file(two, coefs=coefs, states=[[(0, 0, 0)], [(0, 9000, -9000)]])
        assert list(pcm_of(calm)) == list(pcm_of(wild)), (
            "a later block's recorded state was applied as a reset")


# ── the block chain ─────────────────────────────────────────────────

class TestBlockChain:
    def test_blocks_are_gathered_in_order(self):
        pcm, info = hps.decode(hps_file([[FRAME], [FRAME], [FRAME]]))
        assert info["frames"] == 3 * 14

    def test_a_chain_that_points_backwards_terminates(self):
        """A loop point is an ordinary thing for a music stream to carry, and a
        decoder that follows it produces an unbounded file."""
        data = bytearray(hps_file([[FRAME], [FRAME]]))
        first = 0x10 + 1 * 0x38
        second = first + 0x20 + len(FRAME)
        struct.pack_into(">I", data, second + 8, first)     # block 2 -> block 1
        _pcm, info = hps.decode(bytes(data))
        assert info["frames"] == 2 * 14, "the loop was followed"

    def test_a_truncated_final_block_is_read_as_far_as_it_goes(self):
        data = hps_file([[FRAME], [FRAME * 4]])[:-16]
        _pcm, info = hps.decode(data)
        assert info["frames"] > 0, "a short tail lost the whole stream"


# ── stereo ──────────────────────────────────────────────────────────

def test_stereo_channels_are_not_crossed():
    """Each channel has its own coefficients, so crossing them is not a swap --
    it decodes each channel's nibbles against the other's predictor."""
    coefs = [[0] * 16, [0, 0, 2048, 0] + [0] * 12]
    left = bytes([0x01, 0x20] + [0] * 6)            # index 0, scale 1, nibble 2
    right = bytes([0x11, 0x10] + [0] * 6)           # index 1, scale 1, nibble 1
    pcm, info = hps.decode(hps_file([[left, right]], channels=2, coefs=coefs))
    assert info["channels"] == 2
    s = array.array("h")
    s.frombytes(pcm)
    assert s[0] == 4, s[0]                          # left: (2*2 << 11) >> 11
    assert s[1] == 2, s[1]                          # right: (1*2 << 11) >> 11
