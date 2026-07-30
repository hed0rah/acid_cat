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

from acidcat.core.primitives.pcm import PS_ADPCM_FILTER, clip16, signed_nibble

VAG_MAGIC = b"VAGp"
_VAG_HEADER = 0x30                   # 48-byte .VAG header; ADPCM data follows
_BLOCK = 16                          # SPU-ADPCM block: 2 header + 14 data bytes

# SPU-ADPCM filter coefficients (f0, f1), scaled by 1/64 -- the same five pairs
# CD-XA and DTK use (core/primitives/pcm.PS_ADPCM_FILTER).

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
        f0, f1 = PS_ADPCM_FILTER[min(blk[0] >> 4, 4)]
        flag = blk[1]
        for i in range(14):
            for nib in (blk[2 + i] & 0x0F, blk[2 + i] >> 4):     # low nibble first
                t = signed_nibble(nib)
                s = clip16((t << (12 - shift)) + ((p1 * f0 + p2 * f1 + 32) >> 6))
                p2, p1 = p1, s
                out.append(s)
        if stop_on_end and (flag & FLAG_LOOP_END) and not (flag & FLAG_LOOP):
            break
    return out.tobytes()


VAB_MAGIC = b"pBAV"                  # "VABp" little-endian, the VAB header magic


def parse_vab(vh):
    """Parse a VAB header (.VH / .HD). It carries the per-VAG sizes that split
    the sibling .VB / .BD body into individual SPU-ADPCM samples. Returns
    dict(version, programs, tones, vags, sizes) where sizes are byte lengths.
    Raises VagError if the magic is wrong or the header is truncated.

    Layout: 32-byte header, 128 program-attr records (16 B each), then one
    16-tone table (32 B/tone) per used program, then a 256-entry VAG size table
    (u16, in 8-byte units; entry 0 is a leading pad)."""
    if vh[:4] != VAB_MAGIC:
        raise VagError("not a VAB header (missing 'pBAV' magic)")
    version = struct.unpack_from("<I", vh, 4)[0]
    _, nprog, ntone, nvag = struct.unpack_from("<HHHH", vh, 16)
    off = 32 + 2048 + nprog * 512
    if off + (nvag + 1) * 2 > len(vh):
        raise VagError("VAB header truncated")
    sizes = struct.unpack_from(f"<{nvag + 1}H", vh, off)
    return {"version": version, "programs": nprog, "tones": ntone, "vags": nvag,
            "sizes": [s * 8 for s in sizes[1:nvag + 1]]}


def split_vb(vb, sizes):
    """Yield (index, sample_bytes) for each VAG in a .VB / .BD body, given the
    byte sizes from parse_vab. Skips zero-length and out-of-range entries."""
    pos = 0
    for i, sz in enumerate(sizes):
        if sz and pos + sz <= len(vb):
            yield i, vb[pos:pos + sz]
        pos += sz


def looks_like_spu(data, blocks=512, min_nonzero=0.05):
    """Heuristic: does `data` look like raw SPU-ADPCM (a .VB/.BD-style sample
    bank with no header)? Returns the fraction of leading 16-byte blocks with a
    valid header -- shift 0..12, filter 0..4, flag byte 0..7. Random bytes score
    ~0.03 (only flag<=7 is 8/256), a real bank scores ~1.0. Returns 0 if the data
    is essentially all zero (a silent region is not a bank worth carving)."""
    n = min(blocks, len(data) // _BLOCK)
    if n < 8:
        return 0.0
    ok = nonzero = 0
    for b in range(n):
        blk = data[b * _BLOCK:(b + 1) * _BLOCK]
        if (blk[0] & 0x0F) <= 12 and (blk[0] >> 4) <= 4 and blk[1] <= 7:
            ok += 1
        if any(blk[2:]):
            nonzero += 1
    if nonzero / n < min_nonzero:
        return 0.0
    return ok / n


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
