"""A summary must describe what was actually examined.

Every case here is the same defect wearing different clothes: work was skipped
-- capped, unreadable, unparseable, or crashed -- and the report presented the
remainder as the whole answer. For a forensic tool that is worse than an error,
because the user has no reason to look further.
"""

import os
import struct

import pytest

from acidcat.cli import main


def _wav(n_frames=32):
    body = (b"fmt " + struct.pack("<I", 16)
            + struct.pack("<HHIIHH", 1, 1, 44100, 88200, 2, 16)
            + b"data" + struct.pack("<I", n_frames * 2) + bytes(n_frames * 2))
    return b"RIFF" + struct.pack("<I", len(body) + 4) + b"WAVE" + body


def test_survey_distinguishes_absent_from_unreadable(tmp_path, capsys):
    """"no RIFF/WAV files found" for a directory of three WAVs was two false
    statements at once. For a specimen hunter, unparseable is the find."""
    for n in ("b1.wav", "b2.wav", "b3.wav"):
        (tmp_path / n).write_bytes(b"RIFF\x10\x00\x00\x00WAVEjunk")
    main(["survey", str(tmp_path)])
    out = capsys.readouterr().out
    assert "3 unparseable" in out
    assert "none readable as RIFF" in out
    assert "no RIFF/WAV files found" not in out


def test_survey_counts_unparseable_alongside_good_files(tmp_path, capsys):
    (tmp_path / "good.wav").write_bytes(_wav())
    (tmp_path / "bad.wav").write_bytes(b"RIFF\x10\x00\x00\x00WAVEjunk")
    main(["survey", str(tmp_path)])
    out = capsys.readouterr().out
    assert "1 WAV files scanned, 1 unparseable" in out


def test_survey_says_when_the_cap_stopped_it(tmp_path, capsys):
    for i in range(4):
        (tmp_path / f"f{i}.wav").write_bytes(_wav())
    main(["survey", str(tmp_path), "-n", "2"])
    assert "stopped at the -n 2 cap" in capsys.readouterr().out


def test_validate_does_not_call_a_library_consistent_when_files_were_unreadable(
        tmp_path, capsys, monkeypatch):
    """A CI job over a library with locked files printed "all N consistent"
    and exited 0."""
    (tmp_path / "ok.wav").write_bytes(_wav())
    (tmp_path / "locked.wav").write_bytes(_wav())

    real_open = open

    def deny(path, *a, **k):
        if str(path).endswith("locked.wav"):
            raise PermissionError(13, "Permission denied", str(path))
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", deny)
    rc = main(["validate", str(tmp_path)])
    out = capsys.readouterr().out
    assert "unreadable (not checked)" in out
    assert rc != 0, "an unchecked file exited as a clean pass"


def test_a_crashed_anomaly_rule_is_reported_not_erased():
    """Four rules were `except Exception: pass`, so a malformed structure
    removed the check and read as a clean file."""
    from acidcat.core.forensics import anomalies
    src = open(anomalies.__file__, encoding="utf-8").read()
    assert "except Exception:\n            pass" not in src, \
        "an anomaly rule still swallows its own failure"
    assert src.count("check_failed") >= 4


def test_check_failed_findings_say_what_was_not_screened(tmp_path):
    """The message must name the check, or the warning is unactionable."""
    from acidcat.core.forensics import anomalies
    src = open(anomalies.__file__, encoding="utf-8").read()
    for rule in ("ogg_multistream", "mp4_mdat_coverage", "id3_padding",
                 "dual_endian_pcm"):
        assert f"the {rule} check could not run" in src
