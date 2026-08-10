"""Tests for the format-carried integrity checks.

These are the checks where damage is PROVABLE rather than inferred, so the bar
is different from the statistical detectors: a false positive here is not a
noisy heuristic, it is the tool calling an undamaged file corrupt on the
strength of arithmetic it got wrong. Every test below that asserts "clean" is
therefore as important as the ones asserting "damaged".
"""

import json
import os
import struct
import subprocess
import sys

import pytest

from acidcat.core.forensics import checksums as C


# --------------------------------------------------------------- primitives

def test_crc8_known_vector():
    """FLAC's CRC-8 is x^8+x^2+x+1, init 0. The standard check value for
    '123456789' under those parameters is 0xF4."""
    assert C.crc8(b"123456789") == 0xF4


def test_crc16_known_vector():
    """FLAC's CRC-16 is x^16+x^15+x^2+1, init 0 -- the ARC/IBM parameters
    computed MSB-first, whose check value for '123456789' is 0xFEE8."""
    assert C.crc16(b"123456789") == 0xFEE8


def test_crcs_of_empty_are_zero():
    assert C.crc8(b"") == 0
    assert C.crc16(b"") == 0


def test_crc_changes_when_any_byte_changes():
    """The property the whole module rests on."""
    base = bytes(range(64))
    for i in (0, 31, 63):
        m = bytearray(base)
        m[i] ^= 0x01
        assert C.crc16(bytes(m)) != C.crc16(base), f"byte {i} did not affect it"


# ---------------------------------------------------------------- fixtures

def _have(tool):
    try:
        subprocess.run([tool, "-version"], capture_output=True, timeout=20)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


