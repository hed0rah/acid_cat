"""Shannon-entropy primitives, shared across the forensic/scan modules.

Byte entropy (bits/byte, 0..8) is the workhorse "is this random/encrypted?"
measure; ~8 reads as ciphertext or compressed data, structured data well below.
Before this module it was reimplemented in audioscan, anomalies, triage, viz, and
lsb (as the 2-symbol bit variant) -- identical math, five copies.
"""

import math


def byte_counts(data):
    """A 256-bin histogram of ``data``'s byte values."""
    counts = [0] * 256
    for b in data:
        counts[b] += 1
    return counts


def entropy_from_counts(counts, total):
    """Shannon entropy (bits) of a symbol distribution given per-symbol ``counts``
    and their ``total``. Zero counts contribute nothing; ``total <= 0`` -> 0.0."""
    if total <= 0:
        return 0.0
    h = 0.0
    for c in counts:
        if c:
            p = c / total
            h -= p * math.log2(p)
    return h


def byte_entropy(data):
    """Shannon entropy of raw bytes in bits/byte (0.0 .. 8.0). Empty -> 0.0."""
    n = len(data)
    if n == 0:
        return 0.0
    return entropy_from_counts(byte_counts(data), n)
