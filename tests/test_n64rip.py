"""Tests for core.n64rip (container-agnostic N64 VADPCM recovery). The full
coherence-pairing recovery is integration-tested against real ROMs; here we pin
the deterministic pieces: ROM detection, byte-order normalization, and the
codebook scan."""
import array
import struct

from acidcat.core.extract import n64rip


def test_is_n64_rom():
    assert n64rip.is_n64_rom(b"\x80\x37\x12\x40anything")
    assert n64rip.is_n64_rom(b"\x37\x80\x40\x12")          # v64
    assert n64rip.is_n64_rom(b"\x40\x12\x37\x80")          # n64
    assert not n64rip.is_n64_rom(b"RIFF....WAVE")


def test_normalize_byte_orders():
    z = b"\x80\x37\x12\x40" + bytes(range(16))             # native big-endian z64
    assert n64rip.normalize(z) == z
    v = bytearray(z)                                       # v64: swap bytes in each pair
    v[0::2], v[1::2] = z[1::2], z[0::2]
    assert n64rip.normalize(bytes(v)) == z
    a = array.array("I"); a.frombytes(z); a.byteswap()     # n64: reverse each 32-bit word
    assert n64rip.normalize(a.tobytes()) == z


def test_find_codebooks():
    coefs = list(range(-8, 8))                             # 16 s16, one zero -> passes the density gate
    blob = (b"\x80\x37\x12\x40" + bytes(28)
            + struct.pack(">ii", 2, 1) + struct.pack(">16h", *coefs) + bytes(32))
    found = n64rip.find_codebooks(blob)
    assert any(npred == 1 and c == coefs for _off, npred, c in found)


def test_find_codebooks_rejects_noise():
    # an order/npred that decodes but whose coefficients are out of the plausible
    # range (all 20000) must be rejected.
    blob = struct.pack(">ii", 2, 1) + struct.pack(">16h", *([20000] * 16))
    assert n64rip.find_codebooks(blob) == []
