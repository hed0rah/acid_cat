"""Compare our SID render against an independent player, and score it.

WHY THIS IS BLACK-BOX. Every accurate SID emulator -- reSID, reSIDfp,
libsidplayfp, JSIDPlay2 -- is GPL-2.0. acidcat is MIT, so their code and their
measured filter tables cannot be copied in, and reading them to reimplement is
derivative-work territory. Running one as an external process and comparing
audio is clean, and it is the stronger method anyway: it measures what actually
comes out rather than whether we transcribed someone's constants correctly.

WHAT THE SCORES MEAN, and this is the useful part. They are deliberately three
numbers rather than one, because "wrong" has more than one shape here:

  envelope   do the notes start and stop at the same times
  pitch      are they the same notes
  centroid   does the timbre move the same way

High envelope and pitch with a low centroid is a specific, actionable finding:
the tune is being played correctly and rendered with the wrong tone, which
points at the filter and the combined waveforms rather than at the CPU. One
blended score would hide exactly that.

None of this says "accurate". It says how far apart two renders are, against a
reference that is itself an emulator.
"""

import os
import shutil
import subprocess
import tempfile

# A command template, so any player can be the oracle. {sid} {wav} {secs} are
# substituted. sidplayfp is auto-detected; anything else is named here:
#
#   ACIDCAT_SID_ORACLE='java -jar /path/jsidplay2-console.jar --sidToWav {wav} -t {secs} {sid}'
ORACLE_ENV = "ACIDCAT_SID_ORACLE"


def find_oracle():
    """(template, name) for an available reference player, or (None, reason)."""
    tmpl = os.environ.get(ORACLE_ENV)
    if tmpl:
        return tmpl, "ACIDCAT_SID_ORACLE"
    exe = shutil.which("sidplayfp")
    if exe:
        # sidplayfp writes a wav with -w and takes a length with -t
        return '"%s" -q -t{secs} -w{wav} {sid}' % exe, "sidplayfp"
    return None, ("no reference player: install sidplayfp, or set %s to a "
                  "command template using {sid} {wav} {secs}" % ORACLE_ENV)


