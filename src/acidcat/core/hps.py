"""HAL PCM Stream (.hps) -- HAL Laboratory's streamed GameCube music format.

A HAL container around DSP-ADPCM (core/dsp.py): an 8-byte magic " HALPST\\0", a
sample rate and channel count, one DSP channel context per channel (carrying the
16 coefficients), then a chain of interleaved blocks. Each block header gives its
data size and the file offset of the next block (0xFFFFFFFF ends the chain); the
per-channel ADPCM runs contiguously within the block, so a channel decodes as one
continuous DSP stream across all blocks.

Used by HAL's GameCube titles (Chibi-Robo, Kirby, Smash Bros) for music.

    from acidcat.core import hps
    pcm, info = hps.decode(open("song.hps","rb").read())   # interleaved 16-bit PCM
"""

import struct

from acidcat.core import dsp

MAGIC = b" HALPST\x00"
_CHAN_CTX = 0x38                     # per-channel context size; coefs at +0x10
_BLOCK_HDR = 0x20


class HpsError(Exception):
    pass


def decode(data):
    """Decode a .hps to interleaved 16-bit PCM. Returns (pcm_bytes, info) where
    info has channels, rate, frames. Raises HpsError on a bad header."""
    if data[:8] != MAGIC:
        raise HpsError("not a HALPST stream")
    rate, channels = struct.unpack_from(">II", data, 8)
    if not (1 <= channels <= 2):
        raise HpsError(f"unsupported channel count {channels}")
    coefs = [list(struct.unpack_from(">16h", data, 0x10 + c * _CHAN_CTX + 0x10))
             for c in range(channels)]

    chans = [bytearray() for _ in range(channels)]
    off = 0x10 + channels * _CHAN_CTX
    seen = set()
    while 0 <= off <= len(data) - _BLOCK_HDR and off not in seen:
        seen.add(off)
        dsp_size = struct.unpack_from(">I", data, off)[0]
        next_off = struct.unpack_from(">I", data, off + 8)[0]
        per = dsp_size // channels
        body = off + _BLOCK_HDR
        if body + dsp_size > len(data):
            per = (len(data) - body) // channels
        for c in range(channels):
            chans[c] += data[body + c * per:body + (c + 1) * per]
        if next_off == 0xFFFFFFFF or next_off <= off or next_off > len(data):
            break
        off = next_off

    pcms = [dsp.decode(bytes(c), coefs[i]) for i, c in enumerate(chans)]
    n = min(len(p) // 2 for p in pcms) if pcms else 0
    if channels == 1:
        pcm = pcms[0][:n * 2]
    else:
        import array
        inter = array.array("h", bytes(4 * n))
        left = array.array("h"); left.frombytes(pcms[0][:n * 2])
        right = array.array("h"); right.frombytes(pcms[1][:n * 2])
        inter[0::2] = left
        inter[1::2] = right
        pcm = inter.tobytes()
    return pcm, {"channels": channels, "rate": rate, "frames": n}
