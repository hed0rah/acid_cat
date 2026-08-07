"""Tests for the BFD `.bfdlac` (BFDC) walker."""

import struct

from acidcat.core.infra import sniff as sniffmod
from acidcat.core.walk import walk_file
from acidcat.core.walk import bfdlac
from acidcat.core.walk.bfdlac import inspect_bfdlac


def _chunk(cid, payload):
    return cid + struct.pack(">I", len(payload)) + payload


def _fmt(bits=24, enc=10, samples=294141, rate=44100, ch=2):
    return _chunk(b"fmt ", struct.pack(">IIIII", bits, enc, samples, rate, ch))


def _indx(block=1024, frames=288):
    return _chunk(b"Indx", struct.pack(">II", block, frames)
                  + b"\x00\x00\x00\x00" * frames)


def _bfdc(chunks):
    body = b"".join(chunks)
    return b"BFDC" + struct.pack(">I", len(body) + 4) + b"\x00" * 0 + body
    # outer size = (file length - 8); file = 8-byte BFDC header + body


def _write(tmp_path, name, data):
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


def _build(samples=294141, frames=288, pack=b"BFDHP-TESTKIT", data_len=4096):
    inner = [_fmt(samples=samples), _chunk(b"BFDi", pack),
             _indx(frames=frames), _chunk(b"data", b"\x0a\x7c" + b"\x00" * data_len)]
    body = b"".join(inner)
    return b"BFDC" + struct.pack(">I", len(body)) + body


def test_sniff_recognizes_bfdc():
    assert sniffmod.sniff_bytes(b"BFDC" + b"\x00" * 16) == "bfdlac"


def test_basic_walk(tmp_path):
    p = _write(tmp_path, "master001.bfdlac", _build())
    label, chunks, warns = walk_file(p)
    assert label == "BFD compressed audio"
    assert [c["id"] for c in chunks] == ["BFDC", "fmt ", "BFDi", "Indx", "data"]
    assert "24-bit" in chunks[0]["summary"] and "44100 Hz" in chunks[0]["summary"]


def test_fmt_fields(tmp_path):
    p = _write(tmp_path, "k.bfdlac", _build(samples=220577))
    _, chunks, _ = walk_file(p)
    fmt = next(c for c in chunks if c["id"] == "fmt ")
    fv = {f["name"]: f["value"] for f in fmt["fields"]}
    assert fv["bits_per_sample"] == 24
    assert fv["num_samples"] == 220577
    assert fv["sample_rate"] == 44100
    assert fv["channels"] == 2


def test_pack_id(tmp_path):
    p = _write(tmp_path, "k.bfdlac", _build(pack=b"BFDHP-HORSEPOWER"))
    _, chunks, _ = walk_file(p)
    bfdi = next(c for c in chunks if c["id"] == "BFDi")
    assert "BFDHP-HORSEPOWER" in bfdi["summary"]


def test_index_frame_count(tmp_path):
    p = _write(tmp_path, "k.bfdlac", _build(frames=288))
    _, chunks, _ = walk_file(p)
    idx = next(c for c in chunks if c["id"] == "Indx")
    fv = {f["name"]: f["value"] for f in idx["fields"]}
    assert fv["block_size"] == 1024
    assert fv["frame_count"] == 288


def test_index_mismatch_flagged(tmp_path):
    # frame_count wildly off from ceil(samples/block) is flagged, not fatal
    p = _write(tmp_path, "k.bfdlac", _build(samples=294141, frames=5))
    _, chunks, _ = walk_file(p)
    idx = next(c for c in chunks if c["id"] == "Indx")
    assert any("frame_count" in w for w in idx.get("warnings", []))


def test_not_bfdc_rejected(tmp_path):
    data = b"RIFF" + struct.pack(">I", 20) + b"WAVE" + b"\x00" * 12
    chunks, warns = inspect_bfdlac(_write(tmp_path, "x.bfdlac", data))
    assert chunks == []
    assert warns and "BFDC" in warns[0]


def test_a_file_larger_than_the_read_cap_is_not_called_truncated(tmp_path):
    """A short READ is not a short FILE.

    The walker reads at most _READ_CAP bytes, so comparing a chunk's declared
    size against that buffer reports a perfectly good file as truncated once
    its audio outgrows the cap. rx2 had the identical defect and it called 7 of
    347 real files corrupt; those files were all fine.
    """
    payload = bfdlac._READ_CAP + 1024 * 1024
    body = (b"fmt " + struct.pack(">I", 20)
            + struct.pack(">IIIII", 24, 1, 100, 44100, 2)
            + b"data" + struct.pack(">I", payload) + b"\x00" * payload)
    p = tmp_path / "big.bfdlac"
    p.write_bytes(b"BFDC" + struct.pack(">I", len(body)) + body)

    chunks, _ = bfdlac.inspect_bfdlac(str(p))
    notes = [w for c in chunks for w in c.get("warnings", [])]
    assert any("read window" in w for w in notes), notes
    assert not any("truncated" in w for w in notes), notes


def test_a_genuinely_short_file_still_says_truncated(tmp_path):
    """The other half: the fix must not silence a real defect."""
    body = b"data" + struct.pack(">I", 5_000_000) + b"\x00" * 64
    p = tmp_path / "short.bfdlac"
    p.write_bytes(b"BFDC" + struct.pack(">I", len(body)) + body)

    chunks, _ = bfdlac.inspect_bfdlac(str(p))
    notes = [w for c in chunks for w in c.get("warnings", [])]
    assert any("truncated" in w for w in notes), notes
