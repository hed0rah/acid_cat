"""Statistical audio-blob detection -- the signatureless engine behind `locate`.

Signature carving finds files by their magic (headers/footers); raw PCM has no
magic, so a signature scan walks straight past it. This module fills that gap: it
finds raw audio in an unknown blob by its *statistical structure* -- the same
smoothness (sample[n] ~= sample[n-1]) that lets DPCM/Fibonacci/BRR compress audio
is what makes raw audio detectable. Compressibility and detectability are the
same coin.

Phase 1 (this module) is the LOCATOR: a windowed pass that flags candidate audio
regions. It is tuned for *recall* -- catch the audio, tolerate some false hits --
because the precision comes downstream (back up to a container header, hand the
range to the real walker). Its output regions are exactly what `carve` consumes.

The discriminator is entropy + the *shape* of autocorrelation across lags, drawn
from measured class profiles:

    class          entropy   r1     r2     r4     r8
    random noise   ~8.0      ~0     ~0     ~0     ~0
    program code   ~4.8      +0.42  +0.29  +0.15  +0.05   (monotone decay, low H)
    8-bit voice    ~7.5      +0.53  +0.56  +0.17  -0.25   (bump at r2, oscillates)
    clean tone     ~6.8      +0.99  +0.98  +0.91  +0.67   (sustained)

Noise is flat at every lag; code decays monotonically from a modest peak; audio
either *sustains* correlation (tonal) or *oscillates* into negative autocorr
(voiced waveform). A lone lag-1 threshold can't separate low-fi 8-bit audio
(r1~0.5) from code (r1~0.4) -- the decay/oscillation shape is what does.

The features above read a window as 8-bit signed PCM, and on its own that is
blind to the format most audio actually uses. In 16-bit PCM the low byte of a
sample is close to noise, so consecutive bytes alternate noisy/smooth and the
lag-1 correlation this engine keys on is destroyed. Measured on 600 KB of real
16-bit stereo audio:

    read as bytes      r1 = +0.001   r2 = +0.051    -> score 0.000, invisible
    read as int16      r1 = +0.572   r2 = +0.998

So `scan` reads each window three ways -- as-is, and decimated by two at both
byte offsets, which recovers the high byte of a 16-bit sample as a smooth 8-bit
signal the existing features handle unchanged -- and keeps whichever scores
best. The winning reading is reported as `evidence.view` / `evidence.width`.
Cost is ~2x (the decimated passes are half length); the return is that raw
16-bit PCM is found at all, which is the common case.

Compressed audio blobs (BRR, ADPCM, MP3) are high-entropy and not smooth, so
this engine does not find them; they need structural signatures -- still later.

Pure-Python by design: `scan` is a base-install capability, not gated behind the
numpy `analysis` extra.
"""

import math
import struct
from operator import mul as _mul

from acidcat.core.primitives.signal import entropy_from_counts

# ---- tunables (first-cut, derived from the class profiles above; a labeled
# corpus pass is expected to refine these) -------------------------------------

LAGS = (1, 2, 4, 8)               # autocorrelation lags that expose the decay shape

_PEAK_FLOOR = 0.25                # low-lag autocorr below this reads as noise
_PEAK_SPAN = 0.50                 # ... and saturates confidence PEAK_SPAN above the floor
_STRUCT_SPAN = 0.30               # structure needed to clear code's monotone decay
_ENTROPY_FLOOR = 2.0              # below this the window is ~constant, not a live blob
_LIVE_FLOOR = 2.0                 # sample spread (8-bit std) at/below this is flat
_LIVE_FULL = 16.0                 # ... and carries full weight from here up
_LIVE_MIN = 0.25                  # floor on the damping, so flat regions still show

# distribution gate (calibrated on a labeled corpus: real 8SVX audio vs code/
# text/random/binary). autocorrelation already rejects random/compressed/binary
# cold (~0 correlation); the residual false positives are structured CODE and
# TEXT, which are ~99% printable bytes while real audio is ~22% (p90 0.37) --
# a clean, no-overlap separation. So a printable-fraction factor zeroes code/
# text without touching audio recall.
_PRINTABLE_LO = 0.35              # audio sits at/below this; factor is 1.0 here
_PRINTABLE_HI = 0.70             # code/text is ~1.0; factor reaches 0.0 by here
_ENTROPY_CEIL = 7.7              # above this is random/compressed -- skip the
                                 # expensive autocorrelation (it would score 0)

