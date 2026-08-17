"""A skip is not a pass, and nothing was counting them.

CI reported 90 skipped and a green tick. 85 of those 90 were one cause: a test
naming a path inside `data/test_formats/`, which is gitignored and 16 MB, so it
exists on a development machine and on no runner anywhere. The suite that gated
a release was 85 tests smaller than the suite anybody ran locally, and the gap
was invisible from both sides -- locally everything ran, and on CI the count
scrolled past in a line that also said "passed".

That is the same shape as the defect this project keeps finding in its own
output: a limit of ours reported as a fact about the thing measured. A skipped
test does not say "this does not apply here". It says nothing at all, and a
green run says it loudly.

So the cause is asserted rather than the symptom. Counting skips would mean
running the suite inside the suite, and the number moves for honest reasons
too -- a platform guard, a missing optional dependency. What must not come back
is a test reaching for a path that exists on one machine, so that is what is
checked, and it fails by naming the file and line rather than by reporting that
a number went up.

Two specimens genuinely cannot be synthesized -- a Native Instruments preset
and a Reaktor bank, both real files with no generator -- so `test_write.py` is
exempt. Everything else has a committed stand-in.
"""

import os
import subprocess

import pytest


def test_no_new_test_quietly_stops_running():
    """Read as text, because the defect is visible in the source and not at
    runtime: on the machine holding the corpus these tests pass, which is
    exactly the reading that hid the problem for as long as it existed."""
    import glob
    import re

    here = os.path.dirname(os.path.abspath(__file__))
    offenders = []
    # A test that names the gitignored tree is the defect, whether or not it
    # currently skips: on the machine that has the corpus it passes, and that
    # is exactly the reading that hid this for so long.
    allowed = {"test_write.py"}          # .nksf/.nmsv, nothing can make these
    for path in sorted(glob.glob(os.path.join(here, "test_*.py"))):
        name = os.path.basename(path)
        if name in allowed or name == os.path.basename(__file__):
            continue
        with open(path, encoding="utf-8", errors="replace") as fh:
            for n, line in enumerate(fh, 1):
                if "test_formats" not in line:
                    continue
                stripped = line.strip()
                # prose about the corpus is fine; reaching for it is not
                if stripped.startswith("#") or stripped.startswith('"'):
                    continue
                if re.search(r'["\']([^"\']*/)?data/test_formats', line) or \
                        '"test_formats"' in line:
                    offenders.append(f"{name}:{n}: {stripped[:78]}")

    assert not offenders, (
        "these reach into the gitignored corpus, so they do not run on any "
        "runner. Route them through conftest.corpus_path/CORPUS_* so a "
        "committed stand-in serves when the corpus is absent:\n  "
        + "\n  ".join(offenders))


def test_the_committed_corpus_is_actually_committed():
    """The stand-ins only help if a clone has them.

    Checked against git rather than the filesystem, because the filesystem is
    exactly what lied the first time: the files were present here and absent
    everywhere else.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    r = subprocess.run(["git", "ls-files", "data/fixtures"],
                       cwd=root, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        pytest.skip("not a git checkout")
    tracked = {os.path.basename(p) for p in r.stdout.split()}
    required = {"tone.wav", "tone.mp3", "tone.flac", "tone.ogg", "tone.opus",
                "tone.m4a", "tone24.wav", "tone51.wav", "tone.aiff",
                "tone24.flac"}
    missing = sorted(required - tracked)
    assert not missing, (
        f"stand-ins present locally but not committed, so every test relying "
        f"on them skips on CI: {missing}")


def test_every_corpus_constant_resolves():
    """Each CORPUS_* names a file that exists, here and in a clone."""
    import conftest
    unresolved = []
    for attr in sorted(dir(conftest)):
        if not attr.startswith("CORPUS_"):
            continue
        path = getattr(conftest, attr)
        if not (isinstance(path, str) and os.path.isfile(path)):
            unresolved.append(f"{attr} -> {path}")
    assert not unresolved, (
        "corpus constants pointing at nothing:\n  " + "\n  ".join(unresolved))


def test_the_standin_table_points_at_real_files():
    """A typo in the table degrades silently: corpus_path returns None and the
    caller skips, which is the state this whole file exists to prevent."""
    import conftest
    broken = []
    for name, small in sorted(conftest._STANDIN.items()):
        p = os.path.join(conftest.SMALL_FIXTURES, small)
        if not os.path.isfile(p):
            broken.append(f"{name} -> {small} (missing)")
    assert not broken, "stand-in table entries with no file:\n  " + \
        "\n  ".join(broken)
