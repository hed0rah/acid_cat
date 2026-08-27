"""How far is our SID render from an independent player's?

The scoring lives in `sid_oracle`; this file proves the scoring works and then
uses it. Both halves matter, and the first one matters more: a comparison
harness with no reference player available skips, and a skipped differential
looks exactly like a passing one from outside. That has already happened once
in this suite, with an oracle that was quietly checking 45 files out of 3,228.

So the metrics are exercised against signals whose answer is known by
construction -- identical, detuned, inverted, silent -- and the harness is
exercised against our own renderer twice, which needs no external player at
all. Only the last test needs an oracle, and it says so loudly when it does
not have one.
"""
import os

import pytest

np = pytest.importorskip("numpy", reason="the differential needs numpy")

import sid_oracle                                    # noqa: E402
from acidcat.core.codecs import sid_render           # noqa: E402


SR = 44100


def _tone(hz, secs=2.0, sr=SR, amp=1.0):
    t = np.arange(int(secs * sr)) / float(sr)
    return amp * np.sin(2 * np.pi * hz * t)


def _chirp(f0, f1, secs=2.0, sr=SR):
    """A linear chirp. Written out because the naive sin(2pi*(f0+k*t)*t) sweeps
    at twice the rate intended -- instantaneous frequency is the derivative of
    the phase, not the bracketed term."""
    t = np.arange(int(secs * sr)) / float(sr)
    k = (f1 - f0) / secs
    return np.sin(2 * np.pi * (f0 * t + 0.5 * k * t * t))


def _swept_env(sig, shape):
    """Apply an amplitude contour so the envelope metric has something to see."""
    env = np.interp(np.linspace(0, 1, len(sig)),
                    np.linspace(0, 1, len(shape)), shape)
    return sig * env


# ── the metrics, against known answers ──────────────────────────────

def test_a_signal_matches_itself():
    a = _swept_env(_tone(440.0), [0.1, 1.0, 0.3, 0.9, 0.2])
    s = sid_oracle.compare(a, a.copy(), SR, np)
    assert s["envelope_r"] > 0.99
    assert s["pitch_agreement"] > 0.95
    assert s["centroid_r"] > 0.99


def test_a_detuned_signal_fails_the_pitch_metric_but_not_the_envelope():
    """The decomposition that makes these three numbers worth having: same
    performance, wrong notes, and the scores say which."""
    shape = [0.1, 1.0, 0.3, 0.9, 0.2]
    a = _swept_env(_tone(440.0), shape)
    b = _swept_env(_tone(560.0), shape)          # a different note entirely
    s = sid_oracle.compare(a, b, SR, np)
    assert s["envelope_r"] > 0.95, "the notes are in the same places"
    assert s["pitch_agreement"] < 0.15, "but they are not the same notes"


def test_a_pitch_within_tolerance_still_agrees():
    """60 cents is under a semitone. A render that is a hair sharp is the same
    note; one a semitone out is not."""
    a = _tone(440.0)
    close = _tone(440.0 * 2 ** (30 / 1200.0))    # 30 cents sharp
    far = _tone(440.0 * 2 ** (200 / 1200.0))     # two semitones sharp
    assert sid_oracle.pitch_agreement(
        sid_oracle.pitch_track(a, np, SR),
        sid_oracle.pitch_track(close, np, SR), np) > 0.9
    assert sid_oracle.pitch_agreement(
        sid_oracle.pitch_track(a, np, SR),
        sid_oracle.pitch_track(far, np, SR), np) < 0.1


def test_an_inverted_brightness_contour_fails_the_centroid_metric():
    """The metric the filter moves. Two renders whose timbre travels in
    opposite directions must not score as similar, however well their notes
    line up."""
    rising = _chirp(300.0, 3000.0)
    falling = _chirp(3000.0, 300.0)
    assert sid_oracle.correlate(
        sid_oracle.spectral_centroid(rising, np, SR),
        sid_oracle.spectral_centroid(falling, np, SR), np) < -0.5


def test_a_constant_tone_offset_does_not_destroy_the_centroid_score():
    """A render that is uniformly darker but moves the same way should still
    score well on contour. Otherwise the metric measures gain rather than
    behaviour, and every result is dominated by the filter's DC error."""
    bright = _chirp(800.0, 2400.0)
    dark = _chirp(400.0, 1200.0)
    assert sid_oracle.correlate(
        sid_oracle.spectral_centroid(bright, np, SR),
        sid_oracle.spectral_centroid(dark, np, SR), np) > 0.8


def test_silence_and_noise_score_low_not_high():
    """The control. A metric that rewards two unrelated signals is worse than
    no metric, because it reports success."""
    a = _swept_env(_tone(440.0), [0.1, 1.0, 0.2])
    rng = np.random.RandomState(7)
    noise = rng.normal(0, 0.3, len(a))
    s = sid_oracle.compare(a, noise, SR, np)
    assert s["pitch_agreement"] < 0.3
    assert abs(s["envelope_r"]) < 0.7

    quiet = np.zeros(len(a))
    s2 = sid_oracle.compare(a, quiet, SR, np)
    assert s2["pitch_agreement"] == 0.0


def test_alignment_recovers_a_shifted_render():
    """Two players need not start on the same frame. Without realignment the
    scores measure the offset rather than the difference."""
    shape = [0.05, 1.0, 0.2, 0.8, 0.1]
    a = _swept_env(_tone(440.0, secs=3.0), shape)
    shift = 7 * 1024
    b = np.concatenate([np.zeros(shift), a[:-shift]])
    s = sid_oracle.compare(a, b, SR, np)
    assert s["lag_frames"] != 0, "the offset should have been detected"
    assert s["envelope_r"] > 0.9, "and corrected for"


