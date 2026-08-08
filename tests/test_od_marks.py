"""Tests for `od --marks` -- the overlay on the raw dump.

The raw dump is the path with no field spans: `--offset`, `--at`, `--region`,
and any file without a walker. It is where someone is actually reverse-
engineering an unknown header, and it is the only hex path acidcat printed with
no colour at all.

Two things have to hold. The overlay must be opt-in, because the existing raw
dump has literal hex assertions on it and pipes read it. And it must run-length
its escapes: this exact code path once printed 1.4 GB of stdout in five and a
half minutes, and a per-byte escape is ~19 bytes on top of every ~3 bytes of
content.
"""

import re
import struct
import subprocess
import sys

import pytest


ESC = re.compile(r"\033\[[0-9;]*m")


def _od(path, *extra):
    out = subprocess.run(
        [sys.executable, "-m", "acidcat", "od", str(path), *extra],
        capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return out.stdout


@pytest.fixture
def riff(tmp_path):
    p = tmp_path / "t.wav"
    payload = b"\x00" * 64
    p.write_bytes(b"RIFF" + struct.pack("<I", 4 + len(payload)) + b"WAVE" + payload)
    return p


def test_marks_are_off_by_default(riff):
    """The raw dump is parsed by eye and by pipe. Colour is a request."""
    plain = _od(riff, "--offset", "0", "--length", "32", "--color", "always")
    assert not ESC.search(plain.split("\n", 1)[1]), "escapes without --marks"


def test_marks_colour_the_dump(riff):
    out = _od(riff, "--offset", "0", "--length", "32", "--marks", "--color", "always")
    assert ESC.search(out)


def test_color_never_suppresses_the_escapes(riff):
    """--marks asks for the overlay; --color says whether a terminal wants
    escapes. The second has to win, or `od --marks | grep` breaks."""
    out = _od(riff, "--offset", "0", "--length", "32", "--marks", "--color", "never")
    assert not ESC.search(out)


def test_the_bytes_are_unchanged_by_the_overlay(riff):
    """Stripping the escapes must give back exactly the uncoloured dump. An
    overlay that shifts a column has broken the thing it decorates."""
    plain = _od(riff, "--offset", "0", "--length", "48", "--color", "never")
    marked = _od(riff, "--offset", "0", "--length", "48", "--marks",
                 "--color", "always")
    stripped = ESC.sub("", marked)
    # the summary line is new; every other line must match byte for byte
    body = [ln for ln in stripped.splitlines() if not ln.startswith("  marks:")]
    assert body == plain.splitlines()


def test_the_summary_says_it_is_inferred(riff):
    """A statistical inference must never read as a decoded fact."""
    out = _od(riff, "--offset", "0", "--length", "32", "--marks", "--color", "never")
    line = [ln for ln in out.splitlines() if ln.startswith("  marks:")]
    assert line, "marks fired but nothing said so"
    assert "inferred" in line[0]


def test_no_summary_when_nothing_is_marked(tmp_path):
    """Silence, not 'marks: none'. The line exists to qualify a claim."""
    p = tmp_path / "n.bin"
    import random
    rng = random.Random(99)
    p.write_bytes(bytes(rng.randrange(256) for _ in range(2048)))
    out = _od(p, "--offset", "0", "--length", "256", "--marks", "--color", "never")
    assert not [ln for ln in out.splitlines() if ln.startswith("  marks:")]


def test_escapes_are_run_length_encoded(tmp_path):
    """A 4 KB run of one byte class must cost O(1) escapes, not O(n).

    Without this the overlay is unusable at exactly the size where the raw
    dump matters: ~19 bytes of escape per ~3 bytes of hex is a 6x blowup on a
    path with a documented history of printing 1.4 GB.
    """
    p = tmp_path / "z.bin"
    p.write_bytes(b"RIFF" + struct.pack("<I", 4100) + b"\x00" * 4096)
    out = _od(p, "--offset", "0", "--length", "4096", "--marks", "--color", "always")
    escapes = len(ESC.findall(out))
    rows = 4096 // 16
    # one style run per row is the floor; allow a few for the header, the
    # summary and the RIFF fields. O(n) in bytes would be 8000+.
    assert escapes < rows * 4, f"{escapes} escapes for {rows} rows -- not run-length"


def test_an_unwalkable_file_gets_marks_too(tmp_path):
    """The no-walker fallback is the other raw path, and the likelier one for
    an unknown format. It went through a separate call site."""
    p = tmp_path / "u.unknown"
    entries = [0x40, 0x80, 0xC0, 0x100, 0x140]
    p.write_bytes(b"ZZZZ" + b"".join(struct.pack("<I", v) for v in entries)
                  + b"\x00" * (0x200 - 24))
    out = _od(p, "--marks", "--color", "never")
    assert "no structural walker" in out
    assert [ln for ln in out.splitlines() if ln.startswith("  marks:")]


def test_the_overlay_is_bounded_and_says_where_it_stopped(tmp_path):
    """annotate() costs ~217 MB per MB of input -- a tag list per byte -- so
    over od's 16 MB auto-dump cap it would ask for gigabytes. It is bounded.

    The bound is the easy half; saying so is the point. A cap reported as a
    complete scan is the bug this project keeps finding in itself.
    """
    from acidcat.commands.od import _MARK_CAP
    p = tmp_path / "big.unknown"
    p.write_bytes(b"ZZZZ" + struct.pack("<I", _MARK_CAP * 2)
                  + b"\x00" * (_MARK_CAP * 2 - 8))
    out = _od(p, "--offset", "0", "--length", str(_MARK_CAP * 2),
              "--marks", "--color", "never")
    summary = [ln for ln in out.splitlines() if ln.startswith("  marks:")]
    assert summary, "a truncated scan reported nothing at all"
    assert f"first {_MARK_CAP:,} of" in summary[0], summary[0]
    # and every byte is still dumped -- the cap is on the overlay, not the dump
    rows = [ln for ln in out.splitlines() if ln.startswith("  0x")]
    assert len(rows) == (_MARK_CAP * 2) // 16


def test_an_unbounded_dump_that_fits_says_nothing_about_a_cap(riff):
    """The qualifier must only appear when it is true, or it is noise."""
    out = _od(riff, "--offset", "0", "--length", "32", "--marks", "--color", "never")
    summary = [ln for ln in out.splitlines() if ln.startswith("  marks:")]
    assert summary and "first" not in summary[0], summary


def test_an_unaligned_window_still_scans_the_files_grid(tmp_path):
    """`--offset 2` hands over a window whose first byte is not 4-aligned in
    the file. If the scan counts from the window instead, every u32 is read out
    of phase and a real table goes unmarked. annotate takes base_off for this;
    the point of the test is that od passes it."""
    p = tmp_path / "tbl.unknown"
    entries = [0x40, 0x80, 0xC0, 0x100, 0x140]
    p.write_bytes(b"\xff" * 4                       # table starts at absolute 4
                  + b"".join(struct.pack("<I", v) for v in entries)
                  + b"\x00" * (0x200 - 24))
    out = _od(p, "--offset", "2", "--length", "64", "--marks", "--color", "never")
    summary = [ln for ln in out.splitlines() if ln.startswith("  marks:")]
    assert summary, "the offset table went unmarked from an unaligned window"
    assert "offsets" in summary[0]


def test_a_walked_file_is_not_overlaid(riff):
    """A walked file already colours by decoded field. Two channels claiming
    foreground on the same bytes is the collision this design avoids -- so on
    the structured path --marks does nothing rather than fighting."""
    out = _od(riff, "--marks", "--color", "never")
    assert "chunks" in out.splitlines()[0], "expected the structured layout"
    assert not [ln for ln in out.splitlines() if ln.startswith("  marks:")]
