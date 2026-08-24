"""BRSTM (RSTM) -- Nintendo's streamed audio container (GameCube / Wii).

A RIFF-cousin wrapping DSP-ADPCM (core/dsp.py): the "RSTM" magic, then a HEAD
chunk describing the stream and an interleaved DATA chunk. HEAD is a nest of
byte-offset "references" (an 0x01 marker + a 32-bit offset, all relative to
HEAD+8): one into the stream-info block (codec, channels, rate, block layout),
one into the per-channel table whose entries lead to each channel's 16 DSP
coefficients. DATA holds fixed-size blocks interleaved per channel, so a channel
decodes as one continuous DSP stream once its blocks are gathered.

BRSTM is the Wii/GameCube music workhorse (first-party titles, RVL SDK). Only
the DSP-ADPCM codec (type 2) is handled -- the PCM types are vanishingly rare.

    from acidcat.core.codecs import brstm
    pcm, info = brstm.decode(open("bgm.brstm","rb").read())   # interleaved 16-bit PCM
"""

import struct

from acidcat.core.codecs import dsp
from acidcat.core.primitives.pcm import interleave_stereo

MAGIC = b"RSTM"
_DSP_ADPCM = 2
_COEF_LEN = 32                   # 16 signed coefficients, then gain and history


class BrstmError(Exception):
    pass


def _refoff(data, at):
    """The 32-bit offset field of an 8-byte reference at `at` (marker + offset)."""
    return struct.unpack_from(">i", data, at + 4)[0]


def parse_header(data):
    """Parse the RSTM/HEAD header. Returns dict with codec, channels, rate,
    samples, block layout, audio offset, and the per-channel coefficient tables.
    Raises BrstmError if not a BRSTM or not DSP-ADPCM."""
    if data[:4] != MAGIC:
        raise BrstmError("not a BRSTM (missing RSTM magic)")
    head_off = struct.unpack_from(">I", data, 0x10)[0]
    base = head_off + 8                                  # references are relative to here
    h1 = base + _refoff(data, base + 0x00)               # stream-info block
    h3 = base + _refoff(data, base + 0x10)               # channel-info table
    codec, _loop, channels = data[h1], data[h1 + 1], data[h1 + 2]
    if codec != _DSP_ADPCM:
        raise BrstmError(f"unsupported BRSTM codec {codec} (only DSP-ADPCM)")
    rate = struct.unpack_from(">H", data, h1 + 4)[0]
    samples = struct.unpack_from(">I", data, h1 + 0x0C)[0]
    audio_off = struct.unpack_from(">I", data, h1 + 0x10)[0]
    blocks = struct.unpack_from(">I", data, h1 + 0x14)[0]
    block_size = struct.unpack_from(">I", data, h1 + 0x18)[0]
    final_size = struct.unpack_from(">I", data, h1 + 0x20)[0]
    final_pad = struct.unpack_from(">I", data, h1 + 0x28)[0]
    # Behind the 16 coefficients each channel keeps a gain, an initial
    # predictor/scale, and the two history samples the stream starts from. They
    # are zero in every specimen measured -- these streams begin at silence --
    # but a stream that does not begin at silence needs them, and reading past
    # them is how a decoder ends up guessing at its own first frame.
    coefs, hist = [], []
    for c in range(channels):
        chan_info = base + _refoff(data, h3 + 4 + c * 8)
        coef_at = base + _refoff(data, chan_info)
        coefs.append(list(struct.unpack_from(">16h", data, coef_at)))
        at = coef_at + _COEF_LEN
        if at + 8 <= len(data):
            hist.append(struct.unpack_from(">hh", data, at + 4))
        else:
            hist.append((0, 0))
    return {"codec": codec, "channels": channels, "rate": rate, "samples": samples,
            "audio_off": audio_off, "blocks": blocks, "block_size": block_size,
            "final_size": final_size, "final_pad": final_pad, "coefs": coefs,
            "hist": hist}


def decode(data):
    """Decode a BRSTM to interleaved 16-bit PCM. Returns (pcm_bytes, info) with
    channels, rate, frames. Raises BrstmError on a bad header / unsupported codec."""
    h = parse_header(data)
    ch, blksz = h["channels"], h["block_size"]
    chans = [bytearray() for _ in range(ch)]
    for b in range(h["blocks"]):
        last = b == h["blocks"] - 1
        used = h["final_size"] if (last and h["final_size"]) else blksz
        stride = h["final_pad"] if last else blksz      # last block packs at its padded size
        block_base = h["audio_off"] + b * blksz * ch
        for c in range(ch):
            o = block_base + c * stride
            chans[c] += data[o:o + used]
    pcms = [dsp.decode(bytes(chans[c]), h["coefs"][c],
                       h["hist"][c][0], h["hist"][c][1], samples=h["samples"])
            for c in range(ch)]
    n = min((len(p) // 2 for p in pcms), default=0)
    if ch == 1:
        pcm = pcms[0][:n * 2]
    else:
        import array
        left = array.array("h"); left.frombytes(pcms[0][:n * 2])
        right = array.array("h"); right.frombytes(pcms[1][:n * 2])
        pcm = interleave_stereo(left, right)
    return pcm, {"channels": ch, "rate": h["rate"], "frames": n}
