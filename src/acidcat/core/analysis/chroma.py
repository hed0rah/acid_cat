"""Pitch-class energy (chroma) from audio, on numpy alone.

Key finding folds a spectrum into 12 pitch classes and matches the shape
against tonal profiles. What goes into that fold matters more than the matching
does, and three things are done here that a plain magnitude chroma skips:

**Percussion is removed first.** A drum hit is broadband, so it deposits energy
into all 12 bins roughly equally and flattens the very shape the profiles are
supposed to discriminate. Most of a sample library is percussive, so this is the
dominant error source. Harmonic/percussive separation is a median filter on the
spectrogram (Fitzgerald 2010): a horizontal median keeps what persists in time
(pitches), a vertical median keeps what spreads across frequency (transients).

The usual implementation inverts both back to audio, which costs two inverse
transforms. Chroma never needs the time domain again, so the mask is applied to
the magnitude spectrogram and folded directly -- the expensive half is skipped.

**Frames are weighted by energy.** Averaging every frame equally lets the
silence around a one-shot count as much as the note, which is backwards for a
sample library where trailing silence is the norm.

**Magnitude is log-compressed.** Raw magnitude is dominated by the loudest
partials, so a single strong note can outvote the harmony it sits in.
"""

_FFT = 4096
_HOP = 1024
_LO_HZ = 55.0            # A1; below this, pitch class is unreliable and boomy
_HI_HZ = 5000.0          # above this, partials outnumber fundamentals
_MED_TIME = 17           # median-filter spans, in frames and bins
_MED_FREQ = 17
_MASK_POWER = 2.0        # soft-mask exponent (Wiener-style at 2)
_LOG_GAMMA = 100.0       # log(1 + gamma * x) compression


def _stft_mag(np, y, n_fft=_FFT, hop=_HOP):
    if len(y) < n_fft:
        return None
    win = np.hanning(n_fft).astype(np.float32)
    frames = 1 + (len(y) - n_fft) // hop
    idx = np.arange(n_fft)[None, :] + hop * np.arange(frames)[:, None]
    block = y[idx].astype(np.float32) * win
    return np.abs(np.fft.rfft(block, axis=1)).T          # (bins, frames)


def _median_filter(np, mag, size, axis):
    """Sliding median along one axis, edge-padded. numpy-only stand-in for
    scipy.ndimage.median_filter, which lives behind a heavier dependency."""
    if size <= 1 or mag.shape[axis] < 2:
        return mag
    size = min(size, mag.shape[axis])
    if size % 2 == 0:
        size -= 1
    if size < 3:
        return mag
    pad = size // 2
    widths = [(0, 0), (0, 0)]
    widths[axis] = (pad, pad)
    padded = np.pad(mag, widths, mode="edge")
    view = np.lib.stride_tricks.sliding_window_view(padded, size, axis=axis)
    return np.median(view, axis=-1)


def harmonic_magnitude(np, mag):
    """Percussion-suppressed magnitude spectrogram via soft masking."""
    harm = _median_filter(np, mag, _MED_TIME, axis=1)     # persists in time
    perc = _median_filter(np, mag, _MED_FREQ, axis=0)     # spreads in frequency
    hp = harm ** _MASK_POWER
    pp = perc ** _MASK_POWER
    total = hp + pp
    with np.errstate(invalid="ignore", divide="ignore"):
        mask = np.where(total > 0, hp / total, 0.0)
    return mag * mask


def _bin_pitch_classes(np, n_fft, rate):
    """Pitch class per FFT bin, and which bins to use at all."""
    freqs = np.fft.rfftfreq(n_fft, 1.0 / rate)
    usable = (freqs >= _LO_HZ) & (freqs <= min(_HI_HZ, rate / 2.0))
    pcs = np.zeros(len(freqs), dtype=np.int64)
    with np.errstate(divide="ignore", invalid="ignore"):
        midi = 69.0 + 12.0 * np.log2(np.where(freqs > 0, freqs, 1.0) / 440.0)
    # bin 0 of chroma is C, matching how librosa lays it out, so callers and
    # the existing profiles agree on where C is
    pcs[usable] = (np.rint(midi[usable]).astype(np.int64) - 12) % 12
    return pcs, usable


def chroma12(channels, rate, *, harmonic=True):
    """12 pitch-class energies (bin 0 = C) for decoded PCM, or None.

    `harmonic=False` skips the percussion suppression, which is what a plain
    magnitude chroma does -- kept so the two can be compared directly.
    """
    import numpy as np

    y = channels[0] if len(channels) == 1 else np.mean(channels, axis=0)
    if len(y) < _FFT:
        return None
    mag = _stft_mag(np, y)
    if mag is None or not mag.size:
        return None

    # restrict to the bins that can carry a pitch class BEFORE separating.
    # Filtering all 2049 bins and then discarding 78% of them was most of the
    # cost of this function.
    pcs, usable = _bin_pitch_classes(np, _FFT, rate)
    if not usable.any():
        return None
    mag = mag[usable]
    pcs = pcs[usable]
    if harmonic:
        mag = harmonic_magnitude(np, mag)

    # loud frames should count for more than the silence around a one-shot
    energy = mag.sum(axis=0)
    if not energy.any():
        return None
    weighted = mag * energy[None, :]

    acc = np.zeros(12)
    for pc in range(12):
        sel = pcs == pc
        if sel.any():
            acc[pc] = float(weighted[sel].sum())
    if acc.sum() <= 0:
        return None
    # compress before the profiles see it: raw magnitude lets one loud partial
    # outvote the harmony around it
    acc = np.log1p(_LOG_GAMMA * (acc / acc.max()))
    return [float(v) for v in acc]
