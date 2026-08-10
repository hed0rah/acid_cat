"""Tests for ripper-concealment detection.

The bar here is the false positive rate, not the hit rate. Every pattern this
module looks for is ordinary in music -- sample libraries are full of digital
silence, loops produce byte-identical blocks, percussion is broadband -- and an
earlier version that matched patterns alone fired on 8 to 60 percent of 457 real
WAV files. The gates that fixed it (sector shape, and structural discontinuity
with the neighbourhood) are what these tests exist to protect.
"""

import numpy as np
import pytest

from acidcat.core.forensics import concealment as C

SF = C.SECTOR_FRAMES


def music(n_sectors=12, seed=5):
    """Structured stereo content: correlated sample to sample, with real
    high-frequency energy, so a structureless region stands out from it."""
    rng = np.random.default_rng(seed)
    n = n_sectors * SF
    t = np.arange(n) / 44100.0
    left = (9000 * np.sin(2 * np.pi * 220 * t)
            + 3000 * np.sin(2 * np.pi * 3300 * t)
            + rng.normal(0, 1500, n))
    right = (8000 * np.sin(2 * np.pi * 277 * t)
             + 2600 * np.sin(2 * np.pi * 4100 * t)
             + rng.normal(0, 1500, n))
    return np.stack([left, right], axis=1).astype(np.int16)


def at_sector(k=6):
    return k * SF


def apply(x, how, k=6):
    """Inject one concealment strategy at a sector-aligned position."""
    s = x.copy()
    a = at_sector(k)
    b = a + SF
    if how == "mute":
        s[a:b] = 0
    elif how == "hold":
        s[a:b] = s[a - 1]
    elif how == "interpolate":
        lo, hi = s[a - 1].astype(np.float64), s[b].astype(np.float64)
        t = np.linspace(0, 1, SF, endpoint=False)[:, None]
        s[a:b] = (lo + (hi - lo) * t).astype(np.int16)
    elif how == "repeat":
        s[a:b] = s[a - SF:a]
    return s


# ── the strategies it must find ─────────────────────────────────────

@pytest.mark.parametrize("how", ["mute", "hold", "interpolate", "repeat"])
def test_each_strategy_is_found_and_named(how):
    f = C.scan(apply(music(), how))
    assert f, f"{how} went undetected"
    assert f[0]["strategy"] == how, f"{how} reported as {f[0]['strategy']}"
    assert f[0]["frame"] == at_sector()


@pytest.mark.parametrize("how", ["mute", "hold", "interpolate", "repeat"])
def test_the_finding_is_localised(how):
    f = C.scan(apply(music(), how))
    assert f[0]["sector"] == 6
    assert abs(f[0]["frames"] - SF) <= SF * 0.35


def test_clean_audio_is_silent():
    assert C.scan(music()) == []


def test_the_nesting_resolves_to_the_most_specific_strategy():
    """A run of zeros is also constant, and a constant run is also linear, so
    silence matches all three detectors. The narrowest match is the right
    answer, and reporting all three would be three findings for one event."""
    f = C.scan(apply(music(), "mute"))
    assert len(f) == 1
    assert f[0]["strategy"] == "mute"


# ── the false positives that killed the first version ───────────────

def test_leading_and_trailing_silence_is_not_concealment():
    """Every sample library has this. It is a file that starts quietly, not a
    sector that could not be read."""
    x = music()
    x[:SF * 2] = 0
    x[-SF * 2:] = 0
    assert C.scan(x) == []


def test_a_loop_is_not_concealment():
    """Loops repeat many times; a concealed sector repeats exactly once."""
    x = music(n_sectors=16)
    block = x[SF * 4:SF * 5]
    for k in range(5, 12):
        x[k * SF:(k + 1) * SF] = block
    assert C.scan(x) == []


def test_a_quiet_passage_inside_quiet_music_is_not_concealment():
    """The gate is structural contrast, not absolute level."""
    x = (music() // 40).astype(np.int16)
    assert C.scan(x) == []


def test_an_unaligned_gap_is_not_reported():
    """Concealment lands on the 588-frame CD grid. A dropout that does not is
    something else, and calling it a rip artifact would be wrong."""
    x = music()
    a = at_sector() + 137          # deliberately off the grid
    x[a:a + SF] = 0
    assert C.scan(x) == []


def test_a_gap_of_the_wrong_length_is_not_reported():
    x = music()
    a = at_sector()
    x[a:a + SF // 4] = 0           # far too short to be a sector
    assert C.scan(x) == []


# ── shape and honesty of the output ─────────────────────────────────

def test_too_short_to_have_a_neighbourhood_returns_nothing():
    """With no audio either side there is nothing to be discontinuous with, and
    guessing would be worse than declining."""
    assert C.scan(np.zeros((SF * 2, 2), dtype=np.int16)) == []


def test_mono_input_is_accepted():
    x = music()[:, :1]
    f = C.scan(apply(x, "mute"))
    assert f and f[0]["strategy"] == "mute"


def test_summary_names_the_sector_grid():
    """The alignment is the part that identifies the ORIGIN -- 588 frames is not
    a length that arises by accident in a file that never touched a CD."""
    note = C.summarise(C.scan(apply(music(), "mute")))
    assert "588" in note and "CD" in note


def test_summary_is_none_when_nothing_was_found():
    assert C.summarise([]) is None


def test_raw_passthrough_is_not_claimed():
    """Deliberately undetected. Measured over 588-sample windows, real audio and
    random data overlap by 40 percent on lag-1 autocorrelation, so claiming this
    would mean a false positive on roughly six real files in ten."""
    x = music()
    a = at_sector()
    rng = np.random.default_rng(3)
    x[a:a + SF] = rng.integers(-32768, 32767, (SF, 2), dtype=np.int16)
    assert all(f["strategy"] != "raw" for f in C.scan(x))
