"""Find where a CD ripper wrote something because it could not read something.

When a ripper hits an unreadable sector it does not leave a hole -- it writes a
replacement. Which replacement depends on the tool: silence, the last good
sample held, a straight line across the gap, the previous block again, or the
raw uncorrected bytes. What any specific ripper writes is not documented
publicly, so this was built from measurement rather than from a spec.

The find is worth more than "this file is damaged". A CD player conceals errors
in the playback path by design, usually without saying so, while a CD-ROM drive
generally does not. So concealment surviving into a file is evidence the file
came off an optical disc that could not be read cleanly -- evidence that
listening was engineered to destroy.

WHAT MAKES THIS HARD, and why the obvious version does not work: every pattern
here is ordinary in music. Sample libraries are full of digital silence, loops
produce byte-identical repeated blocks, and percussion is broadband and near
full scale. Measured against 457 real WAV files, pattern-matching alone fired on
8 to 60 percent of them depending on the pattern.

Concealment is not a pattern, it is a DISCONTINUITY: a region lasting about one
CD sector, aligned to the sector grid, whose structure differs sharply from its
immediate neighbourhood. It is a hole punched into content. With those two
additional gates the same detectors fire on 0.0 to 0.4 percent of real files.

The contrast is measured on high-frequency content rather than level, and that
distinction is load-bearing. A muted sector is quiet, but a held sample sits at
whatever amplitude the last good sample had and an interpolated ramp runs
between two loud endpoints. Neither is quiet; both are structureless, and
first-difference energy collapses for direct current and for a straight line at
any amplitude.

NOT DETECTED, deliberately: raw passthrough. A sector of uncorrected bytes is
anomalous only if the surrounding music is not already noise, and much of it is.
Measured over 588-sample windows, real audio and random data overlap by 40
percent on lag-1 autocorrelation -- the median real sector correlates at 0.166
and 16.5 percent correlate below zero. Widening the window reduces the overlap
but the event is 13 milliseconds long, so there is nowhere to widen to. Claiming
that detection would mean a false positive on roughly six files in ten.
"""

SECTOR_FRAMES = 588          # 2352 bytes of CD audio: 588 stereo 16-bit frames
_LEN_TOL = 0.35              # a run this close to one sector counts as sector-shaped
_CONTEXT = SECTOR_FRAMES * 2  # neighbourhood that must look like real audio
_CONTRAST_DB = 12.0          # how much more structured the surroundings must be
_MAX_FINDINGS = 20           # how many are listed INDIVIDUALLY, not how many exist


def _numpy():
    """numpy is an optional extra, so core/ imports it inside the functions that
    need it. A module-level import would break `pip install acidcat`, which
    ships with mutagen and nothing else -- there is an invariant test for it."""
    import numpy as np
    return np


def _hf(a):
    """RMS of the first difference: a proxy for high-frequency content.

    Zero for DC and for a constant slope, whatever the amplitude. That is the
    property separating concealment from music; level is not.
    """
    np = _numpy()
    if a.size < 2:
        return 0.0
    return float(np.sqrt(np.mean(np.diff(a.astype(np.float64), axis=0) ** 2)))


def _db(x):
    import math
    return 20 * math.log10(max(x, 1e-9))


# A run may begin one or two samples before the sector boundary, and that is
# inherent rather than sloppy: an interpolation is anchored ON the last good
# sample, so that sample is collinear with the line drawn from it and joins the
# run. Held samples do the same. Two frames out of 588 is 0.3 percent of a
# sector, far too tight to matter for false positives, and demanding exact
# alignment misses every interpolated gap.
_ALIGN_SLACK = 2


def _sector_shaped(start, length):
    off = start % SECTOR_FRAMES
    aligned = off <= _ALIGN_SLACK or off >= SECTOR_FRAMES - _ALIGN_SLACK
    return abs(length / SECTOR_FRAMES - 1.0) <= _LEN_TOL and aligned


