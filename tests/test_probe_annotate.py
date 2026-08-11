"""Tests for `probe.annotate` -- per-byte tags for a raw hex view.

The whole point of the function is the path where acidcat has no walker: an
unknown header, a carved region, `od --offset`. Nothing there is checked, so
every tag it emits is an inference, and the failure that matters is a mark that
fires on bytes that mean nothing -- a confident wrong answer is worse than the
uncoloured dump it replaces.
"""

import os
import struct

from acidcat.core import probe


def _riff(payload=b"\x00" * 32):
    """A minimal RIFF: the size field at offset 4 counts everything after it,
    so it is file_size - 8. That convention is most of why the mark exists."""
    return b"RIFF" + struct.pack("<I", 4 + len(payload)) + b"WAVE" + payload


def test_class_separates_what_ascii_cannot():
    """00, FF and 80 are three identical dots in the ascii column. 0x7F is DEL
    and classes as `high`, not `ctrl` -- printable is 0x20..0x7E, and this
    matches viz.byte_class so a dump and the Hilbert map never disagree."""
    tags = probe.annotate(bytes([0x00, 0xFF, 0x0A, 0x80, 0x41, 0x7F]), file_size=6)
    got = [t[0].split(":")[1] for _, t in sorted(tags.items())]
    assert got == ["null", "ff", "ctrl", "high", "ascii", "high"]


def test_the_riff_size_field_is_marked():
    data = _riff()
    tags = probe.annotate(data, file_size=len(data))
    marked = {p for p, t in tags.items() if "mark:size" in t}
    assert marked == {4, 5, 6, 7}, f"expected the size field, got {sorted(marked)}"


def test_a_size_field_is_found_in_either_byte_order():
    """A big-endian container (AIFF, most 68k-era formats) has the same field."""
    data = b"FORM" + struct.pack(">I", 36) + b"AIFF" + b"\x00" * 32
    tags = probe.annotate(data, file_size=len(data))
    assert {p for p, t in tags.items() if "mark:size" in t} == {4, 5, 6, 7}


def test_a_value_past_eof_is_not_a_size():
    """The mark means 'this equals the file size', not 'this is a u32'."""
    data = b"RIFF" + struct.pack("<I", 0x7FFFFFFF) + b"WAVE" + b"\x00" * 32
    tags = probe.annotate(data, file_size=len(data))
    assert not any("mark:size" in t for t in tags.values())


def test_an_offset_table_is_marked_as_a_run():
    """Four ascending in-file u32s. Narrower than 'could be an offset', which
    for any real file is true of most small integers."""
    entries = [0x40, 0x80, 0xC0, 0x100, 0x140, 0x180]
    data = b"TBL!" + b"".join(struct.pack("<I", v) for v in entries)
    data += b"\x00" * (0x200 - len(data))   # every entry has to land in-file
    tags = probe.annotate(data, file_size=len(data))
    marked = {p for p, t in tags.items() if "mark:table" in t}
    assert marked == set(range(4, 4 + 4 * len(entries)))


def test_three_entries_are_not_a_table():
    """Below the run minimum, ascending small integers are just integers."""
    data = b"TBL!" + b"".join(struct.pack("<I", v) for v in (0x10, 0x20, 0x30))
    data += b"\xff" * 64
    tags = probe.annotate(data, file_size=len(data))
    assert not any("mark:table" in t for t in tags.values())


def test_a_descending_run_is_not_a_table():
    entries = [0x180, 0x140, 0x100, 0xC0, 0x80, 0x40]
    data = b"TBL!" + b"".join(struct.pack("<I", v) for v in entries)
    data += b"\xff" * (0x200 - len(data))
    tags = probe.annotate(data, file_size=len(data))
    assert not any("mark:table" in t for t in tags.values())


def test_noise_does_not_light_up():
    """The precedent is test_triage.py: an inference that fires on random bytes
    is not an inference. Deterministic seed so a failure is reproducible."""
    import random
    rng = random.Random(1337)
    data = bytes(rng.randrange(256) for _ in range(4096))
    tags = probe.annotate(data, file_size=len(data))
    marks = sum(1 for t in tags.values() if any(x.startswith("mark:") for x in t))
    assert marks == 0, f"{marks} of 4096 random bytes were marked"


def test_alignment_follows_the_absolute_offset():
    """A window starting mid-file is still scanned on the FILE's 4-byte grid.
    `od --offset 0x12` hands over a window whose first byte is not aligned, and
    a scan that starts counting from the window instead reads every u32 two
    bytes out of phase -- which finds nothing, or worse, finds junk."""
    entries = [0x40, 0x80, 0xC0, 0x100]
    payload = b"".join(struct.pack("<I", v) for v in entries)
    data = b"\xff" * 4 + payload           # the table sits at absolute 4
    data += b"\x00" * (0x200 - len(data))
    window = data[2:]                       # ... so at 2 within this window
    tags = probe.annotate(window, base_off=2, file_size=len(data))
    assert {p for p, t in tags.items() if "mark:table" in t} == set(range(2, 18))
    # and without base_off the scan is out of phase and sees nothing
    blind = probe.annotate(window, file_size=len(data))
    assert not any("mark:table" in t for t in blind.values())


def test_marks_off_gives_classes_only():
    data = _riff()
    tags = probe.annotate(data, file_size=len(data), marks=False)
    assert tags, "classes should still be emitted"
    assert not any(x.startswith("mark:") for t in tags.values() for x in t)


def test_no_file_size_means_no_marks():
    """Both marks are defined against the file size. Without one they are not
    weaker inferences, they are undefined -- so emit nothing."""
    data = _riff()
    tags = probe.annotate(data)
    assert not any(x.startswith("mark:") for t in tags.values() for x in t)


def test_every_position_is_classified():
    data = _riff()
    tags = probe.annotate(data, file_size=len(data))
    assert set(tags) == set(range(len(data)))


def test_it_marks_a_real_wav_on_disk():
    """The hand-built header above encodes my understanding of RIFF; this
    checks the understanding against a file something else wrote."""
    path = "data/test_formats/generated/src.wav"
    if not os.path.isfile(path):
        import pytest
        pytest.skip("generated wav corpus absent")
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        head = f.read(256)
    tags = probe.annotate(head, file_size=size)
    assert "mark:size" in tags.get(4, []), "the RIFF size field went unmarked"