def render_reference(sid_path, seconds, tmpdir):
    """Run the oracle and return the path to its WAV, or None."""
    tmpl, _name = find_oracle()
    if not tmpl:
        return None
    wav = os.path.join(tmpdir, "reference.wav")
    cmd = tmpl.format(sid='"%s"' % sid_path, wav='"%s"' % wav,
                      secs=int(seconds))
    try:
        subprocess.run(cmd, shell=True, timeout=180,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (subprocess.SubprocessError, OSError):
        return None
    # sidplayfp appends .wav when the argument has no extension
    for cand in (wav, wav + ".wav"):
        if os.path.isfile(cand) and os.path.getsize(cand) > 1024:
            return cand
    return None


# ── the scoring ─────────────────────────────────────────────────────

_HOP = 1024


def _frames(sig, np, hop=_HOP):
    n = (len(sig) // hop) * hop
    if n < hop:
        return np.zeros((0, hop))
    return sig[:n].reshape(-1, hop)


def envelope(sig, np, hop=_HOP):
    """RMS per frame. Level-normalised, because the two renders have no reason
    to agree on absolute gain and we are not asking whether they do."""
    f = _frames(sig, np, hop)
    if not len(f):
        return np.zeros(0)
    e = np.sqrt((f * f).mean(axis=1))
    peak = float(e.max()) if len(e) else 0.0
    return e / peak if peak > 0 else e


def pitch_track(sig, np, sr, hop=_HOP):
    """Dominant frequency per frame, in Hz. 0 where the frame is near-silent.

    A peak-picking tracker, not a real pitch detector: it reports the loudest
    partial, which for a SID voice is usually but not always the fundamental.
    Good enough to answer "the same notes or not", which is the question.
    """
    f = _frames(sig, np, hop)
    if not len(f):
        return np.zeros(0)
    win = np.hanning(hop)
    spec = np.abs(np.fft.rfft(f * win, axis=1))
    freqs = np.fft.rfftfreq(hop, 1.0 / sr)
    lo = freqs > 40.0                      # ignore DC and rumble
    spec = spec * lo
    idx = spec.argmax(axis=1)

    # Parabolic interpolation around the peak bin. A bare argmax quantises to
    # the bin spacing, which at this hop is about 43 Hz -- wider than a
    # semitone anywhere below the top octave, so two clearly different bass
    # notes land in the same bin and compare as identical. Fitting a parabola
    # through the peak and its neighbours recovers the sub-bin position and
    # costs one more gather.
    rows = np.arange(len(f))
    k = np.clip(idx, 1, spec.shape[1] - 2)
    y0 = spec[rows, k - 1]
    y1 = spec[rows, k]
    y2 = spec[rows, k + 1]
    denom = y0 - 2.0 * y1 + y2
    with np.errstate(invalid="ignore", divide="ignore"):
        delta = np.where(denom != 0, 0.5 * (y0 - y2) / denom, 0.0)
    delta = np.nan_to_num(np.clip(delta, -0.5, 0.5))
    bin_hz = sr / float(hop)
    est = (k + delta) * bin_hz

    loud = spec.max(axis=1) > (spec.max() * 0.02 if spec.max() else 1)
    return np.where(loud, est, 0.0)


def spectral_centroid(sig, np, sr, hop=_HOP):
    """Brightness per frame. The measure the filter moves most."""
    f = _frames(sig, np, hop)
    if not len(f):
        return np.zeros(0)
    spec = np.abs(np.fft.rfft(f * np.hanning(hop), axis=1))
    freqs = np.fft.rfftfreq(hop, 1.0 / sr)
    total = spec.sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        c = (spec * freqs).sum(axis=1) / total
    return np.nan_to_num(c)


def correlate(a, b, np):
    """Pearson r over the overlapping span, or 0.0 when either is flat."""
    n = min(len(a), len(b))
    if n < 4:
        return 0.0
    a, b = a[:n], b[:n]
    a = a - a.mean()
    b = b - b.mean()
    da, db = float(np.sqrt((a * a).sum())), float(np.sqrt((b * b).sum()))
    if da == 0 or db == 0:
        return 0.0
    return float((a * b).sum() / (da * db))


def pitch_agreement(a, b, np, cents=60.0):
    """Fraction of frames where both are voiced and within `cents`.

    Compared in cents rather than Hz because a fixed Hz tolerance is far too
    tight in the bass and far too loose in the treble, and a SID tune uses
    both ends.
    """
    n = min(len(a), len(b))
    if n < 4:
        return 0.0
    a, b = a[:n], b[:n]
    both = (a > 0) & (b > 0)
    if not both.any():
        return 0.0
    ratio = np.where(both, a / np.where(b > 0, b, 1.0), 1.0)
    diff = np.abs(1200.0 * np.log2(np.where(ratio > 0, ratio, 1.0)))
    return float((diff[both] <= cents).mean())


def best_lag(a, b, np, max_lag=20):
    """Frame offset that best aligns two envelopes.

    Two players do not necessarily start on the same frame -- one may call
    init a frame earlier, or begin writing its output before the first note.
    Without this the scores measure the offset rather than the difference.
    """
    best, best_r = 0, -2.0
    for lag in range(-max_lag, max_lag + 1):
        if lag < 0:
            r = correlate(a[-lag:], b, np)
        else:
            r = correlate(a, b[lag:], np)
        if r > best_r:
            best, best_r = lag, r
    return best


def compare(ours, theirs, sr, np):
    """Three scores plus the alignment used. Each is 0..1, higher is closer."""
    ea, eb = envelope(ours, np), envelope(theirs, np)
    lag = best_lag(ea, eb, np)
    if lag < 0:
        ours, ea = ours[-lag * _HOP:], ea[-lag:]
    elif lag > 0:
        theirs, eb = theirs[lag * _HOP:], eb[lag:]

    pa = pitch_track(ours, np, sr)
    pb = pitch_track(theirs, np, sr)
    ca = spectral_centroid(ours, np, sr)
    cb = spectral_centroid(theirs, np, sr)
    return {
        "envelope_r": correlate(ea, eb, np),
        "pitch_agreement": pitch_agreement(pa, pb, np),
        "centroid_r": correlate(ca, cb, np),
        "lag_frames": lag,
        "frames": int(min(len(ea), len(eb))),
    }


def load_wav_mono(path, np):
    """Decode a WAV to mono float, reusing acidcat's own PCM loader.

    `pcm.load` returns (channels, rate) with one float64 array per channel.
    The reference player may write stereo even for a mono tune, so the
    channels are averaged rather than assumed to be one.
    """
    from acidcat.core.analysis import pcm
    channels, rate = pcm.load(path)
    if not channels:
        return None, 0
    if len(channels) == 1:
        return np.asarray(channels[0], dtype=np.float64), rate
    n = min(len(c) for c in channels)
    stack = np.vstack([np.asarray(c[:n], dtype=np.float64) for c in channels])
    return stack.mean(axis=0), rate
