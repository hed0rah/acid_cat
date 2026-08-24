"""Tests for headerless compressed-stream detection (core/framescan.py) -- the
third `locate` engine: MPEG audio found by frame-sync cadence, not magic."""

import random

from acidcat.core.forensics import framescan, locate


def _frame():
    # a valid MPEG-1 Layer III, 128 kbps, 44100 Hz mono frame header -> 417 bytes
    return bytes([0xFF, 0xFB, 0x90, 0xC0]) + b"\x00" * (417 - 4)


def _noise(n, seed=1):
    r = random.Random(seed)
    return bytes(r.getrandbits(8) for _ in range(n))


def test_finds_headerless_stream():
    streams = framescan.find_mpeg_streams(_frame() * 30)
    assert len(streams) == 1
    s = streams[0]
    assert s["kind"] == "stream" and s["format"] == "mp3" and s["frames"] == 30
    assert s["stream_info"]["sample_rate"] == 44100 and s["confidence"] > 0.8


def test_short_run_is_not_a_stream():
    # fewer than the minimum consecutive frames -> chance, not a stream
    assert framescan.find_mpeg_streams(_frame() * 3) == []


def test_no_false_positive_on_noise():
    assert framescan.find_mpeg_streams(_noise(300000, 2)) == []


def test_chain_breaks_on_config_change():
    # a run of MPEG-1 frames then a lone 22050 Hz (MPEG-2) frame: the chain stops
    other = bytes([0xFF, 0xF3, 0x90, 0xC0]) + b"\x00" * (417 - 4)  # MPEG-2 header
    streams = framescan.find_mpeg_streams(_frame() * 20 + other)
    assert len(streams) == 1 and streams[0]["frames"] == 20


def test_locate_finds_stream_in_strict_mode():
    # headerless MP3 buried in noise -> found even in strict (no statistical pass)
    blob = _noise(8192, 3) + _frame() * 40 + _noise(8192, 4)
    recs = locate.locate(blob, mode="strict")
    streams = [r for r in recs if r["kind"] == "stream"]
    assert len(streams) == 1
    assert streams[0]["offset"] == 8192 and streams[0]["format"] == "mp3"


def test_stream_not_double_counted_inside_container():
    # an MP3 stream that sits inside a found container is that file's payload;
    # a bare stream in noise stands alone (this checks the standalone path)
    recs = locate.locate(_noise(4096, 5) + _frame() * 40, mode="normal")
    assert sum(1 for r in recs if r["kind"] == "stream") == 1


# ── a chain of valid frames is not proof of a stream ─────────────────

def _art_sheet(blocks=8):
    """A field of 0xFF with a sparse header-shaped byte every 128, which is the
    shape a Duke Nukem art tile sheet happens to have.

    Every structural test passes on this: the headers decode, the version,
    layer and sample rate agree across all of them, and the chain does not
    restart at neighbouring offsets. What gives it away is the payload -- it is
    three quarters 0xFF, and 0xFF is the sync byte itself.
    """
    out = bytearray()
    for _ in range(blocks * 25):
        out += bytes([0xFF, 0xFF, 0x44, 0xFF])      # MPEG 1 Layer I, 128k, 48k
        out += b"\xff" * 100 + bytes(24)            # 128-byte frame, mostly sync
    return bytes(out)


def test_a_field_of_sync_bytes_is_not_a_stream():
    """The false positive this rule exists for. Inside a real art tile sheet
    this was reported as 25 frames of MPEG 1 Layer I at confidence 0.85, with a
    full per-frame table -- a confident wrong answer with a decode attached."""
    art = _art_sheet()
    chained, _end = framescan._chain(art, 0, len(art))
    assert chained >= framescan._MIN_FRAMES, (
        "the fixture must actually chain, or this proves nothing: got "
        f"{chained} frames")
    assert framescan.find_mpeg_streams(art) == [], (
        "a field of sync bytes was reported as MPEG audio")


def test_a_real_stream_is_not_rejected_by_the_same_rule():
    """The guard must not cost the thing it protects. Real MPEG payload runs
    0.003 to 0.019 of 0xFF; the rule sits at 0.10."""
    streams = framescan.find_mpeg_streams(_frame() * 30)
    assert len(streams) == 1 and streams[0]["frames"] == 30


def test_a_stream_carrying_some_sync_bytes_still_reads():
    """Not a ban on 0xFF. A payload can hold a few and still be audio; the rule
    is about saturation, and it has to leave room above what real files do."""
    body = (b"\xff\xfb\x90\xc0" + (b"\x00\xff\x11\x22" * 20)
            + b"\x00" * (417 - 4 - 80))
    assert len(body) == 417
    streams = framescan.find_mpeg_streams(body * 30)
    assert len(streams) == 1, "a stream with 25% sync bytes was thrown away"


def test_the_scan_resumes_past_a_rejected_field_not_inside_it(monkeypatch):
    """Every offset inside a sync field fails the same way, so stepping a byte
    at a time re-derives that once per byte.

    Asserted by counting chain walks rather than by wall-clock: the difference
    between resuming at `end` and at `j + 1` is cost, not correctness, and a
    test that only checks the stream is still found passes either way. That
    version of this test was written first and proved nothing.
    """
    blob = _art_sheet(blocks=4) + _frame() * 30
    calls = []
    real = framescan._chain
    monkeypatch.setattr(framescan, "_chain",
                        lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    streams = framescan.find_mpeg_streams(blob)
    assert len(streams) == 1, streams
    assert streams[0]["frames"] == 30
    assert streams[0]["offset"] >= len(_art_sheet(blocks=4))
    assert len(calls) < 60, (
        f"{len(calls)} chain walks over one rejected field; resuming past it "
        f"should cost a handful, not one per candidate offset inside it")
