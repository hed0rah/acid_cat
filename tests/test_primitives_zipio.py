"""Tests for core/primitives/zipio.py -- zip local-header data offset."""

import io
import zipfile

from acidcat.core.primitives.zipio import zip_data_offset


def _make_zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for name, data in entries:
            zf.writestr(name, data)
    buf.seek(0)
    return buf


def test_offset_points_at_literal_data():
    payload = b"THE-LITERAL-BYTES-1234567890"
    buf = _make_zip([("short.bin", payload), ("a-much-longer-name.bin", payload)])
    with zipfile.ZipFile(buf) as z:
        for zi in z.infolist():
            off = zip_data_offset(z, zi)
            z.fp.seek(off)
            assert z.fp.read(len(payload)) == payload


def test_matches_hand_rolled():
    """Byte-for-byte the same integer as the inline 30 + namelen + extralen."""
    buf = _make_zip([("name.wav", b"\x00" * 40), ("x.flac", b"\x01" * 5)])
    with zipfile.ZipFile(buf) as z:
        for zi in z.infolist():
            z.fp.seek(zi.header_offset)
            hdr = z.fp.read(30)
            n = int.from_bytes(hdr[26:28], "little")
            m = int.from_bytes(hdr[28:30], "little")
            assert zip_data_offset(z, zi) == zi.header_offset + 30 + n + m
