"""Every chunk has to say which bytes it occupies, in a way one rule can read.

There was no such rule. `_field_abs` documented the closest thing to one --
field offsets are relative to `payload_base`, else `offset + 8`
(core/infra/fieldcodec.py:39) -- and nine sites across six modules re-derived it
independently, spelled four different ways, two of them computing an end as
`base + size + 8` and one never consulting `payload_base` at all. Six consumers
guessing at the same geometry is not a convention, it is a coincidence, and the
coincidence had already broken.

It broke because one key was answering two questions. RIFF's `size` is the
PAYLOAD length; MP4's is the TOTAL box length, header included. Both are
reasonable and no single reader serves both, so a normalized chunk now carries
`extent` (every byte it occupies) and `payload` (the bytes inside), and says
whether anyone actually declared them. core/infra/geometry.py is the one reader.

Measured over 47 files and 275 chunks, before and after:

                     before   after
    declared            196     200
    defaulted            72      72
    bytes past EOF       28       3

The three that remain are the point of the distinction. A declared extent
running past the end of a deliberately truncated fixture is a finding about the
FILE, and all three are announced in the file's own numbers -- "claims 176,400
bytes but only 79,922 remain". Under a single `size` key that case and MP4's
systematic 8-byte overshoot looked identical from outside, which is how the
second one survived as long as it did.

A ratchet, not a gate: the known-defect set may shrink and must never grow. Its
own reach is asserted rather than implied, because a test that quietly stops
examining anything passes forever.

Those 47 files are `data/` on a development machine, where `data/test_formats/`
holds 48 specimens that are gitignored. A clone has 7 walkable files and 56
chunks, so the floor below is the CLONE's corpus, not the measurement above.
The first version of this file asserted the development numbers and passed on
the machine it was written on while failing on all five CI platforms -- the
ratchet reporting a local reading as a fact about the repo, which is the exact
defect class the rest of this file exists to catch. The floor rises only when
specimens are COMMITTED.
"""

import glob
import os

import pytest

from acidcat.core.infra import geometry
from acidcat.core.walk import walk_file

_MAX = 8_000_000

# Anchored to the repo, not to os.getcwd(). A relative glob made the whole
# module a no-op when pytest ran from anywhere but the root: no matches, the
# fixture skips, and four assertions report success having examined nothing.
_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "data")


# (walker label, chunk id) -> why this one is known-wrong. Shrink only.
#
# Empty, and it got here by shrinking rather than by starting that way. MP4 now
# states extent and payload separately, because an ISO box size counts its own
# header and the two differ by exactly that much. FLAC's `frames` chunk declares
# its own base rather than inheriting an eight-byte header it does not have. And
# a truncated FLAC reports the damage in the file's own numbers, the way RIFF
# always did -- the three chunks that still fail the arithmetic are damaged
# files being described correctly, which is the tool working.
KNOWN_DEFECTS = {}


def _corpus():
    for path in sorted(glob.glob(os.path.join(_DATA, "**", "*.*"),
                                 recursive=True)):
        try:
            if os.path.getsize(path) > _MAX:
                continue
        except OSError:
            continue
        try:
            label, chunks, warns = walk_file(path, deep=False)
        except Exception:
            continue
        yield path, label, chunks, (warns or [])


def _overshoots(chunks, fsize):
    """Chunks whose bytes leave the file, read through the one accessor.

    Through `geometry.payload_of` rather than by re-deriving the rule here: a
    test that carries its own tenth copy of the thing it is policing would keep
    passing after the copy it polices was fixed.
    """
    out = []
    for c in chunks:
        if c.get("geometry") == geometry.UNPOSITIONED:
            continue
        off = c.get("offset")
        if not isinstance(off, int):
            continue
        base, n = geometry.payload_of(c)
        eoff, elen = geometry.extent_of(c)
        if base + n > fsize or eoff + elen > fsize:
            out.append((str(c.get("id")), base, n,
                        c.get("geometry") or "unnormalized"))
    return out


@pytest.fixture(scope="module")
def walked():
    got = list(_corpus())
    if not got:
        pytest.skip("no walkable corpus in data/")
    return got


