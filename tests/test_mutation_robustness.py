"""Hostile input must produce a clean refusal, never a traceback.

acidcat's contract on a file it cannot read is a `Unsupported`, or a walk that
returns with lint warnings. What it must never do is raise something else --
`struct.error`, `IndexError`, `MemoryError`, a `UnicodeDecodeError` from a tag
it half-read. Those are the same defect wearing different names: a parser that
believed a number the file gave it.

The corpus is `acidcat_lab.mutations`, and its value is that it does NOT
bitflip. Random flips mostly produce files rejected in the first eight bytes,
which tests the magic check and nothing else. These produce files that stay
PLAUSIBLE for longer -- a length that is large but not absurd, a required chunk
dropped, a chunk duplicated, a list nested past any sane depth. That is where a
parser has to decide rather than bail, and therefore where it can be wrong.

WHY THE RUN COUNT IS ASSERTED. A mutation returns its input unchanged when it
does not apply, and those are skipped. If the corpus were to shrink, or the
mutations stopped matching the seeds, this would sail through having tested
nothing at all and report success -- which is precisely the shape of bug this
suite has had to fix twice elsewhere. So the floor is asserted alongside the
result: a green run means BOTH that nothing crashed and that enough was tried
for that to mean something.

Measured when this landed: 1,188 mutated inputs from 28 seeds, zero crashes --
804 walked cleanly and 384 raised the clean refusal.
"""

import glob
import os
import random
import tempfile

import pytest

from acidcat.core.forensics import anomalies
from acidcat.core.walk import Unsupported, walk_file

mutations = pytest.importorskip("acidcat_lab.mutations",
                                reason="acidcat_lab not installed")

# Enough that a single unlucky seed cannot carry the whole result, small enough
# that this is not the slowest thing in the suite. The wider sweep is a manual
# run; this is the regression floor.
_ROUNDS = 2
_MIN_RUNS = 400
_MAX_SEED_BYTES = 4 * 1024 * 1024


def _seeds():
    here = os.path.dirname(__file__)
    roots = [os.path.join(here, "..", "data", "fixtures"),
             os.path.join(here, "..", "data", "test_formats")]
    out = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        for path in sorted(glob.glob(os.path.join(root, "*.*"))):
            try:
                if 64 < os.path.getsize(path) <= _MAX_SEED_BYTES:
                    out.append(path)
            except OSError:
                pass
    return out


def _examine(path):
    """Walk and scan one file. Split out so the sweep can be exercised against
    a parser that DOES fail -- with a real corpus nothing does, which makes
    every assertion below vacuous and the harness itself untested."""
    label, chunks, warns = walk_file(path)
    anomalies.scan(path, label, chunks, warns)


def _sweep(examine=None, seeds=None):
    """(runs, clean, refused, failures) over every seed x mutation."""
    examine = examine or _examine
    runs = clean = refused = 0
    failures = []
    for round_no in range(_ROUNDS):
        rng = random.Random(round_no)
        for seed_path in (seeds if seeds is not None else _seeds()):
            try:
                with open(seed_path, "rb") as fh:
                    base = fh.read()
            except OSError:
                continue
            for name, (_category, fn) in sorted(mutations.ALL.items()):
                try:
                    blob = fn(base, rng)
                except Exception as exc:            # a broken MUTATION, not a
                    failures.append(                # broken parser -- but still
                        (seed_path, name, "mutation raised %r" % (exc,)))
                    continue
                if not blob or blob == base:
                    continue                        # did not apply to this seed
                runs += 1
                fd, tmp = tempfile.mkstemp(suffix=os.path.splitext(seed_path)[1])
                os.close(fd)
                try:
                    with open(tmp, "wb") as fh:
                        fh.write(blob)
                    examine(tmp)
                    clean += 1
                except Unsupported:
                    refused += 1                    # the contract, honoured
                except Exception as exc:
                    # Qualified, because several of the exceptions worth
                    # catching here are named `error` and nothing else --
                    # struct.error reports as "error: unpack requires a buffer
                    # of 4 bytes", which names neither the module nor the
                    # problem. A failure report has to say what failed.
                    kind = type(exc)
                    qual = kind.__name__
                    if getattr(kind, "__module__", "builtins") != "builtins":
                        qual = "%s.%s" % (kind.__module__, qual)
                    failures.append((seed_path, name, "%s: %s" % (qual, exc)))
                finally:
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
    return runs, clean, refused, failures


