"""Forensic recovery orchestration for `locate` -- the two engines, phases 2 and 3.

Two independent engines find audio:

  * SIGNATURE sweep -- scan forward for validated container magics (RIFF/WAVE,
    FORM/AIFF/8SVX, fLaC, OggS...). Finds real files even when their PCM is
    16-bit or compressed and the statistical detector can't see it.
  * STATISTICAL pass -- core/forensics/audioscan locates signatureless raw PCM by
    its structure, which a signature scan cannot find.

A statistical hit that falls inside a container's real extent is part of that
file, not a separate find. A hit that doesn't is a headerless blob -- but before
calling it headerless we back up *a little* (64 KiB) for a container header whose
size field was corrupt so the sweep's extent missed it. That is the governing
rule: **a missing header never discards a hit**, it only downgrades a
"container" recovery to a "blob" recovery.

The forensics level decides what survives:

    strict      only validated containers.
    normal      containers, plus high-confidence headerless blobs.
    aggressive  every candidate -- best-effort carving of raw/unknown/corrupt.

Each record carries offset/end/length + kind + confidence + evidence, shaped to
feed `carve` (whose output feeds `convert` or `inspect`). Nothing here writes
bytes or walks a container -- that is the next stage in the pipe.
"""

import struct

from acidcat.core.forensics import audioscan
from acidcat.core.forensics import framescan
# container magics whose payload can be audio, and the ids we accept -- both from
# the one audio-container table in core/sniff (shared with carve). Each hit is
# still confirmed with sniff_bytes, so a stray "RIFF" in noise is rejected.
from acidcat.core.infra.sniff import (sniff_bytes,
                                AUDIO_CONTAINER_FMTS as _AUDIO_CONTAINER_FMTS,
                                AUDIO_CONTAINER_MAGICS as _CONTAINER_MAGICS)

MODES = ("strict", "normal", "aggressive")

_HEADER_BACKTRACK = 64 * 1024     # "back up a little" for a corrupt-extent header
_CONTAINER_CONF = 0.9             # a validated container is a strong recovery

# A statistical blob can never outrank a signature-validated container. It used
# to: a verified WAV came back at 0.900 and a headerless guess at 1.000, so the
# inference scored above the proof and `--min-confidence 0.90` could not
# separate them -- it filtered out containers and kept guesses. On a compressed
# proprietary container (byte entropy 7.8, which this tool's own `probe entropy`
# calls "encrypted or compressed") that meant four megabytes of noise reported
# as raw PCM at confidence 1.00, with no threshold able to reject it.
#
# Certainty about a headerless region is not available from autocorrelation
# alone, so the scale now says so: blobs occupy [0, _BLOB_CONF_MAX] and only a
# checked magic number reaches _CONTAINER_CONF.
_BLOB_CONF_MAX = 0.89

# What `normal` mode requires of a headerless blob. Expressed on the rescaled
# scale so the gate is exactly where it was before the rescale (0.45 raw), and
# not accidentally tightened by it -- this threshold decides what `locate`
# finds, and moving it would be a detection change wearing a reporting change's
# clothes.
_NORMAL_BLOB_MIN = round(0.45 * _BLOB_CONF_MAX, 3)      # 0.4
_COALESCE_GAP = 32 * 1024        # merge headerless blob fragments within this gap
                                 # (a quiet passage inside a file is still one file)


_RIFF_MIN = 12                    # a RIFF/FORM smaller than its own header is corrupt


def _confirm_container(data, off, fmt):
    """Structural confirmation beyond the leading magic. RIFF/FORM are already
    validated by sniff_bytes (WAVE / form-type at +8). fLaC and OggS are only a
    4-byte magic there, weak enough to fire on chance or on the literal bytes in
    text/code, so confirm their first structure byte."""
    n = len(data)
    if fmt == "flac":
        # first block after 'fLaC' is STREAMINFO: type 0, always 34 bytes long
        if off + 8 > n or (data[off + 4] & 0x7F) != 0:
            return False
        block_len = struct.unpack_from(">I", b"\x00" + data[off + 5:off + 8], 0)[0]
        return block_len == 34
    if fmt == "ogg":
        # OggS page: stream-structure version 0 at +4, header_type uses 3 bits
        return off + 6 <= n and data[off + 4] == 0 and (data[off + 5] & 0xF8) == 0
    if fmt == "mp3":
        # ID3v2-anchored only (bare frame-sync is too noisy to sweep on): a real
        # version byte and a synchsafe (7-bit) size are hard to hit by chance
        if off + 10 > n or data[off:off + 3] != b"ID3":
            return False
        return (data[off + 3] in (2, 3, 4) and data[off + 4] != 0xFF
                and all(data[off + 6 + k] < 0x80 for k in range(4)))
    return True                                               # riff/form: sniff did it