def _discontinuous(x, start, length):
    """Structured audio either side, structureless in the middle."""
    np = _numpy()
    before = x[max(0, start - _CONTEXT):start]
    after = x[start + length:start + length + _CONTEXT]
    if before.size == 0 or after.size == 0:
        return False          # at a file edge this is a beginning, not a hole
    inside = _db(_hf(x[start:start + length]) / 32768.0)
    # BOTH sides must be structured, not either. A hole punched into content has
    # content on both sides of it; taking the max let trailing silence qualify,
    # because the music before it satisfied the test on its own.
    around = _db(min(_hf(before), _hf(after)) / 32768.0)
    return (around - inside) >= _CONTRAST_DB


def _runs(mask, minlen):
    out, start = [], None
    for i, v in enumerate(mask):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if i - start >= minlen:
                out.append((start, i - start))
            start = None
    if start is not None and len(mask) - start >= minlen:
        out.append((start, len(mask) - start))
    return out


def _gated(x, mask):
    np = _numpy()
    minlen = int(SECTOR_FRAMES * (1 - _LEN_TOL))
    return [(s, n) for s, n in _runs(mask, minlen)
            if _sector_shaped(s, n) and _discontinuous(x, s, n)]


def _mute(x):
    np = _numpy()
    return _gated(x, np.abs(x).sum(axis=1) == 0)


def _hold(x):
    np = _numpy()
    same = np.all(np.diff(x, axis=0) == 0, axis=1)
    return _gated(x, np.concatenate([[False], same]))


def _interp(x):
    np = _numpy()
    # a straight line has a constant first difference, so its second difference
    # is zero. Tolerance is PER CHANNEL: a ramp quantised to int16 jitters by
    # one LSB, and summing two channels against a tolerance of 1 is twice as
    # strict as intended and misses every interpolated gap.
    # Tolerance 2, per channel. A line quantised to int16 does not have an
    # exactly-zero second difference, and how far it strays depends on the
    # rounding: nearest-rounding stays within 1, truncation toward zero reaches
    # 2. A ripper written in C truncates, so a tolerance of 1 catches only
    # interpolators that happen to round.
    flat = np.all(np.abs(np.diff(x, n=2, axis=0)) <= 2, axis=1)
    # flat[i] is true when x[i], x[i+1] and x[i+2] are collinear, so it
    # describes the run STARTING at x[i] and the padding goes on the right.
    # Left-padding shifted every run one sample late, which put the start off
    # the sector grid and made every interpolated gap invisible.
    return _gated(x, np.concatenate([flat, [False, False]]))


def _repeat(x):
    """A block identical to its predecessor -- which a loop also produces.

    The discriminator is that a loop repeats many times and a concealed sector
    repeats exactly once, so a block only counts when the one before its
    predecessor differs.
    """
    np = _numpy()
    hits = []
    n = len(x) // SECTOR_FRAMES
    for i in range(2, n - 1):
        a = x[(i - 2) * SECTOR_FRAMES:(i - 1) * SECTOR_FRAMES]
        b = x[(i - 1) * SECTOR_FRAMES:i * SECTOR_FRAMES]
        c = x[i * SECTOR_FRAMES:(i + 1) * SECTOR_FRAMES]
        d = x[(i + 1) * SECTOR_FRAMES:(i + 2) * SECTOR_FRAMES]
        if not (a.shape == b.shape == c.shape == d.shape):
            continue
        # isolated on BOTH sides. Checking only the predecessor flagged the
        # first repetition of a loop, since that one does follow different
        # material -- which made every looped sample library a false positive.
        if (np.array_equal(c, b)
                and not np.array_equal(b, a)
                and not np.array_equal(d, c)):
            hits.append((i * SECTOR_FRAMES, SECTOR_FRAMES))
    return hits


# Ordered most specific first. The first three are NESTED and that is a
# property of the signals, not a defect: a run of zeros is constant, and a
# constant run is linear with slope zero. Silence therefore matches all three.
# Reporting the narrowest match that fires is the correct reading.
_DETECTORS = (("mute", _mute), ("hold", _hold), ("interpolate", _interp),
              ("repeat", _repeat))

_WHAT = {
    "mute": "silence written where a sector could not be read",
    "hold": "the last good sample held across an unreadable sector",
    "interpolate": "a straight line drawn across an unreadable sector",
    "repeat": "the previous block written again over an unreadable sector",
}


