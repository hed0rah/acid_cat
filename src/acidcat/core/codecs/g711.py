"""G.711 companding: mu-law and A-law to linear 16-bit PCM.

The two telephone codecs of the PSTN, standardised as ITU-T G.711 in 1972 and
the encodings a Sun/NeXT .au carries as codes 1 (mu-law) and 27 (A-law). Each is
a non-linear 8-bit mapping that folds roughly 14 bits of dynamic range into a
byte: loud and quiet samples get different step sizes so speech survives eight
bits. Decoding is a fixed 256-entry table, so it is exact -- the same
decode-not-bypass class as the other codecs here.

Python's audioop carried these until it was removed in 3.13, so the tables live
here now. The expansion follows the reference implementation in Sun's g711.c
(BIAS 0x84, the segment and quantisation masks below).
"""

import struct


def _ulaw2lin(u):
    """One mu-law byte to a signed linear sample (Sun g711.c ulaw2linear).

    The peak magnitude is 32124, so the result always fits a signed 16-bit word.
    """
    u = ~u & 0xFF
    t = ((u & 0x0F) << 3) + 0x84
    t <<= (u & 0x70) >> 4
    return (0x84 - t) if (u & 0x80) else (t - 0x84)


def _alaw2lin(a):
    """One A-law byte to a signed linear sample (Sun g711.c alaw2linear).

    A-law inverts the sign convention: the sign bit set means positive. Peak
    magnitude is 32256, within a signed 16-bit word.
    """
    a ^= 0x55
    t = (a & 0x0F) << 4
    seg = (a & 0x70) >> 4
    if seg == 0:
        t += 8
    elif seg == 1:
        t += 0x108
    else:
        t = (t + 0x108) << (seg - 1)
    return t if (a & 0x80) else -t


# 256 entries, each the 2-byte little-endian PCM sample for that code byte. Built
# once at import; decode is then a table lookup per input byte.
_ULAW_LE = [struct.pack("<h", _ulaw2lin(i)) for i in range(256)]
_ALAW_LE = [struct.pack("<h", _alaw2lin(i)) for i in range(256)]


def decode_ulaw(data):
    """mu-law bytes -> interleaved signed 16-bit little-endian PCM.

    Channel interleaving is carried through unchanged: G.711 is one byte per
    sample regardless of channel count, so interleaved input stays interleaved.
    """
    tbl = _ULAW_LE
    out = bytearray()
    for b in data:
        out += tbl[b]
    return bytes(out)


def decode_alaw(data):
    """A-law bytes -> interleaved signed 16-bit little-endian PCM."""
    tbl = _ALAW_LE
    out = bytearray()
    for b in data:
        out += tbl[b]
    return bytes(out)
