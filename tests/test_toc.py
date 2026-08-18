"""Finding the index in a container acidcat has never heard of.

The design line this sits on: acidcat supports audio formats, major and
obscure, and deliberately does not try to know what every *container* is. What
a game mod archive or a console disc happens to be is not its business. Being
able to open one anyway and find the audio inside is.

A signature sweep does that already. But a large family of archives carries
something better than a signature -- an index -- and the shape recurs because
it is the obvious way to write one:

    <length> <name> <fixed-width integer fields> <length> <name> ...

Finding it turns an anonymous blob into a named list, which is the difference
between "22 regions" and "Sounds/Music/AnahitasLure.ogg". None of this knows
any format: the evidence is that a layout keeps predicting where the next entry
begins, and a wrong one stops within an entry or two.
"""

import io
import random
import struct

import pytest

from acidcat.core.forensics import toc


def _varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _table(names, sizes, prefix="varint", nfields=2):
    """A table of contents in the shape this detects."""
    out = b""
    for name, size in zip(names, sizes):
        raw = name.encode()
        if prefix == "varint":
            out += _varint(len(raw))
        elif prefix == "u8":
            out += bytes([len(raw)])
        elif prefix == "u16le":
            out += struct.pack("<H", len(raw))
        out += raw
        fields = [size] * nfields
        out += b"".join(struct.pack("<i", f) for f in fields)
    return out


_NAMES = [f"Sounds/Music/Track{i:02d}.ogg" for i in range(20)]


class TestFindingTheTable:
    def test_it_finds_a_table_and_its_layout(self):
        data = b"HDR\x00" * 8 + _table(_NAMES, [1000] * 20)
        found = toc.find_toc(data)
        assert found is not None
        assert found["prefix"] == "varint"
        assert found["fields"] == 2
        assert len(found["entries"]) == 20
        assert found["entries"][0]["name"] == _NAMES[0]

    def test_it_reads_a_u8_prefix_too(self):
        data = b"\x00" * 16 + _table(_NAMES, [500] * 20, prefix="u8")
        found = toc.find_toc(data)
        assert found and len(found["entries"]) == 20

    def test_a_short_run_is_not_a_table(self):
        """Chance produces short chains. The threshold is measured: over 137
        ordinary audio files the longest accidental chain was 7."""
        data = b"\x00" * 16 + _table(_NAMES[:5], [100] * 5)
        assert toc.find_toc(data) is None

    def test_random_bytes_are_not_a_table(self):
        random.seed(9)
        data = bytes(random.randrange(256) for _ in range(200000))
        assert toc.find_toc(data) is None

    def test_text_is_not_a_table(self):
        """Prose is full of printable runs with dots in them, which is exactly
        what the name pattern matches."""
        data = (b"The quick brown fox. Jumps over the lazy dog. "
                b"See also: readme.txt and notes.md for details. ") * 200
        found = toc.find_toc(data)
        assert found is None or len(found["entries"]) < 12

    def test_the_confidence_never_reaches_a_signature_match(self):
        """This is a shape, not a magic number. It must never outrank a
        container whose signature actually verified (those sit at 0.9)."""
        data = b"\x00" * 16 + _table(_NAMES * 30, [10] * 600)
        found = toc.find_toc(data)
        assert found and found["confidence"] <= 0.85


class TestPlacingTheEntries:
    """The table says how big things are, not always where. An archive that
    writes its index then its payloads back to back makes that derivable."""

    def _archive(self, names, payloads, nfields=2, size_field=1):
        """Table then payloads, with the size in one chosen field and a decoy
        in the other -- so picking the right field is a real question."""
        table = b""
        for name, body in zip(names, payloads):
            raw = name.encode()
            fields = [len(body) * 7 + 3] * nfields      # decoy
            fields[size_field] = len(body)
            table += _varint(len(raw)) + raw
            table += b"".join(struct.pack("<i", f) for f in fields)
        return table + b"".join(payloads)

    def test_it_picks_the_field_that_verifies(self):
        payloads = [b"OggS" + bytes([0, 2]) + b"\x00" * 200 for _ in range(20)]
        blob = self._archive(_NAMES, payloads, size_field=1)
        found = toc.find_toc(blob)
        assert found
        entries, field, verified, checked = toc.place_entries(
            io.BytesIO(blob), found)
        assert field == 1, "picked a field that does not place the payloads"
        assert verified == checked == 20

    def test_the_placed_offsets_are_right(self):
        payloads = [b"OggS" + bytes([0, 2]) + bytes([i]) * 100
                    for i in range(20)]
        blob = self._archive(_NAMES, payloads, size_field=1)
        found = toc.find_toc(blob)
        entries, _f, _v, _c = toc.place_entries(io.BytesIO(blob), found)
        for e, body in zip(entries, payloads):
            assert blob[e["offset"]:e["offset"] + len(body)] == body

    def test_it_reports_a_failure_rather_than_guessing(self):
        """Payloads that are not contiguous after the table cannot be placed.
        Saying so beats returning offsets that point at nothing."""
        payloads = [b"OggS\x00\x02" + b"\x00" * 50 for _ in range(20)]
        blob = self._archive(_NAMES, payloads, size_field=1)
        blob = blob.replace(b"OggS", b"XXXX")      # nothing will verify now
        found = toc.find_toc(blob)
        entries, field, verified, checked = toc.place_entries(
            io.BytesIO(blob), found)
        assert verified == 0

    def test_entries_without_a_known_extension_are_not_counted(self):
        """Verification needs something checkable. Files acidcat has no magic
        for are placed but contribute no evidence either way."""
        names = [f"data/blob{i:02d}.xyz" for i in range(20)]
        payloads = [b"\x00" * 40 for _ in range(20)]
        blob = self._archive(names, payloads, size_field=1)
        found = toc.find_toc(blob)
        assert found
        _e, _f, verified, checked = toc.place_entries(io.BytesIO(blob), found)
        assert checked == 0 and verified == 0