@pytest.fixture(scope="module")
def flac_pair(tmp_path_factory):
    """A real FLAC and a copy with one payload byte flipped.

    Encoded with ffmpeg rather than hand-built: a hand-built FLAC would encode
    my understanding of the format, and the point is to check against a file
    something else wrote.
    """
    if not _have("ffmpeg"):
        pytest.skip("ffmpeg not available")
    d = tmp_path_factory.mktemp("flac")
    wav, good = str(d / "a.wav"), str(d / "a.flac")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "sine=frequency=300:duration=3,aeval=val(0)*0.4+random(3)*0.08:c=same",
                    "-ac", "2", "-ar", "44100", "-sample_fmt", "s16", wav],
                   check=True, capture_output=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", wav, good],
                   check=True, capture_output=True)
    raw = bytearray(open(good, "rb").read())
    raw[len(raw) // 2] ^= 0xFF
    bad = str(d / "bad.flac")
    open(bad, "wb").write(raw)
    return good, bad


def _flac_audio_start(path):
    """Skip the metadata-block chain; audio begins after the last-flagged block."""
    b = open(path, "rb").read()
    pos = 4
    while True:
        hdr = b[pos]
        ln = int.from_bytes(b[pos + 1:pos + 4], "big")
        pos += 4 + ln
        if hdr & 0x80:
            return b, pos


# --------------------------------------------------------------------- FLAC

def test_a_clean_flac_reports_no_failures(flac_pair):
    """The one that matters most. A checksum check that cries wolf on real
    files is worse than no check -- and taking every CRC-8 hit as a frame
    boundary did exactly that, because 0xFF 0xF8 occurs inside audio payload
    and can satisfy an 8-bit checksum by chance."""
    good, _ = flac_pair
    b, start = _flac_audio_start(good)
    r = C.flac_frames(b, start, len(b))
    assert r["frames_found"] > 1, "no frames were located at all"
    assert r["failed"] == 0, f"clean file reported {r['failed']} bad frame(s)"


def test_a_flipped_payload_byte_fails_its_frame(flac_pair):
    good, bad = flac_pair
    b, start = _flac_audio_start(bad)
    r = C.flac_frames(b, start, len(b))
    assert r["failed"] >= 1, "a flipped payload byte passed every CRC-16"
    assert r["offsets"], "a failure was counted but not located"


def test_the_damage_is_localised_near_the_flip(flac_pair):
    """A checker that says "something is wrong somewhere" is much less useful
    than one that names the frame."""
    good, bad = flac_pair
    b, start = _flac_audio_start(bad)
    flip = len(b) // 2
    r = C.flac_frames(b, start, len(b))
    assert any(o <= flip for o in r["offsets"]), \
        f"no reported frame starts at or before the flipped byte {flip:#x}"


def test_ffmpeg_agrees_the_file_is_broken(flac_pair):
    """An independent oracle -- but it has to be asked properly.

    ffmpeg does NOT verify FLAC frame CRCs by default: on a file with a flipped
    payload byte it decodes silently and exits 0. Only `-err_detect crccheck`
    makes it report "CRC error". That is worth knowing beyond this test: a FLAC
    whose damage is provable from its own checksums passes silently through the
    most common tool people would reach for. Some damage additionally breaks
    residual decoding and errors even by default, but the CRC failure is the
    general case and it is quiet.
    """
    if not _have("ffmpeg"):
        pytest.skip("ffmpeg not available")
    good, bad = flac_pair
    for path, expect_error in ((good, False), (bad, True)):
        p = subprocess.run(["ffmpeg", "-v", "error", "-err_detect", "crccheck",
                            "-i", path, "-f", "null", "-"],
                           capture_output=True, text=True)
        got_error = "crc" in p.stderr.lower() or "error" in p.stderr.lower()
        assert got_error == expect_error, \
            f"{os.path.basename(path)}: ffmpeg error={got_error}, " \
            f"expected {expect_error}. stderr: {p.stderr.strip()[:120]}"


def test_a_file_with_no_frames_is_not_a_failure():
    """Metadata-only, or a format we mis-identified: report nothing checked
    rather than inventing a verdict."""
    r = C.flac_frames(b"fLaC" + b"\x00" * 64, 4, 68)
    assert r["checked"] == 0 and r["failed"] == 0


# ---------------------------------------------------------------------- MP3

@pytest.fixture(scope="module")
def mp3_set(tmp_path_factory):
    """A clean MP3, one with a destroyed sync word, one with a damaged frame
    payload."""
    if not _have("ffmpeg"):
        pytest.skip("ffmpeg not available")
    d = tmp_path_factory.mktemp("mp3")
    wav, good = str(d / "a.wav"), str(d / "a.mp3")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "sine=frequency=300:duration=3,aeval=val(0)*0.4+random(5)*0.08:c=same",
                    "-ac", "2", "-ar", "44100", "-sample_fmt", "s16", wav],
                   check=True, capture_output=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", wav, "-b:a",
                    "128k", good], check=True, capture_output=True)
    raw = bytearray(open(good, "rb").read())
    pos = raw.find(b"\xff\xfb", 4096)
    assert pos > 0, "no frame sync found to damage"

    nosync = bytearray(raw)
    nosync[pos] = 0x00
    p1 = str(d / "nosync.mp3")
    open(p1, "wb").write(nosync)

    payload = bytearray(raw)
    for i in range(pos + 8, pos + 60):
        payload[i] ^= 0xFF
    p2 = str(d / "payload.mp3")
    open(p2, "wb").write(payload)
    return good, p1, p2


def _mp3_audio_start(path):
    b = open(path, "rb").read()
    if b[:3] == b"ID3":
        size = ((b[6] & 0x7F) << 21 | (b[7] & 0x7F) << 14
                | (b[8] & 0x7F) << 7 | (b[9] & 0x7F))
        return b, 10 + size
    return b, 0


def test_a_clean_mp3_walks_without_complaint(mp3_set):
    good, _, _ = mp3_set
    b, start = _mp3_audio_start(good)
    r = C.mp3_frames(b, start)
    assert r["frames"] > 10
    assert r["resyncs"] == 0, f"{r['resyncs']} resync(s) in a clean file"
    assert r["bad_bigvalues"] == 0
    assert r["bad_backref"] == 0


