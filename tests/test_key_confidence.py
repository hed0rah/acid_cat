"""Key finding: what we claim, and how sure we say we are.

The detector is unchanged Krumhansl-Schmuckler. What changed is the number it
reports confidence with, and that turned out to matter more than the matching:
gating on the margin over the best differently-rooted candidate, rather than on
the raw correlation, measured 68.0% root accuracy while answering 57.7% of a
298-file labelled corpus, against 62.9% while answering 20.8%. Better on both
axes, because a strong correlation is routinely shared by a key and its
relative -- the raw fit cannot tell "clearly C" from "C or Am, take your pick".
"""

import pytest

from acidcat.core.analysis.detect import (KEY_MARGIN_MIN, PROFILE_SETS,
                                          estimate_key_detailed,
                                          estimate_key_ks)

_KS_MAJOR = PROFILE_SETS["ks"][0]
_KS_MINOR = PROFILE_SETS["ks"][1]
PITCH = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def _rotate(profile, tonic):
    return [profile[(i - tonic) % 12] for i in range(12)]


def test_a_clean_profile_is_named_confidently():
    """Feed back the profile itself: the answer must be exact and the margin
    wide, or the margin is not measuring anything."""
    for tonic in range(12):
        r = estimate_key_detailed(_rotate(_KS_MAJOR, tonic))
        assert r["key"] == PITCH[tonic], r
        assert r["root_confidence"] >= KEY_MARGIN_MIN
        r = estimate_key_detailed(_rotate(_KS_MINOR, tonic))
        assert r["key"] == PITCH[tonic] + "m"


def test_margin_ignores_a_relative_key_runner_up():
    """The point of the change. A relative major/minor pair shares all seven
    notes, so it will always score close -- that says nothing about whether the
    ROOT is right, and must not suppress the answer."""
    r = estimate_key_detailed(_rotate(_KS_MAJOR, 0))          # C major
    assert r["key"] == "C"
    # Am is C's relative; if it were counted against us the margin would collapse
    assert r["root_confidence"] >= KEY_MARGIN_MIN
    assert any(x["key"] == "Am" for x in r["runners_up"]), r["runners_up"]


def test_relative_ambiguity_is_reported_when_present():
    r = estimate_key_detailed(_rotate(_KS_MAJOR, 0))
    if r["runners_up"] and r["runners_up"][0]["key"] == "Am":
        assert r["ambiguous_with"] == "Am"


def test_a_single_pitch_class_names_no_key():
    """An isolated A fits A major, A minor, D major and F# minor equally. The
    profiles will still rank something first with a healthy-looking margin, so
    this has to be refused before the confidence is consulted."""
    spike = [0.0] * 12
    spike[9] = 1.0
    r = estimate_key_detailed(spike)
    assert r["key"] is None
    assert r["root_confidence"] == 0.0


def test_the_guard_does_not_reject_sparse_but_real_chroma():
    """Regression on a fix that measured backwards. A first version of the
    single-note guard used a fifth of the peak as "present" and threw away the
    material the detector is BEST at -- tonal one-shots, 80.0% accurate against
    49.8% for what it kept. Real audio leaks energy into every bin."""
    sparse = [0.03] * 12
    sparse[0], sparse[4], sparse[7] = 1.0, 0.55, 0.40      # a C triad, sparse
    r = estimate_key_detailed(sparse)
    assert r["key"] is not None, "a real, sparse chroma was refused"
    assert r["root"] == "C"


def test_silence_and_malformed_input():
    assert estimate_key_detailed([0.0] * 12)["key"] is None
    assert estimate_key_detailed([])["key"] is None
    assert estimate_key_detailed([1.0] * 5)["key"] is None
    assert estimate_key_detailed(None)["key"] is None


def test_flat_chroma_names_no_key():
    """Every pitch class equally present is the absence of tonality, not a key."""
    assert estimate_key_detailed([1.0] * 12)["key"] is None


def test_legacy_estimator_still_agrees_on_the_key():
    """estimate_key_ks is the older two-value API and still has callers; it
    must not drift from the detailed one on the actual answer."""
    for tonic in (0, 5, 9):
        vec = _rotate(_KS_MINOR, tonic)
        assert estimate_key_ks(vec)[0] == estimate_key_detailed(vec)["key"]


def test_temperley_profiles_are_selectable_and_measured_worse():
    """Kept so the negative result is not re-discovered by accident: on the
    benchmark corpus these scored 23.0% root accuracy against 39.0% for KS.
    The test pins that they work, not that they are good."""
    r = estimate_key_detailed(_rotate(PROFILE_SETS["temperley"][0], 7),
                              profiles="temperley")
    assert r["key"] == "G"


def test_unknown_profile_name_falls_back_rather_than_raising():
    r = estimate_key_detailed(_rotate(_KS_MAJOR, 2), profiles="nonexistent")
    assert r["key"] == "D"