def _midi_extent(data, off):
    """End of a Standard MIDI File at `off`, or None if it does not add up.

    An SMF has no total-size field, but it is exactly walkable: a 14-byte MThd
    (whose own length field is 6), then `ntrks` MTrk chunks each carrying a
    big-endian payload length. Without this a .mid embedded in a disc image
    carved to EOF -- 34 real bytes plus 4,096 of surrounding junk.
    """
    n = len(data)
    if off + 14 > n or bytes(data[off:off + 4]) != b"MThd":
        return None
    hdr_len = struct.unpack_from(">I", data, off + 4)[0]
    if hdr_len != 6:
        return None                                    # not an SMF we can trust
    ntrks = struct.unpack_from(">H", data, off + 10)[0]
    if not 1 <= ntrks <= 65535:
        return None
    pos = off + 14
    for _ in range(ntrks):
        if pos + 8 > n or bytes(data[pos:pos + 4]) != b"MTrk":
            return None                                # truncated or lying header
        tlen = struct.unpack_from(">I", data, pos + 4)[0]
        pos += 8 + tlen
        if pos > n:
            return None                                # runs past EOF
    return pos


def _container_extent(data, off, fmt):
    """End offset of a container at `off`, or None when it can't be trusted.
    RIFF/FORM carry a declared size; a size that is zero, sub-header, or runs
    past EOF is treated as CORRUPT (None) so recovery falls back to a provisional
    extent rather than a stub. Streaming formats (flac/ogg) also return None."""
    n = len(data)
    size = None
    if fmt in ("wav", "rf64", "sf2") and off + 8 <= n:
        size = struct.unpack_from("<I", data, off + 4)[0]     # RIFF: little-endian
    elif fmt in ("aiff", "aifc", "8svx") and off + 8 <= n:
        size = struct.unpack_from(">I", data, off + 4)[0]     # IFF/FORM: big-endian
    elif fmt == "midi":
        return _midi_extent(data, off)
    if size is None:
        return None                                           # flac/ogg: streaming
    end = off + 8 + size
    if size < _RIFF_MIN or end > n:
        return None                                           # corrupt declared size
    return end


# declared-size formats: an absent extent means the size field itself is corrupt
_DECLARED_SIZE_FMTS = {"wav", "rf64", "sf2", "aiff", "aifc", "8svx", "midi"}
_HEADER_SLACK = 4096              # audio may start this far past a container header
_AUDIO_GAP_TOL = 4096            # bridge small non-audio gaps between audio sub-regions


def _ogg_page(data, off):
    """(page_length, header_type, serial) for the Ogg page at `off`, or None.

    Lengths come from the segment table, which is the only way to find the next
    page without searching for the next magic -- and searching is what made
    every page look like its own file.
    """
    if off + 27 > len(data) or bytes(data[off:off + 4]) != b"OggS":
        return None
    nsegs = data[off + 26]
    if off + 27 + nsegs > len(data):
        return None
    body = sum(data[off + 27:off + 27 + nsegs])
    if off + 27 + nsegs + body > len(data):
        return None
    return (27 + nsegs + body, data[off + 5],
            int.from_bytes(bytes(data[off + 14:off + 18]), "little"))


