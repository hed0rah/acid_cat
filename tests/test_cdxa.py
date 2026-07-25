"""Tests for core.cdxa (CD-XA sector-image detection + XA-ADPCM decode).

Synthetic sectors only, so this runs anywhere. A sound group with an all-zero
header selects filter 0 (memoryless) and shift 12, so each 4-bit nibble decodes
to exactly nibble << 12 -- an exact, checkable decode.
"""
import array
import struct

import pytest

from acidcat.core import cdxa


def _xa_sector(file, chan, coding, payload):
    s = bytearray(cdxa.SECTOR)
    s[0:12] = cdxa._SYNC
    s[15] = 2                                  # Mode 2
    for base in (16, 20):                      # duplicated 8-byte subheader
        s[base] = file
        s[base + 1] = chan
        s[base + 2] = cdxa._SUBMODE_AUDIO
        s[base + 3] = coding
    s[24:24 + len(payload)] = payload
    return bytes(s)


def _group(header16, data112):
    return bytes(header16) + bytes(data112)


def test_coding_of():
    assert cdxa.coding_of(0x00) == {"stereo": False, "rate": 37800, "bits": 4}
    assert cdxa.coding_of(0x01) == {"stereo": True, "rate": 37800, "bits": 4}
    assert cdxa.coding_of(0x04)["rate"] == 18900       # bit 2 set = 18900 Hz
    assert cdxa.coding_of(0x10)["bits"] == 8           # bit 4 set = 8-bit


def test_detect_cd_image(tmp_path):
    payload = bytes(cdxa._XA_AUDIO_BYTES)
    img = tmp_path / "disc.bin"
    img.write_bytes(_xa_sector(1, 0, 0x00, payload) * 2)
    info = cdxa.detect_cd_image(str(img))
    assert info["sector_size"] == 2352 and info["mode"] == 2
    assert info["sectors"] == 2 and info["xa"] is True

    # a non-image file is rejected
    plain = tmp_path / "plain.bin"
    plain.write_bytes(b"RIFF....WAVE" + bytes(5000))
    assert cdxa.detect_cd_image(str(plain)) is None


def test_xa_streams_and_dominant_coding(tmp_path):
    silent = bytes(cdxa._XA_AUDIO_BYTES)
    # stream (1,0): three sectors, coding mostly stereo with one mono outlier
    img = tmp_path / "disc.bin"
    img.write_bytes(
        _xa_sector(1, 0, 0x01, silent)
        + _xa_sector(1, 0, 0x01, silent)
        + _xa_sector(1, 0, 0x00, silent)       # outlier
    )
    streams = cdxa.xa_streams(str(img))
    assert list(streams) == [(1, 0)]
    assert len(streams[(1, 0)]["sectors"]) == 3
    assert streams[(1, 0)]["coding"] == 0x01   # dominant, not the outlier


def test_decode_exact_nibbles(tmp_path):
    # header all zero -> filter 0 (no predictor), shift 12, so sample = nibble<<12
    data = bytearray(112)
    data[0] = 0x21                              # low nibble 1, high nibble 2
    payload = bytearray(_group(bytes(16), data))
    payload += bytes(cdxa._XA_AUDIO_BYTES - len(payload))   # 17 silent groups
    img = tmp_path / "disc.bin"
    img.write_bytes(_xa_sector(1, 0, 0x00, bytes(payload)) * 2)  # mono

    pcm, info = cdxa.decode_stream(str(img))
    assert info["channels"] == 1 and info["rate"] == 37800
    assert info["frames"] == 2 * 18 * 224      # 2 sectors x 18 groups x 224 mono

    s = array.array("h")
    s.frombytes(pcm)
    assert s[0] == 1 << 12                      # first low-nibble plane, j=0
    assert s[1] == 0
    assert s[28] == 2 << 12                     # first high-nibble plane, j=0
    assert sum(1 for x in s if x != 0) == 4     # two non-zero per sector, two sectors


def test_decode_stereo_splits_channels(tmp_path):
    data = bytearray(112)
    data[0] = 0x21                              # low->left, high->right
    payload = bytearray(_group(bytes(16), data))
    payload += bytes(cdxa._XA_AUDIO_BYTES - len(payload))
    img = tmp_path / "disc.bin"
    img.write_bytes(_xa_sector(1, 0, 0x01, bytes(payload)) * 2)  # stereo

    pcm, info = cdxa.decode_stream(str(img))
    assert info["channels"] == 2
    s = array.array("h")
    s.frombytes(pcm)
    # interleaved L,R: left[0] from low nibble, right[0] from high nibble
    assert s[0] == 1 << 12                      # left[0]
    assert s[1] == 2 << 12                      # right[0]


def test_eight_bit_not_implemented(tmp_path):
    img = tmp_path / "disc.bin"
    img.write_bytes(_xa_sector(1, 0, 0x10, bytes(cdxa._XA_AUDIO_BYTES)) * 2)  # 8-bit
    with pytest.raises(NotImplementedError):
        cdxa.decode_stream(str(img))


def test_split_gaps(tmp_path):
    info = {"channels": 1, "rate": 1000}       # tiny rate: 100-frame windows
    # 3s tone, 2s silence, 3s tone -> expect 2 songs
    tone = array.array("h", [8000, -8000] * 1500)     # 3000 frames
    quiet = array.array("h", [0] * 2000)              # 2000 frames
    pcm = (tone + quiet + tone).tobytes()
    songs = cdxa.split_gaps(pcm, info, min_gap_s=1.0, min_song_s=1.0)
    assert len(songs) == 2
