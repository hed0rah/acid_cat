"""Tests for the container walkers: CUE sheets and GameCube disc images.

A container holds other things, so its walk answers a different question from a
stream's. Nobody opens a disc image to learn about the image; they open it to
learn what is inside and where. Both readers here already existed and `extract`
was using them, so the structure was being computed and thrown away.

The CUE case is the odd one and worth stating plainly: its positions are in
SECTORS, and they are relative to a binary the sheet names but does not
contain. It is the only format here whose offsets point outside the file being
walked, which is why whether that binary is actually present is reported rather
than assumed.
"""
import os
import struct

import pytest

from acidcat.core.infra import geometry, sniff
from acidcat.core.walk import containers


def _cue(tmp_path, binary="GAME.bin", tracks=(("01", "MODE1/2352", "00:00:00"),
                                              ("02", "AUDIO", "01:33:54"),
                                              ("03", "AUDIO", "03:00:00")),
         make_binary=False):
    lines = ['FILE "%s" BINARY' % binary]
    for num, kind, msf in tracks:
        lines += ["  TRACK %s %s" % (num, kind), "    INDEX 01 %s" % msf]
    p = tmp_path / "disc.cue"
    p.write_text("\n".join(lines) + "\n", encoding="latin-1")
    if make_binary:
        (tmp_path / binary).write_bytes(b"\x00" * 2352)
    return str(p)


def _gcm(tmp_path, name="MUSIC.HPS", body=b"AUDIODATA"):
    from test_gamecube import _gcm_image
    p = tmp_path / "disc.gcm"
    p.write_bytes(_gcm_image(name, body))
    return str(p)


# ── identification and coverage ─────────────────────────────────────

def test_a_cue_is_identified_and_walked(tmp_path):
    path = _cue(tmp_path)
    assert sniff.sniff(path) == "cue"
    chunks, _warns = containers.inspect_cue(path)
    assert chunks[0]["id"] == "sheet"


def test_a_gcm_is_identified_and_walked(tmp_path):
    path = _gcm(tmp_path)
    assert sniff.sniff(path) == "gcm"
    chunks, _warns = containers.inspect_gcm(path)
    assert chunks[0]["id"] == "header"


@pytest.mark.parametrize("build,walk", [(_cue, containers.inspect_cue),
                                        (_gcm, containers.inspect_gcm)],
                         ids=["cue", "gcm"])
def test_every_byte_is_accounted_for(tmp_path, build, walk):
    path = build(tmp_path)
    chunks, _warns = walk(path)
    size = os.path.getsize(path)
    geometry.normalize(chunks, size)
    assert all(geometry.is_trustworthy(c) for c in chunks)
    covered = sum(c["payload_len"] + (c["payload_base"] - c["offset"])
                  for c in chunks)
    assert covered == size, (covered, size)


# ── CUE: positions that point outside the file ──────────────────────

def test_a_cue_says_when_the_binary_it_indexes_is_absent(tmp_path):
    """Every position in a sheet is relative to a file the sheet does not
    contain. If that file is missing the numbers are still printable and mean
    nothing, so the absence is reported rather than left to be discovered."""
    path = _cue(tmp_path, make_binary=False)
    _chunks, warns = containers.inspect_cue(path)
    assert any("not beside it" in w for w in warns), warns


def test_a_cue_is_quiet_when_the_binary_is_present(tmp_path):
    """The control. A warning that fires either way is not a warning."""
    path = _cue(tmp_path, make_binary=True)
    _chunks, warns = containers.inspect_cue(path)
    assert not any("not beside it" in w for w in warns), warns


def test_cue_positions_are_sectors_converted_to_time(tmp_path):
    """75 sectors per second, Red Book. A cue writes minutes:seconds:frames and
    the frames are sectors, not video frames."""
    path = _cue(tmp_path, tracks=(("01", "AUDIO", "00:02:00"),))
    chunks, _ = containers.inspect_cue(path)
    t = [f for f in chunks[0]["fields"] if f["name"] == "track 01"][0]
    assert "00:02:00" in t["value"]
    assert "sector 150" in t["note"], t["note"]      # 2 s x 75
    assert "2.0 s in" in t["note"]


