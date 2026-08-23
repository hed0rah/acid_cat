"""Structural detection of headerless compressed-audio streams -- the third
`locate` engine.

The signature sweep needs a magic; the statistical detector needs raw-PCM
smoothness. Compressed audio with NO container has neither -- it is high-entropy
(so the statistical engine ignores it, correctly) and magicless. But it is not
structure-less: a codec stream is a chain of self-describing FRAMES. MPEG audio
(MP1/2/3) frames open with an 11-bit sync and carry a computable length, so a run
of consecutive valid frames each frame-length apart is an MPEG stream -- found by
CADENCE, not magic. This catches the raw .mp3 with no ID3 tag, audio ripped out
of a game asset, the CTF blob that `strings` can't touch.

Signature-found MP3s (an ID3 tag, caught by the sweep) are not this engine's job;
this is the headerless case. Free-format frames (length measured, not derived)
are skipped in v1.
"""

from acidcat.core.formats.mp3 import decode_frame_header

_MIN_FRAMES = 12                 # a chain this long is a stream, not chance
_MAX_STREAMS = 4096
_READ_CAP = 256 * 1024 * 1024

# Share of a claimed stream that may be the sync byte itself.
#
# A chain of valid, self-consistent frames is not proof of a stream. Inside
# TILES002.ART, a Duke Nukem art tile sheet, this reported 25 frames of "MPEG 1
# Layer I, 128 kbps, 48000 Hz" at confidence 0.85, with a full per-frame table
# -- because the sheet is a field of 0xFF and 0xFF is where a sync lives. Every
# structural check passed: the headers decoded, the version, layer and rate
# agreed across all 25, and the chain did not restart at neighbouring offsets.
#
# What separates it is the payload, and specifically THIS byte rather than
# entropy in general. 0xFF is the sync pattern, so a real MPEG payload is full
# of bit patterns an encoder chose partly to avoid emitting it; a stream that is
# three quarters 0xFF would be riddled with false syncs of its own. Measured
# over 25 real MP3s the density runs 0.0029 to 0.0189, and over the art sheet
# 0.70 to 0.75 -- a factor of 37 with nothing in between.
#
# Generic entropy does NOT work here and the difference matters: the highest
# single-byte share in the corpus is 0.99, in a generated near-silent file whose
# dominant byte is 0x00. A rule about "too uniform" would have rejected quiet
# audio and still let the art sheet through.
_FF_DENSITY_MAX = 0.10           # 5x the worst real reading, 7x under the false one


def _chain(data, start, limit):
    """Frames in the consecutive-valid-frame chain from `start`, and its end.
    Requires a stable version/layer/sample_rate (VBR bitrate is fine)."""
    pos, frames, base = start, 0, None
    while pos + 4 <= limit:
        hdr = decode_frame_header(data[pos:pos + 4])
        if hdr is None:
            break
        flen = hdr["frame_length"]
        if not flen or flen < 4:                          # free-format / zero: stop
            break
        key = (hdr["version_id"], hdr["layer"], hdr["sample_rate"])
        if base is None:
            base = key
        elif key != base:                                 # a different codec config
            break
        frames += 1
        pos += flen
    return frames, pos


def _sync_saturated(data, start, end):
    """Is this claimed stream mostly the sync byte, and therefore not one?

    Counted over the whole claimed extent rather than a prefix, because the art
    sheet that motivated this alternates dense and sparse runs and a prefix
    lands on either depending on where the chain happens to begin.
    """
    span = end - start
    if span <= 0:
        return False
    return (data.count(0xFF, start, end) / span) > _FF_DENSITY_MAX


def _candidate_offsets(data, n):
    """Offsets whose first two header bytes survive the prefilter, or None if
    numpy is unavailable and the caller should scan byte by byte.

    A run of 0xFF is a plausible thing to meet in a disk image, and it makes
    every byte a sync candidate: the byte-at-a-time loop then costs ~16 million
    interpreted iterations regardless of how cheap each rejection is. Evaluating
    the same (exhaustively verified) predicate over the whole buffer at once
    turns that into an array pass, and the loop below only visits survivors.
    """
    try:
        import numpy as np
    except Exception:
        return None
    if n < 4:
        return None
    b = np.frombuffer(data, dtype=np.uint8, count=n)
    b0, b1, b2 = b[:-3], b[1:-2], b[2:-1]
    ok = (b0 == 0xFF)
    ok &= (b1 & 0xE0) == 0xE0                      # 11-bit sync
    ok &= ((b1 >> 3) & 3) != 1                     # reserved version
    ok &= ((b1 >> 1) & 3) != 0                     # reserved layer
    br = (b2 >> 4) & 0x0F
    ok &= (br != 0) & (br != 15)                   # free format / invalid
    ok &= ((b2 >> 2) & 3) != 3                     # reserved sample rate
    return np.flatnonzero(ok)


def _next_candidate(data, candidates, i, n):
    """First offset >= i that could start a frame, or -1."""
    if candidates is None:                         # no numpy: scan for the sync
        return data.find(b"\xff", i, n)
    import numpy as np
    k = int(np.searchsorted(candidates, i, side="left"))
    return int(candidates[k]) if k < candidates.size else -1


def find_mpeg_streams(data, min_frames=_MIN_FRAMES):
    """Find headerless MPEG-audio streams by frame-sync cadence. Returns records
    (kind='stream', format='mp3') shaped like the other locate engines."""
    n = min(len(data), _READ_CAP)
    candidates = _candidate_offsets(data, n)
    out, i = [], 0
    while i < n - 4 and len(out) < _MAX_STREAMS:
        j = _next_candidate(data, candidates, i, n)
        if j < 0 or j + 3 >= n:
            break
        # Reject the candidate on bits alone before paying for a slice and a
        # full header decode. These are exactly the rejections
        # decode_frame_header makes from bytes 1-2 (verified exhaustively over
        # all 65,536 combinations in test_framescan_prefilter), so nothing that
        # would have decoded is dropped. Without it, a run of 0xFF -- which is
        # a plausible thing to find in a disk image -- costs one decode per
        # byte and drags the scan to 0.8 MB/s.
        b1, b2 = data[j + 1], data[j + 2]
        if ((b1 & 0xE0) != 0xE0                           # not an 11-bit sync
                or (b1 >> 3) & 3 == 1                     # reserved version
                or (b1 >> 1) & 3 == 0                     # reserved layer
                or (b2 >> 4) & 0xF in (0, 15)             # free format / invalid
                or (b2 >> 2) & 3 == 3):                   # reserved sample rate
            i = j + 1
            continue
        frames, end = _chain(data, j, n)
        if frames >= min_frames and _sync_saturated(data, j, end):
            # A field of 0xFF chains cleanly and means nothing. Resume past it
            # rather than at j+1: every offset inside it fails the same way,
            # and stepping a byte at a time re-derives that for each one.
            i = end
            continue
        if frames >= min_frames:
            hdr = decode_frame_header(data[j:j + 4])
            out.append({
                "kind": "stream", "format": "mp3",
                "offset": j, "end": end, "length": end - j,
                "confidence": round(min(0.60 + frames * 0.01, 0.99), 2),
                "inspectable": False, "evidence": None, "frames": frames,
                "stream_info": {"mpeg": hdr["version"], "layer": hdr["layer"],
                                "sample_rate": hdr["sample_rate"]},
            })
            i = end                                       # resume past the stream
        else:
            i = j + 1
    return out
