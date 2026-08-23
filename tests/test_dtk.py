"""GameCube DTK / .adp streaming ADPCM.

These are written against the console's behaviour rather than against the
decoder, because the previous version of this file was written the other way
round and that is how the bug survived. It asserted that the high nibble of a
data byte was the LEFT sample -- which is what the code did, and the opposite of
what the hardware does -- so the one test covering the one thing that was wrong
agreed with it.

The format: 32-byte frames of 28 stereo samples. Byte 0 is the left channel's
header and byte 1 the right, each a predictor index in the high nibble and a
scale exponent in the low; bytes 2-3 repeat them; bytes 4-31 carry the samples,
one nibble per channel per byte, LOW nibble left and HIGH nibble right.

That last detail is the load-bearing one. Because each channel has its own
header, reading the nibbles the wrong way round does not swap the channels --
each channel's samples get decoded with the other channel's SCALE EXPONENT, and
the scale is a power of two. The output stays roughly in the shape of the music
while every sample is off by a factor of 2^n, which is why it sounds broken
rather than reversed.
"""
import array
import random

from acidcat.core.codecs import dtk


def frame(hl, hr, payload):
    """One 32-byte DTK frame: the two headers, their copies, 28 data bytes."""
    body = bytes(payload) + bytes(28 - len(payload))
    return bytes([hl, hr, hl, hr]) + body


def samples(pcm):
    s = array.array("h")
    s.frombytes(pcm)
    return s[0::2], s[1::2]


# ── the reference, transcribed from the console's arithmetic ────────

def reference(data):
    """A second implementation, written from the documented step rather than
    from acidcat, so that agreeing with it is evidence rather than a tautology.

    Kept deliberately unfactored and literal: this is the specification in
    executable form, and it earns its keep only for as long as it is obviously
    the thing it claims to be.
    """
    def clamp(x, lo, hi):
        return lo if x < lo else (hi if x > hi else x)

    left, right = array.array("h"), array.array("h")
    hl1 = hl2 = hr1 = hr2 = 0
    for off in range(0, len(data) - 31, 32):
        for i in range(28):
            b = data[off + 4 + i]
            for bits, q, chan in ((b & 0x0F, data[off], 0),
                                  (b >> 4, data[off + 1], 1)):
                h1, h2 = (hl1, hl2) if chan == 0 else (hr1, hr2)
                k = q >> 4
                if k == 0:
                    hist = 0
                elif k == 1:
                    hist = h1 * 0x3C
                elif k == 2:
                    hist = h1 * 0x73 - h2 * 0x34
                elif k == 3:
                    hist = h1 * 0x62 - h2 * 0x37
                else:
                    hist = 0            # only four predictors are defined
                hist = clamp((hist + 0x20) >> 6, -0x200000, 0x1FFFFF)
                nib = (bits << 12) & 0xFFFF          # the 16-bit cast is the
                if nib >= 0x8000:                    # sign extension
                    nib -= 0x10000
                cur = ((nib >> (q & 0x0F)) << 6) + hist
                if chan == 0:
                    hl2, hl1 = hl1, cur
                else:
                    hr2, hr1 = hr1, cur
                out = clamp(cur >> 6, -0x8000, 0x7FFF)
                (left if chan == 0 else right).append(out)
    return left, right


# ── which nibble is which channel ───────────────────────────────────

