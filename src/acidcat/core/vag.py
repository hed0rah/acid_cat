"""PlayStation SPU-ADPCM and the .VAG container.

The sound-effect and instrument-sample codec of the PS1 SPU (and the .VAG files
that wrap it). Where CD-XA (core/cdxa.py) streams the music off the disc, SPU
samples are the short sounds loaded into the SPU's 512 KB of RAM: gunshots,
footsteps, one-shot instrument hits, the pieces a VAB bank stitches into music.

The codec is the same 2-tap predictor family as CD-XA, in a tighter block: 16
bytes = a 1-byte (shift, filter) header + a 1-byte loop/end flag + 14 bytes of
28 nibbles. Mono. A standalone .VAG prepends a 48-byte header (magic "VAGp",
big-endian sample rate and data size, a 16-byte name).

    from acidcat.core import vag
    info = vag.parse_vag(open("hit.vag","rb").read())
    pcm = vag.decode_spu(info["data"])          # 16-bit mono PCM
"""

import struct

VAG_MAGIC = b"VAGp"
_VAG_HEADER = 0x30                   # 48-byte .VAG header; ADPCM data follows
_BLOCK = 16                          # SPU-ADPCM block: 2 header + 14 data bytes

# SPU-ADPCM filter coefficients (f0, f1), scaled by 1/64 -- the same five pairs
# CD-XA uses (core/cdxa._XA_FILTER).
_FILTER = ((0, 0), (60, 0), (115, -52), (98, -55), (122, -60))

# loop/end flag byte (block[1]) bits
FLAG_LOOP_END = 0x01                 # last block of a loop / end of sample
FLAG_LOOP = 0x02                     # sustain point (loop back to loop-start)
FLAG_LOOP_START = 0x04              # loop-start marker


class VagError(Exception):
    pass


def parse_vag(data):
    """Parse a .VAG container. Returns dict(version, rate, name, data). Raises
    VagError if the magic is wrong."""
    if data[:4] != VAG_MAGIC:
        raise VagError("not a VAG file (missing 'VAGp' magic)")
    version = struct.unpack_from(">I", data, 4)[0]
    data_size = struct.unpack_from(">I", data, 0x0C)[0]
    rate = struct.unpack_from(">I", data, 0x10)[0]
    name = data[0x20:0x30].split(b"\x00", 1)[0].decode("latin-1", "replace").strip()
    body = data[_VAG_HEADER:]
    if data_size and data_size <= len(body):
        body = body[:data_size]
    return {"version": version, "rate": rate or 44100, "name": name, "data": body}


def _sign_nibble(n):
    n &= 0x0F
    return n - 16 if n >= 8 else n


def _clip16(s):
    return -32768 if s < -32768 else (32767 if s > 32767 else s)


def decode_spu(data, stop_on_end=True):
    """Decode raw SPU-ADPCM (a run of 16-byte blocks) to 16-bit mono PCM bytes.
    Predictor state carries across blocks. If stop_on_end, decoding halts at the
    first block whose flag byte sets FLAG_LOOP_END without FLAG_LOOP (the SPU's
    one-shot terminator); otherwise all whole blocks are decoded."""
    import array
    out = array.array("h")
    p1 = p2 = 0
    for off in range(0, len(data) - _BLOCK + 1, _BLOCK):
        blk = data[off:off + _BLOCK]
        shift = blk[0] & 0x0F
        if shift > 12:
            shift = 9                    # invalid shift; SPU treats 13..15 as 9
        f0, f1 = _FILTER[min(blk[0] >> 4, 4)]
        flag = blk[1]
        for i in range(14):
            for nib in (blk[2 + i] & 0x0F, blk[2 + i] >> 4):     # low nibble first
                t = _sign_nibble(nib)
                s = _clip16((t << (12 - shift)) + ((p1 * f0 + p2 * f1 + 32) >> 6))
                p2, p1 = p1, s
                out.append(s)
        if stop_on_end and (flag & FLAG_LOOP_END) and not (flag & FLAG_LOOP):
            break
    return out.tobytes()


def loop_points(data):
    """Scan SPU-ADPCM blocks for loop markers. Returns (start_sample, end_sample)
    in mono samples, or None if the sample is a one-shot. Each block is 28
    samples."""
    start = end = None
    for n, off in enumerate(range(0, len(data) - _BLOCK + 1, _BLOCK)):
        flag = data[off + 1]
        if flag & FLAG_LOOP_START:
            start = n * 28
        if flag & FLAG_LOOP_END:
            end = (n + 1) * 28
    return (start, end) if start is not None else None
