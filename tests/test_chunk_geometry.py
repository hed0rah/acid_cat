"""Every chunk has to say which bytes it occupies, in a way one rule can read.

There is no such rule today. `_field_abs` documents the closest thing to one --
field offsets are relative to `payload_base`, else `offset + 8`
(core/infra/fieldcodec.py:39) -- and nine sites across six modules re-derive it
independently, spelled four different ways, including two that compute an end as
`base + size + 8`. Six consumers guessing at the same geometry is not a
convention, it is a coincidence that has held so far.

It has not entirely held. Measured over 47 walked files in data/:

    196 chunks declare a payload_base
     72 rely on the +8 default
     28 put their payload range past the end of the file

and that last 28 splits along a line worth keeping sharp:

  - A DECLARED extent running past EOF on a deliberately truncated fixture is a
    finding about the FILE. RIFF/WAVE reports exactly that, in the file's own
    numbers ("claims 176,400 bytes but only 79,922 remain"). Correct behaviour.
  - MP4's `size` is the TOTAL box size including the 8-byte header, while its
    `payload_base` is `offset + 8`. The pair is self-inconsistent, so every
    container box overshoots by exactly 8. That is a walker DECLARATION defect,
    not damage.

The difference between those two is the whole reason this file exists: one is
the tool doing its job and the other is the tool being wrong, and under a single
`size` key they look identical from the outside.

This is a ratchet, not a gate. The known set below is the measured state as of
today; it may shrink and must never grow.
"""

import glob
import os

import pytest

from acidcat.core.walk import walk_file

_MAX = 8_000_000


# (walker label, chunk id) -> why this one is known-wrong. Shrink only.
KNOWN_DEFECTS = {
    ("MP4/M4A", "moov"): "size is the total box size including the 8-byte "
                         "header while payload_base is offset+8, so the pair "
                         "overshoots by 8 on every container box",
    ("MP4/M4A", "udta"): "as moov",
    ("MP4/M4A", "meta"): "as moov",
    ("MP4/M4A", "ilst"): "as moov",
    ("MP4/M4A", "©too"): "as moov",
    ("FLAC", "frames"): "the frames pseudo-chunk runs to EOF, so the +8 "
                        "default puts its payload 8 bytes past the file",
    ("FLAC", "PADDING"): "a truncated FLAC whose PADDING block claims 8,192 "
                         "bytes inside a 200-byte file, and the walker emits "
                         "no warning at all -- the damage is real, the silence "
                         "about it is the defect",
}


def _corpus():
    for path in sorted(glob.glob("data/**/*.*", recursive=True)):
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
    """Chunks whose payload range, read by the documented rule, leaves the file."""
    out = []
    for c in chunks:
        off, size = c.get("offset"), c.get("size")
        if not isinstance(off, int) or not isinstance(size, int):
            continue
        pb = c.get("payload_base")
        base = pb if pb is not None else off + 8
        if base + size > fsize:
            out.append((str(c.get("id")), base, size,
                        "declared" if pb is not None else "defaulted"))
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
        """A ratchet that examines nothing passes forever. These numbers are the
        measured state; they exist so a corpus that quietly shrinks -- a moved
        fixture, a walker that starts raising -- fails here instead of turning
        every assertion above into a no-op."""
        chunks = sum(len(c) for _p, _l, c, _w in walked)
        assert len(walked) >= 40, f"only {len(walked)} files walked"
        assert chunks >= 250, f"only {chunks} chunks examined"

    def test_its_reach_is_seven_walkers_not_thirty(self, walked):
        """Stated rather than implied. data/ exercises seven formats; the repo
        has roughly thirty walkers, so a clean run here is evidence about those
        seven and silence about the rest. The gap is corpus, not method: every
        format added to data/ widens this automatically."""
        labels = {lab for _p, lab, _c, _w in walked}
        assert len(labels) >= 7, sorted(labels)
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
                pb = c.get("payload_base")
                base = pb if pb is not None else off + 8
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
