"""Does the audio use the bandwidth its container claims?

A 44.1 kHz WAV can carry 22 kHz of signal. If everything above 16 kHz is gone,
the file is not what its header advertises -- most often because it was decoded
from a lossy source and re-saved as WAV. In a downloaded sample library that is
common and invisible: the header says 24-bit 48 kHz, the content is a 128 kbps
MP3.

Measuring bandwidth is easy; attributing it is not, and the naive check is wrong
in a way that matters. A bass sample, a filtered pad or a dark recording is also
missing its top octave, so "low cutoff means transcoded" would misfire on
exactly the material a producer owns most of.

What is actually diagnostic is a *step*: a lossy encoder discards whole bands
and leaves a near-vertical cliff, while filtering and dull material slope. So
this searches for the largest local drop across a 1/6-octave window and reports
its size. Two design points, both forced by measurement rather than assumed:

  - The step is compared with its own neighbourhood, not with a global
    reference level. An earlier version measured against the overall spectrum
    level and fell apart on pink-weighted material, where natural tilt alone
    put the apparent "edge" at 5 kHz.
  - A wall within a few percent of Nyquist is ignored. Any decaying spectrum
    meets the noise floor there, which made a gentle 10 kHz lowpass score
    42.8 dB -- indistinguishable from a codec by size alone.

ACCURACY, measured on real music rather than the synthetic signals this was
first tuned against. 276 files: 12 real stereo sources encoded with ffmpeg to
MP3/AAC/Vorbis/Opus at several bitrates and decoded back, plus filtered and
resampled negative controls.

    recall (lossy round-trips flagged)      47.0%   (62/132)
    specificity (clean not flagged)         90.7%   (98/108)
    precision                               86.1%

So this catches about half of what it is aimed at, and is right about 6 times
in 7 when it does fire. Both failure directions are real and worth knowing:

  - MISSES: 100% of AAC 256k and Vorbis 256k, and half of MP3 320k. High
    bitrates leave little or no wall.
  - FALSE POSITIVES: resampling round-trips, 33% via 22.05 kHz and 50% via
    16 kHz. An anti-imaging filter looks like a codec wall to this measure.

The earlier numbers in this docstring (MP3 57-89 dB, clean under 29.3 dB) came
from `_brickwall(_noise(), ...)` -- white noise with FFT bins zeroed. A flat
spectrum truncated IS a huge local step; real music already has little energy
near Nyquist, so the step an encoder actually leaves is far smaller. On real
material the distributions overlap almost completely, and no single threshold
does much better: the best available on that corpus is 24 dB for 75% accuracy,
against 47%/91% at the shipped 40 dB.

The verdict wording is deliberately "consistent with lossy encoding" rather
than a claim of proof, and a clean verdict is not evidence a file was never
compressed. Improving this needs a second discriminator -- the residual above
the wall is near-flat for a codec kill and sloped for resampling -- not a
different threshold.
"""

_FFT = 8192
_MIN_SAMPLES = _FFT * 2
_WIN = 1.12                 # comparison window each side of a candidate, ~1/6 octave
_GRID = 240                 # log-spaced candidate frequencies
_LO_SEARCH = 0.08           # ignore steps below this fraction of Nyquist
_HI_SEARCH = 0.97           # ... and above it: every spectrum falls at Nyquist
_WALL_DB = 40.0             # midway between the corpora: clean tops out at
                            # 29.3 dB, MP3 bottoms out at 57.1 dB
_ROLLOFF = 0.99             # energy fraction defining the content's top


def _spectrum(np, y, rate):
    """Average power spectrum in dB, and the matching frequencies.

    Silent frames are skipped rather than averaged in: a mostly-silent one-shot
    would otherwise have its level dragged down until every file looked
    band-limited.
    """
    n = _FFT
    win = np.hanning(n)
    acc = np.zeros(n // 2 + 1)
    frames = 0
    for i in range(0, len(y) - n + 1, n // 2):
        seg = y[i:i + n]
        if not seg.any():
            continue
        acc += np.abs(np.fft.rfft(seg * win)) ** 2
        frames += 1
    if not frames:
        return None, None, None, 0
    psd = acc / frames
    return np.fft.rfftfreq(n, 1.0 / rate), psd, 10 * np.log10(psd + 1e-30), frames


def _rolloff(np, freqs, psd, fraction=_ROLLOFF):
    """Frequency below which `fraction` of the spectral energy lies."""
    total = psd.sum()
    if total <= 0:
        return 0.0
    idx = int(np.searchsorted(np.cumsum(psd), total * fraction))
    return float(freqs[min(idx, len(freqs) - 1)])


def _largest_step(np, freqs, db, nyquist):
    """(step_db, frequency) of the sharpest drop in the searchable band.

    Compares the median level just below a candidate with the median just
    above it, so a sloping spectrum contributes only its slope across a sixth
    of an octave rather than its whole tilt.
    """
    best_step, best_f = 0.0, 0.0
    lo, hi = _LO_SEARCH * nyquist, _HI_SEARCH * nyquist
    if hi <= lo:
        return 0.0, 0.0
    for fc in np.exp(np.linspace(np.log(lo), np.log(hi), _GRID)):
        under = (freqs >= fc / _WIN) & (freqs <= fc)
        over = (freqs > fc) & (freqs <= min(fc * _WIN, nyquist))
        if under.sum() < 3 or over.sum() < 3:
            continue
        step = float(np.median(db[under]) - np.median(db[over]))
        if step > best_step:
            best_step, best_f = step, float(fc)
    return best_step, best_f


def analyze(channels, rate):
    """Bandwidth verdict for decoded PCM, or None if too short/silent to judge."""
    import numpy as np

    y = channels[0] if len(channels) == 1 else np.mean(channels, axis=0)
    if len(y) < _MIN_SAMPLES:
        return None
    freqs, psd, db, frames = _spectrum(np, y, rate)
    if db is None:
        return None

    nyquist = rate / 2.0
    top = _rolloff(np, freqs, psd)
    step, step_f = _largest_step(np, freqs, db, nyquist)

    if step >= _WALL_DB:
        verdict = "brick-wall"
        detail = (f"content stops dead at {step_f / 1000:.1f} kHz "
                  f"({step_f / nyquist:.0%} of the {nyquist / 1000:.1f} kHz this "
                  f"container allows) -- a {step:.0f} dB cliff, consistent with "
                  f"lossy encoding rather than filtering")
    else:
        verdict = "no-wall"
        detail = (f"no codec-like cliff; 99% of the energy is below "
                  f"{top / 1000:.1f} kHz of a possible {nyquist / 1000:.1f} kHz")

    # Name the window the verdict actually covers, the way channels.analyze
    # does. pcm.load caps the decode, so on a long file "content stops dead at
    # 16 kHz" is a statement about a prefix, and it reads as absolute unless the
    # scope travels with it. Conditional, so a file that fit says nothing extra.
    #
    # len(y) is the decoded PCM length; `frames` below is the FFT frame count,
    # a different number, which is why it cannot be reused for this.
    from acidcat.core.analysis.pcm import _MAX_FRAMES
    if len(y) >= _MAX_FRAMES:
        detail += (f" (measured over the first {len(y):,} frames, the decode "
                   f"limit -- a file that changes character later would not be "
                   f"seen)")

    return {"check": "bandwidth", "verdict": verdict, "detail": detail,
            "rolloff_hz": round(top, 1), "nyquist_hz": round(nyquist, 1),
            "wall_db": round(step, 1),
            "wall_hz": round(step_f, 1) if step >= _WALL_DB else None,
            "frames_analyzed": frames, "pcm_frames": int(len(y))}