def _ogg_streams(data, first):
    """[(start, end, serials, complete)] for the physical streams from `first`.

    Ogg stamps `OggS` on EVERY page, so treating each hit as a container made a
    single song report as hundreds of regions, and `carve --batch` wrote
    hundreds of unplayable fragments. The file itself says where a stream ends:
    header_type bit 2 (0x04) is end-of-stream, and the serial at +14 says which
    logical stream a page belongs to.

    Grouping is by PHYSICAL stream, not by serial, because a multiplexed file
    (video plus audio) interleaves several serials over the same bytes -- their
    ranges overlap, so carving one serial out by byte range is not a thing you
    can do. A physical stream opens at a BOS page and closes when every serial
    it opened has seen its EOS. Chained files (this is what a concatenation of
    songs is, and it is spec-legal) then fall out as consecutive groups.

    `complete` is False when the pages ran out before EOS -- a truncated file,
    or a scan segment that ended mid-stream. The caller needs to know, because
    an end it inferred is weaker evidence than an end the format declared.
    """
    out = []
    pos, n = first, len(data)
    start = None
    opened, seen = set(), set()
    while pos < n:
        page = _ogg_page(data, pos)
        if page is None:
            # Not a page here. Streams are not necessarily butted together --
            # in one real archive the songs sit 1,116 bytes apart -- so
            # stopping at the first gap found the first stream and missed every
            # one after it. Close whatever is open and resync to the next magic.
            if start is not None:
                out.append((start, pos, tuple(sorted(seen)), False))
                start = None
            nxt = data.find(b"OggS", pos + 1)
            if nxt < 0:
                break
            pos = nxt
            continue
        length, htype, serial = page
        if start is None:
            start, opened, seen = pos, set(), set()
        if htype & 0x02:                       # BOS: a logical stream begins
            opened.add(serial)
        seen.add(serial)
        pos += length
        if htype & 0x04:                       # EOS
            opened.discard(serial)
            if not opened:                     # every stream in the group closed
                out.append((start, pos, tuple(sorted(seen)), True))
                start = None
    if start is not None:
        out.append((start, pos, tuple(sorted(seen)), False))
    return out


def signature_sweep(data):
    """Find every validated audio container by magic (the signature engine).
    Returns container records sorted by offset, each with an ``extent`` (a
    trustworthy declared end, or None when the size is streaming/corrupt and must
    be resolved from the audio itself)."""
    hits = {}
    ogg_spans = None
    for magic in _CONTAINER_MAGICS:
        idx = data.find(magic)
        while idx != -1:
            fmt = sniff_bytes(bytes(data[idx:idx + 20]))
            if fmt in _AUDIO_CONTAINER_FMTS and idx not in hits \
                    and _confirm_container(data, idx, fmt):
                if fmt == "ogg":
                    # Page-per-region is the wrong unit: walk the segment tables
                    # once and keep only the hits that open a physical stream.
                    if ogg_spans is None:
                        ogg_spans = {s: (e, ser, done)
                                     for s, e, ser, done in _ogg_streams(data, idx)}
                    span = ogg_spans.get(idx)
                    if span is not None:
                        end, serials, complete = span
                        hits[idx] = {
                            "kind": "container", "format": fmt, "offset": idx,
                            "extent": end,
                            "confidence": _CONTAINER_CONF, "inspectable": True,
                            "evidence": None, "stream_serials": serials,
                            # an inferred end is weaker than a declared one, and
                            # the caller has to be able to tell them apart
                            "streaming_extent": not complete,
                        }
                else:
                    hits[idx] = {
                        "kind": "container", "format": fmt, "offset": idx,
                        "extent": _container_extent(data, idx, fmt),
                        "confidence": _CONTAINER_CONF, "inspectable": True,
                        "evidence": None,
                    }
            idx = data.find(magic, idx + 1)
    return [hits[o] for o in sorted(hits)]


def _audio_chain_end(offset, upper, regions):
    """End of the contiguous audio run that begins just after a container header,
    or None if no audio starts near the header (a 16-bit or compressed payload
    the statistical pass can't see). Bounds a corrupt extent to the real audio."""
    chain_end = None
    for r in regions:
        s, e = r["start"], r["end"]
        if s >= upper:
            break
        if chain_end is None:
            if offset <= s <= offset + _HEADER_SLACK:
                chain_end = min(e, upper)
        elif s - chain_end <= _AUDIO_GAP_TOL:
            chain_end = min(e, upper)
        elif s > chain_end:
            break
    return chain_end


def _next_region_start(lo, upper, regions):
    """Start of the first audio region in (lo, upper), else None."""
    for r in regions:
        if lo < r["start"] < upper:
            return r["start"]
    return None


def _resolve_container_ends(data, containers, regions):
    """Fill each container's ``end``. A trusted declared extent is used as-is. A
    provisional extent is bounded by the audio that follows the header; failing
    that (undetectable payload) it is capped just before the next distinct audio
    region so a following blob survives, else at the next container / EOF."""
    offsets = [c["offset"] for c in containers]
    n = len(data)
    for i, c in enumerate(containers):
        extent = c.pop("extent")
        upper = offsets[i + 1] if i + 1 < len(offsets) else n
        if extent is not None:
            c["end"] = extent
            # setdefault, not assignment: a sweep that already knows how solid
            # its end is (Ogg resolves one from the stream's own EOS page, and
            # says so when the pages ran out first) must not have that finding
            # overwritten here.
            c.setdefault("streaming_extent", False)
            continue
        chain = _audio_chain_end(c["offset"], upper, regions)
        if chain is not None:
            c["end"] = chain
        else:
            nxt = _next_region_start(c["offset"] + _HEADER_SLACK, upper, regions)
            c["end"] = nxt if nxt is not None else upper
        c["streaming_extent"] = True
        if c["format"] in _DECLARED_SIZE_FMTS:
            c["corrupt_extent"] = True                 # declared size was unusable


