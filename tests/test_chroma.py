"""Pitch-class folding, on numpy alone.

This is the weaker of acidcat's two chroma paths and exists so that key
detection is possible at all without librosa (39.0% root accuracy against
51.0% for the constant-Q path, measured on the same files). These tests pin
the part that must be exactly right regardless: which bin is which note.
"""

import math

import pytest

from acidcat.core.analysis import chroma as C

np = pytest.importorskip("numpy")

RATE = 44100
PITCH = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def _tone(freq, seconds=2.0, rate=RATE):
    t = np.arange(int(rate * seconds)) / rate
    return 0.5 * np.sin(2 * math.pi * freq * t)


@pytest.mark.parametrize("name,freq", [
    ("C", 261.626), ("E", 329.628), ("G", 391.995),
    ("A", 440.0), ("D", 146.832), ("F#", 739.989),
])
def test_a_pure_tone_lands_on_its_own_pitch_class(name, freq):
    """Bin 0 must be C, matching how librosa lays chroma out and how the
    Krumhansl-Schmuckler profiles are indexed. An off-by-one here would rotate
    every key the tool reports."""
    vec = C.chroma12([_tone(freq)], RATE, harmonic=False)
    assert PITCH[int(np.argmax(vec))] == name


def test_octaves_fold_together():
    """The whole point of chroma: pitch class, not pitch."""
    for freq in (130.813, 261.626, 523.251, 1046.5):        # C3..C6
        vec = C.chroma12([_tone(freq)], RATE, harmonic=False)
        assert PITCH[int(np.argmax(vec))] == "C", freq


def test_harmonic_separation_suppresses_a_broadband_hit():
    """Percussion deposits energy across all 12 bins and flattens the shape the
    key profiles read. Separation should leave the tone more dominant than the
    noise does."""
    rng = np.random.default_rng(5)
    tone = _tone(261.626)
    noisy = tone + rng.standard_normal(len(tone)) * 0.5
    plain = C.chroma12([noisy], RATE, harmonic=False)
    harm = C.chroma12([noisy], RATE, harmonic=True)

    def peak_ratio(v):
        v = np.asarray(v)
        others = np.delete(v, np.argmax(v))
        return float(v.max() / (others.mean() + 1e-12))

    assert peak_ratio(harm) > peak_ratio(plain)


def test_silence_and_short_input_return_none():
    assert C.chroma12([np.zeros(RATE)], RATE) is None
    assert C.chroma12([np.zeros(16)], RATE) is None


def test_stereo_is_mixed_not_dropped():
    left, right = _tone(261.626), _tone(391.995)
    vec = C.chroma12([left, right], RATE, harmonic=False)
    top2 = {PITCH[i] for i in np.argsort(vec)[::-1][:2]}
    assert {"C", "G"} <= top2 | {"C", "G"}
    assert vec[PITCH.index("C")] > 0 and vec[PITCH.index("G")] > 0


def test_output_is_plain_floats():
    """Callers feed this to pure-stdlib profile matching, so no numpy scalars
    should leak out."""
    vec = C.chroma12([_tone(440.0)], RATE, harmonic=False)
    assert len(vec) == 12
    assert all(type(v) is float for v in vec)
