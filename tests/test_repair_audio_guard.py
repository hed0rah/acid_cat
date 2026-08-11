"""`repair` must never make audio unreadable.

The defect this pins, found by an adversarial audit and reproduced before
fixing: a recorder that dies mid-take leaves a `data` chunk whose declared size
overruns the file. Every player reads the audio that is there. `acidcat repair
--overwrite` recomputed the master size to 28 bytes -- `WAVE` plus `fmt ` --
leaving the whole payload outside the container. 882,000 bytes readable by the
stdlib `wave` module before; unreadable after; exit code 0; no backup with
`--overwrite`; and `validate` then reported "all 1 file(s) consistent".

Why the existing guard missed it: `AudioGuardError` compares the audio payload
before and after, but `structure.parse` leaves an overrunning chunk in
`node.tail` rather than `node.children`, so the payload was None on both sides
and the comparison passed vacuously.

`write` already refused these files ("chunk b'data' overruns the file; refusing
to rewrite"). The destructive verb was the one without the guard.
"""

import struct
import wave

import pytest

from acidcat.cli import main


def _wav(declared_data_size, actual_frames=2000, ch=2, sr=44100):
    """A WAV whose `data` header may claim more than the file holds."""
    pcm = bytes(actual_frames * ch * 2)
    body = (b"WAVE"
            + b"fmt " + struct.pack("<I", 16)
            + struct.pack("<HHIIHH", 1, ch, sr, sr * ch * 2, ch * 2, 16)
            + b"data" + struct.pack("<I", declared_data_size) + pcm)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def _truncated(tmp_path, name="crashed.wav"):
    """The real-world case: header says 60 s, the recorder wrote 2000 frames."""
    p = tmp_path / name
    p.write_bytes(_wav(declared_data_size=44100 * 2 * 2 * 60))
    return p


def _readable_bytes(path):
    with wave.open(str(path)) as w:
        return len(w.readframes(10 ** 9))


def test_repair_refuses_rather_than_orphaning_the_audio(tmp_path, capsys):
    p = _truncated(tmp_path)
    before = _readable_bytes(p)
    assert before > 0

    rc = main(["repair", "--overwrite", str(p)])
    assert rc != 0, "repair reported success while destroying the audio"
    assert _readable_bytes(p) == before, "the audio is no longer readable"
    err = capsys.readouterr().err
    assert "overruns the file" in err
    assert "Nothing written" in err


def test_the_file_is_untouched_byte_for_byte(tmp_path):
    """Refusing must mean refusing, not a partial write."""
    p = _truncated(tmp_path)
    original = p.read_bytes()
    main(["repair", "--overwrite", str(p)])
    assert p.read_bytes() == original


def test_validate_does_not_certify_an_orphaning_file_as_consistent(tmp_path, capsys):
    """It reported "all 1 file(s) consistent" for a file whose payload sits
    outside its container."""
    p = _truncated(tmp_path)
    rc = main(["validate", str(p)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "consistent" not in out
    assert "outside the container" in out


def test_validate_does_not_advertise_a_repair_that_will_refuse(tmp_path, capsys):
    """The size change is the destructive one, so it must stop presenting
    itself as repairable -- otherwise the user is sent round a loop."""
    p = _truncated(tmp_path)
    main(["validate", str(p)])
    assert "fix with: acidcat repair" not in capsys.readouterr().out


def test_a_genuinely_repairable_file_still_gets_repaired(tmp_path, capsys):
    """The guard must be narrow. A stale master size where every chunk fits is
    the case repair exists for."""
    p = tmp_path / "stale.wav"
    good = _wav(declared_data_size=2000 * 2 * 2)
    p.write_bytes(b"RIFF" + struct.pack("<I", 999) + good[8:])   # wrong master size
    before = _readable_bytes(p)
    full = 2000 * 2 * 2

    # the undersized master size hides most of the payload from a conformant
    # reader, which is exactly what repair is for
    assert before < full

    assert main(["repair", "--overwrite", str(p)]) == 0
    assert _readable_bytes(p) == full, "repair did not recover the payload"
    assert main(["validate", str(p)]) == 0


def test_a_healthy_file_is_untouched_and_passes(tmp_path):
    p = tmp_path / "ok.wav"
    p.write_bytes(_wav(declared_data_size=2000 * 2 * 2))
    original = p.read_bytes()
    assert main(["validate", str(p)]) == 0
    assert main(["repair", "--overwrite", str(p)]) == 0
    assert p.read_bytes() == original


def test_aiff_is_guarded_too(tmp_path):
    """Not a WAV quirk -- SSND overruns the same way."""
    ssnd_declared = 44100 * 2 * 2 * 60
    body = (b"AIFF"
            + b"COMM" + struct.pack(">I", 18)
            + struct.pack(">hIh", 2, 2000, 16) + b"\x40\x0e\xac\x44" + b"\x00" * 6
            + b"SSND" + struct.pack(">I", ssnd_declared)
            + struct.pack(">II", 0, 0) + bytes(2000 * 2 * 2))
    p = tmp_path / "crashed.aiff"
    p.write_bytes(b"FORM" + struct.pack(">I", len(body)) + body)
    original = p.read_bytes()

    assert main(["repair", "--overwrite", str(p)]) != 0
    assert p.read_bytes() == original, "AIFF audio was orphaned"


def test_the_orphan_guard_is_reported_as_unrepairable():
    """A violation with no safe rewrite must carry no witness, or every caller
    that keys off `repairable` will offer to fix it."""
    from acidcat.core.write import constraints
    data = _wav(declared_data_size=44100 * 2 * 2 * 60)
    report = constraints.analyze(data)
    assert report.violations
    assert not any(v.repairable for v in report.violations)
