"""A statistical guess must never outrank a signature-validated container.

It did: a real WAV whose magic had been checked came back at 0.900, and a
headerless region inferred from autocorrelation came back at 1.000. So the
inference scored above the proof, and `--min-confidence 0.90` -- the documented
way to keep only strong recoveries -- filtered the containers out and kept the
guesses.

The consequence was not theoretical. On a compressed proprietary container
(byte entropy 7.8, which this tool's own `probe entropy` calls "encrypted or
compressed") locate reported four megabytes of raw PCM at confidence 1.00, and
no threshold could reject it, while `classify` and `audit` both point users at
`locate` for exactly that kind of file.

Blobs now occupy [0, 0.89] and only a checked magic number reaches 0.90.
"""

import math
import struct

import pytest

from acidcat.core.forensics import locate as locatemod


def _wav_bytes(n_frames=44100):
    pcm = b"".join(struct.pack("<hh", int(9000 * math.sin(i / 40.0)),
                               int(7000 * math.sin(i / 61.0)))
                   for i in range(n_frames))
    body = (b"WAVE"
            + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 2, 44100, 176400, 4, 16)
            + b"data" + struct.pack("<I", len(pcm)) + pcm)
    return b"RIFF" + struct.pack("<I", len(body)) + body, pcm


@pytest.fixture
def container(tmp_path):
    p = tmp_path / "real.wav"
    p.write_bytes(_wav_bytes()[0])
    return str(p)


@pytest.fixture
def headerless(tmp_path):
    p = tmp_path / "raw.bin"
    p.write_bytes(bytes(2000) + _wav_bytes()[1])      # PCM with no header
    return str(p)


def _scan(path, **kw):
    with open(path, "rb") as f:
        return locatemod.locate(f.read(), **kw)


def test_a_validated_container_outranks_every_blob(container, headerless):
    c = [r for r in _scan(container) if r["kind"] == "container"]
    b = [r for r in _scan(headerless) if r["kind"] == "blob"]
    assert c and b
    assert max(r["confidence"] for r in b) < min(r["confidence"] for r in c)


def test_no_blob_can_reach_the_container_score(headerless):
    for r in _scan(headerless, mode="aggressive"):
        if r["kind"] == "blob":
            assert r["confidence"] <= locatemod._BLOB_CONF_MAX
            assert r["confidence"] < locatemod._CONTAINER_CONF


def test_min_confidence_090_means_validated_only(container, headerless):
    kept = [r for r in _scan(container) if r["confidence"] >= 0.90]
    assert kept and all(r["kind"] == "container" for r in kept)
    assert not [r for r in _scan(headerless) if r["confidence"] >= 0.90]


def test_the_blob_is_still_found(headerless):
    """The cap is a reporting change, not a detection change. `normal` mode
    must still surface the same region -- the gate was rescaled with the scale
    so it sits exactly where it did."""
    blobs = [r for r in _scan(headerless) if r["kind"] == "blob"]
    assert len(blobs) == 1
    assert blobs[0]["length"] > 100000


def test_blobs_still_rank_against_each_other(headerless):
    """Rescaled, not clipped. Clipping would flatten every strong blob onto one
    value and destroy the ordering that makes the report sortable."""
    assert locatemod._BLOB_CONF_MAX < locatemod._CONTAINER_CONF
    # the gate must be the old 0.45 carried onto the new scale, not a new value
    assert locatemod._NORMAL_BLOB_MIN == round(0.45 * locatemod._BLOB_CONF_MAX, 3)