def backtrack_header(data, start, bound=_HEADER_BACKTRACK):
    """Scan backward a bounded distance from a region start for the nearest
    validated container header (the corrupt-extent rescue). Returns
    {found, format, container_start, distance} or {found: False}."""
    lo = max(0, start - bound)
    best_off, best_fmt = -1, None
    for magic in _CONTAINER_MAGICS:
        off = data.rfind(magic, lo, start + 4)
        if off > best_off:
            fmt = sniff_bytes(bytes(data[off:off + 20]))
            if fmt in _AUDIO_CONTAINER_FMTS and _confirm_container(data, off, fmt):
                best_off, best_fmt = off, fmt
    if best_off < 0:
        return {"found": False}
    return {"found": True, "format": best_fmt, "container_start": best_off,
            "distance": start - best_off}


def _within(offset, extents):
    """True if `offset` sits inside any [start, end) container extent."""
    for start, end in extents:
        if start <= offset < end:
            return True
    return False


# a statistical hit is the same file as a container it mostly covers, even when
# it starts a little before it. The detector works in windows, so a blob
# routinely opens a few hundred bytes ahead of the header it belongs to -- an
# offset-only test then reports the file twice, once as a container and once as
# a redundant raw blob. Measured on a disk image of six real WAVs: 8 regions for
# 6 files, both extras being blobs that overlapped a container they started
# just before.
_ABSORB_OVERLAP = 0.5


def _mostly_within(start, end, extents, frac=_ABSORB_OVERLAP):
    """True when at least `frac` of [start, end) is covered by one extent."""
    span = end - start
    if span <= 0:
        return _within(start, extents)
    for ext_start, ext_end in extents:
        covered = min(end, ext_end) - max(start, ext_start)
        if covered > 0 and covered / span >= frac:
            return True
    return False


def _survives(rec, mode):
    if rec["kind"] == "stream":                          # structural, like a container
        return True
    if mode == "aggressive":
        return True
    if mode == "strict":
        return rec["kind"] == "container"
    return rec["kind"] == "container" or rec["confidence"] >= _NORMAL_BLOB_MIN