@pytest.fixture(scope="module")
def sweep():
    if not _seeds():
        pytest.skip("no seed files present")
    return _sweep()


def test_nothing_raises_anything_but_a_clean_refusal(sweep):
    """The assertion the file exists for.

    `Unsupported` is an answer. Anything else is the parser having believed a
    number the file gave it, and the file is hostile by construction.
    """
    _runs, _clean, _refused, failures = sweep
    assert not failures, "\n".join(
        "  %s + %s -> %s" % (os.path.basename(p), m, e)
        for p, m, e in failures[:25])


def test_enough_was_actually_tried(sweep):
    """Coverage is part of the claim.

    Mutations that do not apply return the input unchanged and are skipped, so
    a shrinking corpus or a mutation set that stopped matching would leave this
    file passing while testing nothing. That failure looks exactly like success
    from outside, which is why it is asserted rather than assumed.
    """
    runs, _clean, _refused, _failures = sweep
    assert runs >= _MIN_RUNS, (
        "only %d mutated inputs were tried, floor is %d. Either the seed "
        "corpus shrank or the mutations stopped applying to it -- in both "
        "cases the result above is about nothing." % (runs, _MIN_RUNS))


def test_both_outcomes_actually_occur(sweep):
    """A sweep where everything is refused is testing the magic check.

    Random bitflipping produces mostly-rejected input, which is why this corpus
    is structured instead. If nothing walks cleanly the mutations have become
    too destructive to reach the code that decides anything; if nothing is
    refused they have become too gentle to be hostile.
    """
    runs, clean, refused, _failures = sweep
    assert clean > 0, "no mutated file walked at all; the corpus is only testing rejection"
    assert refused > 0, "nothing was refused; the mutations are no longer hostile"
    assert clean + refused == runs, (clean, refused, runs)


# ── the harness itself ──────────────────────────────────────────────

class TestTheSweepMechanism:
    """Exercised against parsers that misbehave on purpose.

    With a real corpus nothing crashes, the run count clears its floor and both
    outcomes occur -- so deleting any assertion above leaves this file green.
    That makes them facts about the corpus rather than about the code, and it
    is the second time in this suite that a passing oracle turned out to be
    testing nothing. The mechanism gets its own tests instead.
    """

    def _one_seed(self, tmp_path):
        p = tmp_path / "seed.wav"
        p.write_bytes(b"RIFF" + (200).to_bytes(4, "little") + b"WAVE"
                      + b"fmt " + (16).to_bytes(4, "little") + bytes(16)
                      + b"data" + (160).to_bytes(4, "little") + bytes(160))
        return [str(p)]

    def test_a_crashing_parser_is_reported(self, tmp_path):
        def explodes(_path):
            raise struct_error()

        def struct_error():
            import struct
            return struct.error("unpack requires a buffer of 4 bytes")

        runs, clean, refused, failures = _sweep(examine=explodes,
                                                seeds=self._one_seed(tmp_path))
        assert runs > 0, "the sweep tried nothing, so it proves nothing"
        assert len(failures) == runs, (len(failures), runs)
        assert "struct.error" in failures[0][2], failures[0]

    def test_a_clean_refusal_is_not_a_failure(self, tmp_path):
        def refuses(_path):
            raise Unsupported("no walker for this")

        runs, clean, refused, failures = _sweep(examine=refuses,
                                                seeds=self._one_seed(tmp_path))
        assert runs > 0 and refused == runs
        assert failures == [], failures
        assert clean == 0

    def test_a_parser_that_accepts_everything_counts_as_clean(self, tmp_path):
        runs, clean, refused, failures = _sweep(examine=lambda _p: None,
                                                seeds=self._one_seed(tmp_path))
        assert runs > 0 and clean == runs and refused == 0 and not failures

    def test_the_counts_always_reconcile(self, tmp_path):
        """clean + refused + failures must be every run. A sweep that loses
        track of an outcome could hide a crash in the arithmetic."""
        calls = {"n": 0}

        def alternates(_path):
            calls["n"] += 1
            if calls["n"] % 3 == 0:
                raise ValueError("boom")
            if calls["n"] % 3 == 1:
                raise Unsupported("nope")

        runs, clean, refused, failures = _sweep(examine=alternates,
                                                seeds=self._one_seed(tmp_path))
        assert clean + refused + len(failures) == runs, (
            clean, refused, len(failures), runs)
        assert clean and refused and failures, (clean, refused, len(failures))