def test_a_real_archive_end_to_end():
    """The specimen this was built against, if it is on this machine.

    266 entries, 64 of them .ogg, every one landing on OggS -- and the offsets
    match, byte for byte, what the Ogg stream sweep finds independently.
    """
    import os
    # Opt-in via the environment rather than one machine's drive letter, which
    # is configuration wearing the clothes of a constant.
    path = os.environ.get("ACIDCAT_TMOD_SPECIMEN", "")
    if not (path and os.path.isfile(path)):
        pytest.skip("set ACIDCAT_TMOD_SPECIMEN to a .tmod archive")
    with open(path, "rb") as fh:
        head = fh.read(1 << 21)
        found = toc.find_toc(head)
        assert found and len(found["entries"]) == 266
        entries, field, verified, checked = toc.place_entries(fh, found)
    assert checked == 64 and verified == 64, f"{verified}/{checked} verified"
    oggs = [e for e in entries if e["name"].lower().endswith(".ogg")]
    assert oggs[0]["offset"] == 0x0000bb31


class TestItDoesNotFindTablesInSmoothBytes:
    """A wrong table is worse than no table.

    freedoom1.wad returned a hundred-entry table at confidence 0.84 whose names
    were the CONCRETE wall texture. Eight-bit image data sits in a narrow band
    of byte values, that band overlaps printable ASCII, and 0x5C is an ordinary
    pixel -- so a run of graphics satisfied "looks like a Windows path". The
    same is true of 8-bit audio, which means a detector built to find audio
    inside unknown containers was being fooled BY the audio.

    The specimen is 2,048 real bytes of that texture rather than a ramp written
    here. That distinction is the reason this class exists in the form it does:
    the first version generated its own smooth data, and mutation testing showed
    every assertion still passed with the fix removed, because synthetic ramps
    trip the OTHER guards. Only the real bytes exercise the real hole.

    (Freedoom is 3-clause BSD; the notice ships beside the specimen in
    data/fixtures/toc-false-positive.bin.txt.)
    """

    def _specimen(self):
        import os
        p = os.path.join(os.path.dirname(__file__), "..", "data", "fixtures",
                         "toc-false-positive.bin")
        with open(p, "rb") as fh:
            return fh.read()

    def test_a_wall_texture_is_not_a_table_of_contents(self):
        assert toc.find_toc(self._specimen()) is None

    def test_the_specimen_still_reproduces_the_hole_it_was_kept_for(self):
        """If the bytes stop being able to fool the OLD rule, the test above
        passes for the wrong reason and is quietly measuring nothing."""
        blob = self._specimen()
        keep = toc._MIN_BYTE_SPAN
        try:
            toc._MIN_BYTE_SPAN = 0          # the pre-fix behaviour
            got = toc.find_toc(blob)
        finally:
            toc._MIN_BYTE_SPAN = keep
        assert got is not None and len(got["entries"]) >= 12, (
            "the specimen no longer trips the original defect, so the guard "
            "above is unverified")

    def test_the_names_it_used_to_invent_were_the_pixels(self):
        """Per NAME, not per file. The lump as a whole spans 0..255 because
        Doom's picture format puts 0xff/0x00/0x80 markers between columns; it
        is the printable RUNS between those markers that the detector reads as
        names, and those are what sit in a narrow band."""
        import re
        runs = re.findall(rb"[ -~]{16,}", self._specimen())
        assert runs, "no printable runs, so this specimen proves nothing"
        longest = max(runs, key=len)
        assert max(longest) - min(longest) < 64, (
            "a narrow band of byte values is the whole reason this fooled a "
            "printable-ASCII test; got %d" % (max(longest) - min(longest)))

    def test_a_real_table_still_reads(self):
        """The guard must not have closed the door on the thing it protects."""
        import struct
        blob = bytearray()
        for i in range(40):
            nm = ("Sounds/Music/Track%02d.ogg" % i).encode()
            blob += bytes([len(nm)]) + nm + struct.pack("<i", 1000 + i * 50)
        got = toc.find_toc(bytes(blob))
        assert got is not None and len(got["entries"]) >= 40
        assert got["entries"][0]["name"].startswith("Sounds/Music/")

    def test_confidence_reflects_the_names_not_just_the_chain(self):
        """0.84 was chain length alone. A long chain only proves the layout is
        self-consistent, and smooth bytes are extremely self-consistent."""
        import struct
        good = bytearray()
        for i in range(60):
            nm = ("assets/audio/hit%02d.wav" % i).encode()
            good += bytes([len(nm)]) + nm + struct.pack("<i", i * 32)
        got = toc.find_toc(bytes(good))
        assert got is not None
        assert got["confidence"] > 0.5, got["confidence"]
        assert got["confidence"] <= 0.85, "must never outrank a signature match"
