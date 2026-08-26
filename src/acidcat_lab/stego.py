"""Hide a payload in the low bits of PCM WAV samples.

Two modes, and which of them is detectable is the opposite of what you would
expect. `embed` whitens the payload against a keystream first, so the low bit
plane comes out uniform; `raw=True` writes the plaintext bits straight in.

MEASURED, because the intuition is backwards. A real 16-bit recording already
has near-random LSBs -- that is what the bottom bit of a microphone signal is.
So a whitened payload lands in noise and looks like noise, while a raw one is
LESS random than the carrier and is the one that shifts the statistic:

    carrier            LSB entropy mean 1.000
    whitened embed     1.000   -- indistinguishable
    raw embed          0.996   -- measurably lower

acidcat's `lsb_entropy` notice reports "uniformly high LSB entropy", which
fires on six of six clean real WAVs before anything is hidden in them. It is
honestly worded and it is not evidence. See tests/test_lab_loop.py, which pins
what is and is not detectable here rather than implying more.

Not a secure tool: the key seeds Python's PRNG.
"""


import os
import struct
import random

_MAGIC = b"ACST"  # 4-byte marker so extract can tell "no payload" from garbage


def _keystream(key, n):
    r = random.Random(key)
    return bytes(r.getrandbits(8) for _ in range(n))


def _data_region(wav):
    """(offset, length, sample_width) of the PCM data chunk (any linear-PCM depth)."""
    if wav[:4] != b"RIFF" or wav[8:12] != b"WAVE":
        raise ValueError("not a RIFF/WAVE file")
    bits = None
    pos = 12
    n = len(wav)
    while pos + 8 <= n:
        cid = wav[pos:pos + 4]
        size = struct.unpack_from("<I", wav, pos + 4)[0]
        body = pos + 8
        if cid == b"fmt " and size >= 16:
            bits = struct.unpack_from("<H", wav, body + 14)[0]
        elif cid == b"data":
            # the LSB lives in the low byte of each little-endian sample at
            # offset i*width, so the embed/extract are width-agnostic for any
            # linear-PCM depth. only the sample width has to be known.
            if bits not in (8, 16, 24, 32):
                raise ValueError(f"unsupported bits_per_sample {bits}")
            return body, min(size, n - body), bits // 8
        pos = body + size + (size & 1)
    raise ValueError("no data chunk")


def capacity(wav):
    """How many payload bytes fit (one bit per sample, minus the header)."""
    _, length, width = _data_region(wav)
    samples = length // width
    header_bits = (len(_MAGIC) + 4) * 8
    return max(0, (samples - header_bits) // 8)


def _bits(data):
    for byte in data:
        for i in range(7, -1, -1):
            yield (byte >> i) & 1


def embed(wav, payload, key=1337, raw=False):
    off, length, width = _data_region(wav)
    blob = _MAGIC + struct.pack("<I", len(payload)) + payload
    if not raw:
        ks = _keystream(key, len(blob))
        blob = bytes(b ^ k for b, k in zip(blob, ks))
    need = len(blob) * 8
    samples = length // width
    if need > samples:
        raise ValueError(f"payload needs {need} samples, carrier holds {samples}")
    out = bytearray(wav)
    for i, bit in enumerate(_bits(blob)):
        p = off + i * width           # low byte of the i-th LE sample
        out[p] = (out[p] & 0xFE) | bit
    return bytes(out)


def _read_bits(wav, off, width, nbits):
    val = bytearray((nbits + 7) // 8)
    for i in range(nbits):
        bit = wav[off + i * width] & 1
        val[i // 8] |= bit << (7 - (i % 8))
    return bytes(val)


def extract(wav, key=1337, raw=False):
    off, length, width = _data_region(wav)
    head_len = len(_MAGIC) + 4
    head = _read_bits(wav, off, width, head_len * 8)
    if not raw:
        head = bytes(b ^ k for b, k in zip(head, _keystream(key, head_len)))
    if head[:4] != _MAGIC:
        raise ValueError("no acidcat-stego payload found (wrong key or none embedded)")
    plen = struct.unpack_from("<I", head, 4)[0]
    total = head_len + plen
    blob = _read_bits(wav, off, width, total * 8)
    if not raw:
        blob = bytes(b ^ k for b, k in zip(blob, _keystream(key, total)))
    return blob[head_len:head_len + plen]


def _arg(flag, default=None, cast=str):
    return cast(sys.argv[sys.argv.index(flag) + 1]) if flag in sys.argv else default
