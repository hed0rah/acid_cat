"""The frame-sync scan must stay exact while skipping work it cannot use.

find_mpeg_streams looks for headerless MPEG audio by frame cadence, which means
treating every 0xFF as a possible frame start. A run of 0xFF -- an ordinary
thing to meet in a disk image -- therefore made every byte a candidate and
dragged the scan to 0.8 MB/s, ~5 minutes at the 256 MB cap.

Two fixes, both of which are only safe if they reject exactly what the real
decoder rejects: an inline bit test before the header decode, and a vectorized
pass that finds surviving offsets in bulk. These tests pin that equivalence.
"""

import struct

import pytest

from acidcat.core.forensics import framescan
from acidcat.core.formats.mp3 import decode_frame_header


def _prefilter(b1, b2):
    """The predicate as the scanner applies it."""
    if (b1 & 0xE0) != 0xE0:
        return False
    if (b1 >> 3) & 3 == 1:
        return False
    if (b1 >> 1) & 3 == 0:
        return False
    br = (b2 >> 4) & 0x0F
    if br in (0, 15):
        return False
    return (b2 >> 2) & 3 != 3


def test_prefilter_matches_the_decoder_exhaustively():
    """Every (byte1, byte2) pair: the cheap test and the real decoder must agree
    on accept/reject. If they ever diverge, the scan silently stops finding some
    class of MPEG frame -- so this is checked over the whole space, not sampled."""
    mismatches = []
    for b1 in range(256):
        for b2 in range(256):
            for b3 in (0x00, 0x5A, 0xFF):
                decoded = decode_frame_header(bytes([0xFF, b1, b2, b3])) is not None
                if decoded != _prefilter(b1, b2):
                    mismatches.append((b1, b2, b3))
    assert not mismatches, f"{len(mismatches)} divergences, e.g. {mismatches[:5]}"


def test_byte3_never_affects_the_decision():
    """The prefilter only reads bytes 1-2, which is only sound because byte 3
    (channel mode, emphasis...) carries no rejection."""
    for b3 in range(256):
        assert (decode_frame_header(bytes([0xFF, 0xFB, 0x90, b3])) is not None)


def _mp3(frames=40):
    """A minimal constant-bitrate Layer III stream: 128 kbps, 44.1 kHz."""
    hdr = bytes([0xFF, 0xFB, 0x90, 0x00])
    length = decode_frame_header(hdr)["frame_length"]
    return (hdr + bytes(length - 4)) * frames


def _pure(data):
    """find_mpeg_streams with the vectorized candidate pass disabled."""
    orig = framescan._candidate_offsets
    framescan._candidate_offsets = lambda *a, **k: None
    try:
        return framescan.find_mpeg_streams(data)
    finally:
        framescan._candidate_offsets = orig


CASES = {
    "clean stream": _mp3(),
    "stream in noise": bytes(3000) + _mp3() + bytes(3000),
    "two streams": _mp3(20) + bytes(2048) + _mp3(20),
    "all 0xFF": b"\xff" * 40_000,
    "ff e0 pairs": b"\xff\xe0" * 20_000,
    "ff fb pairs": b"\xff\xfb" * 20_000,
    "zeros": bytes(40_000),
    "stream after 0xFF run": b"\xff" * 8000 + _mp3(),
    "truncated at edge": _mp3()[:-3],
    "short": b"\xff\xfb",
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_vectorized_and_scalar_paths_agree(name):
    """numpy present or not, the same bytes must yield the same streams."""
    data = CASES[name]
    assert framescan.find_mpeg_streams(data) == _pure(data), name


def test_real_streams_are_still_found():
    """Guard against a prefilter that is fast because it rejects everything."""
    found = framescan.find_mpeg_streams(CASES["stream in noise"])
    assert len(found) == 1
    assert found[0]["offset"] == 3000
    assert found[0]["frames"] >= 12
    assert found[0]["stream_info"]["sample_rate"] == 44100


def test_a_stream_hiding_after_a_sync_run_is_not_missed():
    """The case the optimization could plausibly break: real audio sitting past
    a long run of false candidates."""
    found = framescan.find_mpeg_streams(CASES["stream after 0xFF run"])
    assert len(found) == 1 and found[0]["offset"] == 8000


def test_pathological_input_is_not_quadratic():
    """0.8 MB/s on a run of 0xFF meant ~5 minutes at the read cap. This is a
    coarse ceiling, not a benchmark -- it only has to catch a return to
    per-byte header decoding."""
    import time
    data = b"\xff" * 2_000_000
    start = time.perf_counter()
    assert framescan.find_mpeg_streams(data) == []
    assert time.perf_counter() - start < 5.0
