"""The shared signal primitives (Phase-0 dedup of 5 hand-rolled entropy copies)."""
import math
import os

from acidcat.core.primitives.signal import byte_counts, byte_entropy, entropy_from_counts


def test_byte_counts():
    assert byte_counts(b"") == [0] * 256
    c = byte_counts(b"AAAB")
    assert c[ord("A")] == 3 and c[ord("B")] == 1 and sum(c) == 4


def test_byte_entropy_bounds():
    assert byte_entropy(b"") == 0.0
    assert byte_entropy(b"AAAAAAAA") == 0.0                 # single symbol -> 0 bits
    assert byte_entropy(bytes(range(256))) == 8.0           # uniform 256 -> exactly 8
    assert abs(byte_entropy(b"AB") - 1.0) < 1e-12           # two equal symbols -> 1 bit


def test_byte_entropy_matches_naive():
    def naive(blob):
        if not blob:
            return 0.0
        counts = [0] * 256
        for b in blob:
            counts[b] += 1
        n = len(blob)
        return -sum((c / n) * math.log2(c / n) for c in counts if c)
    for data in (b"hello world" * 7, os.urandom(1000), bytes(range(64)) * 3):
        assert abs(byte_entropy(data) - naive(data)) < 1e-12


def test_entropy_from_counts():
    assert entropy_from_counts([], 0) == 0.0
    assert entropy_from_counts([5, 5], 10) == 1.0           # two equal -> 1 bit
    assert entropy_from_counts([10, 0], 10) == 0.0          # certain -> 0 bits
    # binary form (the old lsb._bit_entropy): ones/total split
    ones, total = 3, 10
    got = entropy_from_counts([ones, total - ones], total)
    p = ones / total
    assert abs(got - (-p * math.log2(p) - (1 - p) * math.log2(1 - p))) < 1e-12
