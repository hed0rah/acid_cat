"""Tests for generic structural triage (core/triage.py) and its walk_file fallback."""

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


def test_the_header_fields_point_at_what_they_name(tmp_path):
    """A field says a value and an offset. Both have to be about the same bytes.

    This chunk's fields ARE the first eight bytes, not a payload behind a
    header, so the default rule in _field_abs (payload base = offset + 8) put
    every one of them eight bytes past what it named: `magic` reported the
    signature's value while pointing at the byte after the size field. In the
    TUI that is the wrong four bytes highlighted, and an edit applied there
    writes over something else.
    """
    from acidcat.core.infra.fieldcodec import _field_abs
    data = _bare_container(magic=b"ABCD", endian="<")
    p = _write(tmp_path, "hdr.bin", data)
    label, chunks, _warns = triage.generic_walk(p)
    header = chunks[0]

    fields = {f["name"]: f for f in header["fields"]}
    magic = fields["magic"]
    at = _field_abs(header, magic)
    assert at == 0, f"magic reports offset 0x{at:x}, but it is the first bytes"
    assert data[at:at + 4] == b"ABCD"
    assert magic["value"] == "ABCD"

    # Position only. Which endianness `declared_size` is READ in comes from the
    # chunk grid rather than from whichever one matched the file length, so its
    # value can disagree with these bytes without this offset being wrong.
    assert _field_abs(header, fields["declared_size"]) == 4


def test_every_positioned_header_field_is_inside_the_chunk(tmp_path):
    """The general form: a field cannot sit past the end of the chunk that
    declares it. An off-by-a-header walks straight out of an 8-byte header."""
    from acidcat.core.infra.fieldcodec import _field_abs
    p = _write(tmp_path, "hdr2.bin", _bare_container(magic=b"WXYZ", endian=">"))
    _label, chunks, _warns = triage.generic_walk(p)
    header = chunks[0]
    start = header.get("offset", 0)
    end = start + header.get("size", 0)
    for f in header["fields"]:
        at = _field_abs(header, f)
        if at is None:
            continue
        assert start <= at < end, (
            f"{f['name']!r} sits at 0x{at:x}, outside its chunk "
            f"0x{start:x}..0x{end:x}")
        assert at + (f.get("len") or 0) <= end, (
            f"{f['name']!r} runs past the end of its chunk")


def test_a_grid_bigger_than_the_read_window_says_so(tmp_path):
    """The count is of the chunks in a 4 MB window, not of the file.

    `_walk_grid` stops where the read stops, so `found` is capped by the same
    window that caps the list -- which is why the existing "listing the first N"
    warning never fires to cover it. A 6 MB container holding 12 chunks
    reported "8 chunk(s)" with no warning at all: a cap wearing a fact's
    clothes, which is the one thing this codebase does not ship.
    """
    n = 12
    parts = [(b"ch%02d" % i, b"\x00" * (512 * 1024)) for i in range(n)]
    body = b"".join(t + struct.pack("<I", len(p)) + p for t, p in parts)
    data = b"BIGC" + struct.pack("<I", len(body) + 4) + b"HDR0" + body
    assert len(data) > triage._READ_CAP, "fixture must exceed the read window"
    p = _write(tmp_path, "big.bin", data)

    label, chunks, warns = triage.generic_walk(p)
    listed = [c for c in chunks if c["id"].startswith("ch")]
    assert len(listed) < n, "fixture did not actually cross the window"

    blame = [w for w in warns if "window" in w or "past that" in w]
    assert blame, f"the grid stopped early and no warning said so: {warns}"
    assert str(triage._READ_CAP) in blame[0] or "4,194,304" in blame[0]

    # and the headline count must not read as the whole file either
    assert "within the first" in chunks[0]["summary"], chunks[0]["summary"]


def test_a_grid_inside_the_window_makes_no_such_claim(tmp_path):
    """The disclosure has to be about this file, not boilerplate on every one."""
    p = _write(tmp_path, "small.bin", _bare_container())
    _label, chunks, warns = triage.generic_walk(p)
    assert not [w for w in warns if "window" in w], warns
    assert "within the first" not in chunks[0]["summary"]