class TestNibbleOrder:
    def test_the_low_nibble_is_the_left_channel(self):
        """Predictor 0 is memoryless and scale 12 makes the step (n<<12)>>12 = n,
        so the sample IS the nibble and there is nowhere for an error to hide."""
        pcm, _ = dtk.decode(frame(0x0C, 0x0C, [0x30]))
        L, R = samples(pcm)
        assert L[0] == 0, "the high nibble was read as the left sample"
        assert R[0] == 3, "the low nibble was read as the right sample"

    def test_each_channel_uses_its_own_header(self):
        """The reason the nibble order matters. Give the two channels different
        scales and the same nibble value: the outputs must differ by exactly the
        difference in scale, which can only happen if each nibble met its own
        header."""
        # left scale 12, right scale 10; both predictor 0; every nibble = 4
        pcm, _ = dtk.decode(frame(0x0C, 0x0A, [0x44] * 28))
        L, R = samples(pcm)
        assert L[0] == 4, L[0]                  # (4 << 12) >> 12
        assert R[0] == 16, R[0]                 # (4 << 12) >> 10
        assert R[0] == L[0] * 4, (
            "the channels did not scale by their own exponents; the headers and "
            "the nibbles have been paired up the wrong way")

    def test_swapping_the_nibbles_is_not_merely_a_channel_swap(self):
        """What the bug actually cost. If the two channels carried the same
        header, reading the nibbles backwards would only exchange them and the
        damage would be inaudible. They do not, so it corrupts both."""
        # headers stay put; only the nibbles change hands, which is exactly
        # what reading them backwards amounts to
        data = frame(0x0C, 0x0A, [0x31] * 28)
        L, R = samples(dtk.decode(data)[0])
        misread = frame(0x0C, 0x0A, [0x13] * 28)
        L2, R2 = samples(dtk.decode(misread)[0])
        assert (L[0], R[0]) == (1, 12), (L[0], R[0])
        assert (L2[0], R2[0]) == (3, 4), (L2[0], R2[0])
        assert (L2[0], R2[0]) != (R[0], L[0]), (
            "reading the nibbles the other way round is recoverable by swapping "
            "the channels back, which would make this a cosmetic bug")


# ── the predictor's state ───────────────────────────────────────────

class TestPredictorState:
    def test_history_keeps_its_fractional_bits(self):
        """The predictor is recursive, so rounding its state to a whole sample
        每 step feeds that rounding back into the next prediction. Six bits are
        carried; a decoder that stores the 16-bit output instead drifts, and the
        drift does not decay because the filter's pole is close to the circle.
        """
        random.seed(3)
        data = bytearray()
        for _ in range(40):                     # predictor 2, mid scales
            data += frame(0x27, 0x27,
                          [random.randrange(256) for _ in range(28)])
        L, _R = samples(dtk.decode(bytes(data))[0])
        rL, _rR = reference(bytes(data))
        assert list(L) == list(rL), (
            "the decoder diverged from the console's arithmetic; the usual "
            "cause is carrying the predictor history as a 16-bit sample")

    def test_an_undefined_predictor_index_is_memoryless(self):
        """Only four predictors exist. The hardware leaves the prediction at
        zero for the other twelve rather than folding the index back into
        range -- masking it would silently use a real filter on a byte that
        never asked for one."""
        data = frame(0x7C, 0x7C, [0x11] * 28)   # index 7, scale 12
        L, _R = samples(dtk.decode(data)[0])
        assert all(v == 1 for v in L), (
            "an undefined predictor index behaved like a defined one")


# ── against the reference, across the whole parameter space ─────────

def test_bit_exact_against_the_reference():
    """Every scale exponent and every predictor index, not a comfortable
    subset: the bug lived at index 0 with well-behaved data and would have
    survived any sample that did not vary the headers."""
    random.seed(7)
    data = bytearray()
    for _ in range(300):
        hl = (random.randrange(16) << 4) | random.randrange(16)
        hr = (random.randrange(16) << 4) | random.randrange(16)
        data += frame(hl, hr, [random.randrange(256) for _ in range(28)])
    data = bytes(data)
    L, R = samples(dtk.decode(data)[0])
    rL, rR = reference(data)
    assert len(L) == len(rL) == 300 * 28
    assert list(L) == list(rL), "left channel differs from the reference"
    assert list(R) == list(rR), "right channel differs from the reference"


# ── the plumbing that already worked ────────────────────────────────

def test_frame_count_and_defaults():
    pcm, info = dtk.decode(frame(0x0C, 0x0C, [0x30]))
    assert info["channels"] == 2 and info["frames"] == 28
    assert info["rate"] == 48000, "DTK is a fixed 48 kHz stream on GameCube"
    assert len(pcm) == 28 * 2 * 2


def test_decode_rate_override():
    _, info = dtk.decode(frame(0x0C, 0x0C, []), rate=32000)
    assert info["rate"] == 32000


def test_empty():
    pcm, info = dtk.decode(b"")
    assert info["frames"] == 0 and pcm == b""


def test_a_partial_trailing_frame_is_not_half_read():
    pcm, info = dtk.decode(frame(0x0C, 0x0C, [0x30]) + bytes(17))
    assert info["frames"] == 28, "a 17-byte tail was decoded as a frame"
