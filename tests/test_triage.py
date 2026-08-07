"""Tests for generic structural triage (core/triage.py) and its walk_file fallback."""

import os
import struct

import pytest

from acidcat.core.forensics import triage
from acidcat.core.walk import walk_file, Unsupported


def _chunk(cid, payload):
    return cid + struct.pack(">I", len(payload)) + payload


def _bare_container(magic=b"ZZZZ", endian=">", tags=((b"fmt ", 16), (b"data", 20000))):
    """magic + outer size, then chunks at +8 (the BFDC shape)."""
    body = b"".join(_chunk(t, b"\x00" * n) for t, n in tags)
    return magic + struct.pack(endian + "I", len(body)) + body


def _riff_container(magic=b"RIFX", formtype=b"WXYZ"):
    """magic + size + form-type, then chunks at +12 (the RIFF/FORM shape)."""
    inner = formtype + _chunk(b"fmt ", b"\x00" * 16) + _chunk(b"data", b"\x11" * 8000)
    return magic + struct.pack("<I", len(inner)) + inner


def _write(tmp_path, name, data):
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


def test_bare_shape_detected(tmp_path):
    p = _write(tmp_path, "m.zzz", _bare_container())
    res = triage.generic_walk(p)
    assert res is not None
    label, chunks, warns = res
    assert "likely audio" in label
    assert [c["id"] for c in chunks] == ["ZZZZ", "fmt ", "data"]


def test_riff_shape_detected(tmp_path):
    p = _write(tmp_path, "r.bin", _riff_container())
    label, chunks, _ = triage.generic_walk(p)
    assert "chunked container" in label
    assert "fmt " in [c["id"] for c in chunks]


def test_little_endian_grid(tmp_path):
    p = _write(tmp_path, "le.bin", _bare_container(endian="<"))
    assert triage.generic_walk(p) is not None


def test_no_audio_tags_is_generic(tmp_path):
    p = _write(tmp_path, "g.bin", _bare_container(tags=((b"HEAD", 32), (b"BODY", 9000))))
    label, chunks, _ = triage.generic_walk(p)
    assert label == "unknown chunked container"          # not "(likely audio)"


def test_random_is_none(tmp_path):
    import random
    r = random.Random(1)
    p = _write(tmp_path, "rand.bin", bytes(r.getrandbits(8) for _ in range(40000)))
    assert triage.generic_walk(p) is None


def test_non_printable_magic_none(tmp_path):
    p = _write(tmp_path, "np.bin", b"\x00\x01\x02\x03" + b"\xff" * 100)
    assert triage.generic_walk(p) is None


def test_too_short_none(tmp_path):
    p = _write(tmp_path, "s.bin", b"ABCD")
    assert triage.generic_walk(p) is None


def test_walk_file_falls_back_to_triage(tmp_path):
    p = _write(tmp_path, "mystery.xyz", _bare_container())
    label, chunks, warns = walk_file(p)
    assert "chunked container" in label
    assert any("generic structural triage" in w for w in warns)


def test_walk_file_still_rejects_noise(tmp_path):
    import random
    r = random.Random(2)
    p = _write(tmp_path, "noise.xyz", bytes(r.getrandbits(8) for _ in range(40000)))
    try:
        walk_file(p)
        assert False, "random noise should not triage as a container"
    except Unsupported:
        pass


# ── the display cap must not decide what IS a container ───────────────────

def _grid(tmp_path, name, n, good_wrapper=False):
    """A container of `n` tiling 4-byte chunks. With good_wrapper=False the
    outer size is wrong, so recognition rests on tiling alone -- which is the
    path the cap used to break."""
    body = b"".join(b"CHNK" + struct.pack(">I", 4) + b"aaaa" for _ in range(n))
    outer = len(body) if good_wrapper else 0xDEAD
    p = tmp_path / name
    p.write_bytes(b"BLOB" + struct.pack(">I", outer) + body)
    return str(p)


@pytest.mark.parametrize("n", [200, 256, 257, 300, 1000])
def test_a_large_container_is_still_a_container(tmp_path, n):
    """The display cap used to decide tiling too, so the walk stopped mid-file,
    `tiled` came out False, and anything over 256 chunks was rejected outright
    as "not a recognized audio/preset file". The cliff sat between 256 and 257.
    """
    assert triage.generic_walk(_grid(tmp_path, f"n{n}.bin", n)) is not None


def test_the_reported_count_is_the_real_one(tmp_path):
    _, chunks, _ = triage.generic_walk(_grid(tmp_path, "big.bin", 300))
    assert "300 chunk(s)" in chunks[0]["summary"]


def test_a_truncated_listing_says_so(tmp_path):
    """"257 chunks" and "257 chunks we stopped counting at" must not read the
    same."""
    _, chunks, warns = triage.generic_walk(_grid(tmp_path, "big.bin", 300))
    assert len(chunks) - 1 == triage._LIST_CAP
    assert any("listing the first" in w for w in warns)


def test_a_small_container_gets_no_truncation_warning(tmp_path):
    _, _, warns = triage.generic_walk(_grid(tmp_path, "small.bin", 10))
    assert not any("listing the first" in w for w in warns)
