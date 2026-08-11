"""GameCube DTK / .adp streaming ADPCM.

The ADPCM the GameCube's disc hardware streams directly (the "DTK", Disk Track),
used for background music. Fixed 32-byte frames of 28 stereo samples: two 1-byte
headers (left, right -- each a predictor index in the high nibble, a scale in the
low nibble), duplicated at bytes 2-3 for error resilience, then 28 data bytes
whose high nibble is a left sample and low nibble a right sample. Unlike DSP-ADPCM
the four predictor coefficient pairs are fixed (the same family as CD-XA), and
the sample is (nibble << 12) >> scale plus the predictor (matching Dolphin).

    from acidcat.core.codecs import dtk
    pcm, info = dtk.decode(open("bgm.adp","rb").read())
"""

from acidcat.core.primitives.pcm import PS_ADPCM_FILTER, clip16, interleave_stereo, signed_nibble


_FRAME = 32
_SAMPLES = 28
_RATE = 48000                        # DTK streams at a fixed 48 kHz on GameCube
# fixed predictor coefficient pairs (f0, f1), scaled by 1/64
_COEF = PS_ADPCM_FILTER[:4]






def decode(data, rate=_RATE):
    """Decode DTK/.adp to interleaved 16-bit stereo PCM. Returns (pcm, info)."""
    import array
    left = array.array("h")
    right = array.array("h")
    l1 = l2 = r1 = r2 = 0
    for off in range(0, len(data) - _FRAME + 1, _FRAME):
        hl, hr = data[off], data[off + 1]
        shl, fl = hl & 0x0F, (hl >> 4) & 0x03
        shr, fr = hr & 0x0F, (hr >> 4) & 0x03
        cl0, cl1 = _COEF[fl]
        cr0, cr1 = _COEF[fr]
        for i in range(_SAMPLES):
            b = data[off + 4 + i]
            ln, rn = signed_nibble(b >> 4), signed_nibble(b & 0x0F)
            lv = clip16(((ln << 12) >> shl) + ((cl0 * l1 + cl1 * l2 + 0x20) >> 6))
            rv = clip16(((rn << 12) >> shr) + ((cr0 * r1 + cr1 * r2 + 0x20) >> 6))
            l2, l1 = l1, lv
            r2, r1 = r1, rv
            left.append(lv)
            right.append(rv)
    n = min(len(left), len(right))
    return interleave_stereo(left, right), {"channels": 2, "rate": rate, "frames": n}