def locate(data, *, mode="normal", scan_kwargs=None):
    """Locate, anchor, and classify audio regions at a forensics level.
    Returns records (offset/end/length + kind + confidence + evidence) sorted by
    offset, each ready to hand to `carve`. Never raises on content."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")

    containers = signature_sweep(data)
    # third engine: headerless compressed streams by frame-sync cadence (fast +
    # structural, so it runs in every mode, like the signature sweep).
    streams = framescan.find_mpeg_streams(data)
    # strict = validated structure only, so skip the (slow) statistical pass
    # entirely -- a signature-only run is fast even on a multi-hundred-MB image.
    regions = [] if mode == "strict" else audioscan.scan(data, **(scan_kwargs or {}))
    _resolve_container_ends(data, containers, regions)
    extents = [(c["offset"], c["end"]) for c in containers if c["end"] > c["offset"]]
    # a stream inside a container we already found is that file's payload, not new
    extents += [(s["offset"], s["end"]) for s in streams]
    records = list(containers) + [s for s in streams
                                  if not _within(s["offset"], [(c["offset"], c["end"])
                                                               for c in containers
                                                               if c["end"] > c["offset"]])]

    for region in regions:
        if _mostly_within(region["start"], region["end"], extents):
            continue                                  # part of a container/stream we found
        records.append({
            "kind": "blob", "format": None, "offset": region["start"],
            "end": region["end"],
            # rescaled, not clipped: clipping would flatten every strong blob
            # onto one value and destroy the ranking that makes `sort` useful
            "confidence": round(region["confidence"] * _BLOB_CONF_MAX, 3),
            "inspectable": False, "evidence": region["evidence"],
        })

    records = [r for r in records if _survives(r, mode)]
    records.sort(key=lambda r: r["offset"])
    records = _coalesce_blobs(records)
    for r in records:
        r["length"] = r["end"] - r["offset"]
    return records


def _coalesce_blobs(records):
    """Merge adjacent headerless-blob records within _COALESCE_GAP. Dynamic audio
    (a music dump with quiet passages) fragments into many below-gate windows;
    headerless recovery is inherently coarse, so nearby blob fragments collapse
    to one region. Containers are never merged, and a container between two blobs
    keeps them separate."""
    out = []
    for r in records:
        if r["kind"] == "blob" and out and out[-1]["kind"] == "blob" \
                and r["offset"] - out[-1]["end"] <= _COALESCE_GAP:
            prev = out[-1]
            prev["end"] = max(prev["end"], r["end"])
            prev["confidence"] = max(prev["confidence"], r["confidence"])
            continue
        out.append(r)
    return out


# A partial Ogg page left dangling at a segment edge. Measured on a 187 MB
# archive scanned in 16 MB segments: the eleven straddling pairs left gaps of
# 4,172 to 4,557 bytes, so this is roughly an order of magnitude of headroom
# over the largest observed and still far below any real inter-file gap.
_SEG_EDGE_SLACK = 64 * 1024


def stitch_segments(regions, seg_size, slack=_SEG_EDGE_SLACK):
    """Rejoin regions that a SEGMENTED scan cut apart at its own boundaries.

    Scanning a large image in fixed segments means every segment is analysed
    blind to its neighbours, so a stream crossing a boundary is seen twice: as
    something that ends at the edge, and as something that starts just past it.
    Measured on a 187 MB archive of 64 songs, all eleven 16 MB boundaries split
    a song in two -- reporting 75 regions, eleven of them unplayable halves,
    with about 4 KB of audio lost in the partial page at each edge.

    A boundary WE introduced is not evidence about the file, so this is not a
    heuristic about proximity: two regions are rejoined only when the format
    itself says they are the same thing. For Ogg that is the bitstream serial,
    which every page carries and which `signature_sweep` already records. All
    eleven pairs in that archive shared one.

    Regions with no such identity are left alone. Adjacency on its own means
    nothing here -- the songs in that archive are packed back to back, so
    merging neighbours would have joined all 64 into 27 runs.
    """
    if not regions or not seg_size:
        return regions
    out = sorted(regions, key=lambda r: (r.get("offset", 0), r.get("end", 0)))
    i = 0
    while i < len(out) - 1:
        a, b = out[i], out[i + 1]
        if _joins_across_a_segment_edge(a, b, seg_size, slack):
            a["end"] = b.get("end", a.get("end"))
            a["length"] = a["end"] - a.get("offset", 0)
            sa = set(a.get("stream_serials") or ())
            sa.update(b.get("stream_serials") or ())
            if sa:
                a["stream_serials"] = sorted(sa)
            # `evidence` is present but None on a fresh record, so setdefault
            # hands back None and an isinstance guard skips in silence. The
            # note is the only thing that tells a reader why this region spans
            # a boundary, so losing it loses the explanation, not a decoration.
            ev = a.get("evidence")
            note = ("rejoined across a scan-segment boundary "
                    "(same bitstream serial)")
            a["evidence"] = (list(ev) + [note]) if isinstance(ev, (list, tuple))                 else ([ev, note] if ev else [note])
            del out[i + 1]
            continue                      # a stream may cross more than one
        i += 1
    return out


def _joins_across_a_segment_edge(a, b, seg_size, slack):
    a_end, b_off = a.get("end"), b.get("offset")
    if not isinstance(a_end, int) or not isinstance(b_off, int):
        return False
    if b_off < a_end or b_off - a_end > slack:
        return False
    # the gap has to CONTAIN a boundary; two regions that merely sit near each
    # other in the middle of a segment are two regions
    # `a_end - 1`, because a_end is EXCLUSIVE. A stream whose last complete
    # page ends exactly on the boundary has a_end == the boundary, which floor
    # division puts in the NEXT segment -- the quotients then match and the
    # pair is read as two regions that merely sit near each other. That is
    # precisely the case this function exists for, and it was the one case it
    # refused.
    if (b_off // seg_size) == ((a_end - 1) // seg_size):
        return False
    if a.get("format") != b.get("format"):
        return False
    sa = set(a.get("stream_serials") or ())
    sb = set(b.get("stream_serials") or ())
    return bool(sa and sb and sa & sb)
