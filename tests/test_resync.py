"""Recovering chunk structure from damaged containers.

A walker follows declared sizes, so one corrupt size field costs every chunk
after it and a damaged magic costs the whole file -- even when those chunks are
sitting on disk perfectly readable. These pin the recovery, and equally pin that
it refuses to invent structure that is not there.

The damage models are the ones measured against real behaviour: smashed magic,
zeroed header, corrupt interior size field, truncation, a torn-off front.
"""

import os
import struct

import pytest

from acidcat.core.forensics import resync
from acidcat.core.walk import walk_file
from acidcat.core.walk.base import Unsupported

from conftest import CORPUS_WAV as WAV


@pytest.fixture
def src():
    if not os.path.isfile(WAV):
        pytest.skip("test corpus WAV not present")
    return open(WAV, "rb").read()


def test_pristine_file_chains_completely(src):
    r = resync.recover(src, known_only=True)
    assert len(r["chain"]) >= 3
    assert r["coverage"] > 0.9
    assert r["endian"] == "<"


def test_smashed_magic_still_recovers_the_grid(src, tmp_path):
    """The walker cannot even dispatch without the magic; the chunks after it
    are untouched, so recovery must find them."""
    damaged = b"\x00\x00\x00\x00" + src[4:]
    p = tmp_path / "smashed.wav"
    p.write_bytes(damaged)
    with pytest.raises(Unsupported):
        walk_file(str(p))                     # the normal path is a total loss

    r = resync.recover(damaged, known_only=True)
    ids = [c["id"] for c in r["chain"]]
    assert "fmt " in ids and "data" in ids, f"lost the payload chunks: {ids}"
    assert all(c["corroborated"] for c in r["chain"])


def test_corrupt_interior_size_recovers_what_the_walk_drops(src, tmp_path):
    """The quiet failure: a bad fmt size makes the walker jump past everything
    after it and return a short chunk list with no indication of what was lost."""
    damaged = src[:16] + struct.pack("<I", 0x7FFFFFFF) + src[20:]
    p = tmp_path / "badsize.wav"
    p.write_bytes(damaged)
    _label, chunks, _warns = walk_file(str(p))
    walked = len(chunks)

    r = resync.recover(damaged, known_only=True)
    assert "data" in [c["id"] for c in r["chain"]], "the data chunk stayed lost"
    assert len(r["chain"]) > walked - 2, "recovery found less than the walk"


def test_refuses_to_invent_structure(src):
    """The front torn off leaves raw payload and no headers. Recovery must
    report nothing rather than manufacture a grid -- that is the difference
    between a forensic tool and a plausible-looking one."""
    headless = src[4096:]
    r = resync.recover(headless, known_only=True)
    assert r["chain"] == [], f"invented {len(r['chain'])} chunks from payload"


def test_noise_yields_no_chain():
    import random
    rng = random.Random(9)
    noise = bytes(rng.randrange(256) for _ in range(200_000))
    r = resync.recover(noise, known_only=True)
    assert r["chain"] == [], "found structure in random bytes"


def test_corroboration_filters_coincidences(src):
    """A record is only kept when its declared end lands on another plausible
    record. Without that check, four printable bytes followed by any small
    integer looks like a chunk."""
    loose = resync.scan(src, endian="<", require_corroboration=False)
    strict = resync.scan(src, endian="<", require_corroboration=True)
    assert len(strict) <= len(loose)
    assert all(r["corroborated"] for r in strict)


def test_big_endian_container_is_detected(src):
    """AIFF/IFF sizes are big-endian; recover() must pick the endianness that
    actually chains rather than assuming RIFF."""
    body = (b"COMM" + struct.pack(">I", 18) + bytes(18)
            + b"SSND" + struct.pack(">I", 64) + bytes(64))
    form = b"FORM" + struct.pack(">I", len(body) + 4) + b"AIFF" + body
    r = resync.recover(form, known_only=True)
    assert r["endian"] == ">", "picked the wrong byte order"
    assert len(r["chain"]) >= 2


def test_confidence_is_bounded_and_evidence_backed(src):
    r = resync.recover(src, known_only=True)
    for c in r["chain"]:
        assert 0.0 < c["confidence"] <= 0.95, "a scan hit must never read as certain"
        assert c["known"] or c["corroborated"], "kept a record with no evidence"


def test_cli_reports_recovery_and_refusal(tmp_path, capsys, src):
    from acidcat.cli import main
    good = tmp_path / "smashed.wav"
    good.write_bytes(b"\x00\x00\x00\x00" + src[4:])
    assert main(["inspect", str(good), "--resync", "--color", "never"]) == 0
    out = capsys.readouterr().out
    assert "recovered" in out and "data" in out
    assert "hypotheses" in out, "recovery must be labelled as such"

    bare = tmp_path / "payload.bin"
    bare.write_bytes(src[4096:])
    assert main(["inspect", str(bare), "--resync", "--color", "never"]) == 1
    out = capsys.readouterr().out
    assert "no recoverable chunk grid" in out
    assert "locate" in out, "should point at the statistical path"
