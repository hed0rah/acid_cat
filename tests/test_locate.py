"""Tests for the forensic recovery orchestrator (core/locate.py) -- phases 2/3.

Covers the backtrack-to-container anchor, the three forensics levels, and the
governing rule that a missing header downgrades (never discards) a hit."""

import io
import math
import random
import struct
import wave

from acidcat.core.forensics import locate


def _noise(n, seed=1):
    r = random.Random(seed)
    return bytes(r.getrandbits(8) for _ in range(n))


def _tone_i16(n, period=40, amp=8000):
    return b"".join(struct.pack("<h", int(amp * math.sin(2 * math.pi * i / period)))
                    for i in range(n))


def _wav(n=6000, rate=11025):
    """A real 16-bit mono WAV (RIFF/WAVE with fmt + data), tone payload."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(_tone_i16(n))
    return buf.getvalue()


def _tone_u8(n, period=40, amp=60):
    return bytes(int(amp * math.sin(2 * math.pi * i / period)) & 0xFF for i in range(n))


# ---- backtrack -------------------------------------------------------------

def test_backtrack_finds_riff_before_region():
    wav = _wav()
    blob = _noise(4096, 2) + wav + _noise(2048, 3)
    # a region somewhere inside the WAV payload
    start = 4096 + 100
    bt = locate.backtrack_header(blob, start)
    assert bt["found"]
    assert bt["format"] == "wav"
    assert bt["container_start"] == 4096              # exactly at the RIFF magic


def test_backtrack_rejects_stray_magic_in_noise():
    # the ASCII "RIFF" can occur in random data, but without a valid WAVE at +8
    # sniff_bytes rejects it, so backtrack reports not-found
    blob = bytearray(_noise(8192, 4))
    blob[2000:2004] = b"RIFF"                          # stray magic, no WAVE tag
    bt = locate.backtrack_header(bytes(blob), 4000)
    assert bt["found"] is False


def test_backtrack_none_before_region():
    assert locate.backtrack_header(_noise(4096, 5), 4000)["found"] is False


# ---- classify + extent ------------------------------------------------------

def test_container_extent_from_riff_size():
    wav = _wav()
    blob = _noise(1024, 6) + wav + _noise(1024, 7)
    recs = locate.locate(blob, mode="strict")
    assert len(recs) == 1
    rec = recs[0]
    assert rec["kind"] == "container" and rec["format"] == "wav"
    assert rec["offset"] == 1024
    # extent comes from the RIFF declared size, so the whole file is carved
    assert rec["end"] == 1024 + len(wav)
    assert rec["inspectable"] is True


# ---- forensics levels -------------------------------------------------------

def test_strict_drops_headerless_blobs():
    blob = _noise(4096, 8) + _tone_u8(6000) + _noise(4096, 9)   # no container
    assert locate.locate(blob, mode="strict") == []


def test_aggressive_keeps_headerless_blob():
    blob = _noise(4096, 10) + _tone_u8(6000) + _noise(4096, 11)
    recs = locate.locate(blob, mode="aggressive")
    assert any(r["kind"] == "blob" for r in recs)
    blob_rec = next(r for r in recs if r["kind"] == "blob")
    assert blob_rec["offset"] >= 4096 - audioscan_window()
    assert blob_rec["inspectable"] is False


def test_normal_keeps_container_and_confident_blob():
    wav = _wav()
    blob = _noise(2048, 12) + wav + _noise(2048, 13) + _tone_u8(6000) + _noise(2048, 14)
    recs = locate.locate(blob, mode="normal")
    kinds = {r["kind"] for r in recs}
    assert "container" in kinds                       # the WAV is recovered
    # the strong headerless tone survives 'normal' too (high confidence)
    assert any(r["kind"] == "blob" for r in recs)


def test_missing_header_downgrades_not_discards():
    # same tone, once with a WAV wrapper and once bare: aggressive recovers both,
    # the bare one as a blob (downgrade), proving backtrack-miss != drop
    bare = _noise(1024, 15) + _tone_u8(6000) + _noise(1024, 16)
    recs = locate.locate(bare, mode="aggressive")
    assert recs and all(r["kind"] == "blob" for r in recs)


def _wav8(n=6000, rate=9016):
    """An 8-bit WAV (statistically visible: the detector can see its PCM)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(1); w.setframerate(rate)
        w.writeframes(bytes((int(90 * math.sin(2 * math.pi * i / 41)) + 128) & 0xFF
                            for i in range(n)))
    return buf.getvalue()