DEFAULT_WINDOW = 1024
DEFAULT_STEP = 512
DEFAULT_MIN_SCORE = 0.25          # recall-oriented: phases 2/3 supply precision
DEFAULT_MERGE_GAP = 4             # bridge up to this many below-gate windows (~2 KiB)
DEFAULT_READ_CAP = 256 * 1024 * 1024

# signed-8-bit lookup: byte 0..255 -> -128..127
_SIGNED = tuple(b - 256 if b > 127 else b for b in range(256))


def _clamp01(x):
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


# text/code tell: bytes in the printable ASCII band (+ tab/newline/return)
_PRINTABLE = frozenset(range(0x20, 0x7F)) | {0x09, 0x0A, 0x0D}


def _distribution(counts, n):
    """Value-distribution shape from a byte histogram.

    printable_frac -- share of bytes in the text range; a code/text signature.
    hist_tv -- total variation of the normalized histogram: audio's value
    histogram is smooth (a sampled continuous signal), code's is spiky (isolated
    peaks at common byte values), random's is flat. So high TV reads as code."""
    printable = sum(counts[b] for b in _PRINTABLE) / n
    tv = 0.0
    prev = counts[0] / n
    for c in counts[1:]:
        cur = c / n
        tv += cur - prev if cur > prev else prev - cur
        prev = cur
    return printable, tv


def _autocorr(samples, mean, den, lag):
    """Pearson autocorrelation at `lag`. Kept for callers outside the scan loop;
    the hot path uses _autocorr_lags, which shares work across the lags."""
    if den <= 0.0 or lag >= len(samples):
        return 0.0
    dev = [s - mean for s in samples]
    return sum(map(_mul, dev, dev[lag:])) / den


def _autocorr_lags(samples, mean, den, lags):
    """Every lag in one go: {lag: r}.

    The scan spends most of its time here, so the shape matters. Two things the
    naive form wastes: it recomputes (sample - mean) once per lag, and it runs
    the multiply-accumulate as a Python loop. Centring once and letting
    sum(map(mul, dev, dev[lag:])) do the accumulation in C turns four
    interpreted passes into one list build plus four C-level reductions.
    """
    if den <= 0.0:
        return {L: 0.0 for L in lags}
    return _autocorr_from_dev([s - mean for s in samples], den, lags)


def _autocorr_from_dev(dev, den, lags):
    """Every lag from deviations the caller already centred.

    The scan spends most of its time here, so the shape matters: one C-level
    multiply-accumulate per lag over a shared list, rather than an interpreted
    loop that re-subtracts the mean on every pass.
    """
    if den <= 0.0:
        return {L: 0.0 for L in lags}
    n = len(dev)
    return {lag: (0.0 if lag >= n else sum(map(_mul, dev, dev[lag:])) / den)
            for lag in lags}


_FLOAT_RANGE = 1.5               # audio floats live in [-1,1]; allow headroom
_FLOAT_MIN_FRAC = 0.85          # this share of samples must be in range to be float
_FLOAT_MIN_SCORE = 0.30