def test_a_destroyed_sync_word_forces_a_resync(mp3_set):
    _, nosync, _ = mp3_set
    b, start = _mp3_audio_start(nosync)
    r = C.mp3_frames(b, start)
    assert r["resyncs"] >= 1, "a destroyed sync word went unnoticed"


def test_an_impossible_big_values_is_caught(mp3_set):
    """big_values counts spectral PAIRS, so 2*big_values must fit a granule's
    576 lines. Over 288 is impossible, not merely unusual -- which is why this
    is a spec-defined bound rather than a heuristic."""
    _, _, payload = mp3_set
    b, start = _mp3_audio_start(payload)
    r = C.mp3_frames(b, start)
    assert r["bad_bigvalues"] >= 1, "an out-of-range big_values passed"


def test_counting_frames_is_not_the_same_as_validating_them(mp3_set):
    """The Phase 1 finding, as a test. Striding the declared bitrate produces
    an identical frame count for a clean file and a damaged one; validating
    tells them apart."""
    good, nosync, payload = mp3_set
    counts, verdicts = [], []
    for p in (good, nosync, payload):
        b, start = _mp3_audio_start(p)
        r = C.mp3_frames(b, start)
        counts.append(r["frames"])
        verdicts.append(r["resyncs"] + r["bad_bigvalues"] + r["bad_backref"])
    assert verdicts[0] == 0 and verdicts[1] > 0 and verdicts[2] > 0, \
        f"verdicts did not separate clean from damaged: {verdicts}"


def test_side_info_arithmetic_is_asserted_not_assumed(mp3_set):
    """The side info must be consumed exactly -- 256 bits stereo, 136 mono.
    An off-by-N in a bit reader produces confident garbage indistinguishable
    from real corruption; an early version dropped three bits per
    granule/channel and reported 357 damaged frames in a clean file."""
    good, _, _ = mp3_set
    b, start = _mp3_audio_start(good)
    h = C._mp3_header(b, start)
    assert h is not None
    assert C._mp3_side_info(b, start, h[1]) is not None, \
        "side info did not consume exactly its declared width"


# ── the --deep flag on validate ──────────────────────────────────────

def _validate(*args):
    return subprocess.run([sys.executable, "-m", "acidcat", "validate", *args],
                          capture_output=True, text=True)


def test_deep_is_off_by_default(flac_pair):
    """A full read at ~10 MB/s is not something to impose on a directory sweep
    that only asked whether the container's arithmetic adds up."""
    _, bad = flac_pair
    assert "FAIL" not in _validate(bad).stdout
    assert "FAIL" in _validate(bad, "--deep").stdout


def test_deep_proves_flac_damage(flac_pair):
    good, bad = flac_pair
    assert "OK" in _validate(good, "--deep").stdout
    out = _validate(bad, "--deep").stdout
    assert "FAIL" in out and "CRC-16" in out


def test_deep_reaches_formats_with_no_structural_model(mp3_set):
    """MP3 is not a structurally-modeled container, so plain validate has
    nothing to say about it -- but its frames are checkable, and a verdict
    exists even where the structural pass has none."""
    good, nosync, payload = mp3_set
    assert "no structurally-modeled" in _validate(good, "--deep").stderr
    for bad in (nosync, payload):
        assert "FAIL" in _validate(bad, "--deep").stdout


def test_deep_failure_is_reported_in_json(flac_pair):
    _, bad = flac_pair
    r = _validate(bad, "--deep", "--output-format", "json")
    doc = json.loads(r.stdout)
    rec = doc[0] if isinstance(doc, list) else doc
    assert rec["status"] == "fail"
    assert "CRC" in rec["detail"]


def test_deep_exit_code_follows_the_grep_family(flac_pair):
    """0 = consistent, 1 = a file has a violation. Same contract as the
    structural pass, so `validate --deep X && ...` behaves."""
    good, bad = flac_pair
    assert _validate(good, "--deep").returncode == 0
    assert _validate(bad, "--deep").returncode == 1
