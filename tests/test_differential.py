"""acidcat's parsers against an independent implementation.

Every other test in this suite checks acidcat against something acidcat's own
authors wrote: a fixture built from the spec, an assertion about what a walker
should return. That catches a walker drifting from its test. It cannot catch a
walker and its test being wrong together, which is what happens when a format is
misread the same way twice.

mutagen is the second opinion. It is already a hard dependency, it parses the
same containers from the same bytes with an entirely separate implementation,
and it reports three facts that are integers rather than judgements:
sample_rate, channels, bits_per_sample. Those are worth comparing exactly. A
mismatch is a confidently-wrong-output bug -- the class fuzzing never finds,
because nothing crashes.

WHY THIS TEST GUARDS ITS OWN COVERAGE.

The version of this that lived in the playground compared 45 files out of 3,228
and reported zero disagreements, which reads like a clean bill of health and was
very nearly nothing. The cause was one word:

    m = mutagen.File(path)
    if not m:                     # <- throws away every tagless file
        return None

`mutagen.File` returns a parsed object whose truthiness is its TAG COUNT, not
whether it parsed. A plain WAV with no metadata is a perfectly good `WAVE`
object that is `False`. Testing `not m` discarded 492 of every 500 real WAVs
while the comparison still reported success on what survived.

So this asserts a coverage floor as well as agreement. A silent collapse in
reach is the failure mode that actually happened, and an oracle that quietly
stops looking is worse than no oracle, because it reads as evidence.
"""

import glob
import os

import pytest

from conftest import FIXTURES_DIR, SMALL_FIXTURES

from acidcat.core.walk import walk_file

mutagen = pytest.importorskip("mutagen")

FIELDS = ("sample_rate", "channels", "bits_per_sample")
AUDIO = (".wav", ".aif", ".aiff", ".flac", ".mp3", ".ogg", ".m4a", ".aac",
         ".ac3", ".opus")
_MAX_BYTES = 60 * 1024 * 1024
# Of the files where acidcat reports these fields, the share mutagen must also
# read. Measured at 0.989 over 2,344 real audio files (2,319 compared); the
# shortfall is formats mutagen declines outright, not files it fumbles. The
# floor is here to catch the reach collapsing, not to pin the exact number.
_COVERAGE_FLOOR = 0.90
_MIN_FILES = 8


def _acidcat(path):
    """The three integers acidcat decodes, or None if it claims nothing."""
    try:
        _label, chunks, _warns = walk_file(path)
    except Exception:
        return None
    out = {}
    for chunk in chunks:
        for field in chunk.get("fields", []):
            name = field.get("name")
            if name in FIELDS and name not in out:
                try:
                    out[name] = int(str(field.get("value")).split()[0]
                                    .replace(",", ""))
                except (ValueError, IndexError):
                    pass
    return out or None


def _mutagen(path):
    """The same three integers from the reference, or None if it declines.

    `m is None` rather than `not m`: see the module docstring. The object's
    truthiness is its tag count.
    """
    try:
        m = mutagen.File(path)
    except Exception:
        return None
    if m is None or getattr(m, "info", None) is None:
        return None
    info = m.info
    return {k: v for k, v in (
        ("sample_rate", getattr(info, "sample_rate", None)),
        ("channels", getattr(info, "channels", None)),
        ("bits_per_sample", getattr(info, "bits_per_sample", None)),
    ) if v}


def _corpus():
    """Whatever audio this machine has: the shipped fixtures, the gitignored
    format corpus, and a wider tree if one is pointed at."""
    # Via conftest, not by naming the tree. The gitignored corpus is absent on
    # every runner, so a test that reaches for it directly does not fail there
    # -- it quietly compares nothing and reports green. Which is the same
    # failure this whole file exists to catch, one layer up.
    roots = [SMALL_FIXTURES, FIXTURES_DIR]
    extra = os.environ.get("ACIDCAT_DIFFERENTIAL_CORPUS", "")
    if extra and os.path.isdir(extra):
        roots.append(extra)
    seen, out = set(), []
    for root in roots:
        if not os.path.isdir(root):
            continue
        for path in glob.glob(os.path.join(root, "**", "*"), recursive=True):
            key = os.path.normcase(os.path.abspath(path))
            if key in seen or not os.path.isfile(path):
                continue
            seen.add(key)
            if os.path.splitext(path)[1].lower() not in AUDIO:
                continue
            try:
                if os.path.getsize(path) > _MAX_BYTES:
                    continue
            except OSError:
                continue
            out.append(path)
    return sorted(out)


