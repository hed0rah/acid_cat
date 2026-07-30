"""Nintendo DSP-ADPCM -- the GameCube / Wii ADPCM codec.

The console's hardware ADPCM: 8-byte frames of 14 samples, each frame a 1-byte
header (high nibble = predictor index 0..7, low nibble = scale exponent) plus 7
data bytes of 14 nibbles. Unlike the PS1's fixed filters, DSP-ADPCM carries 16
signed coefficients (8 predictor pairs) *per stream*, in the file header. The
predictor is fixed-point over 2048 (>> 11).

It underlies almost all GameCube/Wii audio: standalone .dsp, HAL's .hps streams,
BRSTM/BFSTM, .ast, and many game containers -- they differ in how they wrap the
frames and where the coefficients live, not in the codec.

    from acidcat.core.codecs import dsp
    h = dsp.parse_dsp_header(data)           # standalone .dsp
    pcm = dsp.decode(data[0x60:], h["coefs"], h["hist1"], h["hist2"], h["samples"])
"""

import struct

FRAME = 8                            # 1 header byte + 7 data bytes = 14 samples
_FRAME_SAMPLES = 14


def parse_dsp_header(data):
    """Parse a standalone .dsp header (0x60 bytes, big-endian). Returns dict with
    samples, rate, loop info, the 16 coefficients, and initial history."""
    (samples, nibbles, rate) = struct.unpack_from(">III", data, 0)
    loop_flag, fmt = struct.unpack_from(">HH", data, 12)
    coefs = list(struct.unpack_from(">16h", data, 0x1C))
    gain, ps, hist1, hist2 = struct.unpack_from(">HHhh", data, 0x3C)
    return {"samples": samples, "nibbles": nibbles, "rate": rate,
            "loop": bool(loop_flag), "coefs": coefs,
            "ps": ps, "hist1": hist1, "hist2": hist2}


def _clip16(s):
    return -32768 if s < -32768 else (32767 if s > 32767 else s)


def decode(data, coefs, hist1=0, hist2=0, samples=None):
    """Decode DSP-ADPCM frames to 16-bit mono PCM bytes. `coefs` is the 16-entry
    coefficient table; hist1/hist2 seed the predictor. Stops at `samples` if
    given (a stream's exact length; the last frame is often partly padding)."""
    import array
    out = array.array("h")
    p1, p2 = hist1, hist2
    limit = samples if samples is not None else (len(data) // FRAME) * _FRAME_SAMPLES
    for off in range(0, len(data) - FRAME + 1, FRAME):
        header = data[off]
        scale = 1 << (header & 0x0F)
        ci = (header >> 4) & 0x07
        c0, c1 = coefs[ci * 2], coefs[ci * 2 + 1]
        for i in range(_FRAME_SAMPLES):
            if len(out) >= limit:
                return out.tobytes()
            byte = data[off + 1 + (i >> 1)]
            nib = (byte >> 4) if (i & 1) == 0 else (byte & 0x0F)   # high nibble first
            s = nib - 16 if nib >= 8 else nib
            pred = (s * scale << 11) + c0 * p1 + c1 * p2
            samp = _clip16((pred + 1024) >> 11)
            p2, p1 = p1, samp
            out.append(samp)
    return out.tobytes()
