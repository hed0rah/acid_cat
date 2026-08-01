"""Recover chunk structure from a container whose headers are damaged.

A walker reads a chunk's declared size and jumps to the next chunk. One corrupt
size field therefore costs every chunk after it, even when those chunks are
sitting on disk perfectly readable -- and a damaged magic costs the whole file,
because dispatch never starts. Measured on a WAV with a single corrupt ``fmt``
size: the normal walk returns 1 of 3 chunks. With the magic zeroed it returns
nothing at all.

This module recovers what is still there, using the pattern every robust chunked
parser converges on (MP4 atom recovery, PNG chunk repair, ``zip -FF``, EBML
recovery): **scan forward for the next syntactically-plausible record, then
validate it by parsing forward from there.** Nothing here is format-specific
enough to need per-walker work -- it operates on the [id][u32 size] shape that
the whole IFF/RIFF family shares.

What it is not: a decoder. A recovered record is a *hypothesis* about where a
chunk starts, corroborated by evidence, and is labelled as such. Garfinkel's
term for the cheap check that makes this affordable is "fast object validation"
-- reject a wrong reassembly on structure, without decoding it.
"""

import struct

# ids worth trusting on sight. A record whose id is in this set is far less
# likely to be a coincidence than an arbitrary four printable bytes, which is
# what keeps the false-positive rate down on high-entropy data (a 4-byte pattern
# recurs by chance roughly every 2^32 bytes, but *printable* 4-byte runs are far
# more common than that in real payloads).
KNOWN_IDS = frozenset((
    # RIFF / WAVE
    b"RIFF", b"WAVE", b"fmt ", b"data", b"fact", b"cue ", b"LIST", b"INFO",
    b"smpl", b"inst", b"bext", b"acid", b"iXML", b"_PMX", b"ID3 ", b"id3 ",
    b"ds64", b"JUNK", b"PAD ", b"minf", b"elm1", b"cart", b"logic", b"afsp",
    # IFF / AIFF and Amiga kin
    b"FORM", b"AIFF", b"AIFC", b"COMM", b"SSND", b"MARK", b"INST", b"APPL",
    b"8SVX", b"VHDR", b"BODY", b"NAME", b"ANNO", b"AUTH", b"CHAN", b"SMUS",
    # SoundFont
    b"sfbk", b"sdta", b"pdta", b"shdr", b"smpl", b"phdr", b"inst", b"igen",
    # Standard MIDI
    b"MThd", b"MTrk",
))

# an id must at least look like one: four printable, non-space-padded-weirdly
_MIN_ID, _MAX_ID = 0x20, 0x7E


def _plausible_id(b):
    return len(b) == 4 and all(_MIN_ID <= c <= _MAX_ID for c in b)


def _read_size(data, at, endian):
    try:
        return struct.unpack_from(endian + "I", data, at)[0]
    except struct.error:
        return None


def scan(data, *, endian="<", known_only=False, max_records=4096,
         min_size=0, require_corroboration=True):
    """Find plausible ``[id][u32 size]`` records anywhere in ``data``.

    Returns a list of dicts: ``offset``, ``id``, ``size``, ``end``, ``known``
    (the id is in KNOWN_IDS), ``corroborated`` (the record's declared end lands
    on another plausible record, or exactly at EOF), and ``confidence``.

    ``endian`` is "<" for RIFF-family little-endian sizes, ">" for IFF/AIFF and
    MIDI. ``known_only`` restricts to KNOWN_IDS, which is the low-false-positive
    mode worth using on high-entropy data. Corroboration is the lookahead
    validation: a record whose end is another record is very unlikely to be
    coincidence, and is what separates a real chunk from four printable bytes
    that happen to be followed by a plausible integer.
    """
    n = len(data)
    if known_only:
        # search for the ids themselves rather than testing every offset. str.find
        # is memchr-class C, so this is ~150x a per-byte Python loop on a large
        # blob -- 32 MB went from ~11 s to well under a second -- and it is the
        # same technique the container signature sweep already uses.
        offsets = []
        for cid in KNOWN_IDS:
            at = data.find(cid)
            while at != -1 and len(offsets) < max_records * 4:
                offsets.append(at)
                at = data.find(cid, at + 1)
        candidates = sorted(set(offsets))
    else:
        candidates = range(max(0, n - 8))

    out = []
    for i in candidates:
        if len(out) >= max_records:
            break
        cid = bytes(data[i:i + 4])
        if not ((cid in KNOWN_IDS) or (not known_only and _plausible_id(cid))):
            continue
        size = _read_size(data, i + 4, endian)
        if size is None or size < min_size:
            continue
        end = i + 8 + size
        if end > n:
            continue
        nxt = bytes(data[end:end + 4])
        corroborated = (end == n) or _plausible_id(nxt)
        if not (corroborated or not require_corroboration):
            continue
        known = cid in KNOWN_IDS
        out.append({
            "offset": i, "id": cid.decode("latin-1"),
            "size": size, "end": end,
            "known": known, "corroborated": corroborated,
            "next_known": nxt in KNOWN_IDS,
            "confidence": _confidence(known, corroborated, nxt in KNOWN_IDS),
        })
    return out


def _confidence(known, corroborated, next_known):
    """How much to trust one recovered record.

    Evidence, in order of weight: the id is one we know; its declared end lands
    on another record we know; it lands on something at least id-shaped. None of
    these is proof -- they are the cheap structural checks that make scanning
    affordable, so the ceiling is deliberately below 1.0.
    """
    c = 0.35
    if known:
        c += 0.30
    if corroborated:
        c += 0.15
    if next_known:
        c += 0.15
    return round(min(c, 0.95), 2)


def chain_from(records):
    """Longest run of records that link end-to-start -- a surviving chunk grid.

    A real container is a chain: each chunk ends where the next begins. Scanning
    finds records; this finds the *sequence*, which is much stronger evidence
    that they are chunks of one file rather than scattered coincidences.
    """
    if not records:
        return []
    by_offset = {r["offset"]: r for r in records}
    best = []
    for r in records:
        run, cur = [r], r
        while True:
            nxt = by_offset.get(cur["end"])
            if nxt is None:
                break
            run.append(nxt)
            cur = nxt
        if len(run) > len(best):
            best = run
    return best


def recover(data, *, endian=None, known_only=False):
    """Best-effort structure for a damaged container.

    Tries both endiannesses when not told which, and prefers the interpretation
    that yields the longest linked chain. Returns
    ``{"records", "chain", "endian", "coverage"}`` where coverage is the share
    of the file the chain accounts for -- a chain covering most of the file is a
    strong sign the recovery is real rather than a pattern in noise.
    """
    tries = (endian,) if endian else ("<", ">")
    best = None
    for e in tries:
        recs = scan(data, endian=e, known_only=known_only)
        chain = chain_from(recs)
        covered = sum(r["size"] + 8 for r in chain)
        cand = {"records": recs, "chain": chain, "endian": e,
                "coverage": round(covered / len(data), 3) if data else 0.0}
        if best is None or (len(chain), cand["coverage"]) > (len(best["chain"]),
                                                             best["coverage"]):
            best = cand
    return best
