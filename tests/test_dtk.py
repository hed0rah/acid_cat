"""Tests for core.dtk (GameCube DTK / .adp streaming ADPCM). A frame with
predictor index 0 (memoryless) and scale 12 decodes (nibble<<12)>>12 = nibble."""
import array

from acidcat.core import dtk


def test_decode_frame():
    # headers L,R = 0x0C (filter 0, scale 12), duplicated; one data byte 0x30
    frame = bytes([0x0C, 0x0C, 0x0C, 0x0C]) + bytes([0x30] + [0] * 27)
    pcm, info = dtk.decode(frame)
    assert info["channels"] == 2 and info["frames"] == 28 and info["rate"] == 48000
    s = array.array("h")
    s.frombytes(pcm)                                  # interleaved L, R
    assert s[0] == 3                                  # L0: high nibble 3 -> (3<<12)>>12
    assert s[1] == 0                                  # R0: low nibble 0
    assert s[2] == 0                                  # L1: next byte is 0


def test_decode_rate_override():
    frame = bytes([0x0C, 0x0C, 0x0C, 0x0C]) + bytes(28)
    _, info = dtk.decode(frame, rate=32000)
    assert info["rate"] == 32000


def test_empty():
    pcm, info = dtk.decode(b"")
    assert info["frames"] == 0 and pcm == b""