def test_corrupt_extent_is_audio_bounded_not_greedy():
    # an 8-bit WAV with its size smashed, then a gap, then a separate tone blob.
    # the corrupt WAV must bound to its own audio, NOT swallow the trailing blob.
    wav = bytearray(_wav8()); wav[4:8] = b"\x00\x00\x00\x00"
    blob = _noise(2048, 1) + bytes(wav) + _noise(6000, 2) + _tone_u8(6000) + _noise(2048, 3)
    recs = locate.locate(blob, mode="aggressive")
    cont = [r for r in recs if r["kind"] == "container"]
    blobs = [r for r in recs if r["kind"] == "blob"]
    assert cont and cont[0].get("corrupt_extent")
    assert blobs, "the trailing headerless tone must survive as its own blob"
    # the corrupt container ends before the trailing blob begins (no swallow)
    assert cont[0]["end"] <= blobs[-1]["offset"]


def test_mp3_id3_is_swept():
    # an ID3v2-tagged blob is recovered as an mp3 container
    id3 = b"ID3\x03\x00\x00" + bytes([0, 0, 0x20, 0]) + b"\x00" * 4000
    blob = _noise(2048, 5) + id3 + _noise(2048, 6)
    recs = locate.locate(blob, mode="strict")
    assert any(r["format"] == "mp3" and r["offset"] == 2048 for r in recs)


def test_nearby_blobs_coalesce():
    # two tone fragments a small gap apart collapse to a single blob region
    blob = _noise(1024, 7) + _tone_u8(5000) + _noise(3000, 8) + _tone_u8(5000) + _noise(1024, 9)
    blobs = [r for r in locate.locate(blob, mode="aggressive") if r["kind"] == "blob"]
    assert len(blobs) == 1


def test_invalid_mode_raises():
    try:
        locate.locate(b"", mode="paranoid")
    except ValueError as e:
        assert "mode" in str(e)
    else:
        assert False, "expected ValueError"


def audioscan_window():
    from acidcat.core.forensics import audioscan
    return audioscan.DEFAULT_WINDOW


def test_blob_overlapping_a_container_is_absorbed():
    """The statistical detector works in windows, so a blob routinely opens a few
    hundred bytes ahead of the container header it belongs to. An offset-only
    containment test then reports that file twice -- once correctly as a
    container, once as a redundant raw blob. Measured on a disk image of six real
    WAVs: 8 regions for 6 files, both extras being exactly this."""
    from acidcat.core.forensics.locate import _mostly_within

    # a blob starting before a container but almost entirely inside it
    assert _mostly_within(0x402000, 0x4E0000, [(0x40221A, 0x570000)])
    # a blob that merely touches the edge is a separate find
    assert not _mostly_within(0x100, 0x100000, [(0xFF000, 0x200000)])
    # exact containment still absorbed
    assert _mostly_within(0x500, 0x900, [(0x0, 0x1000)])
    # nothing to overlap
    assert not _mostly_within(0x0, 0x1000, [])


