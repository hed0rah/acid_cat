"""Which bytes in this file does nothing account for?

Not "find the hidden data". A detector phrased as discovery can return nothing
and look like a clean bill of health, and the difference between "there is no
cavity here" and "I did not look properly" is invisible from the outside. This
is phrased as ACCOUNTING instead: every byte belongs to some structure or it
does not, the ones that do not are named, and the fraction accounted for is
reported whether or not anything was found. A coverage number cannot quietly
be zero.

One shape is reported: bytes no chunk's extent covers. Trailing data after a
container ends, a gap between siblings, the tail of a truncated file.

WHAT THIS DELIBERATELY DOES NOT DO. A spec-ignorable region CARRYING something
-- a JUNK chunk with a payload in it -- is already `anomalies` rule 5, with a
1 KB floor calibrated on 2,328 real WAVs where innocent non-zero JUNK topped
out at 641 bytes. Reporting it here as well would be a second rule for one
idea, with an uncalibrated threshold, and the two would disagree the first time
either moved. Every byte is accounted for in that case anyway: the chunk is
well-formed and the coverage is 1.0, which is exactly why it needs a rule that
looks at content rather than at geometry.

The accounting is format-agnostic because `geometry.normalize` already answers
"which bytes does this chunk occupy?" for every format acidcat walks -- the
extent is defined there as the bytes no sibling may claim. Anything outside the
union of extents is unaccounted, whether the container is RIFF, AIFF, FLAC or
MP4.

    from acidcat.core.forensics import cavity
    report = cavity.account(path, label, chunks)
    report["coverage"]      # 0.0 .. 1.0
    report["regions"]       # what the structure does not explain
"""

import os

from acidcat.core.infra import geometry

# Padding is a uniform run of a byte a tool reaches for when it needs bytes it
# does not care about. Uniformity alone is NOT the test: 4,096 repetitions of
# "S" appended to a WAV is perfectly uniform and is perfectly obviously not
# padding, and an earlier version of this filtered exactly that away. The value
# has to be one nobody chose for its content.
_PAD_DISTINCT = 1
_PAD_BYTES = frozenset((0x00, 0x20, 0xFF))
# Regions below this are structural noise: an odd-length chunk's pad byte, a
# two-byte alignment gap. Reporting them buries the finding that matters.
_MIN_REGION = 4


def _merge(spans):
    """Union of (start, end) spans, sorted and coalesced."""
    out = []
    for lo, hi in sorted(spans):
        if hi <= lo:
            continue
        if out and lo <= out[-1][1]:
            out[-1][1] = max(out[-1][1], hi)
        else:
            out.append([lo, hi])
    return [(a, b) for a, b in out]


def _gaps(spans, size):
    """The complement of `spans` within 0..size."""
    out = []
    at = 0
    for lo, hi in spans:
        if lo > at:
            out.append((at, lo))
        at = max(at, hi)
    if at < size:
        out.append((at, size))
    return out


def _looks_like_padding(blob):
    """Is this what a tool writes when it needs bytes it does not care about?"""
    seen = set(blob)
    return len(seen) <= _PAD_DISTINCT and seen <= _PAD_BYTES


def _preview(blob, n=16):
    return blob[:n].hex()


def _printable_run(blob):
    """The longest printable run, which is what makes a cavity legible."""
    best = cur = 0
    for b in blob:
        cur = cur + 1 if 0x20 <= b <= 0x7E else 0
        best = max(best, cur)
    return best


def account(path, label, chunks, size=None, read=True):
    """Account for every byte of `path` against the structure `chunks` describes.

    Returns a dict with `size`, `accounted`, `coverage` (0..1) and `regions`.
    `coverage` is reported even when `regions` is empty -- a file the walker
    barely understood and a file with nothing hidden in it are different
    answers, and only the number distinguishes them.
    """
    if size is None:
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
    chunks = list(chunks or [])
    try:
        chunks = geometry.normalize(chunks, size)
    except Exception:
        pass                       # raw walker output is still readable below

    spans = []
    for c in chunks:
        # A chunk whose geometry nobody stated does not get to claim bytes.
        # Trailing data appended after a container reparses as a chunk with a
        # nonsense size -- 1.4 GB inside a 44 KB file, in the case that found
        # this -- and clamping that to the file end let it swallow exactly the
        # bytes it was appended as. The `geometry` field exists to say whether
        # anyone actually declared this; believing an extent marked invalid is
        # how an accounting reports full coverage of a file it did not read.
        if not geometry.is_trustworthy(c):
            continue
        off, n = geometry.extent_of(c)
        if isinstance(off, int) and isinstance(n, int) and n > 0:
            spans.append((max(0, off), min(size, off + n)))
    covered = _merge(spans)
    # The bytes before the first chunk are the container's own header -- RIFF's
    # twelve, FORM's, the magic and whatever the format puts beside it. No
    # chunk claims them because no chunk contains them, and reporting them as
    # unaccounted would flag every well-formed file in existence.
    if covered and covered[0][0] > 0:
        covered = _merge(spans + [(0, covered[0][0])])
    accounted = sum(hi - lo for lo, hi in covered)

    regions = []
    for lo, hi in _gaps(covered, size):
        if hi - lo < _MIN_REGION:
            continue
        regions.append({"offset": lo, "length": hi - lo, "kind": "unaccounted",
                        "why": "no chunk's extent covers these bytes"})

    if read and regions:
        _fill(path, regions)
        regions = [r for r in regions if not r.get("padding")]

    regions.sort(key=lambda r: r["offset"])
    return {"size": size, "accounted": accounted, "regions": regions,
            "coverage": (accounted / float(size)) if size else 0.0}


def _fill(path, regions):
    """Read each region once and describe what is in it."""
    try:
        fh = open(path, "rb")
    except OSError:
        return
    with fh:
        for r in regions:
            try:
                fh.seek(r["offset"])
                blob = fh.read(min(r["length"], 4096))
            except OSError:
                continue
            if not blob:
                continue
            r["padding"] = _looks_like_padding(blob)
            r["preview"] = _preview(blob)
            run = _printable_run(blob)
            if run >= 4:
                r["printable_run"] = run