def test_cue_separates_audio_tracks_from_data(tmp_path):
    """A ripper takes the AUDIO tracks and nothing else, so the count that
    matters is not the track count."""
    path = _cue(tmp_path)
    chunks, _ = containers.inspect_cue(path)
    got = {f["name"]: f["value"] for f in chunks[0]["fields"]}
    assert got["tracks"] == 3 and got["audioTracks"] == 2


def test_an_unparseable_cue_is_reported_not_raised(tmp_path):
    p = tmp_path / "bad.cue"
    p.write_text('FILE "" BINARY\n  TRACK 01 AUDIO\n', encoding="latin-1")
    chunks, warns = containers.inspect_cue(str(p))
    assert chunks and warns
    assert "did not parse" in chunks[0]["summary"]


# ── GCM: describing the index, not the image ────────────────────────

def test_gcm_reads_the_file_table_without_loading_the_image(tmp_path):
    path = _gcm(tmp_path, name="MUSIC.HPS")
    chunks, _warns = containers.inspect_gcm(path)
    got = {f["name"]: f["value"] for f in chunks[0]["fields"]}
    assert got["files"] == "1"
    assert got["audioFiles"] == "1", "an .hps should be recognised as audio"
    assert any(f["name"] == "MUSIC.HPS" for f in chunks[0]["fields"])


def test_gcm_does_not_count_a_non_audio_file_as_audio(tmp_path):
    """The control for the count above."""
    path = _gcm(tmp_path, name="README.TXT")
    chunks, _ = containers.inspect_gcm(path)
    got = {f["name"]: f["value"] for f in chunks[0]["fields"]}
    assert got["files"] == "1" and got["audioFiles"] == "0"


def test_a_truncated_gcm_header_is_reported_not_raised(tmp_path):
    p = tmp_path / "short.gcm"
    p.write_bytes(b"\x00" * 64)
    chunks, warns = containers.inspect_gcm(str(p))
    assert chunks and warns
    assert "truncated" in chunks[0]["summary"]


def test_gcm_flags_a_file_table_declared_past_the_end(tmp_path):
    from test_gamecube import _gcm_image
    blob = bytearray(_gcm_image("A.HPS", b"X"))
    struct.pack_into(">I", blob, 0x424, 0x7FFFFFFF)
    p = tmp_path / "liar.gcm"
    p.write_bytes(bytes(blob))
    _chunks, warns = containers.inspect_gcm(str(p))
    assert any("past the end" in w for w in warns), warns


@pytest.mark.parametrize("n", [0, 8, 64, 0x400, 0x43F])
def test_truncation_at_any_depth_does_not_raise(tmp_path, n):
    from test_gamecube import _gcm_image
    p = tmp_path / "t.gcm"
    p.write_bytes(_gcm_image("A.HPS", b"X")[:n])
    try:
        containers.inspect_gcm(str(p))
    except Exception as exc:                 # noqa: BLE001 - that IS the assertion
        pytest.fail("truncation to %d bytes raised %r" % (n, exc))


# ── the real sheet the repository carries ───────────────────────────

def test_the_reference_cue_sheet(tmp_path):
    """A 38-track Neo Geo CD rip: one data track and thirty-seven audio."""
    from conftest import corpus_path
    real = corpus_path("reference/cue.cue") or os.path.join(
        "data", "test_formats", "reference", "cue.cue")
    if not os.path.isfile(real):
        pytest.skip("reference cue absent")
    chunks, _warns = containers.inspect_cue(real)
    got = {f["name"]: f["value"] for f in chunks[0]["fields"]}
    assert got["tracks"] == 38
    assert got["audioTracks"] == 37
    assert got["binaries"] == 1


def test_a_cue_whose_file_line_is_damaged_names_no_binary(tmp_path):
    """Found by the mutation sweep, not by hand.

    `cue.parse` opens with cur_file = None and only sets it on a line starting
    `FILE `. Corrupt that line and the TRACK entries still parse -- into tracks
    whose file is None. The walker called os.path.basename on it and raised
    TypeError, which is the parser having believed the file rather than
    checking it.
    """
    p = tmp_path / "damaged.cue"
    p.write_text('XILE "GAME.bin" BINARY\n  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n',
                 encoding="latin-1")
    chunks, warns = containers.inspect_cue(str(p))
    got = {f["name"]: f["value"] for f in chunks[0]["fields"]}
    assert got["binaries"] == 0, "a sheet with no FILE line names no binary"
    assert got["tracks"] == 1
    assert any("name no file" in w for w in warns), warns