class TestTheGeometryIsReadableByOneRule:
    def test_no_new_chunk_leaves_the_file_unannounced(self, walked):
        """A payload range past EOF is either damage the walker reports, or a
        walker that cannot describe its own chunks. Nothing else."""
        new = {}
        for path, label, chunks, warns in walked:
            fsize = os.path.getsize(path)
            for cid, base, size, kind in _overshoots(chunks, fsize):
                if (label, cid) in KNOWN_DEFECTS:
                    continue
                # Damage the walker already called out in the file's own numbers
                # is the tool working, not failing.
                if any(str(size) in w or "remain" in w or "claims" in w
                       for w in warns):
                    continue
                new.setdefault((label, cid), (os.path.basename(path), base,
                                              size, fsize, kind))
        assert not new, (
            "chunks whose payload range leaves the file, with no warning and "
            "no known-defect entry:\n" + "\n".join(
                f"  {lab} {cid!r} in {fn}: {kind} payload {b}+{s} > {fs}"
                for (lab, cid), (fn, b, s, fs, kind) in sorted(new.items())))

    def test_the_known_defects_are_still_real(self, walked):
        """A known-failures list that outlives the failure silently re-freezes
        a bug that was already fixed."""
        still = set()
        for path, label, chunks, _warns in walked:
            fsize = os.path.getsize(path)
            for cid, _b, _s, _k in _overshoots(chunks, fsize):
                still.add((label, cid))
        stale = sorted(k for k in KNOWN_DEFECTS if k not in still)
        assert not stale, (
            f"these no longer overshoot and should leave KNOWN_DEFECTS: {stale}")

    def test_a_declared_payload_base_sits_inside_its_own_chunk(self, walked):
        """payload_base is where the contents begin, so it cannot precede the
        chunk. A walker that puts it earlier is describing something else."""
        bad = []
        for path, label, chunks, _warns in walked:
            for c in chunks:
                off, pb = c.get("offset"), c.get("payload_base")
                if not isinstance(off, int) or pb is None:
                    continue
                if pb < off:
                    bad.append((label, str(c.get("id")), off, pb,
                                os.path.basename(path)))
        assert not bad, f"payload_base before its chunk: {bad[:6]}"


class TestTheRatchetSaysWhatItCovers:
    def test_it_is_measuring_a_real_corpus(self, walked):
        """A ratchet that examines nothing passes forever. The floor is the
        COMMITTED corpus, so it holds in a clone as well as on a machine with
        the gitignored specimens; it exists so a corpus that quietly shrinks --
        a moved fixture, a walker that starts raising -- fails here instead of
        turning every assertion above into a no-op."""
        chunks = sum(len(c) for _p, _l, c, _w in walked)
        assert len(walked) >= 7, f"only {len(walked)} files walked"
        assert chunks >= 56, f"only {chunks} chunks examined"

    def test_its_reach_is_five_walkers_not_thirty(self, walked):
        """Stated rather than implied. A clone's data/ exercises five formats;
        the repo has roughly thirty walkers, so a clean run here is evidence
        about those five and silence about the rest. The gap is corpus, not
        method: every format COMMITTED to data/ widens this automatically, and
        a development machine carrying data/test_formats/ already covers more
        than this asserts."""
        labels = {lab for _p, lab, _c, _w in walked}
        assert len(labels) >= 5, sorted(labels)
        assert {"RIFF/WAVE", "MP4/M4A", "FLAC"} <= labels, sorted(labels)


class TestFieldsLandInsideTheirChunk:
    def test_no_positioned_field_escapes_its_payload(self, walked):
        """The check that would have caught the generic_walk header defect on
        the day it was written, rather than on the day someone measured it."""
        from acidcat.core.infra.fieldcodec import _field_abs
        bad = []
        for path, label, chunks, warns in walked:
            fsize = os.path.getsize(path)
            for c in chunks:
                off, size = c.get("offset"), c.get("size")
                if not isinstance(off, int) or not isinstance(size, int):
                    continue
                base, size = geometry.payload_of(c)
                if base + size > fsize:        # already covered above
                    continue
                for fl in c.get("fields") or []:
                    if fl.get("off") is None:
                        continue
                    at = _field_abs(c, fl)
                    end = at + (fl.get("len") or 0)
                    if at < base or end > base + size:
                        bad.append((label, str(c.get("id")), fl["name"],
                                    at, base, size, os.path.basename(path)))
        assert not bad, (
            "fields outside the chunk that declares them:\n" + "\n".join(
                f"  {lab} {cid!r}.{nm!r} at 0x{at:x}, payload 0x{b:x}+{s}"
                f" ({fn})" for lab, cid, nm, at, b, s, fn in bad[:8]))