def _looks_float(win):
    """Cheap float32 pre-check: sample ~32 floats across the window; float PCM
    keeps them in the audio range, random bytes almost never do. Gates the full
    probe so the common (non-float) case stays fast."""
    n = len(win) // 4
    if n < 16:
        return False
    step = max(1, n // 32)
    hits = tot = 0
    for i in range(0, n, step):
        v = struct.unpack_from("<f", win, i * 4)[0]
        tot += 1
        if v == v and -_FLOAT_RANGE <= v <= _FLOAT_RANGE:      # finite + in range
            hits += 1
    return tot and hits / tot >= _FLOAT_MIN_FRAC


def _float_probe(win):
    """Confirm float PCM and return (score, width, endian) or None. Real float
    audio is both in-range AND smooth; random-in-range constants are not."""
    best = None
    for width, code, size in ((32, "f", 4), (64, "d", 8)):
        n = len(win) // size
        if n < 16:
            continue
        for endian in ("<", ">"):
            try:
                vals = struct.unpack_from(f"{endian}{n}{code}", win, 0)
            except struct.error:
                continue
            inrange = sum(1 for v in vals
                          if v == v and -_FLOAT_RANGE <= v <= _FLOAT_RANGE) / n
            if inrange < _FLOAT_MIN_FRAC:
                continue
            m = sum(vals) / n
            den = sum((v - m) * (v - m) for v in vals)
            if den <= 0:
                continue
            num = sum((vals[i] - m) * (vals[i + 1] - m) for i in range(n - 1))
            score = inrange * _clamp01(num / den)
            if score >= _FLOAT_MIN_SCORE and (best is None or score > best[0]):
                best = (round(score, 3), width, endian)
    return best


def window_features(win):
    """Feature vector for one window of bytes. Read as 8-bit signed PCM, unless
    it is float PCM (checked first -- float mantissa bytes look random to the
    integer path, so float audio would otherwise be missed).

    Returns a dict: entropy, autocorr {lag: r}, the derived peak/structure terms,
    and (for float regions) a `float` = (width, endian) tag."""
    n = len(win)
    counts = [0] * 256
    for b in win:
        counts[b] += 1
    entropy = entropy_from_counts(counts, n)
    printable, hist_tv = _distribution(counts, n)

    # float PCM: high byte-entropy (mantissa) hides it from the integer path
    if _looks_float(win):
        fp = _float_probe(win)
        if fp:
            score, width, endian = fp
            return {"entropy": entropy, "autocorr": {L: 0.0 for L in LAGS},
                    "peak": score, "structure": score, "printable": printable,
                    "hist_tv": hist_tv, "float": (width, endian), "n": n}
    # cheap pre-filter: a window at near-maximal entropy is random / compressed /
    # encrypted and cannot be raw audio, so skip the O(n * lags) autocorrelation
    # AND the sample decode below (it would score 0 anyway). This is the bulk of
    # a real disk image, so the early-out is most of the speed.
    if n < LAGS[-1] + 1 or entropy > _ENTROPY_CEIL:
        return {"entropy": entropy, "autocorr": {L: 0.0 for L in LAGS},
                "peak": 0.0, "structure": 0.0, "printable": printable,
                "hist_tv": hist_tv, "n": n}

    samples = [_SIGNED[b] for b in win]
    mean = sum(samples) / n
    # centre once and keep it: the variance and every lag are reductions over the
    # same deviations, so building them here and handing them down replaces an
    # interpreted accumulator loop and a second identical list build inside the
    # autocorrelation with two C-level reductions.
    dev = [s - mean for s in samples]
    den = sum(map(_mul, dev, dev))
    ac = _autocorr_from_dev(dev, den, LAGS)
    # how far the window actually moves. autocorrelation is scale-free, so a
    # near-constant run (silence, padding, a sparse hole) correlates perfectly
    # and would otherwise outscore real audio -- see `liveness` in audio_score.
    spread = (den / n) ** 0.5

    r1, r2, r4, r8 = ac[1], ac[2], ac[4], ac[8]
    peak = max(r1, r2)                                  # low-lag correlation strength
    # structure separates a waveform from code's monotone decay:
    oscillate = max(-r4, -r8, 0.0)                      # dips negative at higher lag (voiced)
    sustain = max(r4, r8, 0.0)                          # stays correlated (tonal)
    periodic = max(r2 - r1, 0.0)                        # bump at lag 2 (pitched)
    structure = max(oscillate, sustain, periodic)
    return {"entropy": entropy, "autocorr": ac, "peak": peak,
            "structure": structure, "printable": printable,
            "hist_tv": hist_tv, "spread": spread, "n": n}


def audio_score(feat):
    """Audio-likeness in [0, 1] from a feature vector. Recall-oriented: a window
    scores only when it is *correlated* (beats noise), *shaped* like a waveform
    (beats code's monotone decay), and not text/code by value distribution."""
    if feat["entropy"] < _ENTROPY_FLOOR:
        return 0.0
    if feat.get("float"):
        return feat["peak"]                             # float score is self-gated
    strength = _clamp01((feat["peak"] - _PEAK_FLOOR) / _PEAK_SPAN)
    shape = _clamp01(feat["structure"] / _STRUCT_SPAN)
    dist = _clamp01((_PRINTABLE_HI - feat.get("printable", 0.0))
                    / (_PRINTABLE_HI - _PRINTABLE_LO))
    # a window that barely moves is weak evidence either way: silence, padding
    # and a sparse hole are all perfectly "smooth", so they saturate strength
    # and shape and would rank above real audio. Damp rather than reject -- the
    # region is still reported, it just stops crowding out actual content.
    live = _LIVE_MIN + (1.0 - _LIVE_MIN) * _clamp01(
        (feat.get("spread", _LIVE_FULL) - _LIVE_FLOOR) / (_LIVE_FULL - _LIVE_FLOOR))
    return strength * shape * dist * live


def _bulk_looks_float(np, data, window, step, first, count):
    """Vectorized _looks_float over windows [first, first+count).

    Returns a bool per window: False means the per-window probe would also have
    said no, so it can be skipped. A True is only a hint -- _looks_float still
    runs and has the final say -- which keeps this an optimization rather than a
    second opinion. Falls back to all-True if the geometry does not line up.
    """
    nf = window // 4
    if nf < 16 or step % 4 or len(data) < window:
        return [True] * count
    flt = np.frombuffer(data, dtype=np.float32, count=len(data) // 4)
    fstep = max(1, nf // 32)
    picks = np.arange(0, nf, fstep, dtype=np.int64)          # same indices
    base = (np.arange(first, first + count, dtype=np.int64)) * (step // 4)
    idx = base[:, None] + picks[None, :]
    if idx[-1, -1] >= flt.size:
        return [True] * count
    inrange = np.abs(flt[idx]) <= _FLOAT_RANGE               # NaN -> False
    frac = inrange.sum(axis=1) / picks.size
    return frac >= _FLOAT_MIN_FRAC


# Peak numpy allocation per batch. The vectorized path holds several
# (windows x window) float64 arrays at once, so doing every window in one shot
# costs ~46x the input -- 1.4 GB for a 32 MB file, and far worse for the sizes
# `locate` is pointed at. Batching makes the memory flat in input size at no
# measurable cost in speed, because each batch is still a big enough array for
# numpy to amortize its per-call overhead.
_BULK_BATCH_BYTES = 48 * 1024 * 1024


def _bulk_features(data, window, step, count):
    """Every window's feature dict at once, using numpy. Returns None if numpy
    is unavailable, so the caller falls back to the per-window path.

    This vectorizes *arithmetic only*. Everything that makes a decision --
    audio_score, the gate, region merging -- stays on the single implementation
    below, and the awkward float-PCM probe delegates back to window_features for
    the few windows that trip it. So the two paths can differ in speed but have
    no independent opinion about what counts as audio.

    Worth 7x on a large blob, which is the difference between a 32 MB image
    taking seconds and taking a quarter of a minute. Runs in batches so peak
    memory is flat in input size -- see _BULK_BATCH_BYTES.
    """
    try:
        import numpy as np
    except Exception:
        return None

    per_batch = max(1, _BULK_BATCH_BYTES // max(1, window * 8))
    out = []
    for first in range(0, count, per_batch):
        nb = min(per_batch, count - first)
        batch = _bulk_batch(np, data, window, step, first, nb)
        if batch is None:
            return None
        out.extend(batch)
    return out


def _bulk_batch(np, data, window, step, first, count):
    """One batch of windows, starting at window index `first`."""
    from numpy.lib.stride_tricks import as_strided

    at = first * step
    span = (count - 1) * step + window
    buf = np.frombuffer(data, dtype=np.uint8, count=span, offset=at)
    win = as_strided(buf, (count, window),
                     (step * buf.strides[0], buf.strides[0]))

    # one bincount over (row * 256 + byte) beats a per-row histogram by ~3x
    rows = np.arange(count, dtype=np.int64)[:, None]
    counts = np.bincount((rows * 256 + win).ravel(),
                         minlength=count * 256).reshape(count, 256)

    # same identity as entropy_from_counts: H = log2(N) - (1/N) * sum(c*log2(c))
    with np.errstate(divide="ignore", invalid="ignore"):
        clog = np.where(counts > 0, counts * np.log2(np.maximum(counts, 1)), 0.0)
    entropy = np.log2(window) - clog.sum(axis=1) / window
    np.clip(entropy, 0.0, None, out=entropy)

    freq = counts / window
    printable = freq[:, sorted(_PRINTABLE)].sum(axis=1)
    hist_tv = np.abs(np.diff(freq, axis=1)).sum(axis=1)

    sg = win.astype(np.int16)
    sg = np.where(sg > 127, sg - 256, sg).astype(np.float64)
    dev = sg - sg.mean(axis=1, keepdims=True)
    den = np.einsum("ij,ij->i", dev, dev)
    safe = np.where(den > 0.0, den, 1.0)
    ac = {L: np.where(den > 0.0,
                      np.einsum("ij,ij->i", dev[:, :-L], dev[:, L:]) / safe, 0.0)
          for L in LAGS}
    spread = np.sqrt(den / window)

    r1, r2, r4, r8 = ac[1], ac[2], ac[4], ac[8]
    peak = np.maximum(r1, r2)
    structure = np.maximum(np.maximum(np.maximum(-r4, -r8), np.maximum(r4, r8)),
                           np.maximum(r2 - r1, 0.0))
    np.clip(structure, 0.0, None, out=structure)

    # The float pre-check samples fixed indices, so it vectorizes exactly. Worth
    # doing: as a per-window call it was ~23% of this function, 400k struct
    # unpacks. `v == v and -R <= v <= R` reduces to `abs(v) <= R` because a NaN
    # fails every comparison anyway.
    maybe_float = _bulk_looks_float(np, data, window, step, first, count)

    # windows above the entropy ceiling take the early-out, exactly as the
    # per-window path does, and float-looking windows are handed back to it
    dead = entropy > _ENTROPY_CEIL
    out = []
    for i in range(count):
        if not dead[i] and maybe_float[i]:
            at = (first + i) * step
            chunk = data[at:at + window]
            if _looks_float(chunk):
                out.append(window_features(chunk))
                continue
        e, pr, tv = float(entropy[i]), float(printable[i]), float(hist_tv[i])
        if dead[i]:
            out.append({"entropy": e, "autocorr": {L: 0.0 for L in LAGS},
                        "peak": 0.0, "structure": 0.0, "printable": pr,
                        "hist_tv": tv, "n": window})
        else:
            out.append({"entropy": e, "autocorr": {L: float(ac[L][i]) for L in LAGS},
                        "peak": float(peak[i]), "structure": float(structure[i]),
                        "printable": pr, "hist_tv": tv,
                        "spread": float(spread[i]), "n": window})
    return out


# How the bytes are read before scoring. The features treat a window as 8-bit
# signed PCM, which is blind to the format most audio actually uses: in 16-bit
# PCM the low byte of each sample is close to noise, so consecutive bytes
# alternate noisy/smooth and the lag-1 correlation the detector keys on is
# destroyed. Measured on 600 KB of real 16-bit stereo audio:
#
#     read as bytes      r1 = +0.001   r2 = +0.051    -> scored 0.000, invisible
#     read as int16      r1 = +0.572   r2 = +0.998
#
# Taking every other byte recovers the high byte, which IS a smooth 8-bit
# signal, so the existing feature set works on it unchanged. Offset 1 is
# little-endian, offset 0 big-endian. Cost is one extra half-length pass per
# view; the win is that `locate` finds 16-bit PCM at all.
# windows folded per block, so only one block of feature dicts is live per view
_VIEW_BLOCK = 4096

_VIEWS = (
    ("8bit", 1, 0),
    ("16bit-le", 2, 1),
    ("16bit-be", 2, 0),
)


def _view_fits(data, window, step, stride):
    """Whether a decimated reading has enough bytes per window to score."""
    if stride == 1:
        return True
    vwin = window // stride
    return vwin >= LAGS[-1] + 1 and step // stride >= 1 and len(data) >= window


def _scores_for(data, window, step, stride, offset, first, n):
    """(score, features) for windows [first, first+n) under one reading.

    The decimation happens on this block only. Slicing `data[offset::stride]`
    for the whole buffer copies half of it per view -- +256 MB on a scan at the
    read cap, which is the same allocation-scales-with-input mistake the batch
    loop in _bulk_features exists to avoid.
    """
    span_start = first * step
    span_end = min(len(data), span_start + (n - 1) * step + window)
    if span_end - span_start < window:
        return []
    block = data[span_start:span_end]
    if stride > 1:
        block = block[offset::stride]
        vwin, vstep = window // stride, step // stride
    else:
        vwin, vstep = window, step
    usable = (len(block) - vwin) // vstep + 1
    n = min(n, max(0, usable))
    if n < 1:
        return []
    feats = _bulk_features(block, vwin, vstep, n) if n > 64 else None
    if feats is None:
        feats = [window_features(block[i * vstep:i * vstep + vwin])
                 for i in range(n)]
    return [(audio_score(f), f) for f in feats]


def _best_marks(data, window, step, count, min_score=0.0):
    """Yield (offset, score, features) per window, under whichever view scores
    best.

    A generator on purpose: materializing one tuple per window held the whole
    scan's features in memory at once, which is how peak allocation ended up
    tracking input size even after the numpy layer was batched.

    A window is audio if ANY reading of it looks like audio -- taking the max
    rather than a fixed interpretation is what lets one scan find 8-bit and
    16-bit material in the same blob. The winning view is recorded on the
    features so `locate --analyze` and the region evidence can report it.
    """
    views = [(name, stride, offset) for name, stride, offset in _VIEWS
             if _view_fits(data, window, step, stride)]
    if not views:
        return

    # Fold the views a block of windows at a time. Building each view's full
    # feature list and then combining would keep two complete sets alive at
    # once, and those dicts are retained per window for the whole scan -- the
    # same shape as the allocation bug the batch loop in _bulk_features exists
    # to prevent.
    for first in range(0, count, _VIEW_BLOCK):
        n = min(_VIEW_BLOCK, count - first)
        best = [(-1.0, None)] * n
        for name, stride, offset in views:
            for i, (score, feat) in enumerate(
                    _scores_for(data, window, step, stride, offset, first, n)):
                if score > best[i][0]:
                    best[i] = (score, dict(feat, view=name))
        for i, (score, feat) in enumerate(best):
            # Only windows that clear the gate have their features kept: scan
            # discards the rest, and retaining a dict per window made memory
            # scale with the INPUT rather than with the audio actually found.
            keep = feat if (feat and score >= min_score) else None
            yield ((first + i) * step, max(score, 0.0), keep)


def scan(data, *, window=DEFAULT_WINDOW, step=DEFAULT_STEP,
         min_score=DEFAULT_MIN_SCORE, merge_gap=DEFAULT_MERGE_GAP,
         read_cap=DEFAULT_READ_CAP):
    """Locate candidate raw-audio regions in `data` (bytes).

    Slides a window, scores each, and merges runs of audio-like windows into
    regions. Real audio is dynamic -- quiet passages and transients dip below the
    gate -- so a region is held open across up to `merge_gap` consecutive
    below-gate windows (hysteresis), keeping one file as one region instead of
    shattering it into fragments. Returns dicts with offset/end, a confidence
    (mean of the audio windows), and averaged evidence. Never raises."""
    if read_cap and len(data) > read_cap:
        data = data[:read_cap]
    n = len(data)
    if n < window:
        return []

    last = n - window
    count = last // step + 1
    marks = _best_marks(data, window, step, count, min_score)

    regions = []
    run = None                                          # accumulating region
    gap = 0                                             # consecutive below-gate windows
    for off, score, feat in marks:
        if score >= min_score:
            if run is None:
                run = _acc_new(off, window, score, feat)
            else:
                _acc_add(run, off, window, score, feat)
            gap = 0
        elif run is not None:
            gap += 1
            if gap > merge_gap:                         # sustained non-audio: close
                regions.append(_finalize(run))
                run = None
                gap = 0
            # else bridge the short dip, keeping the region open
    if run is not None:
        regions.append(_finalize(run))
    return regions


def analyze_geometry(data, cap=16384):
    """Infer the PCM geometry of a raw region -- bit width (8/16), channels
    (mono/stereo), and endianness -- by which interpretation is smoothest
    (highest lag-1 autocorrelation). Sample RATE is playback metadata that does
    not live in the bytes, so it is reported as None with common candidates.
    Returns a dict; never raises."""
    b = data[:cap]

    def _ac(seq):
        n = len(seq)
        if n < 8:
            return -1.0
        m = sum(seq) / n
        den = 0.0
        for s in seq:
            d = s - m
            den += d * d
        if den <= 0:
            return -1.0
        num = 0.0
        for i in range(n - 1):
            num += (seq[i] - m) * (seq[i + 1] - m)
        return num / den

    s8 = [x - 256 if x > 127 else x for x in b]
    n16 = len(b) // 2
    le = [struct.unpack_from("<h", b, i * 2)[0] for i in range(n16)]
    be = [struct.unpack_from(">h", b, i * 2)[0] for i in range(n16)]
    cands = [(8, 1, None, False, _ac(s8)),
             (16, 1, "le", False, _ac(le)),
             (16, 1, "be", False, _ac(be))]
    if n16 >= 16:
        cands.append((16, 2, "le", False, (_ac(le[0::2]) + _ac(le[1::2])) / 2))
        cands.append((16, 2, "be", False, (_ac(be[0::2]) + _ac(be[1::2])) / 2))
    if len(s8) >= 16:
        cands.append((8, 2, None, False, (_ac(s8[0::2]) + _ac(s8[1::2])) / 2))
    fp = _float_probe(b)                                 # float32/64 hypotheses
    if fp:
        cands.append((fp[1], 1, fp[2], True, fp[0]))
    width, channels, endian, is_float, score = max(cands, key=lambda c: c[4])

    # decode the winning interpretation for debug tells
    if is_float:
        code = "f" if width == 32 else "d"
        nf = len(b) // (width // 8)
        vals = list(struct.unpack_from(f"{endian}{nf}{code}", b, 0))
        peak_ref, full = 1.0, 1.0
    else:
        vals = {(8, None): s8, (16, "le"): le, (16, "be"): be}[(width, endian)]
        if channels == 2:
            vals = vals[0::2]
        full = (1 << (width - 1)) - 1
        peak_ref = full
    tells = _debug_tells(vals, peak_ref, full)

    return {"width": width, "channels": channels,
            "endian": {"<": "le", ">": "be"}.get(endian, endian),
            "float": is_float, "confidence": round(max(score, 0.0), 3),
            "rate": None, "rate_candidates": [8000, 11025, 22050, 44100, 48000],
            **tells}


_PCM_TRUST = 0.5                     # linear-PCM autocorr below this is not trustworthy
_SPU_MIN = 0.85                      # SPU block-header validity to call it SPU-ADPCM


def classify(data, cap=32768):
    """Rank what a byte region is, audio-wise, instead of committing to one guess.

    The statistical detector labels anything audio-shaped a "raw-pcm blob", but a
    lot of it is a *codec* (4-bit ADPCM) that turns to noise played as linear PCM.
    The two signals are anti-correlated: SPU-ADPCM has a rigid 16-byte block header
    (shift/filter nibble + a 0..7 flag) that PCM almost never satisfies, while its
    bytes read as PCM are jagged (low autocorrelation); linear PCM is the mirror.
    So a strong SPU score with a weak PCM score means codec, the reverse means PCM,
    and both weak means "uncertain -- do not trust the raw-pcm guess."

    Returns {top, is_codec, uncertain, candidates:[{label, confidence, codec, ...}]}
    sorted most-confident first. Never raises."""
    from acidcat.core.codecs import vag
    b = data[:cap]
    cands = []

    spu = vag.looks_like_spu(b)
    if spu >= _SPU_MIN:
        cands.append({"label": "spu-adpcm", "confidence": round(spu, 2), "codec": True,
                      "detail": "PS1 SPU-ADPCM (4-bit blocks) -- decode, not linear PCM"})

    geo = analyze_geometry(b)
    if geo:
        gl = (f"float{geo['width']}" if geo.get("float")
              else f"{geo.get('endian') or '?'}-{geo['width']}bit")
        ch = "stereo" if geo.get("channels") == 2 else "mono"
        cands.append({"label": f"raw-pcm {gl} {ch}",
                      "confidence": float(geo.get("confidence", 0.0)), "codec": False,
                      "geometry": geo, "detail": "linear PCM (rate is not in the bytes)"})

    cands.sort(key=lambda c: -c["confidence"])
    if not cands:
        return {"top": "unknown", "is_codec": False, "uncertain": True, "candidates": []}
    top = cands[0]
    uncertain = (not top["codec"]) and top["confidence"] < _PCM_TRUST
    return {"top": top["label"] + (" (uncertain)" if uncertain else ""),
            "is_codec": bool(top["codec"]), "uncertain": uncertain,
            "candidates": cands}


def _debug_tells(vals, peak_ref, full):
    """Signal-health flags for a decoded region: silence (flat), DC offset (mean
    far off centre), clipping (pinned at the rails). The 'why is my audio broken'
    triage layer."""
    n = len(vals)
    if n < 8:
        return {}
    mean = sum(vals) / n
    amp = max((abs(v) for v in vals), default=0)
    clip = sum(1 for v in vals if abs(v) >= full * 0.999) / n
    return {
        "silence": amp <= peak_ref * 0.01,
        "dc_offset": round(mean / peak_ref, 3) if abs(mean) > peak_ref * 0.05 else 0.0,
        "clipping": round(clip, 3) if clip > 0.005 else 0.0,
    }


def _acc_new(off, window, score, feat):
    """Start a region accumulator.

    Running sums rather than a list of feature dicts: _finalize only ever wants
    the MEAN of each field, and retaining one dict per window made scan()'s
    peak memory scale with the audio found (4x the input on all-audio data).
    The numpy layer is already batched; this was the other half.
    """
    acc = {"start": off, "end": off + window, "n": 0, "score_sum": 0.0,
           "entropy_sum": 0.0, "ac_sum": {L: 0.0 for L in LAGS},
           "views": {}}
    _acc_add(acc, off, window, score, feat)
    return acc


def _acc_add(acc, off, window, score, feat):
    acc["end"] = off + window                   # end tracks the last HIT only
    acc["n"] += 1
    acc["score_sum"] += score
    if feat:
        acc["entropy_sum"] += feat.get("entropy", 0.0)
        for L in LAGS:
            acc["ac_sum"][L] += feat.get("autocorr", {}).get(L, 0.0)
        view = feat.get("view")
        if view:
            acc["views"][view] = acc["views"].get(view, 0) + 1


def _finalize(acc):
    """Collapse an accumulated run into a reported region with mean evidence."""
    k = max(acc["n"], 1)
    # the reading that won most often across the region -- 8bit / 16bit-le / -be
    view = max(acc["views"], key=acc["views"].get) if acc["views"] else "8bit"
    return {
        "start": acc["start"],
        "end": acc["end"],
        "length": acc["end"] - acc["start"],
        "confidence": acc["score_sum"] / k,
        "windows": acc["n"],
        "evidence": {
            "entropy": acc["entropy_sum"] / k,
            "autocorr": {L: acc["ac_sum"][L] / k for L in LAGS},
            "width": 2 if view.startswith("16bit") else 1,
            "view": view,
        },
    }