def _mismatches(path, ours, theirs):
    """Every shared field the two readings disagree about.

    Split out so the comparison can be tested on inputs that DO disagree. A
    corpus where everything agrees exercises none of this: disabling the
    comparison entirely leaves such a run green, which makes the passing
    assertion evidence about the corpus rather than about the code.
    """
    return [(path, k, ours[k], theirs[k])
            for k in sorted(set(ours) & set(theirs)) if ours[k] != theirs[k]]


def _compare():
    """(claimed, compared, disagreements) over the whole corpus."""
    claimed = 0
    compared = 0
    bad = []
    for path in _corpus():
        ours = _acidcat(path)
        if not ours:
            continue
        claimed += 1
        theirs = _mutagen(path)
        if not theirs:
            continue
        compared += 1
        bad.extend(_mismatches(path, ours, theirs))
    return claimed, compared, bad


def test_acidcat_and_mutagen_agree_on_every_shared_fact():
    """The assertion the whole file exists for.

    These are integers read from the same bytes by two implementations that
    share no code. There is no rounding to allow for and no interpretation to
    differ on: a mismatch means one of them is wrong about the file.
    """
    claimed, compared, bad = _compare()
    if compared < _MIN_FILES:
        pytest.skip("only %d comparable files; set ACIDCAT_DIFFERENTIAL_CORPUS"
                    % compared)
    assert not bad, "\n".join(
        "  %s  %s: acidcat=%s mutagen=%s" % (os.path.basename(p), k, a, m)
        for p, k, a, m in bad[:40])


def test_the_oracle_still_reaches_most_of_the_corpus():
    """Coverage is part of the claim, not context for it.

    Agreement over four files and agreement over four thousand are the same
    sentence and different evidence. This fails when the second opinion stops
    being asked, which is exactly how the original went quiet.
    """
    claimed, compared, _bad = _compare()
    if claimed < _MIN_FILES:
        pytest.skip("corpus too small to measure reach (%d files)" % claimed)
    reach = compared / float(claimed)
    assert reach >= _COVERAGE_FLOOR, (
        "the reference now reads %d of the %d files acidcat parses (%.1f%%, "
        "floor %.0f%%). Agreement below is being measured over a shrinking "
        "sample. The usual cause is a truthiness test on a mutagen object."
        % (compared, claimed, 100 * reach, 100 * _COVERAGE_FLOOR))


def test_a_tagless_file_is_still_comparable():
    """The specific regression, pinned so it cannot come back quietly.

    A file with no metadata is the ordinary case for a sample library, and it
    is exactly what the old reach bug discarded. Nothing else in this file
    would fail if `_mutagen` went back to `not m` on a corpus that happens to
    be tagged.
    """
    tagless = []
    for path in _corpus():
        try:
            m = mutagen.File(path)
        except Exception:
            continue
        if m is not None and not m and getattr(m, "info", None) is not None:
            tagless.append(path)
    if not tagless:
        pytest.skip("no tagless audio in the corpus to check")
    path = tagless[0]
    assert _mutagen(path) is not None, (
        "%s carries no tags, so the mutagen object is falsy while having "
        "parsed perfectly well. Reading that as 'declined' is what silently "
        "cut the oracle's reach to a fiftieth of the corpus."
        % os.path.basename(path))


# ── the comparison itself, on inputs that disagree ──────────────────

class TestTheComparisonMechanism:
    """Exercised with synthetic readings, because the corpus agrees.

    Every assertion above passes today whether or not the comparison works,
    which is a property of the corpus and not of the code. These pin the
    machinery so a passing suite means the oracle would speak up.
    """

    def test_a_disagreement_is_reported(self):
        got = _mismatches("f.wav", {"sample_rate": 44100, "channels": 2},
                          {"sample_rate": 48000, "channels": 2})
        assert got == [("f.wav", "sample_rate", 44100, 48000)]

    def test_agreement_reports_nothing(self):
        assert _mismatches("f.wav", {"sample_rate": 44100, "channels": 2},
                           {"sample_rate": 44100, "channels": 2}) == []

    def test_every_shared_field_is_checked_not_just_the_first(self):
        """A loop that stops at the first mismatch reports one bug per file and
        hides the rest, which is the same shape of wrong answer this suite has
        had to fix elsewhere."""
        got = _mismatches("f.wav",
                          {"sample_rate": 44100, "channels": 2, "bits_per_sample": 16},
                          {"sample_rate": 48000, "channels": 1, "bits_per_sample": 24})
        assert len(got) == 3, got

    def test_a_field_only_one_side_reports_is_not_a_disagreement(self):
        """mutagen omits bits_per_sample for compressed formats. Absence is not
        a claim, and treating it as one would fail every MP3 on the disk."""
        assert _mismatches("f.mp3", {"sample_rate": 44100, "bits_per_sample": 16},
                           {"sample_rate": 44100}) == []
