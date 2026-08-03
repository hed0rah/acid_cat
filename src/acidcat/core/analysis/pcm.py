"""Decode PCM to samples with numpy, without librosa.

The analysis extra pulls librosa/soundfile, which is a heavy stack to require
for measurements that are a handful of array operations. Everything here needs
only numpy, so bandwidth and channel analysis can run on a large library at a
speed the librosa feature path cannot reach.

Scope is deliberate: uncompressed integer and float PCM in WAV/RF64 and AIFF,
which is what a sample library is mostly made of. Compressed codecs are somebody
else's job -- `load` reports that it cannot read them rather than guessing.
"""

import struct

_MAX_FRAMES = 1 << 22          # ~4.2 M frames is plenty to characterize a file


class Unsupported(Exception):
    """The file is not PCM we decode (compressed codec, odd sample format)."""


def _np():
    try:
        import numpy as np
    except ImportError:                              # pragma: no cover
        from acidcat.util.deps import require
        require("numpy", group="analysis")
        raise
    return np


def _find(chunks, cid):
    """Chunk by id. IFF ids are 4 bytes, so "fmt" arrives as "fmt "."""
    for c in chunks:
        if (c.get("id") or "").strip() == cid:
            return c
    return None


def _wav_spec(data, chunks):
    """(bits, float_fmt, channels, rate, start, size) for WAV/RF64."""
    fmt, dat = _find(chunks, "fmt"), _find(chunks, "data")
    if not fmt or not dat:
        raise Unsupported("no fmt/data chunk")
    off = fmt["offset"] + 8
    try:
        tag, ch, rate = struct.unpack_from("<HHI", data, off)
        bits = struct.unpack_from("<H", data, off + 14)[0]
    except struct.error:
        raise Unsupported("truncated fmt chunk")
    if tag == 0xFFFE:                                # WAVE_FORMAT_EXTENSIBLE
        try:                                         # real tag is in the GUID
            tag = struct.unpack_from("<H", data, off + 24)[0]
        except struct.error:
            raise Unsupported("truncated extensible fmt")
    if tag == 3:
        fl = True
    elif tag == 1:
        fl = False
    else:
        raise Unsupported(f"compressed or unknown format tag {tag}")
    return bits, fl, ch, rate, dat["offset"] + 8, dat["size"]


def _aiff_spec(data, chunks):
    comm, ssnd = _find(chunks, "COMM"), _find(chunks, "SSND")
    if not comm or not ssnd:
        raise Unsupported("no COMM/SSND chunk")
    try:
        ch, _frames, bits = struct.unpack_from(">HIH", data, comm["offset"] + 8)
        rate = _extended80(data, comm["offset"] + 8 + 8)
        soff = struct.unpack_from(">I", data, ssnd["offset"] + 8)[0]
    except struct.error:
        raise Unsupported("truncated COMM/SSND")
    start = ssnd["offset"] + 8 + 8 + soff
    return bits, False, ch, rate, start, max(0, ssnd["size"] - 8 - soff)


def _extended80(data, off):
    """AIFF stores the sample rate as an 80-bit IEEE extended float."""
    expon = struct.unpack_from(">H", data, off)[0]
    himant, lomant = struct.unpack_from(">II", data, off + 2)
    sign = -1 if expon & 0x8000 else 1
    expon &= 0x7FFF
    if expon == 0 and himant == 0 and lomant == 0:
        return 0
    return int(sign * (himant * 2.0 ** (expon - 16383 - 31)
                       + lomant * 2.0 ** (expon - 16383 - 63)))


def _decode(np, raw, bits, is_float, byteorder):
    """Raw bytes -> float64 array in [-1, 1)."""
    pre = "<" if byteorder == "little" else ">"
    if is_float:
        if bits == 32:
            return np.frombuffer(raw, dtype=pre + "f4").astype(np.float64)
        if bits == 64:
            return np.frombuffer(raw, dtype=pre + "f8").astype(np.float64)
        raise Unsupported(f"{bits}-bit float")
    if bits == 8:                                    # WAV 8-bit is unsigned
        return (np.frombuffer(raw, dtype="u1").astype(np.float64) - 128.0) / 128.0
    if bits == 16:
        return np.frombuffer(raw, dtype=pre + "i2").astype(np.float64) / 32768.0
    if bits == 32:
        return np.frombuffer(raw, dtype=pre + "i4").astype(np.float64) / 2147483648.0
    if bits == 24:
        # no native 24-bit dtype: widen each 3-byte sample into the high three
        # bytes of an int32 so the sign comes out right, then scale
        b = np.frombuffer(raw, dtype="u1").reshape(-1, 3)
        wide = np.zeros((len(b), 4), dtype="u1")
        if byteorder == "little":
            wide[:, 1:] = b
        else:
            wide[:, :3] = b
        vals = wide.view("<i4" if byteorder == "little" else ">i4").ravel()
        return vals.astype(np.float64) / 2147483648.0
    raise Unsupported(f"{bits}-bit integer PCM")


def load(path, *, max_frames=_MAX_FRAMES):
    """Decode `path` to ``(channels, rate)``.

    `channels` is a list of float64 arrays, one per channel, so callers can look
    at stereo relationships instead of only a mono mix. Raises Unsupported for
    anything that is not PCM we read.
    """
    np = _np()
    from acidcat.core.walk import walk_file
    label, chunks, _warnings = walk_file(path)
    with open(path, "rb") as f:
        data = f.read()

    if label in ("RIFF/WAVE", "RF64/WAVE"):
        bits, fl, ch, rate, start, size = _wav_spec(data, chunks)
        order = "little"
    elif label == "IFF/AIFF":
        bits, fl, ch, rate, start, size = _aiff_spec(data, chunks)
        order = "big"
    else:
        raise Unsupported(f"not PCM we decode: {label or 'unknown format'}")

    if ch < 1 or bits < 8:
        raise Unsupported("degenerate channel count or bit depth")
    # A declared rate of 0 is not merely odd -- every spectral measurement
    # divides by it. Rejecting it here rather than downstream keeps the
    # ZeroDivisionError out of `audit`, which is aimed at untrusted files by
    # definition and must never answer a malformed header with a traceback.
    if not 1 <= rate <= 4_000_000:
        raise Unsupported(f"implausible sample rate: {rate}")
    frame = (bits // 8) * ch
    size = min(size, max(0, len(data) - start))
    size -= size % frame                              # whole frames only
    if max_frames:
        size = min(size, max_frames * frame)
    if size <= 0:
        raise Unsupported("no sample data")

    flat = _decode(np, data[start:start + size], bits, fl, order)
    usable = (len(flat) // ch) * ch
    planes = flat[:usable].reshape(-1, ch).T
    return [np.ascontiguousarray(p) for p in planes], rate