def scan(samples, *, sample_rate=44100):
    """Find ripper concealment in interleaved integer PCM.

    ``samples`` is an (n, channels) integer array. Returns a list of findings,
    each naming the strategy, where it starts, and how long it runs.

    Measured false positive rates against 457 real WAV files: mute 0.0 percent,
    hold 0.2, interpolate 0.4, repeat 3.9. Raw passthrough is not attempted --
    see the module docstring for why it is not detectable at this scale.
    """
    np = _numpy()
    x = np.asarray(samples)
    if x.ndim == 1:
        x = x[:, None]
    if x.shape[0] < SECTOR_FRAMES * 4:
        return []              # too short for a neighbourhood to exist

    seen_list = []

    # Detectors run most-specific first, and a later one may describe the SAME
    # hole a frame or two off -- silence is also constant, and the constant run
    # starts one sample later because the first zero is where the previous
    # sample stopped matching. Keying the dedup on an exact start position
    # reported one muted sector twice, as mute at 3528 and hold at 3529.
    # Overlap is the right test: two findings inside one sector of each other
    # are one event.
    claimed = []
    for name, fn in _DETECTORS:
        for start, length in fn(x):
            if any(start < c_end and c_start < start + length
                   for c_start, c_end in claimed):
                continue
            claimed.append((start, start + length))
            seen_list.append((start, name, length))

    # Every finding is returned. The cap belongs to whoever RENDERS this, which
    # is the only place that knows whether it is filling a terminal or a JSON
    # document, and it has to announce itself when it bites.
    #
    # This used to truncate here, and summarise() then printed len() of the
    # already-truncated list -- so a rip with 65 concealed sectors and one with
    # 20 both reported "20 concealed sector(s)", and the machine-readable output
    # carried only that sentence. A cap reported as a count is precisely the
    # defect this release exists to remove, and it was sitting in the newest
    # forensic check in the tree.
    out = []
    for start, name, length in sorted(seen_list):
        out.append({
            "strategy": name,
            "frame": int(start),
            "frames": int(length),
            "seconds": round(start / float(sample_rate), 3),
            "sector": int(start // SECTOR_FRAMES),
            "what": _WHAT[name],
        })
    return out


def summarise(findings):
    """One line, or None when nothing was found.

    Names the sector grid explicitly, because that is the part that identifies
    the ORIGIN. 588 frames is not a length that arises incidentally in a file
    that never passed through a CD, so the alignment is stronger evidence than
    any individual pattern.
    """
    if not findings:
        return None
    kinds = {}
    for f in findings:
        kinds[f["strategy"]] = kinds.get(f["strategy"], 0) + 1
    parts = ", ".join(f"{n} x {k}" for k, n in sorted(kinds.items()))
    # the count is of everything found, never of what a caller chose to list
    listed = ""
    if len(findings) > _MAX_FINDINGS:
        listed = f"; the first {_MAX_FINDINGS} are listed individually"
    return (f"{len(findings)} concealed sector(s) on the 588-frame CD grid "
            f"({parts}){listed} -- consistent with a rip from a disc that could "
            f"not be read cleanly, not with damage to this file")


def listed(findings):
    """The subset a text renderer should print one-by-one.

    Separate from the count on purpose: summarise() reports how many exist and
    this decides how many are shown, so the two can never drift into each other
    the way they did when scan() truncated its own return value.
    """
    return findings[:_MAX_FINDINGS]


def scan_float_channels(channels, *, bit_depth, sample_rate=44100):
    """Adapter for callers holding normalised float channels.

    Every test in this module is an EXACT one -- an exact-zero run, an exactly
    constant run, a second difference of exactly zero, a byte-identical block --
    because that is what makes the false positive rate low enough to be worth
    reporting. Floats normalised to +/-1 cannot answer those questions.

    16-bit sources round-trip back to their integers exactly, so those are
    reconstructed and scanned. Anything else returns None, which the caller must
    report as "not screened" rather than as "nothing found". Concealment is a
    CD phenomenon and CD audio is 16-bit; a 24-bit file did not come off a Red
    Book disc, so declining is correct rather than merely cautious.
    """
    if bit_depth != 16 or not channels:
        return None
    np = _numpy()
    x = np.stack([np.rint(np.asarray(c) * 32768.0) for c in channels], axis=1)
    return scan(np.clip(x, -32768, 32767).astype(np.int16),
                sample_rate=sample_rate)
