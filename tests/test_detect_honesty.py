"""BPM/key detection must decline rather than invent.

From the audio-analysis audit. Measured against 671 files whose filename states
the tempo, the BPM path was 17.7% accurate at +/-2 BPM and answered 100% of the
time -- so for 56% of labelled files it emitted a confident number with no
metrical relation to the truth. These tests pin the parts of that which are
fixable without redesigning the estimator: it must not ship a value its own
sanity window rejects, and it must not key a filename by accident.
"""

import pytest

from acidcat.core.analysis.detect import (parse_key_from_filename,
                                          validate_and_improve_bpm)


@pytest.mark.parametrize("bpm", [304.0, 9999.0, -5.0, 0.0, 35.89])
def test_an_out_of_range_tempo_is_refused_when_nothing_corroborates_it(bpm):
    """The 60..200 guard sat below the `filename_bpm is None` early return, so
    it only ran when the answer was already known. With no filename tempo,
    anything shipped: 71 of 277 unlabelled files (25.6%) came back outside the
    window, up to 304 BPM on a 0.4-second snare."""
    value, source = validate_and_improve_bpm(bpm, None)
    assert value is None, f"{bpm} was shipped as a tempo"
    assert source == "rejected"


@pytest.mark.parametrize("bpm", [60.0, 126.0, 174.0, 200.0])
def test_a_plausible_tempo_still_passes(bpm):
    assert validate_and_improve_bpm(bpm, None) == (bpm, "detected")


def test_a_filename_tempo_still_rescues_a_bad_detection():
    """The guard must not cost the correction path its whole job."""
    assert validate_and_improve_bpm(304.0, 70) == (70, "filename")
    value, source = validate_and_improve_bpm(63.02, 126)
    assert source == "corrected" and abs(value - 126) < 1


def test_nothing_in_nothing_out():
    assert validate_and_improve_bpm(None, None) == (None, None)


@pytest.mark.parametrize("name", [
    "Tanh-FM Decay.wav", "FM Square 01.wav", "AM Radio Sweep.wav",
    "Kick GM.wav", "BM Bass.wav", "Sub EM 01.wav",
])
def test_common_abbreviations_are_not_keys(name):
    r"""The bare-capital-M pattern under re.IGNORECASE read FM as F major. On
    a real 2,331-file synth corpus, 10 of the 14 major labels this parser
    produced -- 71% -- were that false positive."""
    assert parse_key_from_filename(name) is None, f"{name} was keyed"


@pytest.mark.parametrize("name,expect", [
    ("Loop_126_Am.wav", "Am"),
    ("Bass F#m.wav", "F#m"),
    ("Lead_Ebm.wav", "D#m"),
    ("Pad C Major.wav", "C"),
    ("Song_Gmaj.wav", "G"),
    ("x C minor.wav", "Cm"),
    ("Track A M.wav", "A"),
    ("pad_F#M.wav", "F#"),      # Beatport style: an accidental disambiguates
    ("lead_BbM.wav", "A#"),
])
def test_real_key_spellings_still_parse(name, expect):
    """The fix is narrow: lowercase m may hug the letter, capital M must be
    spaced. Everything a producer actually writes still works."""
    assert parse_key_from_filename(name) == expect


def test_the_tempo_helper_survives_the_librosa_alias_being_removed():
    """`librosa.beat.tempo` is deprecated and goes away in librosa 1.0. The
    caller sits inside `except Exception: pass`, so its removal would have made
    BPM silently filename-only while still reporting bpm_source 'detected'."""
    librosa = pytest.importorskip("librosa")
    import numpy as np
    from acidcat.core.analysis.detect import _tempo

    class _NoAlias:
        """librosa 1.0 as far as this helper can tell."""
        feature = librosa.feature

        class beat:
            pass

    out = _tempo(_NoAlias, onset_envelope=np.ones(400), sr=22050, aggregate=None)
    assert out is not None and len(out) > 0
