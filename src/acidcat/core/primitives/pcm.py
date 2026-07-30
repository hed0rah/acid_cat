"""ADPCM / PCM sample-math primitives shared by the console-audio codecs.

Before this module, clip-to-int16, 4-bit sign extension, and the PlayStation/
CD-XA/GameCube-DTK predictor filter table were each hand-rolled in adpcm, adx,
cdxa, dtk, and vag -- identical math, many copies. Stereo interleaving lived in
five more (adx, brstm, hps, dtk, cdxa).
"""

import array


def clip16(v):
    """Clamp an int to the signed 16-bit range [-32768, 32767]."""
    return -32768 if v < -32768 else (32767 if v > 32767 else v)


def signed_nibble(n):
    """Sign-extend the low 4 bits of ``n`` to a signed int in -8..7."""
    n &= 0x0F
    return n - 16 if n >= 8 else n


# PlayStation SPU / CD-XA / GameCube-DTK ADPCM share one 2-tap predictor filter:
# (f0, f1) coefficients indexed by the block's filter nibble. DTK uses the first
# four entries; XA/VAG use all five. The weights are the standard SPU/XA values.
PS_ADPCM_FILTER = ((0, 0), (60, 0), (115, -52), (98, -55), (122, -60))


def interleave_stereo(left, right):
    """Interleave two channels of 16-bit samples into stereo PCM bytes (L R L R).

    ``left``/``right`` are sequences of ints (a list or ``array('h')``); the
    shorter length wins. Returns ``bytes``."""
    n = min(len(left), len(right))
    inter = array.array("h", bytes(4 * n))
    inter[0::2] = array.array("h", left[:n])
    inter[1::2] = array.array("h", right[:n])
    return inter.tobytes()
