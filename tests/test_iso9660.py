"""Tests for core.iso9660 -- the ISO 9660 filesystem walk.

Builds a minimal in-memory ISO (system area, a PVD at logical block 16, a root
directory with one file) in both the cooked 2048 layout and the raw Mode2/2352
layout, and asserts walk() finds the file and read_file() returns its bytes.
"""
import struct

import pytest

from acidcat.core.containers import iso9660

_USER = 2048


def _rec(lba, size, is_dir, name):
    b = bytearray()
    b += b"\x00\x00"                                  # length placeholder + ext-attr len
    b += struct.pack("<I", lba) + struct.pack(">I", lba)
    b += struct.pack("<I", size) + struct.pack(">I", size)
    b += b"\x00" * 7                                  # datetime
    b += bytes([0x02 if is_dir else 0x00])           # flags
    b += b"\x00\x00"                                  # unit size + interleave
    b += struct.pack("<H", 1) + struct.pack(">H", 1)  # volume sequence
    b += bytes([len(name)]) + name
    if len(b) % 2:
        b += b"\x00"
    b[0] = len(b)
    return bytes(b)


def _pad(block):
    return block + bytes((-len(block)) % _USER)


def _wrap(sectors, sector_size):
    """Assemble logical 2048-byte sectors into an image in the given layout."""
    if sector_size == 2048:
        return b"".join(sectors)
    out = bytearray()
    for blk in sectors:                              # Mode2/2352: user data at offset 24
        out += b"\x00" + b"\xff" * 10 + b"\x00" + bytes(12) + blk + bytes(2352 - 24 - _USER)
    return bytes(out)


def _build_iso(sector_size, file_data):
    pvd = bytearray(_USER)
    pvd[0] = 1
    pvd[1:6] = b"CD001"
    pvd[156:156 + 34] = _rec(17, _USER, True, b"\x00")     # root dir record -> LBA 17
    root = _pad(_rec(17, _USER, True, b"\x00")
                + _rec(17, _USER, True, b"\x01")
                + _rec(18, len(file_data), False, b"HIT.VAG;1"))
    sectors = [bytes(_USER)] * 16 + [bytes(pvd), root, _pad(file_data)]
    return _wrap(sectors, sector_size)


@pytest.mark.parametrize("sector_size", [2048, 2352])
def test_walk_and_read(tmp_path, sector_size):
    payload = b"SPU-ADPCM-BODY" * 100
    img = tmp_path / "game.bin"
    img.write_bytes(_build_iso(sector_size, payload))

    files = list(iso9660.walk(str(img)))
    assert len(files) == 1
    entry = files[0]
    assert entry["path"] == "HIT.VAG" and entry["lba"] == 18
    assert entry["size"] == len(payload)
    assert iso9660.read_file(str(img), entry) == payload


def test_no_filesystem(tmp_path):
    plain = tmp_path / "plain.bin"
    plain.write_bytes(b"not an iso" + bytes(40000))
    assert list(iso9660.walk(str(plain))) == []
