"""GameCube DTK / .adp streaming ADPCM.

The ADPCM the GameCube's disc hardware streams directly (the "DTK", Disk Track),
used for background music. Fixed 32-byte frames of 28 stereo samples: two 1-byte
headers (left, right -- each a predictor index in the high nibble, a scale in the
low nibble), duplicated at bytes 2-3 for error resilience, then 28 data bytes
whose LOW nibble is a left sample and HIGH nibble a right sample. Unlike DSP-ADPCM
the four predictor coefficient pairs are fixed (the same family as CD-XA), and
the sample is (nibble << 12) >> scale plus the predictor.

Which nibble belongs to which channel is not cosmetic. Each channel has its own
header, so reading the nibbles the other way round does not swap the channels --
it decodes each channel's samples with the OTHER channel's scale exponent, and a
scale exponent is a power of two. That is why it sounds like damage rather than
like a mix-up.

    from acidcat.core.codecs import dtk
    pcm, info = dtk.decode(open("bgm.adp","rb").read())
"""

from acidcat.core.primitives.pcm import PS_ADPCM_FILTER, clip16, interleave_stereo, signed_nibble


_FRAME = 32
_SAMPLES = 28
_RATE = 48000                        # DTK streams at a fixed 48 kHz on GameCube
# fixed predictor coefficient pairs (f0, f1), scaled by 1/64
_COEF = PS_ADPCM_FILTER[:4]






_HIST_MIN, _HIST_MAX = -0x200000, 0x1FFFFF   # the predictor's own range, six bits wide


def _step(nibble, header, h1, h2):
    """One sample, and the two history values that follow it.

    History is carried at SIX FRACTIONAL BITS and is not clipped to a sample --
    the shift down to 16 bits happens on the way out, not on the way round. That
    is not an optimisation. The predictor is recursive, so rounding its state to
    a whole sample every step feeds the rounding error back into the next
    prediction, and in a filter with a pole this close to the unit circle the
    error does not decay.
    """
    idx = header >> 4
    c0, c1 = _COEF[idx] if idx < len(_COEF) else (0, 0)
    hist = (c0 * h1 + c1 * h2 + 0x20) >> 6
    hist = _HIST_MIN if hist < _HIST_MIN else (_HIST_MAX if hist > _HIST_MAX else hist)
    cur = (((nibble << 12) >> (header & 0x0F)) << 6) + hist
    return clip16(cur >> 6), cur, h1


def decode(data, rate=_RATE):
    """Decode DTK/.adp to interleaved 16-bit stereo PCM. Returns (pcm, info)."""
    import array
    left = array.array("h")
    right = array.array("h")
    l1 = l2 = r1 = r2 = 0
    for off in range(0, len(data) - _FRAME + 1, _FRAME):
        hl, hr = data[off], data[off + 1]
        for i in range(_SAMPLES):
            b = data[off + 4 + i]
            lv, l1, l2 = _step(signed_nibble(b & 0x0F), hl, l1, l2)
            rv, r1, r2 = _step(signed_nibble(b >> 4), hr, r1, r2)
            left.append(lv)
            right.append(rv)
    n = min(len(left), len(right))
    return interleave_stereo(left, right), {"channels": 2, "rate": rate, "frames": n}