def test_correlate_is_zero_on_a_flat_input_rather_than_nan():
    """A constant signal has no variance, so Pearson r is undefined. NaN would
    propagate silently into a reported score."""
    flat = np.ones(100)
    r = sid_oracle.correlate(flat, np.arange(100.0), np)
    assert r == 0.0 and not np.isnan(r)


# ── the harness, against our own renderer ───────────────────────────

def _tune():
    from test_sid_render import _tune as build
    return build()


def test_our_renderer_is_deterministic():
    """The self-differential, and the one part of this file that exercises the
    whole pipeline without needing anything installed.

    Two renders of one tune must be byte-identical. If they are not, every
    number this harness produces is noise, and no comparison against an
    external player would mean anything.
    """
    a, _ = sid_render.render(_tune(), seconds=2.0)
    b, _ = sid_render.render(_tune(), seconds=2.0)
    assert a == b, "the renderer is not deterministic"

    sig = np.frombuffer(a, dtype="<i2").astype(np.float64)
    s = sid_oracle.compare(sig, sig.copy(), SR, np)
    assert s["envelope_r"] > 0.99 and s["pitch_agreement"] > 0.9, s


def test_the_harness_can_tell_two_different_tunes_apart():
    """The control for the test above. Scoring a render against itself proves
    nothing unless scoring it against a DIFFERENT one comes out lower.

    The note is moved by the frequency HIGH byte. Moving the low byte shifts
    the pitch by under 4 Hz, which is smaller than a tracker bin -- the first
    version of this test did that, passed nothing, and looked like a control.
    """
    from test_sid_render import _tune as build, VOICE1_FREQ

    same, _ = sid_render.render(build(), seconds=2.0)
    blob = build()
    hi = bytes([0xA9, VOICE1_FREQ >> 8, 0x8D, 0x01, 0xD4])
    assert hi in blob, "expected the frequency-high store in the tune"
    moved = blob.replace(hi, bytes([0xA9, 0x28, 0x8D, 0x01, 0xD4]), 1)
    diff, _ = sid_render.render(moved, seconds=2.0)

    a = np.frombuffer(same, dtype="<i2").astype(np.float64)
    b = np.frombuffer(diff, dtype="<i2").astype(np.float64)
    pa = sid_oracle.pitch_track(a, np, SR)
    pb = sid_oracle.pitch_track(b, np, SR)
    assert sid_oracle.pitch_agreement(pa, pb, np) < 0.5, (
        "two different notes must not score as the same note: %.1f vs %.1f Hz"
        % (float(np.median(pa[pa > 0])), float(np.median(pb[pb > 0]))))


# ── the differential itself ─────────────────────────────────────────

_ORACLE_TMPL, _ORACLE_WHY = sid_oracle.find_oracle()


@pytest.mark.skipif(not _ORACLE_TMPL, reason=_ORACLE_WHY)
@pytest.mark.skipif(not os.environ.get("ACIDCAT_SID_CORPUS"),
                    reason="set ACIDCAT_SID_CORPUS to a dir of real .sid files")
def test_how_far_our_render_is_from_the_reference():
    """Score us against an independent player over a sample of real tunes.

    There is no pass mark on tone here, deliberately. The filter is a
    straight-line stand-in for a curve that differs between chip revisions, so
    a centroid threshold would be asserting something we have not earned. What
    IS asserted is that the notes and their timing line up -- if those drift,
    the CPU or the player loop is wrong, and that is a real regression rather
    than a question of taste.
    """
    import glob
    import tempfile

    root = os.environ["ACIDCAT_SID_CORPUS"]
    files = sorted(glob.glob(os.path.join(root, "**", "*.sid"), recursive=True))
    assert files, "no .sid files under %s" % root

    scored, results = 0, []
    with tempfile.TemporaryDirectory() as tmp:
        for path in files[:40]:
            with open(path, "rb") as fh:
                raw = fh.read()
            can, _why = sid_render.can_render(raw)
            if not can:
                continue
            ref = sid_oracle.render_reference(path, 10, tmp)
            if not ref:
                continue
            theirs, rate = sid_oracle.load_wav_mono(ref, np)
            if theirs is None or not len(theirs):
                continue
            pcm, _info = sid_render.render(raw, seconds=10.0, sample_rate=rate)
            ours = np.frombuffer(pcm, dtype="<i2").astype(np.float64)
            s = sid_oracle.compare(ours, theirs, rate, np)
            results.append((os.path.basename(path), s))
            scored += 1

    assert scored >= 5, (
        "only %d tunes were actually compared; the reference player produced "
        "nothing usable, so this test measured almost nothing" % scored)

    env = float(np.median([s["envelope_r"] for _n, s in results]))
    pit = float(np.median([s["pitch_agreement"] for _n, s in results]))
    cen = float(np.median([s["centroid_r"] for _n, s in results]))
    report = "\n".join("  %-40s env %.2f  pitch %.2f  centroid %.2f"
                       % (n[:38], s["envelope_r"], s["pitch_agreement"],
                          s["centroid_r"]) for n, s in results[:15])
    summary = ("scored %d tunes -- median envelope %.2f, pitch %.2f, "
               "centroid %.2f\n%s" % (scored, env, pit, cen, report))
    print("\n" + summary)

    assert env > 0.5, "note timing has drifted from the reference\n" + summary
    assert pit > 0.4, "the notes themselves have drifted\n" + summary