def test_locate_reports_one_region_per_embedded_file(tmp_path):
    """End to end: an image with real containers must yield one region each, not
    a container plus a shadow blob."""
    import os
    wav = os.path.join("data", "test_formats", "generated", "src.wav")
    if not os.path.isfile(wav):
        import pytest
        pytest.skip("test corpus WAV not present")
    payload = open(wav, "rb").read()
    blob = bytes(3000) + payload + bytes(2000) + payload + bytes(1000)
    from acidcat.core.forensics import locate as locatemod
    recs = locatemod.locate(blob, mode="normal")
    containers = [r for r in recs if r["kind"] == "container"]
    assert len(containers) == 2, f"expected 2 containers, got {len(containers)}"
    # no blob may duplicate a container we already reported
    for r in recs:
        if r["kind"] != "blob":
            continue
        for c in containers:
            overlap = min(r["end"], c["end"]) - max(r["offset"], c["offset"])
            span = r["end"] - r["offset"]
            assert not (span > 0 and overlap / span >= 0.5), \
                f"blob at 0x{r['offset']:x} duplicates the container at 0x{c['offset']:x}"


def test_oversize_input_reports_partial_statistical_coverage(tmp_path, capsys,
                                                             monkeypatch):
    """No silent caps. locate's signature sweep covers the whole buffer while
    the statistical pass caps out, so a large image would otherwise report raw
    audio from only part of it with nothing on screen saying so."""
    from acidcat.cli import main
    from acidcat.core.forensics import audioscan
    from acidcat.core.forensics import framescan
    monkeypatch.setattr(audioscan, "DEFAULT_READ_CAP", 4096)
    monkeypatch.setattr(framescan, "_READ_CAP", 4096)
    p = tmp_path / "big.img"
    p.write_bytes(bytes(9000))
    main(["locate", str(p)])
    err = capsys.readouterr().err
    # both bounded engines must name themselves -- an earlier version claimed
    # streams were "found throughout" while the frame scan was capping too
    assert "raw-audio scan covers the first" in err
    assert "stream scan covers the first" in err
    assert "container signatures are found throughout" in err


def test_strict_mode_does_not_claim_partial_coverage(tmp_path, capsys,
                                                     monkeypatch):
    """strict skips the statistical pass entirely, so the caveat would be noise."""
    from acidcat.cli import main
    from acidcat.core.forensics import audioscan
    from acidcat.core.forensics import framescan
    monkeypatch.setattr(audioscan, "DEFAULT_READ_CAP", 4096)
    monkeypatch.setattr(framescan, "_READ_CAP", 1 << 40)
    p = tmp_path / "big.img"
    p.write_bytes(bytes(9000))
    main(["locate", str(p), "--mode", "strict"])
    assert "raw-audio scan covers" not in capsys.readouterr().err


def test_min_confidence_filters_and_says_what_it_withheld(tmp_path, capsys):
    """`locate | carve --batch` should be a one-liner without jq in the middle.
    But a filtered "nothing found" must not look like a genuine one, so the
    count that was withheld goes to stderr."""
    from acidcat.cli import main
    import struct, math
    # a tonal region (scores high) and noise (scores low)
    tone = bytes((128 + int(90 * math.sin(2 * math.pi * i / 64))) & 0xFF
                 for i in range(120_000))
    rng = 1
    noise = bytearray()
    for _ in range(120_000):
        rng = (1103515245 * rng + 12345) & 0x7FFFFFFF
        noise.append((rng >> 16) & 0xFF)
    p = tmp_path / "mix.img"
    p.write_bytes(bytes(noise) + tone)

    main(["locate", str(p), "--output-format", "tsv"])
    all_rows = [l for l in capsys.readouterr().out.splitlines() if l.strip()]

    main(["locate", str(p), "--output-format", "tsv", "--min-confidence", "0.99"])
    cap = capsys.readouterr()
    strict_rows = [l for l in cap.out.splitlines() if l.strip()]
    assert len(strict_rows) < len(all_rows), "filter had no effect"
    assert "not reported" in cap.err, "silently withheld regions"


def test_min_confidence_zero_is_the_old_behaviour(tmp_path, capsys):
    from acidcat.cli import main
    p = tmp_path / "x.img"
    p.write_bytes(bytes(9000))
    main(["locate", str(p), "--output-format", "tsv"])
    base = capsys.readouterr().out
    main(["locate", str(p), "--output-format", "tsv", "--min-confidence", "0"])
    assert capsys.readouterr().out == base
